"""duplicate_title_description, form_without_consent, third_party_form_embed,
insecure_http_links, malformed_youtube, img_missing_alt."""

import json

import pytest

from launchcheck.checks.base import Severity
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.checks.web.a11y import ImgMissingAlt
from launchcheck.checks.web.links import InsecureHttpLinks, MalformedYoutube, youtube_problem
from launchcheck.checks.web.privacy import FormWithoutConsent, ThirdPartyFormEmbed
from launchcheck.checks.web.seo_content import DuplicateTitleDescription
from launchcheck.i18n import finding_text, fragment_fn
from tests.checks.helpers import fixture_html, make_ctx, run

ULKU = "https://ulkuocaklari.org.tr"


def ulku_ctx() -> object:
    return make_ctx(
        html=fixture_html("ulku_home.html"),
        final_url=f"{ULKU}/",
        pages=[
            (f"{ULKU}/iletisim", fixture_html("ulku_iletisim.html")),
            (f"{ULKU}/oneri", fixture_html("ulku_oneri.html")),
            (f"{ULKU}/haberler", fixture_html("ulku_haber.html")),
        ],
    )


def rendered(check_id: str, evidence: dict[str, object], lang: str = "tr") -> str:
    check = load_builtin_checks().get(check_id)
    assert check is not None
    text = finding_text(check_id, lang, check.text_params(evidence, fragment_fn(check_id, lang)))
    joined = " ".join([text.title, text.description, *text.fix_steps, text.fix_prompt])
    assert "{" not in joined, joined
    return text.title


# --- web.seo.duplicate_title_description


