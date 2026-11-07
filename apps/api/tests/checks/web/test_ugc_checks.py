"""UGC/hassas platform sinyalleri ve 7 check: live_chat_without_age_gate,
guest_account_per_request, sensitive_ugc_indexed, crisis_resources_missing, exif_in_uploads,
test_data_in_production, sequential_public_ids."""

import json

import pytest

from launchcheck.checks.base import Severity
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.checks.web.content import SequentialPublicIds, TestDataInProduction
from launchcheck.checks.web.safety import AgeGateMissing, CrisisResourcesMissing
from launchcheck.checks.web.session import GuestAccountPerRequest
from launchcheck.checks.web.ugc_privacy import (
    ExifInUploads,
    SensitiveUgcIndexed,
    exif_flags,
    is_user_upload,
)
from launchcheck.collectors.web_signals import analyze_content
from launchcheck.i18n import finding_text, fragment_fn
from tests.checks.helpers import FIXTURES_WEB, fixture_html, make_ctx, resp, run

DERT = "https://dertlesme.com.tr"
POST_URL = f"{DERT}/iliski/alti-yillik-iliskim-bir-mesajla-bitti"
CORP = "https://yildizmuhendislik.com.tr"
NEW_CHECK_IDS = (
    "web.safety.age_gate_missing",
    "web.session.guest_account_per_request",
    "web.privacy.sensitive_ugc_indexed",
    "web.safety.crisis_resources_missing",
    "web.privacy.exif_in_uploads",
    "web.content.test_data_in_production",
    "web.ids.sequential_public_ids",
)


def image(name: str) -> bytes:
    return (FIXTURES_WEB / "images" / name).read_bytes()


