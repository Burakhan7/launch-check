"""Özel nitelikli veri / eşleşme / giriş sinyalleri, önem yükseltme ve 5 check:
template_placeholders, special_category_without_consent, age_gate_missing (eşleşme senaryoları),
platform_default_domain, kvkk_transfer_basis."""

import json
from pathlib import Path

import pytest

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.checks.web.infra import PlatformDefaultDomain
from launchcheck.checks.web.legal_texts import (
    KvkkTransferBasis,
    TemplatePlaceholders,
    find_placeholders,
)
from launchcheck.checks.web.safety import AgeGateMissing
from launchcheck.checks.web.special_data import SpecialCategoryWithoutConsent
from launchcheck.collectors.web_signals import analyze_content
from launchcheck.i18n import finding_text, fragment_fn
from launchcheck.scoring import escalate_for_sensitive_data
from tests.checks.helpers import FIXTURES_WEB, make_ctx, resp, run

FIX = FIXTURES_WEB / "sensitive"
ESL = "https://eslesme.vercel.app"
NEW_IDS = (
    "web.content.template_placeholders",
    "web.privacy.special_category_without_consent",
    "web.safety.age_gate_missing",
    "web.infra.platform_default_domain",
    "web.privacy.kvkk_transfer_basis",
)


def html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def eslesme_ctx(home: str | None = None, **kwargs: object) -> object:
    """Örnek siteye benzeyen Next.js uygulaması: ana sayfa + anket + gizlilik."""
    return make_ctx(
        html=home or html("next_match_home.html"),
        final_url=f"{ESL}/",
        pages=[
            (f"{ESL}/anket", html("next_match_survey.html")),
            (f"{ESL}/gizlilik", html("next_match_privacy.html")),
        ],
        **kwargs,  # type: ignore[arg-type]
    )


def single(url: str, name: str, **kwargs: object) -> object:
    return make_ctx(html=html(name), final_url=url, **kwargs)  # type: ignore[arg-type]


def rendered(check_id: str, evidence: dict[str, object], lang: str = "tr") -> str:
    check = load_builtin_checks().get(check_id)
    assert check is not None
    text = finding_text(check_id, lang, check.text_params(evidence, fragment_fn(check_id, lang)))
    joined = " ".join([text.title, text.description, *text.fix_steps, text.fix_prompt])
    assert "{" not in joined, joined
    return text.title


# --- ortak sinyaller


def _signals(pages: list[tuple[str, str]]) -> object:
    return analyze_content(pages, [])


def test_signals_matching_site() -> None:
    s = _signals(
        [
            (f"{ESL}/", html("next_match_home.html")),
            (f"{ESL}/anket", html("next_match_survey.html")),
        ]
    )
    flags = s.flags()  # type: ignore[attr-defined]
    assert flags["collects_special_category_data"] is True
    assert flags["special_categories"] == ["religion", "politics"]
    assert flags["is_dating_or_matching"] is True
    assert flags["has_login"] is True


@pytest.mark.parametrize(
    "name",
    ["wp_religion_history_blog.html", "news_politics.html", "plain_gender_form.html"],
)
def test_signals_no_special_data_without_sensitive_questions(name: str) -> None:
    """Dinler tarihi yazısı, siyaset haberi (form yok/ilgisiz form), sadece cinsiyet alanı."""
    s = _signals([("https://s.com/", html(name))])
    assert s.collects_special_category_data is False  # type: ignore[attr-defined]


def test_signals_marriage_blog_is_not_matching_platform() -> None:
    s = _signals([("https://aile.com/", html("wp_marriage_blog.html"))])
    assert s.is_dating_or_matching is False  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("plain_membership_form.html", ["union", "criminal"]),  # düz HTML, label'sız tablo
        ("wp_health_form.html", ["health"]),  # WordPress Contact Form 7
        ("spa_consented_survey.html", ["health"]),  # SPA (önceden render)
    ],
)
def test_signals_special_categories_across_structures(name: str, expected: list[str]) -> None:
    s = _signals([("https://s.com/", html(name))])
    assert sorted(s.special_categories) == sorted(expected)  # type: ignore[attr-defined]


