"""Kullanıcı deneyimi check'leri: 404 sayfası ve bağlantı kopunca davranış."""

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web.deep._common import RenderCheck, path_of
from launchcheck.deep.context import DeepContext
from launchcheck.deep.render_data import RenderData

MIN_404_WORDS = 15  # bundan kısa ve ana sayfa linki olmayan 404 sayfası "boş" sayılır


@register
class Custom404Missing(RenderCheck):
    id = "web.ux.custom_404_missing"
    category = Category.CONVERSION
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        probe = data.not_found
        if probe is None:
            return []
        default = probe.default_page is not None
        empty = probe.words < MIN_404_WORDS and not probe.has_home_link
        if not (default or empty):
            return []
        evidence = {
            "path": probe.path,
            "status": probe.status,
            "default_page": probe.default_page,
            "words": probe.words,
            "has_home_link": probe.has_home_link,
        }
        return [self.finding(evidence)]


@register
class OfflineBreaksPage(RenderCheck):
    id = "web.ux.offline_breaks_page"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: RenderData) -> list[Finding]:
        probe = data.offline
        # Klasik (her tıklamada sayfa yüklenen) sitelerde tarayıcı kendi hata sayfasını gösterir
        if probe is None or probe.outcome not in ("blank", "spinner"):
            return []
        return [self.finding({"page": path_of(probe.url), "outcome": probe.outcome})]
