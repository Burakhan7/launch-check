import uuid

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