def test_single_weak_signal_without_choice_does_not_qualify() -> None:
    page = '<form><label>Sağlık durumunuz hakkında not <input name="not"></label></form>'
    assert _signals([("https://s.com/", page)]).special_categories == []  # type: ignore[attr-defined]


# --- önem yükseltme (scoring)


def _f(check_id: str, severity: Severity, category: Category = Category.SECURITY) -> Finding:
    return Finding(check_id=check_id, severity=severity, category=category, evidence={"x": 1})


def test_escalation_with_special_category_data() -> None:
    findings = [
        _f("a", Severity.MEDIUM),
        _f("b", Severity.HIGH),
        _f("c", Severity.MEDIUM, Category.PROD_READINESS),
        _f("d", Severity.LOW),
        _f("e", Severity.MEDIUM),
    ]
    out = escalate_for_sensitive_data(findings, {"collects_special_category_data": True}, ["e"])
    a, b, c, d, e = out
    assert a.severity == Severity.HIGH
    assert a.evidence["escalated_due_to"] == "special_category_data"
    assert a.evidence["sensitive_data_note"] is True
    assert b.severity == Severity.HIGH and b.evidence["sensitive_data_note"] is True
    assert "escalated_due_to" not in b.evidence
    assert c == findings[2] and d == findings[3]  # prod_readiness ve low değişmez
    assert e == findings[4]  # önemi özel veriyi zaten hesaba katan check atlanır


def test_no_escalation_without_special_data() -> None:
    findings = [_f("a", Severity.MEDIUM)]
    assert escalate_for_sensitive_data(findings, {}) == findings


def test_pipeline_skips_checks_that_consider_special_data() -> None:
    aware = {c.id for c in load_builtin_checks().all() if c.considers_special_data}
    assert {
        "web.content.template_placeholders",
        "web.privacy.special_category_without_consent",
        "web.privacy.kvkk_transfer_basis",
    } <= aware


# --- web.content.template_placeholders


async def test_placeholders_legal_page_with_special_data_is_critical() -> None:
    findings = await run(TemplatePlaceholders, eslesme_ctx())  # type: ignore[arg-type]
    (finding,) = findings
    assert finding.severity == Severity.CRITICAL
    assert finding.category == Category.SECURITY
    texts = [m["text"] for m in finding.evidence["matches"]]
    assert "[Veri sorumlusunun adı soyadı / şirket unvanı]" in texts
    assert "[Adres]" in texts and "[kvkk@alanadiniz.com]" in texts
    assert "[1]" not in texts and "[Güncellendi]" not in texts  # dipnot, etiket
    match = next(m for m in finding.evidence["matches"] if m["text"] == "[Adres]")
    assert match["legal_page"] is True
    assert "şirket unvanı], [Adres]. Kişisel" in match["context"]  # önü ve arkası 40 karakter
    assert (
        rendered(finding.check_id, finding.evidence)
        == "Yasal metinlerinde doldurulmamış alanlar var"
    )


async def test_placeholders_legal_page_without_special_data_is_high() -> None:
    ctx = single("https://firma.com/kullanim-kosullari", "plain_terms_placeholder.html")
    (finding,) = await run(TemplatePlaceholders, ctx)  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH
    texts = {m["text"] for m in finding.evidence["matches"]}
    assert texts == {"[Şirket Unvanı]", "[Şehir]", "__EFFECTIVE_DATE__"}


