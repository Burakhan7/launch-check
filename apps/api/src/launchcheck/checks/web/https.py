"""HTTPS kullanımı ve karışık içerik."""

from urllib.parse import urlsplit

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import MAX_EVIDENCE_ITEMS, WebCheck, html_tree
from launchcheck.collectors.fetcher import REDIRECT_STATUSES
from launchcheck.collectors.web_collector import WebContext


@register
class HttpsMissing(WebCheck):
    id = "web.https.missing"
    category = Category.SECURITY
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        if not ctx.is_https:
            return [self.finding({"reason": "no_https", "final_url": ctx.final_url})]
        host = urlsplit(ctx.final_url).netloc
        res = await ctx.fetch_url(f"http://{host}/")
        if res is None:  # 80 portu kapalı: düz HTTP hiç sunulmuyor, sorun yok
            return []
        location = res.headers.get("location", "")
        if res.status in REDIRECT_STATUSES and location.startswith("https://"):
            return []
        return [self.finding({"reason": "no_redirect", "http_status": res.status})]


@register
class MixedContent(WebCheck):
    id = "web.https.mixed_content"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        if not ctx.is_https:
            return []
        tree = html_tree(ctx)
        urls = [n.attributes.get("src") or "" for n in tree.css("script[src]")]
        urls += [
            n.attributes.get("href") or ""
            for n in tree.css("link[href]")
            if "stylesheet" in (n.attributes.get("rel") or "").lower()
        ]
        insecure = list(dict.fromkeys(u.strip() for u in urls if u.strip().startswith("http://")))
        if not insecure:
            return []
        return [self.finding({"urls": insecure[:MAX_EVIDENCE_ITEMS], "count": len(insecure)})]
