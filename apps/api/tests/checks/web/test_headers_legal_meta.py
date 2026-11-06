from launchcheck.checks.web.headers import (
    CspMissing,
    FrameOptionsMissing,
    HstsMissing,
    NosniffMissing,
    ServerVersionLeak,
)
from launchcheck.checks.web.legal import PrivacyMissing, TermsMissing
from launchcheck.checks.web.meta import (
    FaviconMissing,
    OgMissing,
    SitemapMissing,
    TitleDescriptionMissing,
)
from tests.checks.helpers import SITE, make_ctx, resp, run

SECURE_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Server": "cloudflare",
}


async def test_security_headers_all_missing() -> None:
    ctx = make_ctx(headers={})
    for check in (HstsMissing, CspMissing, FrameOptionsMissing, NosniffMissing):
        assert len(await run(check, ctx)) == 1, check.id


async def test_security_headers_all_present() -> None:
    ctx = make_ctx(headers=SECURE_HEADERS)
    for check in (HstsMissing, CspMissing, FrameOptionsMissing, NosniffMissing, ServerVersionLeak):
        assert await run(check, ctx) == [], check.id


async def test_hsts_not_reported_on_http_site() -> None:
    assert await run(HstsMissing, make_ctx(final_url="http://app.example.com/")) == []


async def test_csp_via_meta_tag_counts() -> None:
    html = '<html><head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'"></head></html>'
    assert await run(CspMissing, make_ctx(html=html)) == []


async def test_frame_options_header_alone_is_enough() -> None:
    assert await run(FrameOptionsMissing, make_ctx(headers={"X-Frame-Options": "DENY"})) == []


async def test_server_version_leak_positive() -> None:
    ctx = make_ctx(headers={"Server": "nginx/1.18.0", "X-Powered-By": "PHP/8.1.2"})
    (finding,) = await run(ServerVersionLeak, ctx)
    assert finding.evidence == {"headers": {"server": "nginx/1.18.0", "x-powered-by": "PHP/8.1.2"}}


async def test_server_version_leak_negative_no_version() -> None:
    ctx = make_ctx(headers={"Server": "nginx", "X-Powered-By": "Next.js"})
    assert await run(ServerVersionLeak, ctx) == []


async def test_legal_missing_positive() -> None:
    ctx = make_ctx(html='<html><body><a href="/about">Hakkımızda</a></body></html>')
    assert len(await run(PrivacyMissing, ctx)) == 1
    assert len(await run(TermsMissing, ctx)) == 1


async def test_legal_found_in_html_links_tr_and_en() -> None:
    ctx = make_ctx(
        html='<html><body><a href="/kvkk">KVKK Aydınlatma Metni</a>'
        '<a href="/legal/terms-of-service">Terms</a></body></html>'
    )
    assert await run(PrivacyMissing, ctx) == []
    assert await run(TermsMissing, ctx) == []


async def test_legal_found_by_link_text() -> None:
    ctx = make_ctx(
        html='<html><body><a href="/p1">Gizlilik Politikası</a><a href="/p2">Kullanım Şartları</a></body></html>'
    )
    assert await run(PrivacyMissing, ctx) == []
    assert await run(TermsMissing, ctx) == []


async def test_legal_found_in_spa_js_routes() -> None:
    """SPA: ham HTML'de link yok, rotalar JS bundle'ında."""
    ctx = make_ctx(
        html='<html><body><div id="root"></div></body></html>',
        scripts={
            f"{SITE}/assets/index.js": '{path:"/privacy-policy",element:P},'
            '{path:"/terms",element:T}'
        },
    )
    assert await run(PrivacyMissing, ctx) == []
    assert await run(TermsMissing, ctx) == []


FULL_HEAD = """<html><head><title>My App</title>
<meta name="description" content="Best app">
<meta property="og:title" content="My App"><meta property="og:description" content="d">
<meta property="og:image" content="https://app.example.com/og.png">
<link rel="icon" href="/favicon.svg"></head></html>"""


async def test_meta_all_present() -> None:
    ctx = make_ctx(
        html=FULL_HEAD,
        routes={f"{SITE}/sitemap.xml": resp("s", body='<?xml version="1.0"?><urlset></urlset>')},
    )
    for check in (TitleDescriptionMissing, OgMissing, FaviconMissing, SitemapMissing):
        assert await run(check, ctx) == [], check.id


async def test_meta_all_missing() -> None:
    ctx = make_ctx(html="<html><head><title> </title></head></html>")
    (title,) = await run(TitleDescriptionMissing, ctx)
    assert title.evidence == {"missing": ["title", "description"]}
    (og,) = await run(OgMissing, ctx)
    assert og.evidence["pages"][0]["missing"] == ["og:title", "og:description", "og:image"]
    assert len(await run(FaviconMissing, ctx)) == 1
    assert len(await run(SitemapMissing, ctx)) == 1


async def test_favicon_ico_file_counts() -> None:
    ctx = make_ctx(
        routes={
            f"{SITE}/favicon.ico": resp("f", body=b"\x00\x00\x01\x00", content_type="image/x-icon")
        }
    )
    assert await run(FaviconMissing, ctx) == []


async def test_favicon_and_sitemap_spa_catch_all_is_missing() -> None:
    ctx = make_ctx(spa=True)
    assert len(await run(FaviconMissing, ctx)) == 1
    assert len(await run(SitemapMissing, ctx)) == 1


async def test_sitemap_declared_in_robots() -> None:
    ctx = make_ctx(
        routes={
            f"{SITE}/robots.txt": resp(
                "r", body="User-agent: *\nSitemap: https://cdn.x.com/sm.xml\n"
            )
        }
    )
    assert await run(SitemapMissing, ctx) == []