async def test_placeholders_wordpress_lorem_and_example_email_medium() -> None:
    (finding,) = await run(
        TemplatePlaceholders, single("https://kafe.com/hakkimizda", "wp_lorem_about.html")
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    assert finding.category == Category.PROD_READINESS
    texts = {m["text"] for m in finding.evidence["matches"]}
    assert "Lorem ipsum" in texts
    assert "info@sirketiniz.com" in texts or "mailto:info@sirketiniz.com" in texts
    # Meşru köşeli parantezler, markdown link, kod bloğu ve input placeholder sayılmaz
    for text in (
        "[1]",
        "[2]",
        "[Güncellendi]",
        "[menüde]",
        "[Adınız]",
        "{{ name }}",
        "[Adınız Soyadınız]",
    ):
        assert text not in texts
    assert (
        rendered(finding.check_id, finding.evidence, "en") == "Your site has unfilled template text"
    )


async def test_placeholders_spa_undefined_but_not_legal_null_and_void() -> None:
    (finding,) = await run(
        TemplatePlaceholders, single("https://app.com/panel", "spa_dashboard_undefined.html")
    )  # type: ignore[arg-type]
    texts = [m["text"] for m in finding.evidence["matches"]]
    assert texts == ["undefined", "NaN"]  # uzun paragraftaki "null and void" sayılmaz


async def test_placeholders_vue_client_templates_not_reported() -> None:
    assert (
        await run(TemplatePlaceholders, single("https://shop.com/", "vue_template_page.html")) == []
    )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "Kaynak [1] ve [12]'ye bakınız.",
        "[Güncellendi] Yeni sürüm yayında",
        "Ayrıntılar [burada](https://x.com) yazıyor",
        "Bu bir test sayfası değildir",
        "Toplam: 0 ürün",
    ],
)
def test_find_placeholders_false_positives(text: str) -> None:
    assert find_placeholders(text, client_templates=False) == []


def test_find_placeholders_variants() -> None:
    kinds = {
        p.text: p.kind
        for p in find_placeholders(
            "İletişim: [Your Name], <<firma adı>> TODO buraya adresinizi yazın", False
        )
    }
    assert kinds["[Your Name]"] == "contact"
    assert kinds["<<firma adı>>"] == "contact"
    assert kinds["TODO"] == "draft"
    assert any(k.startswith("buraya") for k in kinds)


# --- web.privacy.special_category_without_consent


