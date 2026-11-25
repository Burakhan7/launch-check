"""Huni check'leri (PHASE2 §6.6). Veri: `ctx.funnel` (mobilde gerçek tarayıcı yürüyüşü).

Kayıt formu bulunamadıysa form check'leri atlanır ("geçti" sayılmaz): form girişten sonra veya
JS ile açılıyor olabilir. Ücretli plan sinyali ve fiyat yoksa fiyat check'leri geçer.
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
from launchcheck.config import get_settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.funnel_data import FormField, FunnelData

PATH_KINDS = ("cta", "login", "signup")
EMAIL_INPUTMODES = ("email",)
TEL_INPUTMODES = ("tel", "numeric")
AUTOCOMPLETE_KINDS = ("email", "tel", "name", "password")


class FunnelCheck(Check):
    target = TargetType.WEB
    phase = 2
    category = Category.CONVERSION
    module: ClassVar[str | None] = "funnel"

    async def run(self, ctx: ScanContext) -> list[Finding]:
        if not isinstance(ctx, DeepContext):
            raise TypeError("DeepContext bekleniyordu")
        if ctx.funnel is None:
            return []
        return await self.check(ctx, ctx.funnel)

    @abstractmethod
    async def check(self, ctx: DeepContext, data: FunnelData) -> list[Finding]: ...


def _issues_text(evidence: dict[str, Any], fragment: FragmentFn) -> str:
    return ", ".join(fragment(f"issue_{i}", **evidence) for i in evidence.get("issues") or [])


@register
class SignupFriction(FunnelCheck):
    """> 6 alan, > 4 zorunlu alan, kartsız kayıt değil, sosyal giriş yok. Sadece sosyal giriş
    eksikliği varsa low (birçok ürün bilerek e-postayla kayıt ister), diğerleri medium."""

    id = "web.funnel.signup_friction"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: FunnelData) -> list[Finding]:
        form = data.signup
        if form is None:
            return []
        s = get_settings()
        fields = len(form.data_fields)
        required = form.required_count
        issues = [
            name
            for name, hit in (
                ("too_many_fields", fields > s.deep_funnel_max_fields),
                ("too_many_required", required > s.deep_funnel_max_required),
                ("card_required", form.card),
                ("no_social_login", not form.social),
            )
            if hit
        ]
        if not issues:
            return []
        evidence = {
            "page": form.page,
            "issues": issues,
            "fields": fields,
            "required": required,
            "field_kinds": [f.kind for f in form.data_fields][:20],
            "social": form.social,
            "in_dialog": form.in_dialog,
        }
        severity = Severity.LOW if issues == ["no_social_login"] else Severity.MEDIUM
        return [self.finding(evidence, severity)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"issues": _issues_text(evidence, fragment), "page": str(evidence.get("page", ""))}


@register
class TooManySteps(FunnelCheck):
    id = "web.funnel.too_many_steps"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: FunnelData) -> list[Finding]:
        if data.signup is None or data.signup_clicks is None:
            return []
        s = get_settings()
        path = [step for step in data.steps if step.kind in PATH_KINDS]
        slow = [
            step
            for step in path
            if step.load_ms is not None and step.load_ms > s.deep_funnel_slow_step_ms
        ]
        issues = []
        if data.signup_clicks > s.deep_funnel_max_clicks:
            issues.append("too_many_clicks")
        if slow:
            issues.append("slow_step")
        if not issues:
            return []
        evidence = {
            "issues": issues,
            "clicks": data.signup_clicks,
            "steps": [
                {"kind": st.kind, "url": st.url, "clicks": st.clicks, "load_ms": st.load_ms}
                for st in data.steps
            ],
            "slowest_ms": max((st.load_ms or 0 for st in slow), default=None),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        seconds = (evidence.get("slowest_ms") or 0) / 1000
        values = {"clicks": evidence.get("clicks", 0), "seconds": f"{seconds:.1f}"}
        return {"issues": ", ".join(fragment(f"issue_{i}", **values) for i in evidence["issues"])}


def field_issues(field: FormField) -> list[str]:
    issues = []
    if field.kind == "email" and field.type != "email" and field.inputmode not in EMAIL_INPUTMODES:
        issues.append("email_type")
    if field.kind == "tel" and field.type != "tel" and field.inputmode not in TEL_INPUTMODES:
        issues.append("tel_type")
    if field.kind in AUTOCOMPLETE_KINDS and not field.autocomplete:
        issues.append("autocomplete_missing")
    return issues


@register
class MobileInputTypes(FunnelCheck):
    """Doğru `type`/`inputmode` mobilde doğru klavyeyi açar; `autocomplete` tarayıcının ve şifre
    yöneticisinin alanı doldurmasını sağlar."""

    id = "web.funnel.mobile_input_types"
    default_severity = Severity.LOW

    async def check(self, ctx: DeepContext, data: FunnelData) -> list[Finding]:
        if data.signup is None:
            return []
        problems = [
            {"kind": f.kind, "label": f.label, "type": f.type, "issues": issues}
            for f in data.signup.data_fields
            if (issues := field_issues(f))
        ]
        if not problems:
            return []
        evidence = {"page": data.signup.page, "fields": problems, "count": len(problems)}
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"count": str(evidence.get("count", 0)), "page": str(evidence.get("page", ""))}


@register
class PriceHidden(FunnelCheck):
    """Ücretli plan sinyali var (fiyat sayfası, "premium/abonelik/satın al"...) ama fiyat render
    edilmiş sayfalarda hiç yok veya ana sayfadan 2+ tık uzakta. Fiyatı bilerek "teklif al"a
    bağlayan (kurumsal) sitelerde low."""

    id = "web.funnel.price_hidden"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: FunnelData) -> list[Finding]:
        if not data.paid_signals:
            return []
        if data.price_distance is not None and data.price_distance < 2:
            return []
        quote = any(p.quote_based for p in data.pricing)
        evidence = {
            "paid_signals": data.paid_signals,
            "pricing_page": data.pricing_page,
            "price_distance": data.price_distance,
            "quote_based": quote,
        }
        return [self.finding(evidence, Severity.LOW if quote else Severity.MEDIUM)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        clicks = evidence.get("price_distance")
        situation = "not_found" if clicks is None else "far"
        return {"situation": fragment(f"situation_{situation}", clicks=clicks)}


@register
class VatExcludedPrice(FunnelCheck):
    """Fiyatın hemen yanında (aynı kart/kapsayıcı) "+KDV", "KDV hariç", "KDV ödeme adımında"
    gibi ifadeler. Sayfanın başka bir yerindeki (SSS, fatura bilgisi) ifadeler sayılmaz."""

    id = "web.funnel.vat_excluded_price"
    default_severity = Severity.MEDIUM

    async def check(self, ctx: DeepContext, data: FunnelData) -> list[Finding]:
        pages = [p for p in data.pricing if p.vat_excluded]
        if not pages:
            return []
        evidence = {
            "pages": [{"page": p.page, "phrases": p.vat_excluded} for p in pages],
            "phrases": list(dict.fromkeys(x for p in pages for x in p.vat_excluded)),
        }
        return [self.finding(evidence)]

    def text_params(self, evidence: dict[str, Any], fragment: FragmentFn) -> dict[str, str]:
        return {"phrases": ", ".join(f'"{p}"' for p in evidence.get("phrases") or [])}
