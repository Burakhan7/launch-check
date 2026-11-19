"""Mobil görünüm check'leri: render edilmiş sayfanın farklı genişliklerde ölçümü."""

from typing import Any

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web.deep._common import MAX_ITEMS, MAX_PAGES, RenderCheck, path_of
from launchcheck.deep.context import DeepContext
from launchcheck.deep.render_data import RenderData, ViewportMetrics

MOBILE_WIDTHS = (320, 375)
OVERFLOW_TOLERANCE_PX = 4  # yuvarlama farkları
MAX_ELEMENTS = 5
MIN_SMALL_TARGETS = 3  # tek bir küçük ikon bulgu sayılmaz


def _mobile_metrics(widths: dict[int, ViewportMetrics]) -> list[ViewportMetrics]:
    return [widths[w] for w in MOBILE_WIDTHS if w in widths]


@register
class HorizontalOverflow(RenderCheck):
    id = "web.mobile.horizontal_overflow"
    category = Category.CONVERSION
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        pages: list[dict[str, Any]] = []
        for page in data.pages:
            for metrics in _mobile_metrics(page.widths or {}):
                # Viewport etiketi yoksa sayfa 980px'te çizilir: bu viewport_missing'in konusu
                if metrics.inner_width != metrics.width:
                    continue
                if metrics.overflow_px > OVERFLOW_TOLERANCE_PX:
                    pages.append(
                        {
                            "url": path_of(page.url),
                            "width": metrics.width,
                            "overflow_px": metrics.overflow_px,
                            "elements": [b.as_evidence() for b in metrics.overflowing][
                                :MAX_ELEMENTS
                            ],
                        }
                    )
                    break  # sayfa başına en dar genişlik yeter
        return [self.finding({"pages": pages[:MAX_ITEMS], "count": len(pages)})] if pages else []


@register
class OverlappingElements(RenderCheck):
    id = "web.mobile.overlapping_elements"
    category = Category.CONVERSION
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for page in data.pages:
            for width, metrics in sorted((page.widths or {}).items()):
                for a, b in metrics.overlaps:
                    key = (a.selector + a.text, b.selector + b.text)
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(
                        {
                            "page": path_of(page.url),
                            "width": width,
                            "first": a.as_evidence(),
                            "second": b.as_evidence(),
                        }
                    )
        if not items:
            return []
        return [self.finding({"overlaps": items[:MAX_ITEMS], "count": len(items)})]


@register
class CtaBelowFold(RenderCheck):
    id = "web.mobile.cta_below_fold"
    category = Category.CONVERSION
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        home = data.pages[0]
        metrics = home.mobile
        # CTA bulunamadıysa karar verilemez (yanlış alarm yerine sessiz kalınır)
        if metrics is None or metrics.primary_cta is None:
            return []
        cta = metrics.primary_cta
        if cta.y < metrics.height:
            return []
        evidence = {
            "cta_text": cta.text,
            "cta_top_px": round(cta.y),
            "viewport_height": metrics.height,
            "screens_down": round(cta.y / metrics.height, 1),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"cta_text": str(evidence.get("cta_text") or "")}


@register
class TapTargetsSmall(RenderCheck):
    id = "web.mobile.tap_targets_small"
    category = Category.CONVERSION
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        pages = []
        for page in data.pages:
            metrics = page.mobile
            if metrics is None or len(metrics.small_targets) < MIN_SMALL_TARGETS:
                continue
            pages.append(
                {
                    "url": path_of(page.url),
                    "count": len(metrics.small_targets),
                    "examples": [b.as_evidence() for b in metrics.small_targets][:MAX_ELEMENTS],
                }
            )
        return [self.finding({"pages": pages[:MAX_PAGES]})] if pages else []
