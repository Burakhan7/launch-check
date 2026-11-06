"""Yayına debug/geliştirme build'i gönderilmiş mi?"""

from launchcheck.checks.apk._common import ApkCheck, is_true
from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import register
from launchcheck.collectors.apk_collector import ApkContext
from launchcheck.collectors.apk_framework import RN_BUNDLE, Framework
from launchcheck.collectors.apk_signing import DEBUG_CN

FLUTTER_KERNEL_BLOB = "assets/flutter_assets/kernel_blob.bin"
EXPO_DEV_CLIENT_PACKAGES = ("expo/modules/devlauncher", "expo/modules/devmenu")
RN_FRAMEWORKS = (Framework.REACT_NATIVE, Framework.EXPO)


@register
class Debuggable(ApkCheck):
    id = "apk.build.debuggable"
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def check(self, ctx: ApkContext) -> list[Finding]:
        if is_true(ctx.application, "debuggable"):
            return [self.finding({"attribute": "android:debuggable", "value": "true"})]
        return []


@register
class DebugCertificate(ApkCheck):
    id = "apk.build.debug_certificate"
    category = Category.PROD_READINESS
    default_severity = Severity.CRITICAL

    async def check(self, ctx: ApkContext) -> list[Finding]:
        if ctx.signing.is_debug:
            return [self.finding({"common_name": DEBUG_CN, "schemes": ctx.signing.schemes})]
        return []


@register
class FlutterDebug(ApkCheck):
    id = "apk.build.flutter_debug"
    category = Category.PROD_READINESS
    default_severity = Severity.CRITICAL

    async def check(self, ctx: ApkContext) -> list[Finding]:
        if FLUTTER_KERNEL_BLOB in ctx.zip_entries:
            return [self.finding({"file": FLUTTER_KERNEL_BLOB})]
        return []


def _has_package(ctx: ApkContext, package: str) -> bool:
    return any(root.joinpath(package).is_dir() for root in ctx.decoded_dir.glob("smali*"))


@register
class RnDevMode(ApkCheck):
    """Release RN/Expo build'lerinde JS bundle APK'nın içindedir. Bundle yoksa uygulama kodu
    geliştirme sunucusundan (Metro) yükler. Not: RN'nin kendi kodu release'te de Metro adresi
    (localhost:8081) içerdiği için adres string'lerine bakılmaz."""

    id = "apk.build.rn_dev_mode"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: ApkContext) -> list[Finding]:
        if ctx.framework not in RN_FRAMEWORKS:
            return []
        reasons = []
        if RN_BUNDLE not in ctx.zip_entries:
            reasons.append("no_js_bundle")
        dev_client = [p for p in EXPO_DEV_CLIENT_PACKAGES if _has_package(ctx, p)]
        if dev_client:
            reasons.append("expo_dev_client")
        return [self.finding({"reasons": reasons, "packages": dev_client})] if reasons else []