async def test_special_without_consent_multi_step_is_high() -> None:
    (finding,) = await run(SpecialCategoryWithoutConsent, eslesme_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH
    (form,) = finding.evidence["forms"]
    assert form["page"] == f"{ESL}/anket"
    assert form["categories"] == ["politics", "religion"]
    assert form["has_explicit_consent"] is False
    assert form["multi_step_unverified"] is True  # CTA doğrudan ankete gidiyor, onay adımı yok
    dumped = json.dumps(finding.evidence, ensure_ascii=False)
    assert "Sünni" not in dumped and "Muhafazakâr" not in dumped  # alan değerleri yok
    rendered(finding.check_id, finding.evidence)
    text = finding_text(finding.check_id, "tr", {})
    assert "hukuki destek al" in text.description


async def test_special_consent_on_earlier_step_via_cta_no_finding() -> None:
    home = html("next_match_home.html").replace('href="/anket"', 'href="/basla"')
    step0 = (
        '<html><body><form><label><input type="checkbox" name="riza"> Özel nitelikli '
        'verilerimin işlenmesine açık rıza veriyorum</label><a href="/anket">Devam</a></form></body></html>'
    )
    routes = {f"{ESL}/basla": resp(f"{ESL}/basla", 200, step0, "text/html")}
    assert await run(SpecialCategoryWithoutConsent, eslesme_ctx(home=home, routes=routes)) == []  # type: ignore[arg-type]


async def test_special_plain_html_other_categories_medium() -> None:
    (finding,) = await run(
        SpecialCategoryWithoutConsent,
        single("https://birlik.org/basvuru", "plain_membership_form.html"),
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["forms"][0]["categories"] == ["criminal", "union"]


async def test_special_wordpress_bundled_consent_is_low() -> None:
    (finding,) = await run(
        SpecialCategoryWithoutConsent,
        single("https://diyet.com.tr/on-degerlendirme/", "wp_health_form.html"),
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    (form,) = finding.evidence["forms"]
    assert form["has_explicit_consent"] is True and form["bundled_consent"] is True


async def test_special_spa_with_explicit_consent_no_finding() -> None:
    assert (
        await run(
            SpecialCategoryWithoutConsent, single("https://anket.com/", "spa_consented_survey.html")
        )
        == []
    )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name", ["wp_religion_history_blog.html", "news_politics.html", "plain_gender_form.html"]
)
async def test_special_false_positives_not_run(name: str) -> None:
    assert await run(SpecialCategoryWithoutConsent, single("https://s.com/", name)) == []  # type: ignore[arg-type]


async def test_special_footer_link_is_not_consent() -> None:
    survey = html("next_match_survey.html").replace(
        "<footer>", '<footer><label><input type="checkbox"> Açık rıza veriyorum</label>'
    )
    ctx = make_ctx(html=survey, final_url=f"{ESL}/anket")
    (finding,) = await run(SpecialCategoryWithoutConsent, ctx)
    assert finding.evidence["forms"][0]["has_explicit_consent"] is False


# --- web.safety.age_gate_missing (eşleşme senaryoları)


async def test_age_gate_matching_site_high_with_under_18_years() -> None:
    (finding,) = await run(AgeGateMissing, eslesme_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH
    ev = finding.evidence
    assert ev["platform_types"] == ["dating_or_matching"]
    assert ev["age_statement_found"] is False
    assert ev["allows_under_18_birth_year"] is True
    assert f"{ESL}/anket" in ev["pages_checked"]
    assert (
        "18 yaş altı biri kayıt olabilir"
        in finding_text(
            finding.check_id,
            "tr",
            load_builtin_checks()
            .get(finding.check_id)
            .text_params(ev, fragment_fn(finding.check_id, "tr")),  # type: ignore[union-attr]
        ).description
    )
    rendered(finding.check_id, ev, "en")


async def test_age_gate_wordpress_dating_with_gate_no_finding() -> None:
    assert await run(AgeGateMissing, single("https://ciddi.com.tr/", "wp_dating_gated.html")) == []  # type: ignore[arg-type]


async def test_age_gate_statement_but_birth_year_allows_minors_is_low() -> None:
    page = html("wp_dating_gated.html").replace("<option>2005</option>", "<option>2015</option>")
    (finding,) = await run(AgeGateMissing, make_ctx(html=page, final_url="https://ciddi.com.tr/"))
    assert finding.severity == Severity.LOW
    assert finding.evidence["allows_under_18_birth_year"] is True


async def test_age_gate_marriage_blog_not_run() -> None:
    assert (
        await run(AgeGateMissing, single("https://aile.com/flort", "wp_marriage_blog.html")) == []
    )  # type: ignore[arg-type]


async def test_age_gate_live_course_platform_not_run() -> None:
    ctx = single(
        "https://akademi.com/",
        "spa_live_course.html",
        scripts={
            "https://meet.jit.si/external_api.js": "window.JitsiMeetExternalAPI = function(){}"
        },
    )
    assert await run(AgeGateMissing, ctx) == []  # type: ignore[arg-type]


# --- web.infra.platform_default_domain


async def test_platform_domain_matching_site_medium() -> None:
    (finding,) = await run(PlatformDefaultDomain, eslesme_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence == {
        "host": "eslesme.vercel.app",
        "platform": "vercel",
        "reasons": ["special_category_data", "has_login"],
    }
    rendered(finding.check_id, finding.evidence)


async def test_platform_domain_plain_shop_with_payment_medium() -> None:
    page = (
        '<html><head><script src="https://js.stripe.com/v3/"></script></head><body>'
        '<a href="/sepet">Sepetim</a><h1>El yapımı sabunlar</h1></body></html>'
    )
    (finding,) = await run(
        PlatformDefaultDomain,
        make_ctx(html=page, final_url="https://sabun.onrender.com/", inline=[]),
    )
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["platform"] == "onrender"
    assert "payment" in finding.evidence["reasons"]


async def test_platform_domain_spa_login_only_low() -> None:
    page = '<html><body><div id="root"><a href="/login">Log in</a></div></body></html>'
    (finding,) = await run(
        PlatformDefaultDomain, make_ctx(html=page, final_url="https://app.up.railway.app/")
    )
    assert finding.severity == Severity.LOW
    assert finding.evidence["reasons"] == ["has_login"]


async def test_platform_domain_wordpress_contact_form_low() -> None:
    page = (FIXTURES_WEB / "form_consent_no_captcha.html").read_text(encoding="utf-8")
    (finding,) = await run(
        PlatformDefaultDomain, make_ctx(html=page, final_url="https://kafe.netlify.app/")
    )
    assert finding.evidence["reasons"] == ["personal_data_form"]


async def test_platform_domain_portfolio_no_finding() -> None:
    page = '<html><body><h1>Ali Yılmaz — Portföy</h1><a href="/projeler">Projeler</a></body></html>'
    assert (
        await run(PlatformDefaultDomain, make_ctx(html=page, final_url="https://ali.vercel.app/"))
        == []
    )


async def test_platform_domain_custom_domain_no_finding() -> None:
    ctx = make_ctx(html=html("next_match_home.html"), final_url="https://eslesme.com.tr/")
    assert await run(PlatformDefaultDomain, ctx) == []


# --- web.privacy.kvkk_transfer_basis


async def test_kvkk_transfer_matching_site_medium() -> None:
    (finding,) = await run(KvkkTransferBasis, eslesme_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    ev = finding.evidence
    assert ev["page"] == f"{ESL}/gizlilik"
    assert ev["transfer_basis_consent_only"] is True
    assert ev["consent_conditioned_on_service"] is True
    assert ev["mentioned_destinations"] == ["İrlanda", "Almanya", "ABD"]
    assert ev["amendment_referenced"] is False
    for lang in ("tr", "en"):
        text = finding_text(finding.check_id, lang, {})
        assert ("hukuki görüş değildir" in text.description) or (
            "not legal advice" in text.description
        )


async def test_kvkk_transfer_wordpress_consent_only_low() -> None:
    ctx = single("https://kargo.com.tr/kvkk/", "wp_kvkk_consent_only.html")
    (finding,) = await run(KvkkTransferBasis, ctx)  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    assert finding.evidence["mentioned_destinations"] == ["Hollanda"]
    assert finding.evidence["consent_conditioned_on_service"] is False


async def test_kvkk_transfer_footer_legal_page_is_fetched() -> None:
    home = '<html lang="tr"><body><h1>Kargo</h1><footer><a href="/kvkk/">KVKK</a></footer></body></html>'
    routes = {
        "https://kargo.com.tr/kvkk/": resp(
            "https://kargo.com.tr/kvkk/", 200, html("wp_kvkk_consent_only.html"), "text/html"
        )
    }
    ctx = make_ctx(html=home, final_url="https://kargo.com.tr/", routes=routes)
    (finding,) = await run(KvkkTransferBasis, ctx)
    assert finding.evidence["page"] == "https://kargo.com.tr/kvkk/"


async def test_kvkk_transfer_correct_text_with_safeguards_no_finding() -> None:
    ctx = single("https://firma.com/aydinlatma-metni", "plain_kvkk_correct.html")
    assert await run(KvkkTransferBasis, ctx) == []  # type: ignore[arg-type]


async def test_kvkk_transfer_english_page_not_run() -> None:
    assert await run(KvkkTransferBasis, single("https://firm.com/privacy", "en_privacy.html")) == []  # type: ignore[arg-type]


async def test_kvkk_transfer_non_legal_page_ignored() -> None:
    # Gizlilik sayfası değil: blogda geçen "yurt dışı aktarım" ve "açık rıza" sayılmaz
    page = html("wp_kvkk_consent_only.html").replace(
        "KVKK Aydınlatma Metni", "Blog: kargo ipuçları"
    )
    assert (
        await run(
            KvkkTransferBasis, make_ctx(html=page, final_url="https://kargo.com.tr/blog/ipuclari")
        )
        == []
    )


# --- kayıt ve metinler


@pytest.mark.parametrize("check_id", NEW_IDS)
def test_checks_registered_with_texts(check_id: str) -> None:
    assert load_builtin_checks().get(check_id) is not None
    for lang in ("tr", "en"):
        text = finding_text(check_id, lang, {})
        assert text.title and text.description and text.fix_steps and text.fix_prompt


def test_old_live_chat_check_removed() -> None:
    assert load_builtin_checks().get("web.safety.live_chat_without_age_gate") is None


def test_keyword_files_balanced_between_languages() -> None:
    import yaml

    base = Path(__file__).parents[3] / "src" / "launchcheck" / "keywords"
    tr = yaml.safe_load((base / "tr.yaml").read_text(encoding="utf-8"))
    en = yaml.safe_load((base / "en.yaml").read_text(encoding="utf-8"))
    assert set(tr) == set(en)
    assert set(tr["special_categories"]) == set(en["special_categories"])
    for name, words in tr["special_categories"].items():
        assert words and en["special_categories"][name], name


# --- canlı örnekten gelen genellemeler: düğmeli seçenekler, for'suz label + number alanı


def test_signals_button_chip_questions_count_as_choices() -> None:
    s = _signals([("https://s.com/anket", html("react_chip_survey.html"))])
    assert sorted(s.special_categories) == ["politics", "religion"]  # type: ignore[attr-defined]


def test_signals_navigation_buttons_are_not_questions() -> None:
    page = (
        "<html><body><header><nav><button type=button>Din</button>"
        "<button type=button>Siyaset</button></nav></header></body></html>"
    )
    assert _signals([("https://s.com/", page)]).special_categories == []  # type: ignore[attr-defined]


async def test_special_react_chip_survey_without_consent_high() -> None:
    ctx = make_ctx(html=html("react_chip_survey.html"), final_url=f"{ESL}/anket")
    (finding,) = await run(SpecialCategoryWithoutConsent, ctx)
    assert finding.severity == Severity.HIGH
    assert finding.evidence["forms"][0]["multi_step_unverified"] is True


def _dating_page(birth_input: str) -> str:
    return (
        '<html lang="tr"><head><title>Eşleşme — eş adayı bul</title></head><body>'
        '<nav><a href="/giris">Giriş yap</a></nav><h1>Evlilik için eşleşme</h1>'
        "<p>Bu platform 18 yaşından büyükler içindir.</p>"
        f'<div><label class="font-medium">Doğum yılınız</label>{birth_input}</div></body></html>'
    )


async def test_age_gate_number_birth_year_with_adult_max_no_finding() -> None:
    from datetime import UTC, datetime

    cutoff = datetime.now(UTC).year - 18
    page = _dating_page(f'<input type="number" min="1950" max="{cutoff}">')
    assert await run(AgeGateMissing, make_ctx(html=page, final_url="https://e.com/")) == []


async def test_age_gate_number_birth_year_without_max_allows_minors() -> None:
    page = _dating_page('<input type="number" min="1950">')
    (finding,) = await run(AgeGateMissing, make_ctx(html=page, final_url="https://e.com/"))
    assert finding.severity == Severity.LOW  # 18 yaş ifadesi var ama alan 18 altını kabul ediyor
    assert finding.evidence["allows_under_18_birth_year"] is True


def test_old_reports_map_to_renamed_check() -> None:
    from launchcheck.api.report import canonical_check_id

    assert (
        canonical_check_id("web.safety.live_chat_without_age_gate") == "web.safety.age_gate_missing"
    )
    assert canonical_check_id("web.files.env_exposed") == "web.files.env_exposed"
