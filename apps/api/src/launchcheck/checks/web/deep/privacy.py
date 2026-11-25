"""Gizlilik ve tedarik zinciri check'leri (PHASE2 §6.5). Veri: `ctx.privacy` (gerçek tarayıcı).

Takip aracı: elle tutulan liste (ad + kategori) veya EasyPrivacy alan adı. Çerezsiz ölçüm ve hata
izleme araçları, tag manager yükleyicileri ve Google Consent Mode "reddedildi" ping'leri takip
sayılmaz (bkz. deep/supply/trackers.yaml).
"""

from abc import abstractmethod
from typing import Any, ClassVar

from launchcheck.checks.base import (
    Category,
    Check,
    Finding,
    FragmentFn,
    ScanContext,
    Severity,
    TargetType,
)
from launchcheck.checks.registry import register
from launchcheck.deep.context import DeepContext
from launchcheck.deep.privacy_data import PhaseResult, PrivacyData, RejectState

MAX_ITEMS = 15
# retire önemi → bulgu önemi. Tarayıcıdaki bir kütüphane açığı tek başına "herkes tüm veriyi
# okuyabilir" düzeyinde değildir (AGENTS.md critical tanımı): en fazla high.
LIBRARY_SEVERITY = {
    "critical": Severity.HIGH,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
}
# axe kuralı → aynı sorunu bulan Faz 1 check'i
PHASE1_COVERED = {
    "image-alt": "web.a11y.img_missing_alt",
    "meta-viewport": "web.meta.viewport_missing",
}


class PrivacyCheck(Check):
    target = TargetType.WEB
    phase = 2
    category = Category.SECURITY
    module: ClassVar[str | None] = "privacy"

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, DeepContext):
            raise TypeError("DeepContext bekleniyordu")
        if ctx.privacy is None:
            return []
        return await self.check(ctx, ctx.privacy)

    @abstractmethod
    async def check(self, ctx: DeepContext, data: PrivacyData) -> list[Finding]: ...


def trackers_evidence(phase: PhaseResult) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "category": t.category,
            "domain": t.domain,
            "requests": t.requests,
            "cookies": list(t.cookies),
        }
        for t in phase.trackers[:MAX_ITEMS]
    ]


def _names(evidence: dict[str, Any]) -> str:
    return ", ".join(str(t.get("name")) for t in (evidence.get("trackers") or [])[:4])


def _has_mechanism(data: PrivacyData) -> bool:
    return data.consent.banner or data.consent.cmp is not None


@register
class TrackersBeforeConsent(PrivacyCheck):
    """Onay mekanizması VAR ama takip araçları ziyaretçi seçim yapmadan çalışıyor. Mekanizma
    hiç yoksa aynı durum `consent_banner_missing` olarak raporlanır (çift sayılmaz)."""

    id = "web.privacy.trackers_before_consent"
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: PrivacyData) -> list[Finding]:
        if data.before is None or not data.before.tracking or not _has_mechanism(data):
            return []
        evidence = {
            "trackers": trackers_evidence(data.before),
            "count": len(data.before.trackers),
            "cmp": data.consent.cmp,
            "banner_visible": data.consent.banner,
            "consent_mode_pings": data.before.consent_mode_pings,
            "tag_managers": data.before.tag_managers,
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"count": str(evidence.get("count", 0)), "names": _names(evidence)}


@register
class TrackersAfterReject(PrivacyCheck):
    id = "web.privacy.trackers_after_reject"
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: PrivacyData) -> list[Finding]:
        if data.after is None or not data.after.tracking:
            return []
        evidence = {
            "trackers": trackers_evidence(data.after),
            "count": len(data.after.trackers),
            "clicked": data.consent.reject_label,
            "second_layer": data.consent.reject == RejectState.CLICKED_SECOND_LAYER,
            "banner_after_reject": data.consent.banner_after_reject,
            "cmp": data.consent.cmp,
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {
            "count": str(evidence.get("count", 0)),
            "names": _names(evidence),
            "clicked": str(evidence.get("clicked") or ""),
        }


@register
class ConsentBannerMissing(PrivacyCheck):
    id = "web.privacy.consent_banner_missing"
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: PrivacyData) -> list[Finding]:
        if data.before is None or not data.before.tracking or _has_mechanism(data):
            return []
        evidence = {
            "trackers": trackers_evidence(data.before),
            "count": len(data.before.trackers),
            "tag_managers": data.before.tag_managers,
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"count": str(evidence.get("count", 0)), "names": _names(evidence)}


@register
class VulnerableJsLibrary(PrivacyCheck):
    id = "web.security.vulnerable_js_library"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PrivacyData) -> list[Finding]:
        if not data.libraries:
            return []
        libraries = [
            {
                "library": lib.library,
                "version": lib.version,
                "url": lib.url,
                "severity": lib.severity,
                "vulnerabilities": list(lib.vulnerabilities),
            }
            for lib in data.libraries[:MAX_ITEMS]
        ]
        worst = min(
            (LIBRARY_SEVERITY.get(lib.severity, Severity.LOW) for lib in data.libraries),
            key=lambda s: s.rank,
        )
        return [self.finding({"libraries": libraries, "count": len(data.libraries)}, worst)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        libs = evidence.get("libraries") or []
        names = ", ".join(f"{lib.get('library')} {lib.get('version')}" for lib in libs[:4])
        return {"count": str(evidence.get("count", 0)), "names": names}


@register
class RiskyThirdPartyScript(PrivacyCheck):
    """Ele geçirildiği veya terk edildiği kamuya açık olaylarla bilinen CDN'lerden script."""

    id = "web.security.risky_third_party_script"
    default_severity = Severity.CRITICAL

    async def check(self, ctx: DeepContext, data: PrivacyData) -> list[Finding]:
        if not data.risky:
            return []
        evidence = {
            "scripts": [{"url": r.url, "domain": r.domain, "source": r.source} for r in data.risky][
                :MAX_ITEMS
            ],
            "count": len(data.risky),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        domains = list(dict.fromkeys(str(s.get("domain")) for s in evidence.get("scripts") or []))
        return {"domains": ", ".join(domains[:4])}


@register
class AxeViolations(PrivacyCheck):
    """axe-core `serious` → medium, `critical` → high; kural bazında gruplu tek bulgu."""

    id = "web.a11y.axe_violations"
    category = Category.CONVERSION
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: PrivacyData) -> list[Finding]:
        # Faz 1'in aynı konudaki bulgusu varsa axe kuralı tekrar sayılmaz (çift ceza olmasın)
        parent = {f.check_id for f in ctx.parent_findings}
        covered = [v.rule for v in data.axe if PHASE1_COVERED.get(v.rule) in parent]
        violations = [v for v in data.axe if v.rule not in covered]
        if not violations:
            return []
        rules = [
            {
                "rule": v.rule,
                "impact": v.impact,
                "help": v.help,
                "help_url": v.help_url,
                "count": v.count,
                "pages": list(v.pages),
                "viewports": list(v.viewports),
                "examples": list(v.targets),
            }
            for v in violations
        ]
        critical = any(v.impact == "critical" for v in violations)
        evidence = {
            "rules": rules,
            "count": len(rules),
            "runs": data.axe_runs,
            "covered_by_phase1": covered,
        }
        return [self.finding(evidence, Severity.HIGH if critical else Severity.MEDIUM)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"count": str(evidence.get("count", 0))}
