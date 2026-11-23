"""Dış görünürlük check'leri (PHASE2 §6.3). Veri: `ctx.external` (crt.sh, DNS, Wayback, RDAP,
sertifika). Modül bir parçayı tamamlayamadıysa o parçanın check'i hiç çalışmaz (atlanır)."""

from abc import abstractmethod
from datetime import UTC, datetime
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
from launchcheck.config import get_settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.external_data import ExternalData

MAX_ITEMS = 10
DAY_S = 86400


class ExternalCheck(Check):
    target = TargetType.WEB
    phase = 2
    category = Category.SECURITY
    module: ClassVar[str | None] = "external"

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, DeepContext):
            raise TypeError("DeepContext bekleniyordu")
        if ctx.external is None:
            return []
        return await self.check(ctx, ctx.external)

    @abstractmethod
    async def check(self, ctx: DeepContext, data: ExternalData) -> list[Finding]: ...


def now() -> datetime:
    return datetime.now(UTC)


def days_until(when: datetime) -> float:
    return (when - now()).total_seconds() / DAY_S


def _worst(severities: list[Severity]) -> Severity:
    return min(severities, key=lambda s: s.rank)


@register
class ForgottenSubdomain(ExternalCheck):
    """Alt alan adında Faz 1 check'lerinin bulduğu sorunlar. Önem: alt bulguların en yükseği;
    sadece bilgi niteliğindeki sonuçlar (ör. staging'in noindex olması) bulgu üretmez."""

    id = "web.external.forgotten_subdomain"
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: ExternalData) -> list[Finding]:
        info = set(get_settings().deep_subdomain_info_checks)
        findings = []
        for scan in data.subdomains:
            real = [f for f in scan.findings if f.check_id not in info]
            if not real:
                continue
            severity = _worst([Severity(f.severity) for f in real])
            evidence = {
                "host": scan.host,
                "url": scan.url,
                "priority_name": scan.priority,
                "checks": [
                    {"check_id": f.check_id, "severity": f.severity, "evidence": f.evidence}
                    for f in sorted(real, key=lambda f: Severity(f.severity).rank)
                ][:MAX_ITEMS],
                "info_checks": [f.check_id for f in scan.findings if f.check_id in info],
            }
            findings.append(self.finding(evidence, severity=severity))
        return findings

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {
            "host": str(evidence.get("host") or ""),
            "count": str(len(evidence.get("checks") or [])),
        }


@register
class SubdomainTakeoverRisk(ExternalCheck):
    id = "web.external.subdomain_takeover_risk"
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: ExternalData) -> list[Finding]:
        return [
            self.finding(
                {
                    "host": r.host,
                    "cname": r.cname,
                    "service": r.service,
                    "reason": r.reason,
                    "confidence": r.confidence,
                },
                # "Edge case": servis çoğunlukla alan adı doğrulaması ister; risk daha düşük
                severity=Severity.HIGH if r.confidence == "vulnerable" else Severity.MEDIUM,
            )
            for r in data.takeovers
        ]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        reason = "reason_nxdomain" if evidence.get("reason") == "nxdomain" else "reason_page"
        return {
            "host": str(evidence.get("host") or ""),
            "service": str(evidence.get("service") or ""),
            "reason": fragment(reason),
        }


# Sahte e-posta gönderimini doğrudan mümkün kılan sorunlar
STRONG_EMAIL_PROBLEMS = ("spf_missing", "spf_pass_all", "dmarc_missing", "dmarc_none")
EMAIL_PROBLEM_ORDER = (*STRONG_EMAIL_PROBLEMS, "spf_multiple", "spf_too_many_lookups")


