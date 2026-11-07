import uuid
from pathlib import Path

import httpx
import pytest
import respx

from launchcheck.collectors.base import CollectorError
from launchcheck.collectors.web_collector import WebCollector, WebContext
from launchcheck.config import Settings
from launchcheck.security.ssrf import UrlGuard
from tests.conftest import FakeResolver

SITE = "https://app.example.com"
INDEX = """<html><head>
<script type="module" src="/assets/index-a1.js"></script>
<script src="https://cdn.other.com/x.js"></script>
<script>const url = "https://abcdefghijklmnopqrst.supabase.co";</script>
</head></html>"""
MAIN_JS = 'import("./Lazy-b2.js"); const x = ["assets/Missing-c3.js"]; // __vitePreload'
LAZY_JS = 'const deep = "./Deep-d4.js";'
DEEP_JS = "console.log('deep')"
SPA_FALLBACK = "<!doctype html><html><body>app</body></html>"


def collector(**overrides: object) -> WebCollector:
    return WebCollector(Settings(**overrides), UrlGuard(FakeResolver()))  # type: ignore[arg-type]


def mock_site() -> respx.MockRouter:
    router = respx.mock(assert_all_called=False)
    router.get("http://app.example.com/").mock(
        return_value=httpx.Response(301, headers={"location": f"{SITE}/"})
    )
    router.get(f"{SITE}/").mock(
        return_value=httpx.Response(
            200, text=INDEX, headers={"content-type": "text/html; charset=utf-8", "server": "x"}
        )
    )
    js = {"content-type": "application/javascript"}
    router.get(f"{SITE}/assets/index-a1.js").mock(
        return_value=httpx.Response(200, text=MAIN_JS, headers=js)
    )
    router.get(f"{SITE}/assets/Lazy-b2.js").mock(
        return_value=httpx.Response(200, text=LAZY_JS, headers=js)
    )
    router.get(f"{SITE}/assets/Deep-d4.js").mock(
        return_value=httpx.Response(200, text=DEEP_JS, headers=js)
    )
    # SPA catch-all: olmayan dosya için 200 + index.html
    router.get(f"{SITE}/assets/Missing-c3.js").mock(
        return_value=httpx.Response(200, text=SPA_FALLBACK, headers={"content-type": "text/html"})
    )
    router.get("https://cdn.other.com/x.js").mock(return_value=httpx.Response(200, text="cdn"))
    return router


async def test_collects_page_scripts_and_chunks() -> None:
    with mock_site() as router:
        ctx = await collector().collect(uuid.uuid4(), {"url": "http://app.example.com/"})
        assert isinstance(ctx, WebContext)
        try:
            assert ctx.target_label == "http://app.example.com"
            assert ctx.final_url == f"{SITE}/"
            assert ctx.status == 200
            assert ctx.headers["server"] == "x"
            urls = [s.url for s in ctx.scripts if not s.inline]
            assert urls == [
                f"{SITE}/assets/index-a1.js",
                f"{SITE}/assets/Lazy-b2.js",
                f"{SITE}/assets/Deep-d4.js",
            ]
            assert len([s for s in ctx.scripts if s.inline]) == 1
            assert not router.routes[-1].called  # farklı origin script'i çekilmedi
            assert ctx.meta["platform"] == ["supabase", "vite"]
            assert ctx.meta["js_files_scanned"] == 3
            assert ctx.meta["supabase_urls"] == ["https://abcdefghijklmnopqrst.supabase.co"]
        finally:
            await ctx.aclose()


async def test_js_file_limit() -> None:
    with mock_site():
        ctx = await collector(max_js_files=2).collect(uuid.uuid4(), {"url": f"{SITE}/"})
        assert isinstance(ctx, WebContext)
        assert len([s for s in ctx.scripts if not s.inline]) == 2
        await ctx.aclose()


