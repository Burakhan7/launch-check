"""Huni yürüyüşü modülü (P7, PHASE2 §6.6) — tarayıcısız testler.

Tarayıcının döndürdüğü sayfa incelemeleri (`funnel.js` çıktısı biçiminde) sentetik verilir;
form seçimi, alan türleri, fiyat/KDV yorumu, tık mesafesi, check'ler ve huni haritası burada
doğrulanır. Gerçek Chromium yürüyüşü `tests/deep/browser/test_funnel_collect.py`'de.
"""

from typing import Any

import pytest

from launchcheck.checks.base import Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.config import Settings
from launchcheck.deep.browser.funnel import FunnelInput, FunnelRaw, RawStep, skip_patterns
from launchcheck.deep.context import DeepContext
from launchcheck.deep.events import MemoryEventSink
from launchcheck.deep.funnel.analysis import (
    classify_field,
    has_phrase,
    link_distances,
    pick_signup,
    pricing_info,
)
from launchcheck.deep.funnel_data import FormField, FunnelData, PartStatus, SignupForm
from launchcheck.deep.funnel_module import FunnelBlockedError, FunnelModule, candidates
from launchcheck.deep.modules import ModuleOutput
from launchcheck.deep.render_data import RenderData, RenderedPage, SdSignals
from launchcheck.i18n import finding_text, fragment_fn

SITE = "https://app.acme.test"
FUNNEL_IDS = {
    "web.funnel.signup_friction",
    "web.funnel.too_many_steps",
    "web.funnel.mobile_input_types",
    "web.funnel.price_hidden",
    "web.funnel.vat_excluded_price",
}


def f(type_: str = "text", name: str = "", label: str = "", **kw: Any) -> dict[str, Any]:
    return {
        "type": type_,
        "name": name,
        "label": label,
        "inputmode": kw.get("inputmode", ""),
        "autocomplete": kw.get("autocomplete", ""),
        "required": kw.get("required", False),
    }


def group(fields: list[dict[str, Any]], buttons: list[str], **kw: Any) -> dict[str, Any]:
    return {"kind": "form", "fields": fields, "buttons": buttons, "dialog": False, **kw}


# --- alan türleri ve form seçimi


@pytest.mark.parametrize(
    ("info", "kind"),
    [
        (f("email"), "email"),
        (f("text", "user_email", "E-posta adresiniz"), "email"),
        (f("text", "phone"), "tel"),
        (f("text", "", "Cep telefonu"), "tel"),
        (f("text", "fullname", "Adınız Soyadınız"), "name"),
        (f("text", "", "Ad"), "name"),
        (f("text", "x", "", autocomplete="cc-number"), "card"),
        (f("text", "cardNumber", "Kart numarası"), "card"),
        (f("password"), "password"),
        (f("checkbox", "kvkk"), "checkbox"),
        (f("text", "company", "Şirket"), "text"),
        (f("text", "address", "Adres"), "text"),  # "ad" öneki adres'i ad saymamalı
    ],
)
def test_classify_field(info: dict[str, Any], kind: str) -> None:
    assert classify_field(info) == kind


SIGNUP_8 = group(
    [
        f("text", "name", "Ad Soyad *", required=True),
        f("text", "email", "E-posta *", required=True),
        f("text", "phone", "Telefon *", required=True),
        f("text", "company", "Şirket *", required=True),
        f("text", "title", "Unvan"),
        f("text", "cardNumber", "Kart numarası *", required=True),
        f("password", "password", "Şifre *", required=True),
        f("password", "password2", "Şifre tekrar *", required=True),
        f("checkbox", "kvkk", "KVKK aydınlatma metnini okudum"),
    ],
    ["Kayıt ol"],
)


def test_pick_signup_prefers_signup_over_login_and_newsletter() -> None:
    inspect = {
        "groups": [
            group([f("email", "email"), f("password", "password")], ["Giriş yap"]),
            group([f("email", "email")], ["Bültene abone ol"]),
            SIGNUP_8,
        ],
        "social": [],
    }
    form = pick_signup(f"{SITE}/signup", inspect)
    assert form is not None
    assert len(form.data_fields) == 8 and form.required_count == 7
    assert form.card and form.consent_checkbox and form.social == []


def test_login_and_newsletter_forms_are_not_signup() -> None:
    """Yanlış alarm: giriş formu ve bülten formu kayıt sayılmaz (kayıt adresinde bile bülten
    formu tek başına değil, e-posta + kayıt düğmesi gerekir)."""
    login = {"groups": [group([f("email"), f("password")], ["Giriş yap"])]}
    newsletter = {"groups": [group([f("email")], ["Abone ol"])]}
    search = {"groups": [group([f("search", "q")], ["Ara"])]}
    assert pick_signup(f"{SITE}/login", login) is None
    assert pick_signup(f"{SITE}/", newsletter) is None
    assert pick_signup(f"{SITE}/", search) is None


