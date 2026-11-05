"""Güvenlik header'ları (ana sayfanın son yanıtı üzerinden)."""

import re

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.web._common import WebCheck, html_tree
from launchcheck.collectors.web_collector import WebContext

VERSION_RE = re.compile(r"\d+\.\d+")
VERSION_HEADERS = ("server", "x-powered-by", "x-aspnet-version", "x-aspnetmvc-version")


def _csp(ctx: WebContext) -> str:
    header = ctx.headers.get("content-security-policy", "")
    meta = [
        n.attributes.get("content") or ""
        for n in html_tree(ctx).css("meta[http-equiv]")
        if (n.attributes.get("http-equiv") or "").lower() == "content-security-policy"
    ]
    return "; ".join([header, *meta]).strip("; ")


@register
class HstsMissing(WebCheck):
    id = "web.headers.hsts_missing"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        # HTTP sitelerde HSTS anlamsız; o durumu web.https.missing raporlar.
        if not ctx.is_https or "strict-transport-security" in ctx.headers:
            return []
        return [self.finding({"header": "Strict-Transport-Security"})]


@register
class CspMissing(WebCheck):
    id = "web.headers.csp_missing"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        return [] if _csp(ctx) else [self.finding({"header": "Content-Security-Policy"})]


@register
class FrameOptionsMissing(WebCheck):
    id = "web.headers.frame_options_missing"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        if "x-frame-options" in ctx.headers or "frame-ancestors" in _csp(ctx).lower():
            return []
        return [self.finding({"header": "X-Frame-Options"})]


@register
class NosniffMissing(WebCheck):
    id = "web.headers.nosniff_missing"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        if ctx.headers.get("x-content-type-options", "").strip().lower() == "nosniff":
            return []
        return [self.finding({"header": "X-Content-Type-Options"})]


@register
class ServerVersionLeak(WebCheck):
    id = "web.headers.server_version_leak"
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def check(self, ctx: WebContext) -> list[Finding]:
        leaks = {
            name: ctx.headers[name]
            for name in VERSION_HEADERS
            if name in ctx.headers and VERSION_RE.search(ctx.headers[name])
        }
        return [self.finding({"headers": leaks})] if leaks else []
