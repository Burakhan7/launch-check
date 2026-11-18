"""Sızan anahtarlar: JS bundle'ları ve inline script'ler."""

from typing import Any

from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.secret_patterns import (
    GOOGLE_API_KEY_PATTERN,
    connection_evidence,
    connection_findings_meta,
    leaked_key_text_params,
    to_evidence,
)
from launchcheck.checks.web._common import (
    WebCheck,
    cached_connections,
    cached_jwts,
    cached_secrets,
)
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

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return leaked_key_text_params(evidence, fragment)


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


@register
class DbConnectionString(WebCheck):
    """Kullanıcı adı + şifre içeren veritabanı bağlantı adresi. Şifre, kullanıcı adı ve tam host
    kanıta hiçbir biçimde girmez (mask_secret de kullanılmaz)."""

    id = "web.secrets.db_connection_string"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: WebContext) -> list[Finding]:
        scan = cached_connections(ctx)
        ctx.meta.update(connection_findings_meta(scan))
        return [self.finding(connection_evidence(h), severity=h.severity) for h in scan.hits]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {
            "kind": str(evidence.get("kind") or ""),
            "provider_domain": str(evidence.get("provider_domain") or ""),
        }
