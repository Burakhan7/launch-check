"""Bundle'da unutulmuş geliştirme/test adresleri."""

from urllib.parse import urlsplit

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.dev_urls import (
    LIBRARY_DEFAULT_LOCAL_RE,
    LOCALHOST_URL_RE,
    URL_HOST_RE,
    is_non_prod_host,
    unique_matches,
    url_evidence,
)
from launchcheck.checks.web._common import WebCheck, js_sources
from launchcheck.collectors.web_collector import WebContext

__all__ = ["LocalhostUrls", "StagingUrls", "is_non_prod_host"]


@register
class LocalhostUrls(WebCheck):
    id = "web.bundle.localhost_urls"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: WebContext) -> list[Finding]:
        found = {
            url: files
            for url, files in unique_matches(js_sources(ctx), LOCALHOST_URL_RE).items()
            if not LIBRARY_DEFAULT_LOCAL_RE.match(url)
        }
        return [self.finding(url_evidence(found))] if found else []


@register
class StagingUrls(WebCheck):
    id = "web.bundle.staging_urls"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        own_host = (urlsplit(ctx.final_url).hostname or "").lower()
        found = {
            url: files
            for url, files in unique_matches(js_sources(ctx), URL_HOST_RE).items()
            if (host := (urlsplit(url).hostname or "")) != own_host and is_non_prod_host(host)
        }
        return [self.finding(url_evidence(found))] if found else []