def test_passwordless_and_spa_signups_are_found() -> None:
    magic = {"groups": [group([f("email", "email")], ["Hesap oluştur"])], "social": ["google"]}
    form = pick_signup(f"{SITE}/", magic)
    assert form is not None and form.social == ["google"]
    # <form> olmayan SPA: kayıt adresinde e-posta alanı yeter
    spa = {"groups": [{"kind": "container", "fields": [f("email")], "buttons": ["Devam"]}]}
    assert pick_signup(f"{SITE}/register", spa) is not None
    # Ödeme iframe'i kart sayılır
    stripe = {
        "groups": [
            group(
                [f("email"), f("password")],
                ["Sign up"],
                iframes=["https://js.stripe.com/v3/elements-inner-card"],
            )
        ]
    }
    signup = pick_signup(f"{SITE}/signup", stripe)
    assert signup is not None and signup.card


# --- fiyat ve KDV


def test_phrases_next_to_digits() -> None:
    assert has_phrase("funnel.vat_excluded", "₺1.299+kdv") == ["+kdv"]
    assert has_phrase("funnel.monthly", "₺99/ay") == ["/ay"]
    assert has_phrase("funnel.vat_excluded", "fiyatlara kdv dahildir") == []


@pytest.mark.parametrize(
    ("contexts", "text", "vat"),
    [
        (["pro ₺299 /ay +kdv"], "", ["+kdv"]),
        (["baslangic 990 tl kdv haric"], "", ["kdv haric"]),
        (["pro €29 /mo excl. vat"], "", ["excl. vat"]),
        # Yanlış alarm: KDV dahil
        (["pro ₺299 /ay kdv dahil"], "", []),
        # Yanlış alarm: KDV ifadesi fiyatın yanında değil, SSS'te
        (["pro ₺299 /ay"], "sss: faturalarimiza kdv haric tutar ayrica yazilir", []),
    ],
)
def test_pricing_info_vat(contexts: list[str], text: str, vat: list[str]) -> None:
    info = pricing_info(
        f"{SITE}/pricing", {"prices": len(contexts), "priceContexts": contexts, "text": text}
    )
    assert info.vat_excluded == vat


def test_pricing_info_periods_free_and_quote() -> None:
    info = pricing_info(
        f"{SITE}/pricing",
        {
            "prices": 3,
            "currencies": ["TL"],
            "priceContexts": ["ucretsiz ₺0", "pro ₺99/ay", "yillik ₺990"],
        },
    )
    assert info.free_tier and info.periods == ["monthly", "yearly"] and not info.quote_based
    quote = pricing_info(f"{SITE}/enterprise", {"prices": 0, "text": "fiyat icin bize ulasin"})
    assert quote.quote_based and quote.prices == 0


# --- mesafe ve adaylar


def page(url: str, links: list[str], status: int = 200, prices: int = 0) -> RenderedPage:
    return RenderedPage(
        url=url, final_url=url, status=status, internal_links=links, sd=SdSignals(prices=prices)
    )


def site_render(price_page_prices: int = 4) -> RenderData:
    return RenderData(
        pages=[
            page(f"{SITE}/", [f"{SITE}/features", f"{SITE}/login", f"{SITE}/pricing"]),
            page(f"{SITE}/features", [f"{SITE}/plans/details", f"{SITE}/signup"]),
            page(f"{SITE}/pricing", [], prices=price_page_prices),
            page(f"{SITE}/plans/details", [], prices=2),
            page(f"{SITE}/login", [f"{SITE}/register"], status=200),
            page(f"{SITE}/broken-pricing", [], status=404),
        ]
    )


def test_link_distances_bfs() -> None:
    d = link_distances(site_render(), f"{SITE}/")
    assert d["app.acme.test/"] == 0
    assert d["app.acme.test/pricing"] == 1
    assert d["app.acme.test/plans/details"] == 2
    assert d["app.acme.test/register"] == 2


def test_candidates_ordered_by_distance() -> None:
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, render=site_render())
    signup, pricing = candidates(ctx, f"{SITE}/")
    assert signup == [(f"{SITE}/signup", 2), (f"{SITE}/register", 2)]
    assert pricing[0] == (f"{SITE}/pricing", 1)


# --- modül (sahte yürüyüş)


def inspect_with(
    groups: list[dict[str, Any]] | None = None,
    prices: list[str] | None = None,
    text: str = "",
    social: list[str] | None = None,
    links: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "groups": groups or [],
        "social": social or [],
        "prices": len(prices or []),
        "priceContexts": prices or [],
        "currencies": ["TL"] if prices else [],
        "text": text,
        "links": links or [],
    }


