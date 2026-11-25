"""Takipçi tanıma: elle tutulan liste (`trackers.yaml`) + EasyPrivacy alan adları.

EasyPrivacy (https://easylist.to, CC BY-SA 3.0; decisions P0): liste sadece eşleştirme verisi
olarak okunur, değiştirilmiş hâli dağıtılmaz. Sadece "alan adını engelle" kuralları alınır:
`||alanadi^` ve seçenekleri sadece kaynak türü / `third-party` olan satırlar. Yol içeren,
`domain=` ile sınırlı, istisna (`@@`) ve sadece birinci taraf (`~third-party`) kurallar alınmaz.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml

TRACKING = frozenset({"analytics", "ads", "session_recording", "social"})
EASYPRIVACY_RULE_RE = re.compile(r"^\|\|([a-z0-9][a-z0-9.-]*[a-z0-9])\^(?:\$([a-z,-]+))?$")
ALLOWED_OPTIONS = frozenset(
    {"third-party", "script", "image", "xmlhttprequest", "ping", "subdocument", "other", "media"}
)
MAX_LIST_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Tracker:
    name: str
    category: str
    domains: tuple[str, ...]
    cookies: tuple[str, ...] = ()

    @property
    def tracking(self) -> bool:
        return self.category in TRACKING


@dataclass
class TrackerDb:
    curated: list[Tracker]
    easyprivacy: frozenset[str] = frozenset()
    _by_domain: dict[str, Tracker] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for tracker in self.curated:
            for domain in tracker.domains:
                self._by_domain.setdefault(domain, tracker)

    def match(self, host: str) -> Tracker | None:
        """Host'un kendisi veya üst alan adlarından biri listede mi? Elle tutulan liste
        önceliklidir (çerezsiz araçlar EasyPrivacy'de olsa bile `exempt` kalır)."""
        host = host.lower().rstrip(".")
        labels = host.split(".")
        candidates = [".".join(labels[i:]) for i in range(len(labels) - 1)]
        for candidate in candidates:
            tracker = self._by_domain.get(candidate)
            if tracker is not None:
                return tracker
        for candidate in candidates:
            if candidate in self.easyprivacy:
                return Tracker(candidate, "analytics", (candidate,))
        return None

    def cookie_tracker(self, name: str) -> Tracker | None:
        for tracker in self.curated:
            if not tracker.tracking:
                continue
            for pattern in tracker.cookies:
                if pattern.endswith("*") and name.startswith(pattern[:-1]):
                    return tracker
                if name == pattern:
                    return tracker
        return None


def parse_easyprivacy(text: str) -> frozenset[str]:
    domains: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        match = EASYPRIVACY_RULE_RE.match(line)
        if not match:
            continue
        options = set(filter(None, (match.group(2) or "").split(",")))
        if options - ALLOWED_OPTIONS:
            continue
        domains.add(match.group(1))
    return frozenset(domains)


def load_curated() -> list[Tracker]:
    text = resources.files("launchcheck.deep.supply").joinpath("trackers.yaml").read_text("utf-8")
    items = yaml.safe_load(text) or []
    return [
        Tracker(
            name=str(item["name"]),
            category=str(item["category"]),
            domains=tuple(str(d).lower() for d in item.get("domains") or []),
            cookies=tuple(str(c) for c in item.get("cookies") or []),
        )
        for item in items
    ]


@lru_cache(maxsize=4)
def load_tracker_db(easyprivacy_path: str) -> TrackerDb:
    """Liste dosyası yoksa veya okunamazsa sadece elle tutulan liste kullanılır."""
    domains: frozenset[str] = frozenset()
    path = Path(easyprivacy_path) if easyprivacy_path else None
    if path is not None and path.is_file() and path.stat().st_size <= MAX_LIST_BYTES:
        domains = parse_easyprivacy(path.read_text("utf-8", errors="replace"))
    return TrackerDb(load_curated(), domains)
