"""Yasal sayfalar (gizlilik politikası / KVKK, kullanım şartları).

SPA'larda (Lovable, Bolt...) linkler JS ile çizildiği için ham HTML'de görünmez. Bu yüzden
HTML'de link yoksa JS bundle'larında rota/metin aranır. Faz 2'de tarayıcıyla render edilecek.
"""

import re

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, html_tree, js_sources
from launchcheck.collectors.web_collector import WebContext

PRIVACY_RE = re.compile(
    r"privacy|gizlilik|kvkk|ki[sş]isel[\s_-]*veri|ayd[ıi]nlatma|datenschutz|cookie[\s_-]*policy"
    r"|[cç]erez[\s_-]*politika",
    re.IGNORECASE,
)
TERMS_RE = re.compile(
    r"terms|conditions|\btos\b|kullan[ıi]m[\s_-]*(?:[sş]artlar|ko[sş]ullar)|hizmet[\s_-]*[sş]art"
    r"|s[oö]zle[sş]me|eula|legal",
    re.IGNORECASE,
)
# JS içinde: "/privacy" gibi bir rota ya da link metni olarak geçen string
JS_ROUTE_TEMPLATE = r"""["'`]/?[\w/-]*(?:{kw})[\w/-]*["'`]"""


def _html_links_match(ctx: WebContext, pattern: re.Pattern[str]) -> bool:
    for node in html_tree(ctx).css("a"):
        href = node.attributes.get("href") or ""
        if pattern.search(href) or pattern.search(node.text(deep=True)):
            return True
    return False


def _js_mentions(ctx: WebContext, pattern: re.Pattern[str]) -> bool:
    route = re.compile(JS_ROUTE_TEMPLATE.format(kw=pattern.pattern), re.IGNORECASE)
    return any(route.search(text) for _, text in js_sources(ctx))


class _LegalPageCheck(WebCheck):
    pattern: re.Pattern[str]

    async def check(self, ctx: WebContext) -> list[Finding]:
        if _html_links_match(ctx, self.pattern) or _js_mentions(ctx, self.pattern):
            return []
        return [self.finding({"searched": ["html_links", "js_routes"]})]


@register
class PrivacyMissing(_LegalPageCheck):
    id = "web.legal.privacy_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM
    pattern = PRIVACY_RE


@register
class TermsMissing(_LegalPageCheck):
    id = "web.legal.terms_missing"
    category = Category.PROD_READINESS
    default_severity = Severity.LOW
    pattern = TERMS_RE