def saas_raw() -> FunnelRaw:
    """SaaS: ana sayfa → CTA → giriş sayfası (yavaş) → kayıt; fiyat sayfasında +KDV."""
    return FunnelRaw(
        cta_text="Ücretsiz başla",
        steps=[
            RawStep("home", f"{SITE}/", 0, 900, "direct", inspect_with(text="premium planlar")),
            RawStep("login", f"{SITE}/login", 1, 3800, "cta", inspect_with()),
            RawStep("signup", f"{SITE}/register", 2, 1200, "link", inspect_with([SIGNUP_8])),
            RawStep(
                "pricing",
                f"{SITE}/pricing",
                1,
                800,
                "link",
                inspect_with(prices=["pro ₺299 /ay +kdv", "team ₺599 /ay +kdv"]),
            ),
        ],
    )


def ctx_for(render: RenderData | None = None) -> DeepContext:
    return DeepContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        target_url=SITE,
        render=render,
        events=MemoryEventSink(),
    )


async def run_module(raw: FunnelRaw, render: RenderData | None = None) -> tuple[DeepContext, Any]:
    seen: list[FunnelInput] = []

    async def collect(ctx: DeepContext, data_in: FunnelInput) -> FunnelRaw:
        seen.append(data_in)
        return raw

    ctx = ctx_for(render)
    out = ModuleOutput()
    await FunnelModule(Settings(), collect=collect).run(ctx, out)
    ctx.cache["seen"] = seen
    return ctx, {x.check_id: x for x in out.findings}


async def test_saas_funnel_end_to_end() -> None:
    ctx, found = await run_module(saas_raw(), site_render())
    assert set(found) == FUNNEL_IDS - {"web.funnel.price_hidden"}
    friction = found["web.funnel.signup_friction"]
    assert friction.severity == Severity.MEDIUM
    assert friction.evidence["issues"] == [
        "too_many_fields",
        "too_many_required",
        "card_required",
        "no_social_login",
    ]
    steps = found["web.funnel.too_many_steps"].evidence
    assert steps["issues"] == ["slow_step"] and steps["slowest_ms"] == 3800
    inputs = {
        x["kind"]: x["issues"] for x in found["web.funnel.mobile_input_types"].evidence["fields"]
    }
    assert inputs["email"] == ["email_type", "autocomplete_missing"]
    assert inputs["tel"] == ["tel_type", "autocomplete_missing"]
    assert "company" not in str(inputs)
    assert found["web.funnel.vat_excluded_price"].evidence["phrases"] == ["+kdv"]
    funnel = ctx.meta["funnel"]
    assert [s["kind"] for s in funnel["steps"]] == ["home", "login", "signup", "pricing"]
    assert funnel["steps"][2]["issues"] == [
        "web.funnel.signup_friction",
        "web.funnel.mobile_input_types",
        "web.funnel.too_many_steps",
    ]
    assert funnel["signup"]["fields"] == 8 and funnel["price_distance"] == 1
    # Adaylar render'ın link grafiğinden
    seen: list[FunnelInput] = ctx.cache["seen"]
    assert seen[0].pricing_candidates[0] == (f"{SITE}/pricing", 1)
    assert "deep.event.funnel_walked" in [e[1] for e in ctx.events.events]  # type: ignore[attr-defined]


async def test_clean_funnel_has_no_findings() -> None:
    """Yanlış alarm: kısa form (e-posta + şifre, doğru türler, Google ile giriş), CTA doğrudan
    kayda gidiyor, fiyatlar KDV dahil ve menüde."""
    signup = group(
        [
            f("email", "email", "E-posta", autocomplete="email", required=True),
            f("password", "password", "Şifre", autocomplete="new-password", required=True),
        ],
        ["Hesap oluştur"],
    )
    raw = FunnelRaw(
        steps=[
            RawStep("home", f"{SITE}/", 0, 700, "direct", inspect_with(text="fiyatlar")),
            RawStep(
                "signup", f"{SITE}/signup", 1, 900, "cta", inspect_with([signup], social=["google"])
            ),
            RawStep(
                "pricing",
                f"{SITE}/pricing",
                1,
                700,
                "link",
                inspect_with(prices=["₺199/ay kdv dahil"]),
            ),
        ]
    )
    ctx, found = await run_module(raw, site_render())
    assert found == {}
    assert ctx.funnel is not None and ctx.funnel.skipped_checks() == []