def dert_ctx(
    home: str | None = None,
    post: str | None = None,
    canli: str | None = None,
    routes: dict[str, object] | None = None,
    **kwargs: object,
) -> object:
    return make_ctx(
        html=home if home is not None else fixture_html("ugc_home.html"),
        final_url=f"{DERT}/",
        pages=[
            (POST_URL, post if post is not None else fixture_html("ugc_post.html")),
            (f"{DERT}/canli", canli if canli is not None else fixture_html("ugc_canli.html")),
        ],
        routes=routes,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def corp_ctx(**kwargs: object) -> object:
    return make_ctx(html=fixture_html("corporate_home.html"), final_url=f"{CORP}/", **kwargs)  # type: ignore[arg-type]


def rendered(check_id: str, evidence: dict[str, object], lang: str = "tr") -> str:
    check = load_builtin_checks().get(check_id)
    assert check is not None
    text = finding_text(check_id, lang, check.text_params(evidence, fragment_fn(check_id, lang)))
    joined = " ".join([text.title, text.description, *text.fix_steps, text.fix_prompt])
    assert "{" not in joined, joined
    return text.title


# --- ortak tespit


def test_signals_support_platform_all_true() -> None:
    signals = analyze_content(
        [
            (f"{DERT}/", fixture_html("ugc_home.html")),
            (POST_URL, fixture_html("ugc_post.html")),
            (f"{DERT}/canli", fixture_html("ugc_canli.html")),
        ],
        [],
    )
    flags = signals.flags()
    for name in (
        "is_ugc",
        "has_live_media",
        "is_emotional_support",
        "has_anonymous_posting",
        "has_media_upload",
    ):
        assert flags[name] is True, name
    assert signals.live_with_strangers  # "rastgele biriyle görüntülü sohbet"
    assert "getUserMedia" in signals.live_media_signals  # iç sayfanın inline script'i
    assert {"dert", "loneliness", "depression", "anxiety"} <= set(signals.emotional_concepts)


def test_signals_corporate_site_all_false() -> None:
    signals = analyze_content([(f"{CORP}/", fixture_html("corporate_home.html"))], [])
    assert not any(signals.flags().values())  # iletişim formu (e-posta alanlı) UGC sayılmaz
    assert not signals.live_with_strangers


def test_signals_live_media_from_js_bundle() -> None:
    signals = analyze_content([(f"{CORP}/", "<html><body>Merhaba</body></html>")], ["x.livekit.io"])
    assert signals.has_live_media
    assert signals.live_media_signals == ["livekit"]


def test_signals_single_emotional_word_is_not_enough() -> None:
    html = "<html><body><p>Kaygı duymayın, ekibimiz yanınızda.</p></body></html>"
    assert not analyze_content([(f"{CORP}/", html)], []).is_emotional_support


# --- web.safety.age_gate_missing (canlı medya senaryoları)


async def test_live_chat_anonymous_without_age_is_critical() -> None:
    (finding,) = await run(AgeGateMissing, dert_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.CRITICAL
    ev = finding.evidence
    assert ev["live_media_pages"] == [f"{DERT}/", f"{DERT}/canli"]
    assert "getUserMedia" in ev["signals"]
    assert ev["age_statement_found"] is False
    assert ev["no_signup_required"] is True
    title = rendered(finding.check_id, ev)
    assert title == "Yabancılarla üyeliksiz görüntülü sohbet var ama yaş sınırı yok"
    assert rendered(finding.check_id, ev, "en").startswith("Strangers can video chat")


async def test_live_chat_terms_page_age_and_checkbox_no_finding() -> None:
    canli = fixture_html("ugc_canli.html").replace(
        '<button id="start">',
        '<label><input type="checkbox" name="kurallar_onay"> Kullanım şartlarını ve '
        'kuralları kabul ediyorum</label>\n    <button id="start">',
    )
    routes = {
        f"{DERT}/kurallar": resp(
            f"{DERT}/kurallar", 200, fixture_html("ugc_kurallar_age.html"), "text/html"
        )
    }
    ctx = dert_ctx(canli=canli, routes=routes)
    assert await run(AgeGateMissing, ctx) == []  # type: ignore[arg-type]
    assert f"{DERT}/kurallar" in ctx.fetcher.calls  # type: ignore[attr-defined]


async def test_live_chat_text_only_18_plus_is_low() -> None:
    home = fixture_html("ugc_home.html").replace(
        "Üye olmaya gerek yok.", "Sadece 18+ kullanıcılar."
    )
    (finding,) = await run(AgeGateMissing, dert_ctx(home=home))  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    assert finding.evidence["age_statement_found"] is True
    assert finding.evidence["age_checkbox_found"] is False
    assert rendered(finding.check_id, finding.evidence) == ("Yaş sınırı yazıyor ama onay alınmıyor")


async def test_live_chat_without_signup_phrase_is_high() -> None:
    home = fixture_html("ugc_home.html").replace("Üye olmaya gerek yok.", "")
    (finding,) = await run(AgeGateMissing, dert_ctx(home=home))  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH


async def test_live_chat_not_run_without_live_media() -> None:
    ctx = corp_ctx()
    assert await run(AgeGateMissing, ctx) == []  # type: ignore[arg-type]
    assert ctx.fetcher.calls == []  # type: ignore[attr-defined]  # ek sayfa çekilmedi


# --- web.session.guest_account_per_request


def _samples(names: list[str], html: str | None = None) -> list[tuple[str, dict[str, str]]]:
    base = html or fixture_html("ugc_home.html")
    return [
        (
            base.replace("AAnonim4227", name),
            {"set-cookie": f"laravel_session=v{i}; path=/; httponly"},
        )
        for i, name in enumerate(names)
    ]


async def test_guest_rotating_identity_is_medium() -> None:
    samples = _samples(["AAnonim4227", "AAnonim7872", "AAnonim8775"])
    (finding,) = await run(GuestAccountPerRequest, dert_ctx(samples=samples))  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    ev = finding.evidence
    assert ev["signals"] == ["meta_auth", "rotating_identity", "rotating_session_cookie"]
    assert ev["sample_identities"] == ["AAnonim4227", "AAnonim7872", "AAnonim8775"]
    assert ev["session_cookie_names"] == ["laravel_session"]
    assert "v0" not in json.dumps(ev)  # çerez değerleri kanıta yazılmaz
    rendered(finding.check_id, ev)


async def test_guest_only_meta_auth_is_low() -> None:
    samples = _samples(["AAnonim4227"] * 3)
    (finding,) = await run(GuestAccountPerRequest, dert_ctx(samples=samples))  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    assert finding.evidence["signals"][0] == "meta_auth"
    assert finding.evidence["sample_identities"] == []


async def test_guest_only_csrf_changes_no_finding() -> None:
    page = '<html><head><meta name="csrf-token" content="{t}"></head><body><a href="/giris">Giriş yap</a></body></html>'
    samples = [
        (page.replace("{t}", f"tok{i}abc123"), {"set-cookie": f"XSRF-TOKEN=x{i}"}) for i in range(3)
    ]
    assert await run(GuestAccountPerRequest, corp_ctx(samples=samples)) == []  # type: ignore[arg-type]


async def test_guest_single_sample_is_skipped() -> None:
    assert await run(GuestAccountPerRequest, dert_ctx()) == []  # type: ignore[arg-type]


# --- web.privacy.sensitive_ugc_indexed


async def test_sensitive_post_indexed_with_text_in_meta() -> None:
    (finding,) = await run(SensitiveUgcIndexed, dert_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    ev = finding.evidence
    assert ev["pages"] == [POST_URL]
    assert ev["full_text_in_meta"] is True
    assert "section:İlişki" in ev["sensitive_signals"]
    assert "Altı yıl" not in json.dumps(ev, ensure_ascii=False)  # gönderi metni kanıtta yok
    rendered(finding.check_id, ev)


async def test_sensitive_post_noindex_no_finding() -> None:
    post = fixture_html("ugc_post.html").replace("index,follow", "noindex,follow")
    assert await run(SensitiveUgcIndexed, dert_ctx(post=post)) == []  # type: ignore[arg-type]


async def test_sensitive_non_ugc_blog_not_run() -> None:
    blog = """<html><head><meta property="og:type" content="article">
    <meta property="article:author" content="Yıldız Mühendislik">
    <meta property="article:section" content="Sağlık ve güvenlik">
    <meta name="description" content="İş sağlığı ve güvenliği için fabrikada alınması gereken önlemler."></head>
    <body><h1>İş sağlığı</h1><p>İş sağlığı ve güvenliği için fabrikada alınması gereken önlemler.</p></body></html>"""
    ctx = corp_ctx(pages=[(f"{CORP}/saglik/is-guvenligi", blog)])
    assert await run(SensitiveUgcIndexed, ctx) == []  # type: ignore[arg-type]


# --- web.safety.crisis_resources_missing


async def test_crisis_missing_with_self_harm_report_option() -> None:
    (finding,) = await run(CrisisResourcesMissing, dert_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    ev = finding.evidence
    assert ev["found_on"] == []
    assert ev["missing_on_post_pages"] is True
    assert ev["self_harm_report_option"] is True
    assert rendered(finding.check_id, ev) == "Dert paylaşım platformunda acil yardım bilgisi yok"


def _with_footer_note(html: str) -> str:
    return html.replace("<footer>", "<footer>\n    <p>Acil durumdaysan 112'yi ara.</p>")


async def test_crisis_footer_on_every_page_no_finding() -> None:
    ctx = dert_ctx(
        home=_with_footer_note(fixture_html("ugc_home.html")),
        post=_with_footer_note(fixture_html("ugc_post.html")),
        canli=_with_footer_note(fixture_html("ugc_canli.html")),
    )
    assert await run(CrisisResourcesMissing, ctx) == []  # type: ignore[arg-type]


async def test_crisis_only_on_help_page_is_low() -> None:
    help_page = "<html><body><h1>Yardım</h1><p>Kriz anında 112 Acil Çağrı'yı ara.</p></body></html>"
    home = fixture_html("ugc_home.html").replace(
        '<a href="/kurallar">Kurallar</a>',
        '<a href="/kurallar">Kurallar</a><a href="/yardim">Yardım</a>',
    )
    routes = {f"{DERT}/yardim": resp(f"{DERT}/yardim", 200, help_page, "text/html")}
    (finding,) = await run(CrisisResourcesMissing, dert_ctx(home=home, routes=routes))  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    assert finding.evidence["found_on"] == [f"{DERT}/yardim"]
    assert rendered(finding.check_id, finding.evidence, "en") == (
        "Emergency help isn't visible on post and chat pages"
    )


async def test_crisis_not_run_without_emotional_support() -> None:
    assert await run(CrisisResourcesMissing, corp_ctx()) == []  # type: ignore[arg-type]


def test_crisis_texts_dont_hardcode_other_hotlines() -> None:
    for lang in ("tr", "en"):
        text = finding_text("web.safety.crisis_resources_missing", lang, {})
        joined = " ".join([text.description, *text.fix_steps, text.fix_prompt])
        assert "112" in joined
        for number in ("988", "116 123", "182"):
            assert number not in joined


# --- web.privacy.exif_in_uploads

IMG_URL = f"{DERT}/uploads/posts/2024/kahve.jpg"


def _img_routes(data: bytes) -> dict[str, object]:
    return {IMG_URL: resp(IMG_URL, 200, data, "image/jpeg")}


async def test_exif_gps_is_high_without_coordinates() -> None:
    ctx = dert_ctx(routes=_img_routes(image("gps.jpg")))
    (finding,) = await run(ExifInUploads, ctx)  # type: ignore[arg-type]
    assert finding.severity == Severity.HIGH
    ev = finding.evidence
    assert ev == {"images_checked": 1, "with_gps": [IMG_URL], "with_device_info": 1}
    dumped = json.dumps(ev)
    assert "41" not in dumped and "29" not in dumped  # koordinatlar kanıtta yok
    assert f"{DERT}/assets/logo.png" not in ctx.fetcher.calls  # type: ignore[attr-defined]
    assert rendered(finding.check_id, ev) == "Yüklenen görsellerde konum (GPS) bilgisi var"


async def test_exif_device_only_is_low() -> None:
    (finding,) = await run(ExifInUploads, dert_ctx(routes=_img_routes(image("device_only.jpg"))))  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    assert finding.evidence["with_gps"] == []
    assert finding.evidence["with_device_info"] == 1


async def test_exif_clean_image_no_finding() -> None:
    assert await run(ExifInUploads, dert_ctx(routes=_img_routes(image("clean.jpg")))) == []  # type: ignore[arg-type]


def test_exif_theme_and_asset_images_not_selected() -> None:
    host = "dertlesme.com.tr"
    assert is_user_upload(f"{DERT}/uploads/a.jpg", host)
    assert is_user_upload("https://cdn.dertlesme.com.tr/media/u/1.webp", host)  # kendi CDN'i
    assert not is_user_upload(f"{DERT}/theme/uploads/bg.jpg", host)
    assert not is_user_upload(f"{DERT}/uploads/logo.jpg", host)
    assert not is_user_upload(f"{DERT}/uploads/a.png", host)
    assert not is_user_upload("https://other.com/uploads/a.jpg", host)


async def test_exif_upload_without_images_is_info() -> None:
    home = fixture_html("ugc_home.html").replace(
        '<img src="/uploads/posts/2024/kahve.jpg" alt="Kahve fincanı">', ""
    )
    (finding,) = await run(ExifInUploads, dert_ctx(home=home))  # type: ignore[arg-type]
    assert finding.severity == Severity.INFO
    assert finding.evidence == {"images_checked": 0}
    assert rendered(finding.check_id, finding.evidence) == (
        "Yüklenen görsellerin konum bilgisini kontrol edemedik"
    )


def test_exif_flags_rejects_non_images_and_garbage() -> None:
    assert exif_flags(b"<html></html>") is None
    assert exif_flags(b"\xff\xd8\xff" + b"\x00" * 10) is None  # kesik JPEG
    assert exif_flags(image("clean.jpg")) == (False, False)


# --- web.content.test_data_in_production


async def test_test_data_room_titles_is_low() -> None:
    (finding,) = await run(TestDataInProduction, dert_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    texts = [m["text"] for m in finding.evidence["matches"]]
    assert texts == ["Test", "Test Ozel Oda"]
    rendered(finding.check_id, finding.evidence)


async def test_test_data_menu_link_exception_no_finding() -> None:
    html = """<html><body><main><ul><li><a href="/hiz">Hızlı Okuma Testi</a></li>
    <li><a href="/s">Test sonuçları</a></li><li>Testimonials</li></ul></main>
    <nav><a href="/t">Test</a></nav></body></html>"""
    assert await run(TestDataInProduction, corp_ctx(pages=[(f"{CORP}/x", html)])) == []  # type: ignore[arg-type]


async def test_test_data_three_matches_is_medium() -> None:
    html = """<html><body><main><h3>deneme</h3><h3>asdf</h3><h3>Lorem ipsum dolor</h3>
    <h3>foo</h3><h3>Foo Fighters konseri</h3></main></body></html>"""
    (finding,) = await run(TestDataInProduction, corp_ctx(pages=[(f"{CORP}/x", html)]))  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    texts = [m["text"] for m in finding.evidence["matches"]]
    assert texts == ["deneme", "asdf", "Lorem ipsum dolor", "foo"]


# --- web.ids.sequential_public_ids


async def test_sequential_room_ids_with_private_room_is_medium() -> None:
    (finding,) = await run(SequentialPublicIds, dert_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    ev = finding.evidence
    (pattern,) = ev["patterns"]
    assert pattern["pattern"] == "/canli/{id}"
    assert pattern["sample_ids"] == [9, 18, 19]
    assert pattern["pages"] == [f"{DERT}/", f"{DERT}/canli"]
    assert ev["private_items_listed"] is True
    assert (
        "şifreli"
        in finding_text(
            finding.check_id,
            "tr",
            load_builtin_checks()
            .get(finding.check_id)
            .text_params(ev, fragment_fn(finding.check_id, "tr")),  # type: ignore[union-attr]
        ).description
    )


async def test_sequential_blog_ids_not_sensitive() -> None:
    assert await run(SequentialPublicIds, corp_ctx()) == []  # type: ignore[arg-type]


async def test_sequential_uuid_rooms_no_finding() -> None:
    links = "".join(
        f'<a href="/room/{u}">Oda</a>'
        for u in (
            "0b0c5f6e-1f5e-4c55-9a39-2f4f8a1d7e10",
            "6f1d2c3b-4a5e-4f60-8b7c-9d0e1f2a3b4c",
            "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
        )
    )
    ctx = corp_ctx(pages=[(f"{CORP}/rooms", f"<html><body>{links}</body></html>")])
    assert await run(SequentialPublicIds, ctx) == []  # type: ignore[arg-type]


async def test_sequential_query_param_ids() -> None:
    links = "".join(f'<a href="/paylas?alinti={i}">Alıntı</a>' for i in (3, 4, 8))
    (finding,) = await run(
        SequentialPublicIds, corp_ctx(pages=[(f"{CORP}/x", f"<body>{links}</body>")])
    )  # type: ignore[arg-type]
    assert finding.severity == Severity.LOW
    assert finding.evidence["patterns"][0]["pattern"] == "/paylas?alinti={id}"


# --- i18n kapsaması


@pytest.mark.parametrize("check_id", NEW_CHECK_IDS)
def test_new_checks_registered_with_texts(check_id: str) -> None:
    assert load_builtin_checks().get(check_id) is not None
    for lang in ("tr", "en"):
        text = finding_text(check_id, lang, {})
        assert text.title and text.description and text.why_it_matters
        assert text.fix_steps and text.fix_prompt


# --- canlı örnekten gelen regresyonlar


async def test_guest_identity_with_avatar_initial_in_link() -> None:
    page = (
        '<html><head><meta name="auth" content="1"></head><body><a class="rail-profile" href="/ben">'
        '<span class="avatar">A</span><span>{name}</span></a></body></html>'
    )
    samples = [(page.replace("{name}", n), {}) for n in ("Anonim9371", "Anonim6074", "Anonim1180")]
    (finding,) = await run(GuestAccountPerRequest, corp_ctx(samples=samples))  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["sample_identities"] == ["Anonim9371", "Anonim6074", "Anonim1180"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("cevap verdiğin anda sakin bir sohbet açılır", False),  # "acilir" ≠ "acil"
        ("babam geçen ay kalp krizi geçirdi", False),
        ("hepten kafayı yedim 112 görüntüleme", False),  # sayaç, yardım hattı değil
        ("Acil durumdaysan 112'yi ara.", True),
        ("Kriz anında bir uzmana başvur.", True),
        ("Emergency? Call 112.", True),
    ],
)
def test_crisis_info_detection(text: str, expected: bool) -> None:
    from launchcheck.checks.shared.text import fold
    from launchcheck.checks.web.safety import has_crisis_info

    assert has_crisis_info(fold(text)) is expected


async def test_test_data_span_inside_room_link() -> None:
    html = """<html><body><main>
    <a class="list-row" href="/canli/19"><span class="strong">Test</span><span class="muted">Görüntülü · 0 kişi</span></a>
    <a class="list-row" href="/canli/18"><span class="strong">Anonim3508 odası</span></a>
    </main></body></html>"""
    (finding,) = await run(TestDataInProduction, corp_ctx(pages=[(f"{CORP}/canli", html)]))  # type: ignore[arg-type]
    assert [m["text"] for m in finding.evidence["matches"]] == ["Test"]
