"""Hız check'leri (PHASE2 §6.2). Veri: Lighthouse (laboratuvar, mobil ortanca) + CrUX (saha).

LCP/CLS: saha verisi varsa o (gerçek kullanıcılar) kullanılır, yoksa laboratuvar ortancası.
INP sadece saha verisinden. Diğerleri laboratuvar ölçümünden; kanıta Lighthouse'un kendi
"tahmini kazanç" değeri yazılır.
"""

from abc import abstractmethod
from typing import Any, ClassVar
from urllib.parse import urlsplit

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
from launchcheck.config import get_settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.perf_data import MOBILE, LighthouseRun, PerfData

MAX_ITEMS = 5
KB = 1024
# Kanıtta adı geçebilen Lighthouse önerileri (i18n fragments: opp_<id>)
KNOWN_OPPORTUNITIES = {
    "render-blocking-insight",
    "image-delivery-insight",
    "unused-javascript",
    "unused-css-rules",
    "cache-insight",
    "document-latency-insight",
    "lcp-discovery-insight",
    "font-display-insight",
    "legacy-javascript-insight",
    "duplicated-javascript-insight",
    "unminified-javascript",
    "unminified-css",
    "redirects",
    "server-response-time",
    "modern-http-insight",
    "network-dependency-tree-insight",
    "third-parties-insight",
}


class PerfCheck(Check):
    target = TargetType.WEB
    phase = 2
    category = Category.PERFORMANCE
    module: ClassVar[str | None] = "perf"

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, DeepContext):
            raise TypeError("DeepContext bekleniyordu")
        if ctx.perf is None:
            return []
        return await self.check(ctx, ctx.perf)

    @abstractmethod
    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]: ...


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def rating(value: float, good: float, poor: float) -> str:
    if value <= good:
        return "good"
    return "poor" if value > poor else "needs_improvement"


def severity_for(rate: str) -> Severity | None:
    return {"poor": Severity.HIGH, "needs_improvement": Severity.MEDIUM}.get(rate)


def kb(value: float) -> int:
    return round(value / KB)


def median_runs(data: PerfData) -> list[LighthouseRun]:
    """Her mobil sayfanın ortanca ölçümü (ana sayfa önce)."""
    return [r for u in data.mobile_urls if (r := data.median_run(u, MOBILE)) is not None]


def seconds(fragment: FragmentFn, ms: float | int | None) -> str:
    value = round((ms or 0) / 1000, 1)
    whole, frac = divmod(round(value * 10), 10)
    return fragment("seconds", whole=whole, frac=frac)


def opportunity_text(fragment: FragmentFn, evidence: dict[str, Any]) -> str:
    opportunities = evidence.get("opportunities") or []
    known = [o for o in opportunities if o.get("audit") in KNOWN_OPPORTUNITIES]
    if not known:
        return ""
    top = known[0]
    name = fragment("opp_" + str(top["audit"]).replace("-", "_"))
    return fragment("top_fix", fix=name, saving=seconds(fragment, top.get("savings_ms")))


# --- Core Web Vitals


class _VitalCheck(PerfCheck):
    """LCP/CLS: saha verisi (CrUX p75) varsa o, yoksa sayfaların laboratuvar ortancası."""

    metric: ClassVar[str]  # PerfData/FieldData alan adı
    good_attr: ClassVar[str]
    poor_attr: ClassVar[str]

    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]:
        s = get_settings()
        good, poor = getattr(s, self.good_attr), getattr(s, self.poor_attr)
        lab = [
            {"page": path_of(u), "value": v}
            for u in data.mobile_urls
            if (v := data.median(u, self.metric, MOBILE)) is not None
        ]
        field_value = getattr(data.field, self.metric) if data.field else None
        if field_value is not None:
            value = float(field_value)
            source: dict[str, Any] = {
                "source": "field",
                "scope": data.field.scope if data.field else None,
            }
            worst_url = data.home_url
        elif lab:
            worst = max(lab, key=lambda p: p["value"])
            value = float(worst["value"])
            source = {"source": "lab", "page": worst["page"]}
            worst_url = next((u for u in data.mobile_urls if path_of(u) == worst["page"]), "")
        else:
            return []
        rate = rating(value, good, poor)
        severity = severity_for(rate)
        if severity is None:
            return []
        median = data.median_run(worst_url, MOBILE) or data.median_run(data.home_url, MOBILE)
        evidence = {
            **source,
            "value": round(value, 3) if self.metric == "cls" else round(value),
            "rating": rate,
            "good_threshold": good,
            "lab": [
                {**p, "value": round(p["value"], 3) if self.metric == "cls" else round(p["value"])}
                for p in lab
            ][:MAX_ITEMS],
            "opportunities": self._opportunities(median),
        }
        return [self.finding(evidence, severity)]

    def _opportunities(self, run: LighthouseRun | None) -> list[dict[str, Any]]:
        return []

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {
            "value": self._format(fragment, evidence.get("value")),
            "source": fragment(f"source_{evidence.get('source', 'lab')}"),
            "top_fix": opportunity_text(fragment, evidence),
        }

    def _format(self, fragment: FragmentFn, value: Any) -> str:
        return seconds(fragment, value)


