"""Tarayıcıdan gelen ham kayıtları (istekler, çerez adları, script'ler) bulgu verisine çevirir.
Tarayıcıdan bağımsızdır; birim testleri bu fonksiyonları doğrudan çağırır."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from launchcheck.checks.shared.domains import registered_domain
from launchcheck.deep.privacy_data import LibraryFinding, PhaseResult, RiskyScript, TrackerHit
from launchcheck.deep.supply.retire import SEVERITIES, RetireDb
from launchcheck.deep.supply.trackers import Tracker, TrackerDb

if TYPE_CHECKING:
    from launchcheck.config import Settings
    from launchcheck.deep.browser.privacy import CookieRecord, RequestRecord

logger = logging.getLogger(__name__)
RETIRE_FILE = "jsrepository.json"
MAX_LIBRARIES = 15


@dataclass
class _Bucket:
    tracker: Tracker
    domain: str
    requests: int = 0
    cookies: list[str] = field(default_factory=list)


def classify_phase(
    requests: Iterable["RequestRecord"],
    cookies: Iterable["CookieRecord"],
    db: TrackerDb,
    site_domain: str,
) -> PhaseResult:
    """Birinci taraf istekleri sayılmaz (sitenin kendi alan adı). Yükleyiciler (GTM) ve Consent
    Mode "reddedildi" ping'leri takip sayılmaz ama ayrıca raporlanır."""
    result = PhaseResult()
    buckets: dict[str, _Bucket] = {}
    for record in requests:
        result.requests += 1
        if not record.host or registered_domain(record.host) == site_domain:
            continue
        tracker = db.match(record.host)
        if tracker is None or tracker.category == "exempt":
            continue
        if tracker.category == "tag_manager":
            if tracker.name not in result.tag_managers:
                result.tag_managers.append(tracker.name)
            continue
        if record.consent_denied:
            result.consent_mode_pings += 1
            continue
        if not tracker.tracking:
            continue
        domain = registered_domain(record.host) or record.host
        buckets.setdefault(tracker.name, _Bucket(tracker, domain)).requests += 1
    for cookie in cookies:
        tracker = db.cookie_tracker(cookie.name)
        if tracker is None and cookie.domain and registered_domain(cookie.domain) != site_domain:
            matched = db.match(cookie.domain)
            tracker = matched if matched is not None and matched.tracking else None
        if tracker is None:
            continue
        domain = tracker.domains[0] if tracker.domains else cookie.domain
        entry = buckets.setdefault(tracker.name, _Bucket(tracker, domain))
        if cookie.name not in entry.cookies and len(entry.cookies) < 10:
            entry.cookies.append(cookie.name)
    result.trackers = sorted(
        (
            TrackerHit(b.tracker.name, b.domain, b.tracker.category, b.requests, tuple(b.cookies))
            for b in buckets.values()
        ),
        key=lambda h: (-len(h.cookies), -h.requests, h.name),
    )
    return result


def _worst(severities: Iterable[str]) -> str:
    return max(severities, key=SEVERITIES.index, default="low")


def scan_libraries(db: RetireDb, scripts: dict[str, str | None]) -> list[LibraryFinding]:
    """Her script adresi ve (varsa) gövdesi; aynı kütüphane+sürüm bir kez raporlanır."""
    found: dict[tuple[str, str], LibraryFinding] = {}
    for url, body in scripts.items():
        for detection in db.detect(url, body):
            key = (detection.library, detection.version)
            if key in found:
                continue
            vulns = db.vulnerabilities(detection.library, detection.version)
            if not vulns:
                continue
            found[key] = LibraryFinding(
                library=detection.library,
                version=detection.version,
                url=detection.url,
                severity=_worst(v.severity for v in vulns),
                vulnerabilities=tuple(
                    {
                        "severity": v.severity,
                        "cves": list(v.cves),
                        "summary": v.summary,
                        "info": v.info,
                        "fixed_in": v.below or "",
                    }
                    for v in sorted(vulns, key=lambda v: -SEVERITIES.index(v.severity))[:5]
                ),
            )
    ordered = sorted(found.values(), key=lambda f: -SEVERITIES.index(f.severity))
    return ordered[:MAX_LIBRARIES]


def risky_scripts(urls: Iterable[str], domains: dict[str, str]) -> list[RiskyScript]:
    out: list[RiskyScript] = []
    seen: set[str] = set()
    for url in urls:
        host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
        for domain, source in domains.items():
            if (host == domain or host.endswith("." + domain)) and url not in seen:
                seen.add(url)
                out.append(RiskyScript(url=url, domain=domain, source=source))
    return out


# --- retire veritabanı: imajdaki kopya + haftalık güncel kopya

_CACHE: dict[tuple[str, float], RetireDb] = {}


def retire_paths(settings: "Settings") -> list[Path]:
    return [
        Path(settings.deep_retire_update_dir) / RETIRE_FILE,
        Path(settings.deep_retire_db_path),
    ]


def load_current_retire(settings: "Settings") -> RetireDb | None:
    """Güncel kopya geçerliyse o, değilse imajdaki kopya; ikisi de yoksa None (check atlanır)."""
    for path in retire_paths(settings):
        try:
            if not path.is_file():
                continue
            key = (str(path), path.stat().st_mtime)
        except OSError:
            continue
        if key not in _CACHE:
            db = RetireDb.load(path)
            if db is None:
                continue
            _CACHE.clear()
            _CACHE[key] = db
        return _CACHE[key]
    return None
