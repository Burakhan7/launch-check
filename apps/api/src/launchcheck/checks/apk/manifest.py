"""Manifest ayarları: target SDK, yedekleme, dışa açık bileşenler, izinler, paket adı."""

from typing import Any

from launchcheck.checks.apk._common import (
    ApkCheck,
    components,
    is_true,
    uses_permissions,
)
from launchcheck.checks.base import Category, Finding, FragmentFn, Severity
from launchcheck.checks.registry import register
from launchcheck.checks.shared.dev_urls import MAX_EVIDENCE_ITEMS
from launchcheck.collectors.apk_collector import ApkContext, android_attr
from launchcheck.config import get_settings

# Google Play'in kısıtladığı veya özel beyan istediği izinler + kişisel veri izinleri
SENSITIVE_PERMISSIONS = {
    f"android.permission.{name}"
    for name in (
        "READ_SMS",
        "SEND_SMS",
        "RECEIVE_SMS",
        "RECEIVE_MMS",
        "RECEIVE_WAP_PUSH",
        "READ_CALL_LOG",
        "WRITE_CALL_LOG",
        "PROCESS_OUTGOING_CALLS",
        "READ_CONTACTS",
        "WRITE_CONTACTS",
        "ACCESS_BACKGROUND_LOCATION",
        "MANAGE_EXTERNAL_STORAGE",
        "QUERY_ALL_PACKAGES",
        "REQUEST_INSTALL_PACKAGES",
        "READ_MEDIA_IMAGES",
        "READ_MEDIA_VIDEO",
        "USE_FULL_SCREEN_INTENT",
    )
}


@register
class TargetSdkTooLow(ApkCheck):
    id = "apk.sdk.target_too_low"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: ApkContext) -> list[Finding]:
        required = get_settings().play_min_target_sdk
        target = ctx.target_sdk
        if target is None or target >= required:
            return []
        return [self.finding({"target_sdk": target, "required": required})]


@register
class AllowBackup(ApkCheck):
    id = "apk.manifest.allow_backup"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: ApkContext) -> list[Finding]:
        app = ctx.application
        if app is None:
            return []
        value = (android_attr(app, "allowBackup") or "").lower()
        if value == "false":
            return []
        # Yedekleme kuralları tanımlanmışsa geliştirici bilinçli seçim yapmış demektir.
        if android_attr(app, "dataExtractionRules") or android_attr(app, "fullBackupContent"):
            return []
        return [self.finding({"value": value or "default"})]


@register
class ExportedComponents(ApkCheck):
    """intent-filter'ı olmayan ama exported=true olan ve izinle korunmayan bileşenler."""

    id = "apk.manifest.exported_components"
    category = Category.SECURITY
    default_severity = Severity.MEDIUM

    async def check(self, ctx: ApkContext) -> list[Finding]:
        exposed = [
            {"type": node.tag, "name": android_attr(node, "name") or ""}
            for node in components(ctx)
            if is_true(node, "exported")
            and node.find("intent-filter") is None
            and not android_attr(node, "permission")
        ]
        if not exposed:
            return []
        return [self.finding({"components": exposed[:MAX_EVIDENCE_ITEMS], "count": len(exposed)})]


# Google Play bu ad alanını reddeder (Flutter/Android Studio şablon varsayılanı)
PLAY_REJECTED_NAMESPACE = "com.example"
# Play kabul eder ama şablondan kaldığı belli olan ad alanları (paket adı sonradan değişmez)
PLACEHOLDER_NAMESPACES = (
    "com.mycompany",
    "com.yourcompany",
    "com.company",
    "com.test",
    "com.demo",
    "io.flutter",
)


def _in_namespace(package: str, namespace: str) -> bool:
    """Önek tam segment olmalı: com.examplecorp.app `com.example` ad alanında değildir."""
    return package == namespace or package.startswith(namespace + ".")


@register
class ExamplePackage(ApkCheck):
    id = "apk.manifest.example_package"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: ApkContext) -> list[Finding]:
        package = ctx.package.strip()
        if not package:  # manifest okunamadı; başka check'ler yakalar
            return []
        if _in_namespace(package, PLAY_REJECTED_NAMESPACE):
            return [self.finding({"package": package, "reason": "play_rejects"})]
        if any(_in_namespace(package, ns) for ns in PLACEHOLDER_NAMESPACES):
            evidence = {"package": package, "reason": "placeholder_namespace"}
            return [self.finding(evidence, severity=Severity.LOW)]
        return []

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        reason = str(evidence.get("reason") or "play_rejects")
        return {
            "package": str(evidence.get("package") or ""),
            "reason_text": fragment(reason),
            "impact": fragment(f"impact_{reason}"),
        }


@register
class SensitivePermissions(ApkCheck):
    id = "apk.permissions.sensitive"
    category = Category.PROD_READINESS
    default_severity = Severity.MEDIUM

    async def check(self, ctx: ApkContext) -> list[Finding]:
        found = [p for p in uses_permissions(ctx) if p in SENSITIVE_PERMISSIONS]
        if not found:
            return []
        return [self.finding({"permissions": [p.rsplit(".", 1)[-1] for p in found]})]