async def test_probe_is_cached_and_does_not_follow_redirects() -> None:
    with mock_site() as router:
        env = router.get(f"{SITE}/.env").mock(
            return_value=httpx.Response(302, headers={"location": "/login"})
        )
        ctx = await collector().collect(uuid.uuid4(), {"url": f"{SITE}/"})
        assert isinstance(ctx, WebContext)
        first = await ctx.probe("/.env")
        second = await ctx.probe("/.env")
        assert first is second and first is not None
        assert first.status == 302
        assert env.call_count == 1
        await ctx.aclose()


async def test_unreachable_site_raises_collector_error() -> None:
    with respx.mock:
        respx.get(f"{SITE}/").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(CollectorError) as exc:
            await collector().collect(uuid.uuid4(), {"url": f"{SITE}/"})
        assert exc.value.code == "site_unreachable"


async def test_private_target_raises_collector_error() -> None:
    c = WebCollector(Settings(), UrlGuard(FakeResolver({"app.example.com": ["10.0.0.2"]})))
    with pytest.raises(CollectorError) as exc:
        await c.collect(uuid.uuid4(), {"url": f"{SITE}/"})
    assert exc.value.code == "url_not_public"


CF_CHALLENGE = (
    "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body></body></html>"
)


@pytest.mark.parametrize(
    ("status", "body", "headers", "code"),
    [
        (403, CF_CHALLENGE, {}, "site_blocked_bot"),
        (200, "<html>ok</html>", {"cf-mitigated": "challenge"}, "site_blocked_bot"),
        (403, "<html><title>Vercel Security Checkpoint</title></html>", {}, "site_blocked_bot"),
        (403, "<html></html>", {"x-vercel-mitigated": "challenge"}, "site_blocked_bot"),
        (404, "<html>Not found</html>", {}, "site_http_error"),
        (502, "Bad gateway", {}, "site_http_error"),
    ],
)
async def test_unscannable_home_page(
    status: int, body: str, headers: dict[str, str], code: str
) -> None:
    with respx.mock:
        respx.get(f"{SITE}/").mock(
            return_value=httpx.Response(
                status, text=body, headers={"content-type": "text/html", **headers}
            )
        )
        with pytest.raises(CollectorError) as exc:
            await collector().collect(uuid.uuid4(), {"url": f"{SITE}/"})
        assert exc.value.code == code


# --- İç sayfa taraması

HOME_LINKS = (Path(__file__).parent.parent / "fixtures" / "web" / "collector_home.html").read_text(
    encoding="utf-8"
)


def test_internal_links_priority_dedupe_and_filters() -> None:
    from launchcheck.collectors.web_assets import extract_internal_links

    links = extract_internal_links(HOME_LINKS, f"{SITE}/", exclude=(f"{SITE}/",))
    # nav/header/footer önce; "/about/" ve "/about" tek; ana sayfa ("/") hariç
    assert links[:3] == [f"{SITE}/shop", f"{SITE}/about/", f"{SITE}/privacy"]
    assert f"{SITE}/blog/post-2" in links  # query ve fragment atıldı
    joined = " ".join(links)
    for bad in ("brochure.pdf", "photo.JPG", "mailto", "tel:", "javascript", "other-site.com", "#"):
        assert bad not in joined
    assert len(links) == len(set(links))


def mock_pages(router: respx.MockRouter, html_home: str) -> None:
    html = {"content-type": "text/html; charset=utf-8"}
    router.get(f"{SITE}/").mock(return_value=httpx.Response(200, text=html_home, headers=html))
    for path in (
        "shop",
        "about/",
        "privacy",
        "blog/post-1",
        "blog/post-2",
        *[f"p{i}" for i in range(3, 11)],
    ):
        router.get(f"{SITE}/{path}").mock(
            return_value=httpx.Response(
                200, text=f"<html><body><h1>{path}</h1></body></html>", headers=html
            )
        )


