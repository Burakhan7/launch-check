"""Alt alan adı ele geçirme riski (sadece okuma; servis asla "claim" edilmez).

Sinyal iki parçalı olmalı: (1) CNAME zinciri bilinen bir barındırma servisine yöneliyor ve
(2) o servis "böyle bir proje/uygulama yok" diyor (sayfa parmak izi) veya CNAME hedefi DNS'te
hiç yok (NXDOMAIN tipi servisler). Sadece CNAME'in servise gitmesi bulgu değildir: çalışan
sitelerin çoğu da böyle yapılandırılır.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from launchcheck.deep.external.dns import DnsClient, addresses
from launchcheck.deep.external_data import TakeoverRisk

FINGERPRINTS_FILE = Path(__file__).with_name("takeover_fingerprints.yaml")
# (host) → ana sayfa metni (https, olmazsa http) veya None
FetchBody = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class Fingerprint:
    service: str
    cnames: tuple[str, ...]
    patterns: tuple[re.Pattern[str], ...]
    nxdomain: bool
    confidence: str


@lru_cache(maxsize=4)
def load_fingerprints(path: Path = FINGERPRINTS_FILE) -> tuple[Fingerprint, ...]:
    entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    out = []
    for e in entries:
        texts = [str(t) for t in e.get("fingerprints") or []]
        regex = bool(e.get("regex"))
        patterns = tuple(re.compile(t if regex else re.escape(t), re.IGNORECASE) for t in texts)
        out.append(
            Fingerprint(
                service=str(e["service"]),
                cnames=tuple(str(c).lower() for c in e.get("cname") or []),
                patterns=patterns,
                nxdomain=bool(e.get("nxdomain")),
                confidence=str(e.get("confidence") or "edge"),
            )
        )
    return tuple(out)


def _ends_with(name: str, suffix: str) -> bool:
    return name == suffix or name.endswith("." + suffix)


def match_service(
    chain: list[str], fingerprints: tuple[Fingerprint, ...]
) -> tuple[Fingerprint, str] | None:
    for name in chain:
        for fp in fingerprints:
            if any(_ends_with(name, s) for s in fp.cnames):
                return fp, name
    return None


async def assess(
    host: str,
    chain: list[str],
    dns: DnsClient,
    fetch_body: FetchBody,
    fingerprints: tuple[Fingerprint, ...] | None = None,
) -> TakeoverRisk | None:
    matched = match_service(chain, fingerprints or load_fingerprints())
    if matched is None:
        return None
    fp, cname = matched
    if fp.nxdomain:
        if await addresses(dns, chain[-1]) is None:
            return TakeoverRisk(host, chain[-1], fp.service, "nxdomain", fp.confidence)
        return None
    if not fp.patterns:
        return None
    body = await fetch_body(host)
    if body and any(p.search(body) for p in fp.patterns):
        return TakeoverRisk(host, cname, fp.service, "fingerprint", fp.confidence)
    return None
