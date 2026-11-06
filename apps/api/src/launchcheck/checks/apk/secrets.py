"""Uygulama içine gömülmüş anahtarlar (smali, strings.xml, assets, bundle, libapp.so)."""

from launchcheck.checks.apk._common import ApkCheck, cached_jwts, cached_secrets
from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.secret_patterns import GOOGLE_API_KEY_PATTERN, to_evidence
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
