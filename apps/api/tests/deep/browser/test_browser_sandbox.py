"""Gerçek Chromium ile tarayıcı üzerinden SSRF ve sandbox sınırları (docs/PHASE2.md §12.1-12.2).

`pytest -m browser`; Playwright + Chromium yoksa atlanır (CI'da worker-deep imajında çalışır).
Gerçek ağa çıkılmaz: sayfalar 127.0.0.1'deki yerel sunucudan, test allowlist'iyle gelir;
çözümleyici bilinmeyen her host için hata verir.
"""

import json

import pytest

from launchcheck.deep.browser.egress_proxy import Reason
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.security.ssrf import UrlGuard
from tests.deep.browser.servers import Canary, LocalHttpServer, Route

pytestmark = pytest.mark.browser
playwright = pytest.importorskip("playwright")

UA = "LaunchCheckBot/1.0 (+https://launchcheck.example/bot)"

ATTACK = """<!doctype html><html><head><title>yükleniyor</title></head><body>
<img src="http://10.0.0.1/pixel.png">
<iframe src="http://169.254.169.254/latest/meta-data/"></iframe>
<script>
const tryFetch = (url) => fetch(url, {mode: "no-cors"}).then(() => "ok", () => "blocked");
const trySocket = (url) => new Promise((resolve) => {
  try {
    const ws = new WebSocket(url);
    ws.onopen = () => resolve("open");
    ws.onerror = () => resolve("blocked");
    setTimeout(() => resolve("timeout"), 3000);
  } catch (e) { resolve("blocked"); }
});
Promise.all([
  tryFetch("http://169.254.169.254/latest/meta-data/"),
  tryFetch("http://127.0.0.1:CANARY/"),
  tryFetch("http://localhost:CANARY/"),
  tryFetch("http://rebind.test:SERVER/"),
  trySocket("ws://redis:6379/"),
  trySocket("ws://127.0.0.1:CANARY/"),
]).then((results) => { document.title = "done:" + JSON.stringify(results); });
</script></body></html>"""


def resolver_for() -> object:
    mapping = {
        "site.test": ["127.0.0.1"],
        "localhost": ["127.0.0.1"],
        "redis": ["172.18.0.5"],
        "rebind.test": ["10.0.0.1"],  # DNS rebinding: herkese açık görünen ad iç IP'ye çözülür
    }

    async def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError("NXDOMAIN")  # testten gerçek ağa çıkış olmasın
        return mapping[host]

    return resolve


def guard() -> UrlGuard:
    return UrlGuard(resolver=resolver_for())  # type: ignore[arg-type]


async def test_page_javascript_cannot_reach_internal_addresses() -> None:
    async with Canary() as canary, LocalHttpServer() as server:
        page_html = ATTACK.replace("CANARY", str(canary.port)).replace("SERVER", str(server.port))
        server.routes["/attack.html"] = Route(page_html.encode())
        allow = frozenset({("127.0.0.1", server.port)})
        async with BrowserSession.open(guard(), UA, allow=allow) as session:
            page = await session.new_page()
            await page.goto(f"http://site.test:{server.port}/attack.html")
            await page.wait_for_function("document.title.startsWith('done:')", timeout=15000)
            results = json.loads((await page.title()).removeprefix("done:"))
            blocked = {(b.host, b.port, b.reason) for b in session.proxy.stats.blocked}

    assert canary.hits == 0  # hiçbir istek proxy'yi atlayıp loopback'e ulaşmadı
    assert "open" not in results
    hosts = {host for host, _, _ in blocked}
    assert {
        "169.254.169.254",
        "10.0.0.1",
        "127.0.0.1",
        "localhost",
        "rebind.test",
        "redis",
    } <= hosts
    assert ("redis", 6379, Reason.PORT) in blocked
    # rebind.test (iç IP'ye çözülen ad) da engellendi: yukarıdaki host kümesinde


async def test_navigation_only_works_through_proxy() -> None:
    """`site.test` gerçek DNS'te yok ve Chromium kendi çözümleme yapamıyor
    (`--host-resolver-rules`): sayfa açılıyorsa trafik proxy'den geçmiştir."""
    async with LocalHttpServer({"/": Route(b"<title>merhaba</title>")}) as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with BrowserSession.open(guard(), UA, allow=allow) as session:
            page = await session.new_page()
            await page.goto(f"http://site.test:{server.port}/")
            assert await page.title() == "merhaba"
            assert session.proxy.stats.allowed >= 1
    assert server.requests[0].split(b"\r\n")[0] == b"GET / HTTP/1.1"
    assert UA.encode() in server.requests[0]


async def test_dialogs_popups_service_workers_permissions() -> None:
    html = """<!doctype html><title>start</title><script>
    alert("a"); confirm("b"); prompt("c");
    window.open("/other.html");
    document.title = "after-dialogs";
    </script>"""
    routes = {
        "/": Route(html.encode()),
        "/other.html": Route(b"<title>popup</title>"),
        "/sw.js": Route(b"self.addEventListener('fetch', () => {});", "text/javascript"),
    }
    async with LocalHttpServer(routes) as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with BrowserSession.open(guard(), UA, allow=allow) as session:
            page = await session.new_page()
            await page.goto(f"http://site.test:{server.port}/")
            assert await page.title() == "after-dialogs"  # dialoglar sayfayı kilitlemedi
            await page.wait_for_timeout(1000)
            assert session.context.pages == [page]  # pop-up kapatıldı
            sw = await page.evaluate(
                "navigator.serviceWorker ? navigator.serviceWorker.register('/sw.js')"
                ".then(() => 'registered', () => 'blocked') : 'unavailable'"
            )
            assert sw != "registered"
            geo = await page.evaluate(
                """() => new Promise((resolve) => navigator.geolocation.getCurrentPosition(
                    () => resolve("granted"), (e) => resolve("denied:" + e.code)))"""
            )
            assert geo.startswith("denied")


async def test_downloads_are_not_saved() -> None:
    routes = {
        "/file.bin": Route(
            b"MZ" + b"\x00" * 100,
            "application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=setup.exe"},
        ),
        "/": Route(b"<title>ok</title>"),
    }
    async with LocalHttpServer(routes) as server:
        allow = frozenset({("127.0.0.1", server.port)})
        async with BrowserSession.open(guard(), UA, allow=allow) as session:
            page = await session.new_page()
            with pytest.raises(playwright.async_api.Error):
                await page.goto(f"http://site.test:{server.port}/file.bin")
            # oturum kullanılabilir kalır
            await page.goto(f"http://site.test:{server.port}/")
            assert await page.title() == "ok"