async def test_price_hidden_far_and_quote_based() -> None:
    render = RenderData(
        pages=[
            page(f"{SITE}/", [f"{SITE}/about"]),
            page(f"{SITE}/about", [f"{SITE}/premium"]),
            page(f"{SITE}/premium", [], prices=1),
        ]
    )
    raw = FunnelRaw(
        steps=[RawStep("home", f"{SITE}/", 0, 500, "direct", inspect_with(text="premium uyelik"))]
    )
    _, found = await run_module(raw, render)
    hidden = found["web.funnel.price_hidden"]
    assert hidden.severity == Severity.MEDIUM and hidden.evidence["price_distance"] == 2
    quote = FunnelRaw(
        steps=[
            RawStep("home", f"{SITE}/", 0, 500, "direct", inspect_with(text="kurumsal paketler")),
            RawStep(
                "pricing",
                f"{SITE}/pricing",
                1,
                500,
                "link",
                inspect_with(text="fiyat icin bize ulasin"),
            ),
        ]
    )
    _, found = await run_module(quote, site_render(price_page_prices=0))
    assert found["web.funnel.price_hidden"].severity == Severity.LOW
    assert found["web.funnel.price_hidden"].evidence["quote_based"] is True


async def test_no_paid_signal_and_no_signup() -> None:
    """Ücretli plan sinyali yok → fiyat check'leri geçer; kayıt formu yok → form check'leri
    atlanır ("geçti" sayılmaz)."""
    raw = FunnelRaw(
        steps=[RawStep("home", f"{SITE}/", 0, 500, "direct", inspect_with(text="kisisel blog"))]
    )
    ctx, found = await run_module(raw, RenderData(pages=[page(f"{SITE}/", [])]))
    assert found == {}
    assert ctx.funnel is not None
    assert ctx.funnel.status["pricing"] == PartStatus.NO_DATA
    assert set(ctx.meta["checks_skipped"]) == {
        "web.funnel.signup_friction",
        "web.funnel.mobile_input_types",
        "web.funnel.too_many_steps",
    }


async def test_failed_walk_skips_everything() -> None:
    ctx, found = await run_module(FunnelRaw())
    assert found == {} and set(ctx.meta["checks_skipped"]) == FUNNEL_IDS


async def test_blocked_site_does_not_walk() -> None:
    ctx = ctx_for()
    ctx.meta["deep_blocked"] = "site_blocked_bot"
    with pytest.raises(FunnelBlockedError):
        await FunnelModule(Settings()).run(ctx, ModuleOutput())


async def test_only_missing_social_login_is_low() -> None:
    data = FunnelData(site=SITE)
    data.signup = SignupForm(
        "/signup",
        fields=[
            FormField("email", "email", "", "email", True, "E-posta"),
            FormField("password", "password", "", "new-password", True, "Şifre"),
        ],
    )
    ctx = ctx_for()
    ctx.funnel = data
    checks = [c for c in load_builtin_checks().all() if c.id == "web.funnel.signup_friction"]
    result = await run_checks(ctx, checks)
    assert result.findings[0].severity == Severity.LOW


def test_skip_patterns_block_state_changing_ctas() -> None:
    import re

    patterns = [re.compile(p) for p in skip_patterns()]

    def blocked(label: str) -> bool:
        return any(p.search(label) for p in patterns)

    assert blocked("sepete ekle") and blocked("hemen satin al") and blocked("add to cart")
    assert (
        not blocked("ucretsiz basla")
        and not blocked("silikon kilif")
        and not blocked("get started")
    )


async def test_texts_render_with_params_in_both_languages() -> None:
    _, found = await run_module(saas_raw(), site_render())
    render = RenderData(pages=[page(f"{SITE}/", [])])
    raw = FunnelRaw(
        steps=[RawStep("home", f"{SITE}/", 0, 1, "direct", inspect_with(text="premium"))]
    )
    _, hidden = await run_module(raw, render)
    found.update(hidden)
    registry = {c.id: c for c in load_builtin_checks().all()}
    assert set(found) == FUNNEL_IDS
    for lang in ("tr", "en"):
        for check_id, finding in found.items():
            params = registry[check_id].text_params(finding.evidence, fragment_fn(check_id, lang))
            text = finding_text(check_id, lang, params)
            assert "{" not in text.title + text.description, (check_id, lang, text.description)


def test_funnel_checks_are_phase2_only() -> None:
    registry = load_builtin_checks()
    ids = {c.id for c in registry.all() if c.module == "funnel"}
    assert ids == FUNNEL_IDS
    assert not ids & {c.id for c in registry.for_target(TargetType.WEB, 1)}


async def test_funnel_checks_reject_phase1_context() -> None:
    from tests.checks.helpers import make_ctx

    checks = [c for c in load_builtin_checks().all() if c.module == "funnel"]
    result = await run_checks(make_ctx(), checks)
    assert sorted(result.failed) == sorted(FUNNEL_IDS)