@register
class LcpSlow(_VitalCheck):
    id = "web.perf.lcp_slow"
    default_severity = Severity.MEDIUM
    metric = "lcp_ms"
    good_attr = "deep_perf_lcp_good_ms"
    poor_attr = "deep_perf_lcp_poor_ms"

    def _opportunities(self, run: LighthouseRun | None) -> list[dict[str, Any]]:
        if run is None:
            return []
        return [{"audit": o.audit, "savings_ms": o.savings_ms} for o in run.lcp_opportunities]


@register
class ClsHigh(_VitalCheck):
    id = "web.perf.cls_high"
    default_severity = Severity.MEDIUM
    metric = "cls"
    good_attr = "deep_perf_cls_good"
    poor_attr = "deep_perf_cls_poor"

    def _format(self, fragment: FragmentFn, value: Any) -> str:
        whole, frac = divmod(round(float(value or 0) * 100), 100)
        return fragment("decimal", whole=whole, frac=f"{frac:02d}")


@register
class InpSlow(PerfCheck):
    """Sadece saha verisi: laboratuvarda gerçek etkileşim ölçülemez."""

    id = "web.perf.inp_slow"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]:
        if data.field is None or data.field.inp_ms is None:
            return []
        s = get_settings()
        value = data.field.inp_ms
        rate = rating(value, s.deep_perf_inp_good_ms, s.deep_perf_inp_poor_ms)
        severity = severity_for(rate)
        if severity is None:
            return []
        evidence = {
            "source": "field",
            "scope": data.field.scope,
            "value": round(value),
            "rating": rate,
            "good_threshold": s.deep_perf_inp_good_ms,
        }
        return [self.finding(evidence, severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"value": str(evidence.get("value", ""))}


# --- Laboratuvar bulguları


@register
class BlankScreenLong(PerfCheck):
    id = "web.perf.blank_screen_long"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]:
        limit = get_settings().deep_perf_blank_screen_ms
        pages = [
            {
                "page": path_of(u),
                "blank_ms": round(blank),
                "fcp_ms": round(data.median(u, "fcp_ms") or 0),
            }
            for u in data.mobile_urls
            if (blank := data.median(u, "blank_ms")) is not None and blank > limit
        ]
        if not pages:
            return []
        worst = max(pages, key=lambda p: p["blank_ms"])
        filmstrip = (
            ctx.meta.get("perf_filmstrip") if worst["page"] == path_of(data.home_url) else None
        )
        evidence = {
            **worst,
            "pages": pages[:MAX_ITEMS],
            "threshold_ms": limit,
            "filmstrip_asset": (filmstrip or {}).get("asset_id"),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"value": seconds(fragment, evidence.get("blank_ms"))}


