"""Kıyas site taraması gerçek Chromium ile (`pytest -m browser`).

Yerel siteler (gerçek ağ yok; `peer.test`, `wp.test`, `blocked.test` sadece testte 127.0.0.1):
1. bench_saas: Next.js benzeri örnek SaaS — hero, logolar, özellikler, yorumlar, SSS, son CTA;
   fiyat ve kısa kayıt sayfası.
2. bench_wp: WordPress benzeri — robots.txt fiyat sayfasını kapatıyor: o sayfa açılmamalı.
3. robots.txt her şeyi kapatan site: hiçbir sayfa istenmemeli.
Örnek sitelerde güvenlik check'i çalışmaz: tarayıcı sadece sayfa açar (POST yok, form yok).
"""

from pathlib import Path

import pytest

from launchcheck.config import Settings
from launchcheck.deep.benchmark.robots import parse_policy
from launchcheck.deep.benchmark_module import anatomy_from_crawl
from launchcheck.deep.browser.benchmark import SiteCrawl, SiteCrawler
from launchcheck.deep.browser.launcher import BrowserSession
from launchcheck.security.ssrf import UrlGuard
from tests.deep.browser.servers import LocalHttpServer, Route

pytestmark = pytest.mark.browser
pytest.importorskip("playwright")

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "deep"
UA = "LaunchCheckBot/1.0 (+https://launchcheck.example/bot)"


def png(width: int = 600, height: int = 300) -> Route:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "steelblue").save(buf, "PNG")
    return Route(buf.getvalue(), "image/png")


async def resolve(host: str, port: int) -> list[str]:
    if host not in ("peer.test", "wp.test", "blocked.test"):
        raise OSError("NXDOMAIN")
    return ["127.0.0.1"]


def html(path: str) -> Route:
    return Route((FIXTURES / path).read_bytes())


async def run(routes: dict[str, Route], host: str, robots: str) -> tuple[SiteCrawl, list[bytes]]:
    async with LocalHttpServer(routes) as server:
        origin = f"http://{host}:{server.port}"
        policy = parse_policy(origin, 200 if robots else 404, robots)
        allow = frozenset({("127.0.0.1", server.port)})
        async with BrowserSession.open(UrlGuard(resolver=resolve), UA, allow=allow) as session:
            crawl = await SiteCrawler(Settings().deep_competitor_max_pages).crawl(
                session, origin, policy.allowed
            )
        requests = list(server.requests)
    return crawl, requests


async def test_saas_peer_anatomy() -> None:
    crawl, requests = await run(
        {
            "/": html("bench_saas/index.html"),
            "/pricing": html("bench_saas/pricing.html"),
            "/signup": html("bench_saas/signup.html"),
            "/shot.png": png(),
            **{f"/l{i}.png": png(80, 30) for i in range(1, 6)},
            **{f"/f{i}.png": png(200, 120) for i in range(1, 4)},
        },
        "peer.test",
        "",
    )
    assert crawl.ok and crawl.pages == 3 and set(crawl.shots) == {"mobile", "desktop"}
    assert not any(r.startswith(b"POST") for r in requests)
    a = anatomy_from_crawl(crawl, None)
    assert a["sections"] == ["hero", "logos", "features", "testimonials", "faq", "final_cta"]
    assert a["hero"]["subheadline"] and a["hero"]["cta_count_above_fold"] >= 1
    assert a["hero"]["media_type"] == "image"
    assert a["trust"]["logo_band"] and a["trust"]["testimonials"] >= 2
    assert a["trust"]["user_count_claim"] and a["trust"]["security_badges"]
    assert a["pricing"]["clicks_to_price"] == 1 and a["pricing"]["plans"] == 3
    assert a["pricing"]["free_tier"] and a["pricing"]["highlighted_plan"]
    assert a["signup"] == {
        "fields": 2,
        "required": 2,
        "social_login": ["google"],
        "card_required": False,
    }
    assert {"pricing", "blog", "customers"} <= set(a["page_inventory"])
    assert a["perf"]["lcp_ms"] is not None and a["share"]["og_image_ratio_ok"] is True
    # Desen kopyalanır, içerik kopyalanmaz: başlık/slogan metni anatomide yok
    assert "Plan projects" not in str(a) and "Start free" not in str(a)


async def test_wordpress_robots_closes_pricing() -> None:
    crawl, requests = await run(
        {"/": html("bench_wp/index.html"), "/fiyatlar": Route(b"<p>1.000 TL</p>")},
        "wp.test",
        "User-agent: *\nDisallow: /fiyatlar\n",
    )
    assert crawl.ok and crawl.pricing_url is None
    assert not any(b"/fiyatlar" in r for r in requests)
    a = anatomy_from_crawl(crawl, None)
    assert "wordpress" in a["stack"]
    assert a["trust"]["company_info"] and a["trust"]["contact"]
    assert "testimonials" not in a["sections"] and "faq" not in a["sections"]
    assert {"pricing", "about", "contact"} <= set(a["page_inventory"])


async def test_robots_disallow_all_sends_no_page_requests() -> None:
    crawl, requests = await run(
        {"/": html("bench_saas/index.html")},
        "blocked.test",
        "User-agent: LaunchCheckBot\nDisallow: /\n",
    )
    assert not crawl.ok and crawl.reason == "robots_blocked"
    assert requests == []
