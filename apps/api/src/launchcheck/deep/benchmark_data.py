"""Kıyas modülünün verisi (PHASE2 §7). Check'ler ve rapor bunu okur."""

from dataclasses import dataclass, field
from typing import Any

from launchcheck.deep.benchmark.compare import Comparison


class PartStatus:
    OK = "ok"
    NOT_ENOUGH = "not_enough_examples"  # 2'den az geçerli örnek: kıyas yapılmaz
    NO_DATA = "no_data"
    PENDING = "pending"


PART_CHECKS: dict[str, tuple[str, ...]] = {
    "compare": ("web.benchmark.common_pattern_missing", "web.benchmark.missing_page_types"),
    "speed": ("web.benchmark.slower_than_peers",),
}


@dataclass
class Example:
    origin: str
    source: str  # competitor | reference
    ok: bool
    reason: str = ""  # robots_blocked | unreachable | bot_challenge | no_anatomy
    cached: bool = False
    popularity: dict[str, Any] = field(default_factory=dict)  # {"crux": bool, "majestic_rank": int}
    anatomy: dict[str, Any] | None = None
    assets: dict[str, str] = field(default_factory=dict)  # mobile/desktop → asset id
    # Önbellekteki ekran görüntüleri (iç kullanım; rapora asset id'leri girer)
    shots: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkData:
    site: str
    category: str | None = None
    category_source: str = "none"  # user | detected | none
    category_scores: dict[str, int] = field(default_factory=dict)
    status: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(PART_CHECKS, PartStatus.PENDING)
    )
    user: dict[str, Any] | None = None  # kullanıcının anatomisi
    user_assets: dict[str, str] = field(default_factory=dict)
    examples: list[Example] = field(default_factory=list)
    comparison: Comparison | None = None

    @property
    def valid(self) -> list[Example]:
        return [e for e in self.examples if e.ok and e.anatomy]

    def skipped_checks(self) -> list[str]:
        return [
            c
            for part, ids in PART_CHECKS.items()
            if self.status[part] != PartStatus.OK
            for c in ids
        ]

    def summary(self) -> dict[str, Any]:
        c = self.comparison
        return {
            "status": dict(self.status),
            "category": self.category,
            "category_source": self.category_source,
            "user_assets": self.user_assets,
            "examples": [
                {
                    "origin": e.origin,
                    "source": e.source,
                    "ok": e.ok,
                    "reason": e.reason,
                    "popularity": e.popularity,
                    "assets": e.assets,
                }
                for e in self.examples
            ],
            "matrix": c.matrix if c else [],
            "patterns": c.patterns if c else [],
            "ahead": c.ahead if c else [],
            "missing_pages": c.missing_pages if c else [],
            "speed": c.speed if c else None,
            "rebuild_sections": c.rebuild_sections if c else [],
        }
