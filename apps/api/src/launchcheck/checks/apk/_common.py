"""APK check'lerinin ortak tabanı ve yardımcıları."""

from abc import abstractmethod
from xml.etree.ElementTree import Element

from launchcheck.checks.base import Check, Finding, ScanContext, TargetType
from launchcheck.checks.shared.secret_patterns import (
    ConnectionScan,
    SecretHit,
    find_connection_strings,
    find_secrets,
    find_supabase_jwts,
)
from launchcheck.collectors.apk_collector import ApkContext, android_attr

COMPONENT_TAGS = ("activity", "activity-alias", "service", "receiver", "provider")


class ApkCheck(Check):
    target = TargetType.APK

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, ApkContext):
            raise TypeError("ApkContext bekleniyordu")
        return await self.check(ctx)

    @abstractmethod
    async def check(self, ctx: ApkContext) -> list[Finding]: ...


def cached_secrets(ctx: ApkContext) -> list[SecretHit]:
    if "secrets" not in ctx.cache:
        ctx.cache["secrets"] = find_secrets(ctx.string_sources)
    hits: list[SecretHit] = ctx.cache["secrets"]
    return hits


def cached_connections(ctx: ApkContext) -> ConnectionScan:
    """DB bağlantı adresleri: secret taramasıyla aynı string kaynakları."""
    if "connections" not in ctx.cache:
        ctx.cache["connections"] = find_connection_strings(ctx.string_sources)
    scan: ConnectionScan = ctx.cache["connections"]
    return scan


def cached_jwts(ctx: ApkContext) -> list[SecretHit]:
    if "jwts" not in ctx.cache:
        ctx.cache["jwts"] = find_supabase_jwts(ctx.string_sources)
    hits: list[SecretHit] = ctx.cache["jwts"]
    return hits


def is_true(node: Element | None, attr: str) -> bool:
    return node is not None and (android_attr(node, attr) or "").lower() == "true"


def components(ctx: ApkContext) -> list[Element]:
    app = ctx.application
    if app is None:
        return []
    return [node for tag in COMPONENT_TAGS for node in app.findall(tag)]


def uses_permissions(ctx: ApkContext) -> list[str]:
    if ctx.manifest is None:
        return []
    tags = ("uses-permission", "uses-permission-sdk-23")
    names = [android_attr(n, "name") or "" for t in tags for n in ctx.manifest.findall(t)]
    return list(dict.fromkeys(n for n in names if n))