async def test_collector_fetches_at_most_limit_internal_pages() -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_pages(router, HOME_LINKS)
        ctx = await collector(web_max_internal_pages=10).collect(uuid.uuid4(), {"url": f"{SITE}/"})
        assert isinstance(ctx, WebContext)
        try:
            urls = [p.url for p in ctx.pages]
            assert urls[0] == f"{SITE}/"  # pages[0] ana sayfa
            assert len(urls) == 11  # ana sayfa + 10 iç sayfa
            assert urls[1:4] == [f"{SITE}/shop", f"{SITE}/about/", f"{SITE}/privacy"]
            assert ctx.meta["pages_scanned"] == 11
        finally:
            await ctx.aclose()


async def test_collector_skips_non_html_and_error_pages() -> None:
    home = '<html><body><nav><a href="/ok">a</a><a href="/missing">b</a><a href="/data">c</a></nav></body></html>'
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}/").mock(
            return_value=httpx.Response(200, text=home, headers={"content-type": "text/html"})
        )
        router.get(f"{SITE}/ok").mock(
            return_value=httpx.Response(
                200, text="<html>ok</html>", headers={"content-type": "text/html"}
            )
        )
        router.get(f"{SITE}/missing").mock(
            return_value=httpx.Response(404, text="nope", headers={"content-type": "text/html"})
        )
        router.get(f"{SITE}/data").mock(
            return_value=httpx.Response(
                200, json={"a": 1}, headers={"content-type": "application/json"}
            )
        )
        ctx = await collector().collect(uuid.uuid4(), {"url": f"{SITE}/"})
        assert isinstance(ctx, WebContext)
        assert [p.url for p in ctx.pages] == [f"{SITE}/", f"{SITE}/ok"]
        await ctx.aclose()


async def test_collector_page_timeout_keeps_what_it_has() -> None:
    import asyncio

    home = '<html><body><nav><a href="/fast">a</a><a href="/slow">b</a></nav></body></html>'

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text="<html>late</html>", headers={"content-type": "text/html"})

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{SITE}/").mock(
            return_value=httpx.Response(200, text=home, headers={"content-type": "text/html"})
        )
        router.get(f"{SITE}/fast").mock(
            return_value=httpx.Response(
                200, text="<html>fast</html>", headers={"content-type": "text/html"}
            )
        )
        router.get(f"{SITE}/slow").mock(side_effect=slow)
        ctx = await collector(web_internal_pages_timeout_s=0.5).collect(
            uuid.uuid4(), {"url": f"{SITE}/"}
        )
        assert isinstance(ctx, WebContext)
        assert [p.url for p in ctx.pages] == [f"{SITE}/", f"{SITE}/fast"]  # tarama başarısız olmadı
        await ctx.aclose()


async def test_cookieless_samples_and_content_flags() -> None:
    home = (
        '<html><body><a href="/canli">Canlı</a><p>Rastgele biriyle görüntülü sohbet</p>'
        '<form><textarea name="t"></textarea><button>Paylaş</button></form></body></html>'
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"{SITE}/").mock(
            return_value=httpx.Response(
                200,
                text=home,
                headers={"content-type": "text/html", "set-cookie": "sid=abc; Path=/"},
            )
        )
        inner = router.get(f"{SITE}/canli").mock(
            return_value=httpx.Response(
                200, text="<html></html>", headers={"content-type": "text/html"}
            )
        )
        ctx = await collector().collect(uuid.uuid4(), {"url": f"{SITE}/"})
        assert isinstance(ctx, WebContext)
        try:
            assert route.call_count == 3  # ana sayfa + 2 çerezsiz örnek
            assert len(ctx.cookieless_samples) == 3
            # Ana sayfa çerez koydu; örnek istekleri yine de çerezsiz gitmeli
            assert all("cookie" not in c.request.headers for c in route.calls)
            assert inner.calls[0].request.headers.get("cookie") == "sid=abc"  # normal istekler
            assert ctx.meta["is_ugc"] is True
            assert ctx.meta["has_live_media"] is True
            assert ctx.meta["is_emotional_support"] is False
        finally:
            await ctx.aclose()
