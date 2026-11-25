"""Gizlilik toplayıcısı ve P6 render alanları gerçek Chromium ile (`pytest -m browser`).

Yerel siteler (gerçek ağ yok; `site.test`, `track.test`, `pixel.example-ads.test`,
`cdn.polyfill.io` sadece bu testte 127.0.0.1'e çözülür ve SSRF allowlist'i yalnız test sunucusunun
portunu açar):

1. privacy_spa: Vite benzeri SPA, bilerek hatalı — takip onaysız başlar, "Reddet"ten sonra da
   sürer; eski jQuery ve ele geçirilmiş CDN'den script; erişilebilirlik ihlalleri.
2. privacy_cmp: düz HTML + CookieYes benzeri banner, doğru davranış → takip bulgusu yok
   (yanlış alarm kontrolü).
3. privacy_wp: WordPress benzeri, iki katmanlı banner ("Çerez ayarları" → "Tümünü reddet");
   piksel onaysız yüklenir ama reddedince durur.
"""

from pathlib import Path
from typing import Any

import pytest

from launchcheck.checks.base import TargetType
from launchcheck.config import Settings
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.deep.browser.privacy import PrivacyCollector, PrivacyInput, PrivacyRaw
from launchcheck.deep.browser.render import RenderCollector, RenderInput
from launchcheck.deep.context import DeepContext
from launchcheck.deep.modules import ModuleOutput
from launchcheck.deep.privacy_data import RejectState
from launchcheck.deep.privacy_module import PrivacyModule
from launchcheck.deep.supply.axe import load_axe_source
from launchcheck.deep.supply.trackers import load_tracker_db
from launchcheck.security.ssrf import UrlGuard
from tests.deep.browser.servers import LocalHttpServer, Route

pytestmark = pytest.mark.browser
pytest.importorskip("playwright")

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "deep"
UA = "LaunchCheckBot/1.0 (+https://launchcheck.example/bot)"
LOCAL_HOSTS = {"site.test", "track.test", "pixel.example-ads.test", "cdn.polyfill.io"}
SETTINGS = Settings(
    deep_privacy_observe_s=1.5,
    deep_privacy_after_click_s=0.5,
    deep_tracker_list_path=str(FIXTURES / "privacy" / "easyprivacy.txt"),
    deep_retire_db_path=str(FIXTURES / "privacy" / "jsrepository.json"),
    deep_retire_update_dir="/tmp/launchcheck-test-none",
    deep_axe_timeout_s=30,
    deep_render_quiet_s=0.5,
    deep_render_max_wait_s=3,
    deep_max_pages=2,
    deep_measure_widths=[375, 1440],
)
AXE = load_axe_source(SETTINGS.deep_axe_path)


async def resolve(host: str, port: int) -> list[str]:
    if host not in LOCAL_HOSTS:
        raise OSError("NXDOMAIN")
    return ["127.0.0.1"]


def routes(name: str) -> dict[str, Route]:
    return {
        "/": Route((FIXTURES / name / "index.html").read_bytes()),
        "/collect": Route(b"", "text/plain", 204),
        "/tr": Route(b"GIF89a", "image/gif"),
        "/vendor/jquery-1.8.3.min.js": Route(
            b"/*! jQuery v1.8.3 jquery.com | jquery.org/license */\nwindow.jQuery=function(){};",
            "text/javascript",
        ),
        "/v3/polyfill.min.js": Route(b"window.__polyfilled=true;", "text/javascript"),
    }


async def collect(server: LocalHttpServer) -> PrivacyRaw:
    home = f"http://site.test:{server.port}/"
    data = PrivacyInput(
        home_url=home,
        axe_urls=[home],
        axe_source=AXE,
        tracker_db=load_tracker_db(SETTINGS.deep_tracker_list_path),
    )
    allow = frozenset({("127.0.0.1", server.port)})
    async with BrowserSession.open(UrlGuard(resolver=resolve), UA, allow=allow) as session:
        return await PrivacyCollector(SETTINGS).collect(session, data)


async def run_module(raw: PrivacyRaw, port: int) -> tuple[DeepContext, dict[str, Any]]:
    async def fake(ctx: DeepContext, data_in: PrivacyInput) -> PrivacyRaw:
        return raw

    ctx = DeepContext(
        target_type=TargetType.WEB,
        target_label=f"http://site.test:{port}",
        target_url=f"http://site.test:{port}",
    )
    out = ModuleOutput()
    await PrivacyModule(SETTINGS, collect=fake).run(ctx, out)
    return ctx, {f.check_id: f for f in out.findings}