@register
class UnusedJs(PerfCheck):
    id = "web.perf.unused_js"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]:
        s = get_settings()
        hits = []
        for run in median_runs(data):
            total = max(run.total_js_bytes, sum(i.total_bytes for i in run.unused_js))
            if total <= 0:
                continue
            ratio = run.unused_js_bytes / total
            if (
                ratio > s.deep_perf_unused_js_ratio
                and run.unused_js_bytes > s.deep_perf_unused_js_min_bytes
            ):
                hits.append((run, total, ratio))
        if not hits:
            return []
        run, total, ratio = max(hits, key=lambda h: h[0].unused_js_bytes)
        evidence = {
            "page": path_of(run.url),
            "unused_kb": kb(run.unused_js_bytes),
            "total_kb": kb(total),
            "unused_pct": round(ratio * 100),
            "savings_ms": round(run.unused_js_savings_ms),
            "files": [
                {
                    "url": i.url,
                    "unused_kb": kb(i.wasted_bytes),
                    "total_kb": kb(i.total_bytes),
                    "third_party": i.third_party,
                }
                for i in run.unused_js[:MAX_ITEMS]
            ],
            "pages": [path_of(h[0].url) for h in hits],
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        saving = evidence.get("savings_ms") or 0
        return {
            "unused_kb": str(evidence.get("unused_kb", 0)),
            "total_kb": str(evidence.get("total_kb", 0)),
            "unused_pct": str(evidence.get("unused_pct", 0)),
            "saving": fragment("saving", value=seconds(fragment, saving)) if saving >= 100 else "",
        }


@register
class OversizedImages(PerfCheck):
    id = "web.perf.oversized_images"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]:
        s = get_settings()
        hits = [
            r
            for r in median_runs(data)
            if r.image_savings_bytes >= s.deep_perf_image_min_waste_bytes
            or len(r.unsized_images) >= s.deep_perf_unsized_images_min
        ]
        if not hits:
            return []
        run = max(hits, key=lambda r: (r.image_savings_bytes, len(r.unsized_images)))
        evidence = {
            "page": path_of(run.url),
            "savings_kb": kb(run.image_savings_bytes),
            "savings_ms": round(run.image_savings_ms),
            "images": [
                {
                    "url": i.url,
                    "total_kb": kb(i.total_bytes),
                    "savings_kb": kb(i.wasted_bytes),
                    "reasons": list(i.reasons),
                    "natural": list(i.natural) if i.natural else None,
                    "displayed": list(i.displayed) if i.displayed else None,
                }
                for i in run.images[:MAX_ITEMS]
                if i.wasted_bytes > 0
            ],
            "unsized": run.unsized_images[:MAX_ITEMS],
            "unsized_count": len(run.unsized_images),
            "pages": [path_of(r.url) for r in hits],
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        saving = evidence.get("savings_ms") or 0
        savings_kb = evidence.get("savings_kb") or 0
        unsized = evidence.get("unsized_count") or 0
        return {
            "weight": fragment("weight", kb=savings_kb) if savings_kb else "",
            "unsized": fragment("unsized", count=unsized)
            if unsized >= get_settings().deep_perf_unsized_images_min
            else "",
            "saving": fragment("saving", value=seconds(fragment, saving)) if saving >= 100 else "",
        }


@register
class NoCompressionOrCache(PerfCheck):
    """Metin dosyaları (JS/CSS/HTML/JSON) sıkıştırılmadan geliyor veya sitenin kendi statik
    dosyaları çok kısa (veya hiç) önbelleklenmiyor."""

    id = "web.perf.no_compression_or_cache"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]:
        s = get_settings()
        uncompressed: dict[str, dict[str, Any]] = {}
        short_cache: dict[str, dict[str, Any]] = {}
        for run in median_runs(data):
            for u in run.uncompressed:
                if u.resource_bytes >= s.deep_perf_uncompressed_min_bytes:
                    uncompressed[u.url] = {"url": u.url, "size_kb": kb(u.resource_bytes)}
            for c in run.short_cache:
                if c.ttl_s < s.deep_perf_short_cache_s:
                    short_cache[c.url] = {
                        "url": c.url,
                        "ttl_s": c.ttl_s,
                        "size_kb": kb(c.total_bytes),
                    }
        compression = (
            sum(u["size_kb"] for u in uncompressed.values()) * KB
            >= s.deep_perf_uncompressed_total_bytes
        )
        cache = (
            sum(c["size_kb"] for c in short_cache.values()) * KB
            >= s.deep_perf_short_cache_min_bytes
        )
        if not (compression or cache):
            return []
        problems = "both" if compression and cache else "compression" if compression else "cache"
        evidence = {
            "problem": problems,
            "uncompressed": sorted(uncompressed.values(), key=lambda i: -i["size_kb"])[:MAX_ITEMS]
            if compression
            else [],
            "short_cache": sorted(short_cache.values(), key=lambda i: -i["size_kb"])[:MAX_ITEMS]
            if cache
            else [],
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"problem": fragment(f"problem_{evidence.get('problem', 'both')}")}


@register
class HeavyThirdParty(PerfCheck):
    id = "web.perf.heavy_third_party"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PerfData) -> list[Finding]:
        s = get_settings()
        run = data.median_run(data.home_url, MOBILE)
        if run is None or not run.third_parties:
            return []
        total_bytes = sum(t.transfer_bytes for t in run.third_parties)
        main_thread = sum(t.main_thread_ms for t in run.third_parties)
        if (
            total_bytes <= s.deep_perf_third_party_bytes
            and main_thread <= s.deep_perf_third_party_main_thread_ms
        ):
            return []
        evidence = {
            "total_kb": kb(total_bytes),
            "main_thread_ms": round(main_thread),
            "entities": [
                {
                    "name": t.entity,
                    "size_kb": kb(t.transfer_bytes),
                    "main_thread_ms": round(t.main_thread_ms),
                }
                for t in run.third_parties[:MAX_ITEMS]
            ],
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        names = [str(e.get("name")) for e in evidence.get("entities") or []][:3]
        return {
            "total_kb": str(evidence.get("total_kb", 0)),
            "main_thread": seconds(fragment, evidence.get("main_thread_ms")),
            "names": ", ".join(names),
        }
