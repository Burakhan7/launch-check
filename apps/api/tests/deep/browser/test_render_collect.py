"""Render toplayıcısı gerçek Chromium ile, üç farklı yapıda yerel sitede (`pytest -m browser`).

1. spa_shop: Vite benzeri SPA, bilerek hatalı (JS hatası, başarısız API, undefined, kırık görsel,
   taşma, çakışma, küçük hedefler, CTA ekran altında, JWT'li localStorage, HttpOnly'siz çerez,
   istemci tarafı admin rotası, doğrudan açınca 404 veren iç sayfa, çevrimdışı boş ekran)
2. plain_clean: düz HTML, sorunsuz → hiçbir render bulgusu olmamalı (yanlış pozitif kontrolü)
3. wp_like: WordPress benzeri sunucu HTML'i (tablo taşması, küçük footer ikonları, nginx 404)
"""

import json
from pathlib import Path

import pytest

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.config import Settings
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.deep.browser.render import RenderCollector, RenderInput
from launchcheck.deep.context import DeepContext
from launchcheck.deep.render_data import RenderData
from launchcheck.security.ssrf import UrlGuard
from tests.deep.browser.servers import LocalHttpServer, Route

pytestmark = pytest.mark.browser
pytest.importorskip("playwright")

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "deep"
UA = "LaunchCheckBot/1.0 (+https://launchcheck.example/bot)"
SETTINGS = Settings(
    deep_render_quiet_s=0.5,
    deep_render_max_wait_s=3,
    deep_max_pages=6,
    deep_render_concurrency=2,
    deep_measure_widths=[320, 375, 1440],
)
SOURCEMAP = (
    b'{"version":3,"sources":["webpack://acme/./src/App.tsx","./src/api/admin.ts"],"mappings":"AA"}'
)
CLEAN_PAGE = (FIXTURES / "plain_clean" / "index.html").read_bytes()


def html(name: str) -> Route:
    return Route((FIXTURES / name).read_bytes())


async def resolve(host: str, port: int) -> list[str]:
    if host != "site.test":
        raise OSError("NXDOMAIN")
    return ["127.0.0.1"]


async def collect(server: LocalHttpServer, **kw: object) -> tuple[RenderData, list[str]]:
    base = f"http://site.test:{server.port}"
    shots: list[str] = []

    async def fetch(url: str) -> bytes | None:
        return SOURCEMAP if url.endswith(".map") else None

    def save(kind: str, url: str, data: bytes, width: int, height: int) -> None:
        assert data[:4] == b"RIFF" and width <= 1440
        shots.append(kind)

    data = RenderInput(
        home_url=base + "/",
        seed_urls=[],
        baseline_path="/a1b2c3-missing.txt",
        bundle_texts=[str(kw.get("bundle", ""))],
        sourcemap_urls=[base + "/assets/app.js.map"] if kw.get("sourcemap") else [],
        fetch=fetch,
        save_screenshot=save,  # type: ignore[arg-type]
        deadline=kw.get("deadline"),  # type: ignore[arg-type]
    )
    allow = frozenset({("127.0.0.1", server.port)})
    async with BrowserSession.open(UrlGuard(resolver=resolve), UA, allow=allow) as session:
        result = await RenderCollector(SETTINGS).collect(session, data)
    return result, shots


async def render_check_ids(data: RenderData) -> dict[str, dict]:  # type: ignore[type-arg]
    checks = [c for c in load_builtin_checks().all() if c.module == "render"]
    ctx = DeepContext(target_type=TargetType.WEB, target_label="http://site.test", render=data)
    result = await run_checks(ctx, checks)
    assert result.failed == []
    return {
        f.check_id: {"severity": str(f.severity), "evidence": f.evidence} for f in result.findings
    }