async def test_bad_spa_tracks_before_and_after_reject() -> None:
    async with LocalHttpServer(routes("privacy_spa")) as server:
        raw = await collect(server)
        port = server.port
    assert raw.consent.banner and raw.consent.reject == RejectState.CLICKED
    assert raw.consent.reject_label == "reddet"
    assert not raw.consent.banner_after_reject  # tercih saklanıyor; sorun takibin sürmesi
    # Kayıtlarda sorgu dizesi (uid=42) ve çerez değeri yok
    assert all("?" not in r.url for r in raw.before_requests)
    assert all("111.222" not in c.name for c in raw.before_cookies)
    ctx, found = await run_module(raw, port)
    before = found["web.privacy.trackers_before_consent"].evidence["trackers"]
    after = found["web.privacy.trackers_after_reject"].evidence["trackers"]
    assert {t["name"] for t in before} == {"Google Analytics", "track.test"}
    assert {t["name"] for t in after} == {"Google Analytics", "track.test"}
    assert "web.privacy.consent_banner_missing" not in found
    libs = found["web.security.vulnerable_js_library"].evidence["libraries"]
    assert [(lib["library"], lib["version"]) for lib in libs] == [("jquery", "1.8.3")]
    risky = found["web.security.risky_third_party_script"].evidence["scripts"]
    assert [s["domain"] for s in risky] == ["polyfill.io"]
    if AXE:
        rules = {r["rule"] for r in found["web.a11y.axe_violations"].evidence["rules"]}
        assert {"button-name", "color-contrast"} <= rules
    else:
        assert "web.a11y.axe_violations" in ctx.meta["checks_skipped"]


async def test_good_cmp_site_has_no_privacy_findings() -> None:
    async with LocalHttpServer(routes("privacy_cmp")) as server:
        raw = await collect(server)
        port = server.port
    assert raw.consent.cmp == "CookieYes"
    assert raw.consent.reject == RejectState.CLICKED and raw.consent.reject_label == "reject all"
    ctx, found = await run_module(raw, port)
    assert found == {}, {k: v.evidence for k, v in found.items()}
    assert ctx.privacy is not None and ctx.privacy.ok("reject")
    # Reddettikten sonra takip yok ama tercih çerezi kaldı (sadece takipçi çerezleri silindi)
    assert any(c.name == "cookieyes-consent" for c in raw.after_cookies or [])


async def test_wordpress_two_layer_banner() -> None:
    async with LocalHttpServer(routes("privacy_wp")) as server:
        raw = await collect(server)
        port = server.port
    assert raw.consent.reject == RejectState.CLICKED_SECOND_LAYER
    assert raw.consent.reject_label == "tumunu reddet"
    _, found = await run_module(raw, port)
    names = {t["name"] for t in found["web.privacy.trackers_before_consent"].evidence["trackers"]}
    assert names == {"Meta Pixel", "pixel.example-ads.test"}
    assert "web.privacy.trackers_after_reject" not in found


async def test_tracker_without_banner_is_banner_missing() -> None:
    page = b"""<!doctype html><html lang="en"><head><title>x</title><script>
      fetch('http://track.test:' + location.port + '/collect', {mode: 'no-cors'});
    </script></head><body><main><h1>Hi</h1></main></body></html>"""
    async with LocalHttpServer(
        {"/": Route(page), "/collect": Route(b"", "text/plain", 204)}
    ) as server:
        raw = await collect(server)
        port = server.port
    assert not raw.consent.banner and raw.consent.reject == RejectState.NO_BANNER
    ctx, found = await run_module(raw, port)
    assert set(found) == {"web.privacy.consent_banner_missing"}
    assert "web.privacy.trackers_after_reject" in ctx.meta["checks_skipped"]


async def test_render_collects_p6_fields() -> None:
    """measure.js: JSON-LD (JS ile eklenen dahil), microdata, video gömme/link ayrımı, dış linkler
    (sorgu dizesiz, konumuyla) ve yapılandırılmış veri sinyalleri."""
    async with LocalHttpServer(
        {"/": Route((FIXTURES / "links_shop" / "index.html").read_bytes())}
    ) as server:
        base = f"http://site.test:{server.port}"
        data = RenderInput(
            home_url=base + "/", seed_urls=[], baseline_path="/x-missing.txt", bundle_texts=[]
        )
        allow = frozenset({("127.0.0.1", server.port)})
        async with BrowserSession.open(UrlGuard(resolver=resolve), UA, allow=allow) as session:
            result = await RenderCollector(SETTINGS).collect(session, data)
    home = result.pages[0]
    assert len(home.jsonld) == 2 and "BreadcrumbList" in home.jsonld[1]
    assert home.microdata == ["https://schema.org/Offer"]
    assert home.videos == {
        "https://www.youtube.com/embed/AAAAAAAAAAA": True,
        "https://youtu.be/BBBBBBBBBBB": False,
        "https://www.youtube.com/watch?v=CCCCCCCCCCC": True,
    }
    assert home.external_links == {
        "https://partner.test/katalog": "chrome",
        "https://social.test/acme": "chrome",
        "https://youtu.be/BBBBBBBBBBB": "content",  # link modülü videoları oEmbed'e bırakır
    }
    assert home.og["og:image"] == "/og.png"
    sd = home.sd
    assert sd.prices == 2 and "Sepete Ekle" in sd.actions
    assert sd.questions == 3 and sd.accordions >= 3
    assert sd.tel == 1 and sd.address
