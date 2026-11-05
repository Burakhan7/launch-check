"""Sızan anahtarlar: JS bundle'ları ve inline script'ler."""

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.secret_patterns import GOOGLE_API_KEY_PATTERN, to_evidence
from launchcheck.checks.web._common import WebCheck, cached_jwts, cached_secrets
from launchcheck.collectors.web_collector import WebContext


@register
class LeakedKey(WebCheck):
    id = "web.secrets.leaked_key"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: WebContext) -> list[Finding]:
        return [
            self.finding(to_evidence(hit), severity=hit.severity)
            for hit in cached_secrets(ctx)
            if hit.pattern_id != GOOGLE_API_KEY_PATTERN
        ]


@register
class GoogleApiKey(WebCheck):
    """Firebase web config anahtarları public'tir; ama aynı format Gemini vb. anahtar olabilir."""

    id = "web.secrets.google_api_key"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: WebContext) -> list[Finding]:
        return [
            self.finding(to_evidence(hit))
            for hit in cached_secrets(ctx)
            if hit.pattern_id == GOOGLE_API_KEY_PATTERN
        ]


@register
class ServiceRole(WebCheck):
    id = "web.secrets.service_role"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: WebContext) -> list[Finding]:
        return [
            self.finding(to_evidence(hit))
            for hit in cached_jwts(ctx)
            if hit.extra.get("role") == "service_role"
        ]
