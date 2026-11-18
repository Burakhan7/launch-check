"""Uygulama içine gömülmüş anahtarlar (smali, strings.xml, assets, bundle, libapp.so)."""

from typing import Any

from launchcheck.checks.apk._common import (
    ApkCheck,
    cached_connections,
    cached_jwts,
    cached_secrets,
)
from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.secret_patterns import (
    GOOGLE_API_KEY_PATTERN,
    connection_evidence,
    connection_findings_meta,
    leaked_key_text_params,
    to_evidence,
)
from launchcheck.collectors.apk_collector import ApkContext


@register
class LeakedKey(ApkCheck):
    id = "apk.secrets.leaked_key"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: ApkContext) -> list[Finding]:
        return [
            self.finding(to_evidence(hit), severity=hit.severity)
            for hit in cached_secrets(ctx)
            if hit.pattern_id != GOOGLE_API_KEY_PATTERN
        ]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return leaked_key_text_params(evidence, fragment)


@register
class GoogleApiKey(ApkCheck):
    """google-services.json'dan gelen Firebase anahtarı normaldir; kısıtlama kontrolü önerilir."""

    id = "apk.secrets.google_api_key"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: ApkContext) -> list[Finding]:
        return [
            self.finding(to_evidence(hit))
            for hit in cached_secrets(ctx)
            if hit.pattern_id == GOOGLE_API_KEY_PATTERN
        ]


@register
class ServiceRole(ApkCheck):
    id = "apk.secrets.service_role"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: ApkContext) -> list[Finding]:
        return [
            self.finding(to_evidence(hit))
            for hit in cached_jwts(ctx)
            if hit.extra.get("role") == "service_role"
        ]


@register
class DbConnectionString(ApkCheck):
    """Uygulamaya gömülmüş, kullanıcı adı + şifre içeren veritabanı bağlantı adresi."""

    id = "apk.secrets.db_connection_string"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: ApkContext) -> list[Finding]:
        scan = cached_connections(ctx)
        ctx.meta.update(connection_findings_meta(scan))
        return [self.finding(connection_evidence(h), severity=h.severity) for h in scan.hits]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {
            "kind": str(evidence.get("kind") or ""),
            "provider_domain": str(evidence.get("provider_domain") or ""),
        }