@register
class EmailSpoofing(ExternalCheck):
    """MX varsa (e-posta kullanılıyor) zayıf SPF/DMARC → high. MX yoksa alan adı yine taklit
    edilebilir ama gelen kutusu/itibar riski daha düşük → low."""

    id = "web.external.email_spoofing"
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: ExternalData) -> list[Finding]:
        email = data.email
        if email is None:
            return []
        problems: list[str] = []
        if not email.spf_records:
            problems.append("spf_missing")
        elif len(email.spf_records) > 1:
            problems.append("spf_multiple")
        else:
            if email.spf_all == "+all":
                problems.append("spf_pass_all")
            if email.spf_lookups > get_settings().deep_spf_max_lookups:
                problems.append("spf_too_many_lookups")
        if not email.dmarc_present:
            problems.append("dmarc_missing")
        elif email.dmarc_policy in (None, "none"):
            problems.append("dmarc_none")
        if not problems:
            return []
        if not email.has_mx:
            severity = Severity.LOW
        elif any(p in STRONG_EMAIL_PROBLEMS for p in problems):
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM
        evidence = {
            "domain": email.domain,
            "has_mx": email.has_mx,
            "problems": problems,
            "spf_all": email.spf_all,
            "spf_lookups": email.spf_lookups,
            "dmarc_policy": email.dmarc_policy,
        }
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        problems = [p for p in EMAIL_PROBLEM_ORDER if p in (evidence.get("problems") or [])]
        return {
            "domain": str(evidence.get("domain") or ""),
            "problems": "; ".join(fragment(p) for p in problems),
            "mx_note": fragment("mx_note" if evidence.get("has_mx") else "no_mx_note"),
        }


@register
class ArchivedSensitivePaths(ExternalCheck):
    """Arşivde hassas yol var ve şu an hâlâ gerçek içerikle açık → high; artık açık değil →
    info (o dönemde açık kalan anahtarlar değiştirilmeli)."""

    id = "web.external.archived_sensitive_paths"
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: ExternalData) -> list[Finding]:
        findings = []
        for live, severity, state in (
            (True, Severity.HIGH, "live"),
            (False, Severity.INFO, "gone"),
        ):
            items = [a for a in data.archived if a.live is live]
            if items:
                evidence = {
                    "state": state,
                    "paths": [
                        {"path": a.path, "kind": a.kind, "archived_at": a.archived_at}
                        for a in items[:MAX_ITEMS]
                    ],
                    "count": len(items),
                }
                findings.append(self.finding(evidence, severity=severity))
        return findings

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        paths = [str(p.get("path")) for p in evidence.get("paths") or []]
        return {
            "paths": ", ".join(paths[:5]),
            "state": fragment("state_live" if evidence.get("state") == "live" else "state_gone"),
        }


@register
class DomainExpirySoon(ExternalCheck):
    id = "web.external.domain_expiry_soon"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: ExternalData) -> list[Finding]:
        if data.domain_expiry is None:
            return []
        s = get_settings()
        days = days_until(data.domain_expiry.expires_at)
        if days >= s.deep_domain_expiry_medium_days:
            return []
        severity = Severity.HIGH if days < s.deep_domain_expiry_high_days else Severity.MEDIUM
        evidence = {
            "domain": data.domain_expiry.domain,
            "expires_at": data.domain_expiry.expires_at.date().isoformat(),
            "days_left": int(days),
        }
        return [self.finding(evidence, severity=severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return _expiry_params(evidence, fragment, "domain")


@register
class CertExpirySoon(ExternalCheck):
    """Eşikler sertifika ömrüyle de sınırlanır: 90/45/6 günlük otomatik sertifikalar kalan
    sürenin ~1/3'ünde yenilenir; normal yenileme penceresindeki sertifikaya alarm verilmez."""

    id = "web.external.cert_expiry_soon"
    category = Category.PROD_READINESS
    default_severity = Severity.HIGH

    async def check(self, ctx: DeepContext, data: ExternalData) -> list[Finding]:
        cert = data.cert
        if cert is None:
            return []
        s = get_settings()
        lifetime = max((cert.not_after - cert.not_before).total_seconds() / DAY_S, 1.0)
        high = min(s.deep_cert_expiry_high_days, lifetime * s.deep_cert_expiry_high_ratio)
        medium = min(s.deep_cert_expiry_medium_days, lifetime * s.deep_cert_expiry_medium_ratio)
        days = days_until(cert.not_after)
        if days >= medium:
            return []
        evidence = {
            "host": cert.host,
            "expires_at": cert.not_after.date().isoformat(),
            "days_left": int(days),
            "lifetime_days": round(lifetime),
            "issuer": cert.issuer,
        }
        return [self.finding(evidence, severity=Severity.HIGH if days < high else Severity.MEDIUM)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return _expiry_params(evidence, fragment, "host")


def _expiry_params(evidence: dict[str, Any], fragment: FragmentFn, name_key: str) -> dict[str, str]:
    days = int(evidence.get("days_left") or 0)
    when = fragment("expired", days=-days) if days < 0 else fragment("days_left", days=days)
    return {
        "name": str(evidence.get(name_key) or ""),
        "date": str(evidence.get("expires_at") or ""),
        "when": when,
    }