async def test_duplicate_ulkuocaklari_three_of_four_same_title() -> None:
    (finding,) = await run(DuplicateTitleDescription, ulku_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    ev = finding.evidence
    (group,) = ev["duplicate_titles"]
    assert group["title"] == "Ülkü Ocakları Eğitim ve Kültür Vakfı Genel Merkezi"
    assert group["pages"] == [f"{ULKU}/", f"{ULKU}/iletisim", f"{ULKU}/oneri"]
    assert ev["majority_same_title"] is True
    assert len(ev["duplicate_descriptions"]) == 1
    assert ev["description_equals_title"] == [f"{ULKU}/", f"{ULKU}/iletisim", f"{ULKU}/oneri"]
    assert ev["short_descriptions"] == []  # başlık uzunluğundaki açıklama 50 karakterden uzun
    assert rendered(finding.check_id, ev) == "Sayfalarının çoğu aynı başlığı kullanıyor"
    assert rendered(finding.check_id, ev, "en") == "Most of your pages use the same title"


def _page(title: str, desc: str, canonical: str = "") -> str:
    link = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f'<html><head><title>{title}</title><meta name="description" content="{desc}">{link}</head><body></body></html>'


LONG = "Bu sayfa hakkında yeterince uzun ve sayfaya özel bir açıklama metni burada yer alıyor."


async def test_duplicate_all_unique_no_finding() -> None:
    ctx = make_ctx(
        html=_page("Ana sayfa", "Ana " + LONG),
        pages=[
            ("https://s.com/a", _page("A", "A " + LONG)),
            ("https://s.com/b", _page("B", "B " + LONG)),
        ],
    )
    assert await run(DuplicateTitleDescription, ctx) == []


async def test_duplicate_same_canonical_counts_once() -> None:
    same = _page("Ürün", "Ürün " + LONG, canonical="https://s.com/urun")
    ctx = make_ctx(
        html=_page("Ana", "Ana " + LONG),
        final_url="https://s.com/",
        pages=[
            ("https://s.com/urun", same),
            ("https://s.com/urun?ref=nav", same),  # aynı canonical → tek sayfa
            ("https://s.com/hakkimizda", _page("Hakkımızda", "Hakkımızda " + LONG)),
        ],
    )
    assert await run(DuplicateTitleDescription, ctx) == []


async def test_duplicate_needs_three_pages() -> None:
    ctx = make_ctx(html=_page("Aynı", "kısa"), pages=[("https://s.com/a", _page("Aynı", "kısa"))])
    assert await run(DuplicateTitleDescription, ctx) == []


async def test_duplicate_description_only_issues_are_low() -> None:
    ctx = make_ctx(
        html=_page("A", "kısa"),
        pages=[("https://s.com/b", _page("B", LONG)), ("https://s.com/c", _page("C", "C " + LONG))],
    )
    (finding,) = await run(DuplicateTitleDescription, ctx)
    assert finding.severity == Severity.LOW
    assert finding.evidence["short_descriptions"] == ["https://app.example.com/"]


# --- web.privacy.form_without_consent


async def test_form_ulkuocaklari_iletisim_no_consent_no_captcha() -> None:
    ctx = make_ctx(html=fixture_html("ulku_iletisim.html"), final_url=f"{ULKU}/iletisim")
    (finding,) = await run(FormWithoutConsent, ctx)
    assert finding.severity == Severity.MEDIUM
    (form,) = finding.evidence["forms"]
    assert form["fields"] == ["email", "name", "tel"]
    assert form["has_consent"] is False
    assert form["has_spam_protection"] is False
    assert form["issues"] == ["no_consent", "no_spam_protection"]
    assert form["action"] == f"{ULKU}/iletisim/gonder"
    assert "csrf-should-never-appear" not in json.dumps(finding.evidence)
    title = rendered(finding.check_id, finding.evidence)
    assert title == "Kişisel veri toplayan formunda KVKK aydınlatması/onayı yok"


async def test_form_with_tckn_is_high() -> None:
    (finding,) = await run(FormWithoutConsent, make_ctx(html=fixture_html("form_tckn.html")))
    assert finding.severity == Severity.HIGH
    assert finding.evidence["forms"][0]["fields"] == ["birth_date", "id_number", "name"]


async def test_form_with_kvkk_checkbox_and_turnstile_ok() -> None:
    assert (
        await run(FormWithoutConsent, make_ctx(html=fixture_html("form_consent_turnstile.html")))
        == []
    )


async def test_form_consent_without_captcha_is_low() -> None:
    (finding,) = await run(
        FormWithoutConsent, make_ctx(html=fixture_html("form_consent_no_captcha.html"))
    )
    assert finding.severity == Severity.LOW
    assert finding.evidence["forms"][0]["issues"] == ["no_spam_protection"]
    assert (
        rendered(finding.check_id, finding.evidence)
        == "Kişisel veri formunda spam koruması görünmüyor"
    )


async def test_form_http_action_is_high() -> None:
    (finding,) = await run(FormWithoutConsent, make_ctx(html=fixture_html("form_http_action.html")))
    assert finding.severity == Severity.HIGH
    assert finding.evidence["forms"][0]["insecure_action"] is True
    assert finding.evidence["forms"][0]["issues"] == ["insecure_action"]


async def test_search_and_login_forms_skipped() -> None:
    assert (
        await run(FormWithoutConsent, make_ctx(html=fixture_html("form_search_login.html"))) == []
    )


async def test_same_form_on_every_page_reported_once() -> None:
    html = fixture_html("ulku_iletisim.html")
    ctx = make_ctx(
        html=html, final_url=f"{ULKU}/", pages=[(f"{ULKU}/a", html), (f"{ULKU}/b", html)]
    )
    (finding,) = await run(FormWithoutConsent, ctx)
    assert finding.evidence["count"] == 1


# --- web.privacy.third_party_form_embed


async def test_embed_monday_no_consent_is_medium_without_form_id() -> None:
    ctx = make_ctx(html=fixture_html("ulku_oneri.html"), final_url=f"{ULKU}/oneri")
    (finding,) = await run(ThirdPartyFormEmbed, ctx)
    assert finding.severity == Severity.MEDIUM
    (embed,) = finding.evidence["embeds"]
    assert embed == {
        "page": f"{ULKU}/oneri",
        "provider": "monday.com",
        "url_host": "forms.monday.com/forms",
        "type": "iframe",
        "page_has_consent": False,
    }
    assert "9f8e7d6c5b4a" not in json.dumps(finding.evidence)  # form kimliği yok


async def test_embed_google_forms_with_consent_is_low() -> None:
    (finding,) = await run(
        ThirdPartyFormEmbed, make_ctx(html=fixture_html("embed_gforms_consent.html"))
    )
    assert finding.severity == Severity.LOW
    (embed,) = finding.evidence["embeds"]
    assert embed["url_host"] == "docs.google.com/forms"
    assert "1FAIpQLSf" not in json.dumps(finding.evidence)


async def test_embed_plain_link_is_info() -> None:
    (finding,) = await run(ThirdPartyFormEmbed, make_ctx(html=fixture_html("embed_link_only.html")))
    assert finding.severity == Severity.INFO
    (embed,) = finding.evidence["embeds"]
    assert embed["type"] == "link"
    assert embed["url_host"] == "tally.so/r"  # host + yolun ilk parçası; "3xAmPl" kimliği yok
    assert "3xAmPl" not in json.dumps(finding.evidence)


async def test_embed_none() -> None:
    assert await run(ThirdPartyFormEmbed, make_ctx(html=fixture_html("form_tckn.html"))) == []


# --- web.links.insecure_http_links


async def test_http_link_in_nav_is_medium_ulkuocaklari_uzem() -> None:
    (finding,) = await run(InsecureHttpLinks, ulku_ctx())  # type: ignore[arg-type]
    assert finding.severity == Severity.MEDIUM
    (link,) = finding.evidence["links"]
    assert link["url"] == "http://ulkuocaklariuzem.com.tr"
    assert link["location"] == "nav"
    assert link["same_site"] is False
    assert len(link["pages"]) == 4  # 4 sayfada görüldü, tek kayıt


async def test_http_link_to_subdomain_medium_and_content_link_low() -> None:
    ctx = make_ctx(html=fixture_html("links_mixed.html"), final_url=f"{ULKU}/")
    (finding,) = await run(InsecureHttpLinks, ctx)
    assert finding.severity == Severity.MEDIUM
    links = {link["url"]: link for link in finding.evidence["links"]}
    assert set(links) == {"http://uzem.ulkuocaklari.org.tr/giris", "http://some-blog.net/makale"}
    assert links["http://uzem.ulkuocaklari.org.tr/giris"]["same_site"] is True
    assert links["http://some-blog.net/makale"]["location"] == "content"


async def test_http_content_link_only_is_low() -> None:
    html = '<html><body><main><a href="http://some-blog.net/makale">x</a></main></body></html>'
    (finding,) = await run(InsecureHttpLinks, make_ctx(html=html))
    assert finding.severity == Severity.LOW


async def test_http_links_localhost_and_https_ignored() -> None:
    html = '<html><body><a href="http://localhost:3000">a</a><a href="https://x.com">b</a><a href="http://example.com">c</a></body></html>'
    assert await run(InsecureHttpLinks, make_ctx(html=html)) == []


async def test_same_http_link_on_five_pages_single_entry() -> None:
    html = '<html><body><footer><a href="http://partner.net/">p</a></footer></body></html>'
    pages = [(f"https://s.com/p{i}", html) for i in range(6)]
    (finding,) = await run(
        InsecureHttpLinks, make_ctx(html=html, final_url="https://s.com/", pages=pages)
    )
    (link,) = finding.evidence["links"]
    assert link["location"] == "footer"
    assert len(link["pages"]) == 5  # en fazla 5 sayfa


# --- web.links.malformed_youtube


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        (
            "https://www.youtube.com/watch?v=Jmtb5ryNsQ4%3Fsi%3DuFNTR_suc1B593g7",
            "encoded_query_in_id",
        ),
        ("https://youtu.be/abc", "invalid_length"),
        ("https://www.youtube.com/watch?list=PL1234567890", "missing_v"),
        ("https://www.youtube.com/embed/", "invalid_length"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ?si=abc&start=10", None),
        ("https://youtu.be/dQw4w9WgXcQ?si=xyz", None),
        ("https://www.youtube.com/shorts/aBcDeFgHiJk", None),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", None),
        ("https://www.youtube.com/@kanal", None),
        ("https://www.youtube.com/embed/videoseries?list=PL1", None),
        ("https://vimeo.com/123", None),
    ],
)
def test_youtube_problem(url: str, reason: str | None) -> None:
    assert youtube_problem(url) == reason


