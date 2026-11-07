"""web.infra.origin_exposed, web.render.empty_server_html ve yeni web.meta.og_missing."""

import pytest

from launchcheck.checks.base import Severity
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.checks.web.infra import OriginExposed, matches_platform
from launchcheck.checks.web.meta import OgMissing
from launchcheck.checks.web.render import EmptyServerHtml, is_empty_shell, measure
from launchcheck.collectors.web_collector import PageSnapshot
from launchcheck.config import get_settings
from launchcheck.i18n import finding_text, fragment_fn
from tests.checks.helpers import SITE, fixture_html, make_ctx, run

RAILWAY = "metalgymturkey-production.up.railway.app"
CF_HEADERS = {"cf-ray": "8f1a2b3c4d5e-IST", "server": "cloudflare"}

# --- web.infra.origin_exposed


async def test_origin_behind_cloudflare_is_high_and_one_finding_per_host() -> None:
    ctx = make_ctx(
        html=fixture_html("origin_cloudflare.html"),
        headers=CF_HEADERS,
        final_url="https://www.metalgymturkey.com/tr",
        pages=[("https://www.metalgymturkey.com/tr/shop", fixture_html("origin_cloudflare.html"))],
    )
    (finding,) = await run(OriginExposed, ctx)
    assert finding.severity == Severity.HIGH
    ev = finding.evidence
    assert ev["host"] == RAILWAY
    assert ev["behind_cdn"] is True
    # img src, srcset (2), <style> url(), inline style url(), og:image → 6 tekil kaynak
    assert ev["resource_count"] == 6
    assert len(ev["sample_urls"]) == 5
    assert ev["pages"] == [
        "https://www.metalgymturkey.com/tr",
        "https://www.metalgymturkey.com/tr/shop",
    ]


async def test_origin_srcset_and_inline_style_are_caught() -> None:
    ctx = make_ctx(
        html='<html><body><img src="/a.png" srcset="https://s.koyeb.app/a-480.jpg 480w, /b.jpg 2x">'
        '<div style="background:url(&quot;https://x.onrender.com/bg.png&quot;)"></div>'
        "<style>.a{background:url(https://y.fly.dev/a.png)}</style></body></html>"
    )
    hosts = {f.evidence["host"] for f in await run(OriginExposed, ctx)}
    assert hosts == {"s.koyeb.app", "x.onrender.com", "y.fly.dev"}


async def test_origin_without_cdn_is_medium() -> None:
    ctx = make_ctx(html=fixture_html("origin_render_script.html"), headers={"server": "nginx"})
    (finding,) = await run(OriginExposed, ctx)
    assert finding.severity == Severity.MEDIUM
    assert finding.evidence["host"] == "my-api.onrender.com"
    assert finding.evidence["behind_cdn"] is False


async def test_origin_in_js_bundle() -> None:
    ctx = make_ctx(
        scripts={f"{SITE}/assets/a.js": 'fetch("https://api-prod.herokuapp.com/v1/products")'}
    )
    (finding,) = await run(OriginExposed, ctx)
    assert finding.evidence["host"] == "api-prod.herokuapp.com"


async def test_origin_site_already_on_platform_domain() -> None:
    ctx = make_ctx(
        html='<html><body><img src="https://other.vercel.app/a.png"></body></html>',
        final_url="https://my-app.vercel.app/",
    )
    assert await run(OriginExposed, ctx) == []


async def test_origin_expected_storage_and_cdns_are_fine() -> None:
    ctx = make_ctx(html=fixture_html("origin_safe.html"), headers=CF_HEADERS)
    assert await run(OriginExposed, ctx) == []


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("app.up.railway.app", True),
        ("x.onrender.com", True),
        ("vercel.app", False),  # çıplak alan adı (ör. "Hosted on Vercel" linki)
        ("abc.supabase.co", False),
        ("res.cloudinary.com", False),
    ],
)
def test_matches_platform(host: str, expected: bool) -> None:
    assert matches_platform(host, get_settings().web_platform_default_domains) is expected


# --- web.render.empty_server_html


async def test_render_empty_spa_shell_is_high() -> None:
    ctx = make_ctx(html=fixture_html("render_shell.html"))
    (finding,) = await run(EmptyServerHtml, ctx)
    assert finding.severity == Severity.HIGH
    (page,) = finding.evidence["pages"]
    assert page == {"url": f"{SITE}/", "main_content_words": 0, "empty_shell": True}


async def test_render_layout_without_content_only_that_page_reported() -> None:
    full = fixture_html("render_full.html")
    ctx = make_ctx(
        html=full,
        final_url="https://www.metalgymturkey.com/tr",
        pages=[
            ("https://www.metalgymturkey.com/tr/shop", fixture_html("render_layout_only.html")),
            # Sayfaya özgü başlıklı kategori sayfaları (aynı başlık + aynı içerik ana sayfa
            # kopyası sayılır, bkz. route_serves_root_shell)
            (
                "https://www.metalgymturkey.com/tr/kategori/bench",
                full.replace("<title>", "<title>Bench "),
            ),
            (
                "https://www.metalgymturkey.com/tr/kategori/rack",
                full.replace("<title>", "<title>Rack "),
            ),
        ],
    )
    (finding,) = await run(EmptyServerHtml, ctx)
    assert finding.severity == Severity.MEDIUM
    (page,) = finding.evidence["pages"]
    assert page["url"] == "https://www.metalgymturkey.com/tr/shop"
    assert page["empty_shell"] is False
    assert (
        page["main_content_words"] == 1
    )  # sadece <h1>Mağaza</h1>; header/nav/aside/footer sayılmaz
    assert finding.evidence["reference_median_words"] > 40


