"""Kıyas check'leri (PHASE2 §7.4). Veri: `ctx.benchmark` (deterministik karşılaştırma).

En az 2 geçerli örnek yoksa check'ler hiç çalışmaz (atlanır, "geçti" sayılmaz). Kanıtta örnek
sitelerin metni yok; sadece desen adı ve "kaç örnekte var" sayısı.
"""

from abc import abstractmethod
from typing import Any, ClassVar

from launchcheck.checks.base import (
    Category,
    Check,
    Finding,
    FragmentFn,
    ScanContext,
    Severity,
    TargetType,
)
from launchcheck.checks.registry import register
from launchcheck.deep.benchmark.compare import SLOWER_FACTOR, STRONG_SHARE
from launchcheck.deep.benchmark_data import BenchmarkData
from launchcheck.deep.context import DeepContext


class BenchmarkCheck(Check):
    target = TargetType.WEB
    phase = 2
    category = Category.CONVERSION
    module: ClassVar[str | None] = "benchmark"

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, DeepContext):
            raise TypeError("DeepContext bekleniyordu")
        if ctx.benchmark is None or ctx.benchmark.comparison is None:
            return []
        return await self.check(ctx, ctx.benchmark)

    @abstractmethod
    async def check(self, ctx: DeepContext, data: BenchmarkData) -> list[Finding]: ...


def _examples(data: BenchmarkData) -> list[dict[str, str]]:
    return [{"origin": e.origin, "source": e.source} for e in data.valid]


@register
class CommonPatternMissing(BenchmarkCheck):
    id = "web.benchmark.common_pattern_missing"
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: BenchmarkData) -> list[Finding]:
        patterns = data.comparison.patterns if data.comparison else []
        if not patterns:
            return []
        strong = any(p["share"] >= STRONG_SHARE for p in patterns)
        evidence = {"patterns": patterns, "examples": _examples(data), "category": data.category}
        return [self.finding(evidence, Severity.MEDIUM if strong else Severity.LOW)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        items = [
            fragment(f"pattern_{p['key']}") + f" ({p['have']}/{p['total']})"
            for p in evidence.get("patterns") or []
        ]
        return {"count": str(len(items)), "patterns": "; ".join(items[:4])}


@register
class MissingPageTypes(BenchmarkCheck):
    id = "web.benchmark.missing_page_types"
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: BenchmarkData) -> list[Finding]:
        missing = data.comparison.missing_pages if data.comparison else []
        if not missing:
            return []
        return [self.finding({"pages": missing, "examples": _examples(data)})]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        pages = [
            fragment(f"page_{p['type']}") + f" ({p['have']}/{p['total']})"
            for p in evidence.get("pages") or []
        ]
        return {"pages": ", ".join(pages)}


@register
class SlowerThanPeers(BenchmarkCheck):
    """LCP örneklerin ortancasının 1,5 katından kötü. Saha verisi (CrUX) sende ve en az 2 örnekte
    varsa o, yoksa aynı tarayıcıda aynı yöntemle ölçülmüş LCP; iki yöntem karıştırılmaz."""

    id = "web.benchmark.slower_than_peers"
    category = Category.PERFORMANCE
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: BenchmarkData) -> list[Finding]:
        speed = data.comparison.speed if data.comparison else None
        if not speed or not speed["median_ms"]:
            return []
        if speed["user_ms"] <= SLOWER_FACTOR * speed["median_ms"]:
            return []
        evidence = {
            **speed,
            "ratio": round(speed["user_ms"] / speed["median_ms"], 1),
            "examples": _examples(data),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {
            "user": f"{float(evidence.get('user_ms') or 0) / 1000:.1f}",
            "median": f"{float(evidence.get('median_ms') or 0) / 1000:.1f}",
            "ratio": str(evidence.get("ratio", "")),
            "method": fragment(f"method_{evidence.get('method', 'lab')}"),
        }