async def test_youtube_fixture_three_broken_low() -> None:
    (finding,) = await run(MalformedYoutube, make_ctx(html=fixture_html("youtube_links.html")))
    assert finding.severity == Severity.LOW
    reasons = sorted(link["reason"] for link in finding.evidence["links"])
    assert reasons == ["encoded_query_in_id", "invalid_length", "missing_v"]


async def test_youtube_four_broken_is_medium() -> None:
    extra = '<a href="https://youtu.be/tooshort1">x</a>'
    html = fixture_html("youtube_links.html").replace("</main>", extra + "</main>")
    (finding,) = await run(MalformedYoutube, make_ctx(html=html))
    assert finding.severity == Severity.MEDIUM


async def test_youtube_valid_only_no_finding() -> None:
    html = '<html><body><a href="https://youtu.be/dQw4w9WgXcQ">a</a></body></html>'
    assert await run(MalformedYoutube, make_ctx(html=html)) == []


# --- web.a11y.img_missing_alt


async def test_img_half_missing_alt_is_low() -> None:
    (finding,) = await run(ImgMissingAlt, make_ctx(html=fixture_html("images_half_missing.html")))
    assert finding.severity == Severity.LOW
    # 10 ürün görseli + alt="" süs görseli = 11; 1x1 piksel ve küçük data: URI sayılmadı
    assert finding.evidence["total_images"] == 11
    assert finding.evidence["missing_alt"] == 5
    assert len(finding.evidence["samples"]) == 5


async def test_img_one_of_twenty_missing_no_finding() -> None:
    assert await run(ImgMissingAlt, make_ctx(html=fixture_html("images_one_missing.html"))) == []


async def test_img_empty_alt_is_not_a_problem() -> None:
    html = "<html><body>" + '<img src="/d.svg" alt="">' * 3 + "</body></html>"
    assert await run(ImgMissingAlt, make_ctx(html=html)) == []


async def test_img_same_logo_on_many_pages_counted_once() -> None:
    html = '<html><body><img src="/logo.png"><img src="/a.jpg" alt="a"></body></html>'
    pages = [(f"https://app.example.com/p{i}", html) for i in range(5)]
    (finding,) = await run(ImgMissingAlt, make_ctx(html=html, pages=pages))
    assert (finding.evidence["total_images"], finding.evidence["missing_alt"]) == (2, 1)