async def test_render_full_ssr_pages_no_finding() -> None:
    full = fixture_html("render_full.html")
    ctx = make_ctx(html=full, pages=[(f"{SITE}/a", full), (f"{SITE}/b", full)])
    assert await run(EmptyServerHtml, ctx) == []


async def test_render_login_and_noindex_pages_skipped() -> None:
    ctx = make_ctx(
        html=fixture_html("render_full.html"),
        pages=[
            (f"{SITE}/tr/login", fixture_html("render_login.html")),
            (f"{SITE}/tesekkurler", fixture_html("render_noindex.html")),
        ],
    )
    assert await run(EmptyServerHtml, ctx) == []


def test_empty_shell_detection_variants() -> None:
    from selectolax.lexbor import LexborHTMLParser

    shell = '<html><body><div id="__next"></div><script src="/a.js"></script></body></html>'
    assert is_empty_shell(LexborHTMLParser(shell))
    ssr = '<html><body><div id="__next"><main><p>Real content</p></main></div></body></html>'
    assert not is_empty_shell(LexborHTMLParser(ssr))
    assert not measure(PageSnapshot("u", 200, {}, ssr)).empty_shell


# --- web.meta.og_missing


async def test_og_metalgym_home_only_image_and_twitter_card() -> None:
    ctx = make_ctx(
        html=fixture_html("og_metalgym_home.html"),
        final_url="https://www.metalgymturkey.com/tr",
        pages=[
            (
                "https://www.metalgymturkey.com/tr/kategori/bench",
                fixture_html("og_full_category.html"),
            )
        ],
    )
    (finding,) = await run(OgMissing, ctx)
    assert finding.severity == Severity.MEDIUM  # ana sayfada og:image eksik, diğerleri var
    assert finding.evidence["pages"] == [
        {
            "url": "https://www.metalgymturkey.com/tr",
            "missing": ["og:image"],
            "issues": ["twitter_card_without_image"],
        }
    ]


async def test_og_none_reports_all_three_with_default_severity() -> None:
    (finding,) = await run(OgMissing, make_ctx(html=fixture_html("og_none.html")))
    assert finding.severity == Severity.LOW
    assert finding.evidence["pages"][0]["missing"] == ["og:title", "og:description", "og:image"]


async def test_og_relative_image_and_name_attribute() -> None:
    (finding,) = await run(OgMissing, make_ctx(html=fixture_html("og_relative_image.html")))
    (page,) = finding.evidence["pages"]
    assert page["missing"] == []  # name="og:..." de okunuyor
    assert page["issues"] == ["og_image_not_absolute", "og_image_aspect_ratio"]
    assert finding.severity == Severity.LOW


async def test_og_home_missing_inner_page_complete_only_home_listed() -> None:
    ctx = make_ctx(
        html=fixture_html("og_none.html"),
        pages=[(f"{SITE}/kategori/bench", fixture_html("og_full_category.html"))],
    )
    (finding,) = await run(OgMissing, ctx)
    assert [p["url"] for p in finding.evidence["pages"]] == [f"{SITE}/"]


async def test_og_inner_page_missing_image_is_low() -> None:
    ctx = make_ctx(
        html=fixture_html("og_full_category.html"),
        pages=[(f"{SITE}/a", fixture_html("og_metalgym_home.html"))],
    )
    (finding,) = await run(OgMissing, ctx)
    assert finding.severity == Severity.LOW


async def test_og_complete_no_finding() -> None:
    assert await run(OgMissing, make_ctx(html=fixture_html("og_full_category.html"))) == []


@pytest.mark.parametrize(
    ("lang", "expected_title"),
    [("tr", "Ana sayfanda og:image eksik"), ("en", "Your home page is missing og:image")],
)
async def test_og_dynamic_texts(lang: str, expected_title: str) -> None:
    ctx = make_ctx(
        html=fixture_html("og_metalgym_home.html"), final_url="https://www.metalgymturkey.com/tr"
    )
    (finding,) = await run(OgMissing, ctx)
    check = load_builtin_checks().get("web.meta.og_missing")
    assert check is not None
    params = check.text_params(finding.evidence, fragment_fn(check.id, lang))
    text = finding_text(check.id, lang, params)
    assert text.title == expected_title
    assert "og:image" in text.fix_prompt
    assert "og:title" not in text.fix_prompt.split("\n")[0]  # prompt sadece eksikleri istiyor
    for part in (text.title, text.description, *text.fix_steps, text.fix_prompt):
        assert "{" not in part, part