async def test_spa_shop_finds_runtime_problems() -> None:
    app_js = (FIXTURES / "spa_shop" / "app.js").read_text("utf-8")
    routes = {
        "/": html("spa_shop/index.html"),
        "/assets/app.js": Route(app_js.encode(), "text/javascript"),
        "/hero-big.svg": Route(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', "image/svg+xml"
        ),
        "/api/items": Route(b'{"error":"db down"}', "application/json", 500),
        "/api/about": Route(b"Biz bir ekibiz.", "text/plain"),
        "/admin": html("spa_shop/admin.html"),
    }
    async with LocalHttpServer(routes) as server:
        data, shots = await collect(server, bundle=app_js, sourcemap=True)
        port = server.port
    found = await render_check_ids(data)

    expected = {
        "web.render.js_runtime_errors",
        "web.render.failed_requests",
        "web.render.visible_junk_text",
        "web.render.broken_images",
        "web.render.spa_deep_link_404",
        "web.mobile.horizontal_overflow",
        "web.mobile.overlapping_elements",
        "web.mobile.cta_below_fold",
        "web.mobile.tap_targets_small",
        "web.ux.custom_404_missing",
        "web.ux.offline_breaks_page",
        "web.security.token_in_web_storage",
        "web.security.insecure_cookie_flags",
        "web.security.client_only_admin_route",
        "web.security.sourcemap_tree_exposed",
    }
    assert expected <= set(found), sorted(expected - set(found))
    assert found["web.render.failed_requests"]["severity"] == "high"  # API isteği
    assert found["web.render.js_runtime_errors"]["severity"] == "high"  # ana sayfada
    assert found["web.security.token_in_web_storage"]["severity"] == "high"  # 60 günlük JWT
    assert found["web.security.client_only_admin_route"]["severity"] == "high"
    assert found["web.render.spa_deep_link_404"]["evidence"]["pages"][0]["url"] == "/pricing"
    assert found["web.security.sourcemap_tree_exposed"]["evidence"]["paths"] == [
        "src/App.tsx",
        "src/api/admin.ts",
    ]
    # Değerler hiçbir yere taşınmaz: JWT, çerez değeri yok
    dumped = json.dumps(found)
    assert "abc123" not in dumped and "eyJ" not in dumped
    assert {"screenshot_mobile", "screenshot_desktop", "screenshot_nojs"} <= set(shots)
    home = data.pages[0]
    assert home.mobile is not None and home.mobile.first_screen  # ilk ekran özeti
    assert 320 in home.widths and str(port)


async def test_plain_clean_site_has_no_render_findings() -> None:
    page = CLEAN_PAGE
    routes = {"/": Route(page), "/about": Route(page), "/contact": Route(page)}
    not_found = Route(
        b'<!doctype html><title>Bulunamadi</title><h1>Sayfa bulunamadi</h1><p>Aradigin sayfa tasinmis olabilir. <a href="/">Ana sayfaya don</a></p>',
        status=404,
    )
    async with LocalHttpServer(routes, fallback=not_found) as server:
        data, _ = await collect(server)
    found = await render_check_ids(data)
    assert found == {}, found
    assert data.pages[0].mobile is not None
    assert data.pages[0].mobile.primary_cta is not None  # "Randevu al" ilk ekranda
    assert len(data.pages) == 3
    assert (data.pages_planned, data.skipped_for_time, data.probes_skipped) == (3, 0, False)


async def test_time_budget_keeps_home_and_counts_skipped_pages() -> None:
    """Süre biterse ana sayfa yine taranır; açılmayan sayfalar ve atlanan yoklamalar sayılır
    (rapor ve P11 bütçe ayarı kapsamı buradan görür)."""
    import time

    page = CLEAN_PAGE
    routes = {"/": Route(page), "/about": Route(page), "/contact": Route(page)}
    async with LocalHttpServer(routes) as server:
        data, _ = await collect(server, deadline=time.monotonic())
    assert len(data.pages) == 1 and data.pages[0].url.endswith("/")
    assert (data.pages_planned, data.skipped_for_time, data.probes_skipped) == (3, 2, True)
    assert data.not_found is None and data.deep_links == []


async def test_wp_like_site_server_rendered_issues() -> None:
    page = html("wp_like/index.html")
    routes = {"/": page, "/haberler/": page, "/iletisim/": page}
    nginx = Route(
        b"<html><head><title>404 Not Found</title></head><body><center><h1>404 Not Found</h1></center><hr><center>nginx</center></body></html>",
        status=404,
    )
    async with LocalHttpServer(routes, fallback=nginx) as server:
        data, _ = await collect(server)
    found = await render_check_ids(data)
    assert {
        "web.mobile.horizontal_overflow",
        "web.mobile.tap_targets_small",
        "web.ux.custom_404_missing",
    } <= set(found)
    assert found["web.ux.custom_404_missing"]["evidence"]["default_page"] == "nginx"
    assert "web.render.js_runtime_errors" not in found
    assert "web.security.token_in_web_storage" not in found
    assert "web.ux.offline_breaks_page" not in found  # klasik site: tarayıcının kendi sayfası


async def test_bot_challenge_page_is_not_scanned() -> None:
    from launchcheck.deep.browser.errors import BotChallengeError

    checkpoint = Route(
        b"<!doctype html><title>Vercel Security Checkpoint</title><p>We're verifying your browser</p>",
        status=429,
    )
    async with LocalHttpServer({"/": checkpoint}) as server:
        with pytest.raises(BotChallengeError):
            await collect(server)
