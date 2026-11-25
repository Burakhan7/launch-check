"""Link, meta ve yapılandırılmış veri modülü (P6, PHASE2 §6.4).

Dış dünya respx ile taklit edilir (gerçek ağ yok): site sayfaları, dış linkler, YouTube oEmbed.
Render verisi sentetik `RenderData`; render yoksa Faz 1 sunucu HTML'i kullanılır. Üç farklı yapı:
Next.js benzeri SPA (bilerek hatalı), temiz düz HTML site (yanlış alarm kontrolü) ve render
verisi olmayan WordPress benzeri site (Faz 1 HTML'iyle geri düşüş).
"""

import io
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.collectors.fetcher import FetchError, FetchResult
from launchcheck.collectors.web_collector import PageSnapshot, WebContext
from launchcheck.config import Settings
from launchcheck.db.models import Verification
from launchcheck.deep.context import DeepContext, ParentFinding
from launchcheck.deep.events import MemoryEventSink
from launchcheck.deep.external.services import NoLimit
from launchcheck.deep.links.media import image_size, og_issues, video_id, video_state
from launchcheck.deep.links.probe import (
    HostPacer,
    classify_error,
    classify_status,
    probe_link,
)
from launchcheck.deep.links.structured import analyze_blocks, clean_block, opportunities
from launchcheck.deep.links_data import (
    HttpsTarget,
    LinkOutcome,
    LinksData,
    LinkStatus,
    OgImage,
    PartStatus,
)
from launchcheck.deep.links_module import LinksModule, LinksUnavailableError, insecure_targets
from launchcheck.deep.modules import ModuleOutput
from launchcheck.deep.render_data import DeepLinkProbe, RenderData, RenderedPage, SdSignals
from launchcheck.deep.verification import insecure_http_links
from launchcheck.i18n import finding_text, fragment_fn
from launchcheck.security.ssrf import UrlGuard
from tests.conftest import FakeResolver

LINK_IDS = {
    "web.links.broken_internal",
    "web.links.broken_external",
    "web.links.http_target_no_https",
    "web.links.youtube_unavailable",
    "web.meta.og_image_unreachable",
    "web.seo.structured_data_invalid",
    "web.seo.structured_data_opportunity",
}
SHOP = "https://www.shop.test"
HTML = {"content-type": "text/html; charset=utf-8"}


def png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, "PNG")
    return buffer.getvalue()


def page(url: str, status: int | None = 200, **kw: Any) -> RenderedPage:
    return RenderedPage(url=url, final_url=url, status=status, **kw)


def settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "deep_links_per_host_interval_s": 0,
        "deep_links_own_host_interval_s": 0,
        "deep_links_render_wait_s": 0,
    }
    return Settings(**{**base, **kw})


def module(resolver: FakeResolver | None = None, **kw: Any) -> LinksModule:
    return LinksModule(
        settings(**kw),
        guard=UrlGuard(resolver or FakeResolver({"gone-domain.test": []})),  # type: ignore[arg-type]
        limiter=NoLimit(),
    )


def deep_ctx(
    render: RenderData | None = None,
    base: WebContext | None = None,
    parent: list[ParentFinding] | None = None,
    target: str = SHOP,
) -> DeepContext:
    ctx = DeepContext(
        target_type=TargetType.WEB,
        target_label=target,
        target_url=target,
        render=render,
        base=base,
        parent_findings=parent or [],
        events=MemoryEventSink(),
    )
    ctx.render_ready.set()
    return ctx


async def run(mod: LinksModule, ctx: DeepContext) -> dict[str, Any]:
    out = ModuleOutput()
    await mod.run(ctx, out)
    return {f.check_id: f for f in out.findings}


# --- Yapı 1: Next.js benzeri SPA (bilerek hatalı)

BROKEN_JSONLD = '{"@context": "https://schema.org", "@type": "Product", "name": "Kupa",}'
SPA_SD = SdSignals(
    headings=["Kupalar"], actions=["Sepete Ekle", "Detay"], prices=5, accordions=0, questions=0
)


def spa_render() -> RenderData:
    home = page(
        f"{SHOP}/",
        internal_links=[
            f"{SHOP}/",
            f"{SHOP}/products",
            f"{SHOP}/old-page",
            f"{SHOP}/pricing",
            f"{SHOP}/logout",
            f"{SHOP}/brochure.pdf",
        ],
        priority_links=[f"{SHOP}/products", f"{SHOP}/old-page"],
        external_links={
            "https://partner.example.org/x": "content",
            "https://gone-domain.test/": "content",
            "https://www.linkedin.com/company/acme": "chrome",
            "https://ok.example.org/": "chrome",
            "https://youtu.be/BBBBBBBBBBB": "content",  # oEmbed'e bırakılır, HEAD atılmaz
        },
        og={"og:image": "/og.png"},
        jsonld=[BROKEN_JSONLD],
        videos={
            "https://www.youtube.com/embed/AAAAAAAAAAA": True,
            "https://youtu.be/BBBBBBBBBBB": False,
            "https://www.youtube.com/watch?v=CCCCCCCCCCC": False,
        },
        sd=SPA_SD,
    )
    products = page(f"{SHOP}/products", internal_links=[f"{SHOP}/", f"{SHOP}/pricing"], sd=SPA_SD)
    return RenderData(pages=[home, products])


def mock_spa(router: respx.MockRouter, requested: list[str]) -> None:
    def youtu(request: httpx.Request) -> httpx.Response:
        requested.append(f"{request.method} {request.url}")
        return httpx.Response(200)

    router.route(host="youtu.be").mock(side_effect=youtu)

    def shop(request: httpx.Request) -> httpx.Response:
        requested.append(f"{request.method} {request.url}")
        path = request.url.path
        if path == "/old-page":
            return httpx.Response(404, text="nope", headers=HTML)
        if path == "/og.png":
            return httpx.Response(
                200, content=png(1200, 630), headers={"content-type": "image/png"}
            )
        return httpx.Response(200, text="<html></html>", headers=HTML)

    def partner(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(404, text="gone", headers=HTML)

    def oembed(request: httpx.Request) -> httpx.Response:
        url = parse_qs(urlsplit(str(request.url)).query)["url"][0]
        vid = parse_qs(urlsplit(url).query)["v"][0]
        status = {"AAAAAAAAAAA": 401, "BBBBBBBBBBB": 404, "CCCCCCCCCCC": 200}[vid]
        return httpx.Response(status, json={"title": "x"} if status == 200 else None)

    router.route(host="www.shop.test").mock(side_effect=shop)
    router.route(host="partner.example.org").mock(side_effect=partner)
    router.route(host="ok.example.org").mock(return_value=httpx.Response(200, headers=HTML))
    router.route(host="www.youtube.com", path="/oembed").mock(side_effect=oembed)
    router.route(host="legacy.partner.test", scheme="https").mock(
        side_effect=httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    )
    router.route(host="docs.vendor.test", scheme="https").mock(
        return_value=httpx.Response(200, headers=HTML)
    )


INSECURE_PARENT = ParentFinding(
    "web.links.insecure_http_links",
    Severity.MEDIUM,
    Category.SECURITY,
    {
        "links": [
            {"url": "http://legacy.partner.test/page", "pages": ["/"]},
            {"url": "http://docs.vendor.test/guide", "pages": ["/"]},
            {"url": "http://docs.vendor.test/other", "pages": ["/"]},
        ]
    },
)


async def test_spa_site_end_to_end() -> None:
    requested: list[str] = []
    with respx.mock(assert_all_called=False) as router:
        mock_spa(router, requested)
        ctx = deep_ctx(spa_render(), parent=[INSECURE_PARENT])
        found = await run(module(), ctx)
    data = ctx.links
    assert data is not None and data.source == "render"
    assert all(data.done(p) for p in data.status), data.status
    assert set(found) == LINK_IDS

    internal = found["web.links.broken_internal"]
    assert internal.severity == Severity.MEDIUM
    assert [link["url"] for link in internal.evidence["links"]] == ["/old-page"]
    assert internal.evidence["links"][0]["in_menu"] is True
    # Durum değiştiren yollar ve indirilebilir dosyalar istenmez; render'ın açtığı sayfa da
    assert not any("/logout" in r or "brochure.pdf" in r for r in requested)
    assert not any(r.endswith("/products") for r in requested)
    assert not any("youtu.be" in r for r in requested)

    external = {link["url"]: link for link in found["web.links.broken_external"].evidence["links"]}
    assert set(external) == {"https://partner.example.org/x", "https://gone-domain.test/"}
    assert external["https://gone-domain.test/"]["reason"] == "dns"
    assert found["web.links.broken_external"].severity == Severity.LOW

    https = found["web.links.http_target_no_https"]
    assert https.severity == Severity.INFO
    assert https.evidence["fixable_count"] == 1 and https.evidence["no_https_count"] == 1

    videos = {
        v["video_id"]: v["state"] for v in found["web.links.youtube_unavailable"].evidence["videos"]
    }
    assert videos == {"AAAAAAAAAAA": "not_embeddable", "BBBBBBBBBBB": "removed"}

    og = found["web.meta.og_image_unreachable"]
    assert og.severity == Severity.MEDIUM
    assert og.evidence["images"][0]["issues"] == ["relative_url"]
    assert og.evidence["images"][0]["size"] == [1200, 630]

    invalid = found["web.seo.structured_data_invalid"].evidence["pages"][0]
    assert invalid["issues"][0]["kind"] == "parse_error"
    opp = found["web.seo.structured_data_opportunity"].evidence["opportunities"]
    assert [o["kind"] for o in opp] == ["product"]

    assert ctx.meta["links"]["internal_checked"] >= 3
    events = [e[1] for e in ctx.events.events]  # type: ignore[attr-defined]
    assert "deep.event.links_checked" in events
    # Faz 1 bulgusunun doğrulaması: bir hedefte SSL yok → escalated
    assert insecure_http_links(ctx, INSECURE_PARENT) == Verification.ESCALATED


# --- Yapı 2: temiz düz HTML site (yanlış alarm olmamalı)

CLEAN = "https://example-clean.test"
ORG_JSONLD = json.dumps(
    {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Organization", "name": "Acme", "url": CLEAN},
            {
                "@type": "BreadcrumbList",
                "itemListElement": [{"@type": "ListItem", "position": 1, "name": "Ana sayfa"}],
            },
        ],
    }
)


def clean_render() -> RenderData:
    # Footer'da telefon + adres var ama harita yok, fiyat yok, 2 soru başlığı: fırsat değil
    signals = SdSignals(headings=["Hakkımızda", "Nasıl çalışır?"], questions=2, tel=1, address=True)
    home = page(
        f"{CLEAN}/",
        internal_links=[f"{CLEAN}/", f"{CLEAN}/about", f"{CLEAN}/contact"],
        external_links={"https://ok.example.org/": "content"},
        og={"og:image": f"{CLEAN}/share.png"},
        jsonld=[ORG_JSONLD],
        videos={"https://www.youtube-nocookie.com/embed/CCCCCCCCCCC": True},
        sd=signals,
    )
    return RenderData(pages=[home, page(f"{CLEAN}/about", sd=signals)])


async def test_clean_site_has_no_findings() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="example-clean.test", path="/share.png").mock(
            return_value=httpx.Response(
                200, content=png(1200, 630), headers={"content-type": "image/png"}
            )
        )
        router.route(host="example-clean.test").mock(return_value=httpx.Response(200, headers=HTML))
        router.route(host="ok.example.org").mock(return_value=httpx.Response(200, headers=HTML))
        router.route(host="www.youtube.com", path="/oembed").mock(
            return_value=httpx.Response(200, json={"title": "ok"})
        )
        ctx = deep_ctx(clean_render(), target=CLEAN)
        found = await run(module(), ctx)
    assert found == {}
    data = ctx.links
    assert data is not None
    # Faz 1'de http:// link bulgusu yok → kontrol edilecek bir şey yok ("geçti" sayılır)
    assert data.status["https_targets"] == PartStatus.NO_DATA
    assert data.skipped_checks() == []
    assert data.present_types == ["BreadcrumbList", "Organization"]


# --- Yapı 3: render verisi yok → Faz 1 sunucu HTML'i (WordPress benzeri)

WP = "https://blog.wp.test"
WP_HOME = f"""<html><head>
<meta property="og:image" content="{WP}/wp-content/uploads/logo.svg">
<script type="application/ld+json">
//<![CDATA[
{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{{"@type":"Question","name":"Ne?"}}]}}
//]]>
</script></head><body>
<nav><a href="/kategori/haberler/">Haberler</a></nav>
<a href="/2019/05/eski-yazi/">Eski yazı</a>
<a href="https://dead.example.net/page">Kaynak</a>
<iframe src="https://www.youtube.com/embed/BBBBBBBBBBB"></iframe>
</body></html>"""


def wp_base() -> WebContext:
    return WebContext(
        target_type=TargetType.WEB,
        target_label=WP,
        final_url=f"{WP}/",
        pages=[PageSnapshot(f"{WP}/", 200, HTML, WP_HOME)],
    )


async def test_phase1_fallback_without_render() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="blog.wp.test", path="/wp-content/uploads/logo.svg").mock(
            return_value=httpx.Response(
                200, text="<svg/>", headers={"content-type": "image/svg+xml"}
            )
        )
        router.route(host="blog.wp.test", path="/2019/05/eski-yazi/").mock(
            return_value=httpx.Response(410)
        )
        router.route(host="blog.wp.test").mock(return_value=httpx.Response(200, headers=HTML))
        router.route(host="dead.example.net").mock(return_value=httpx.Response(503))
        router.route(host="www.youtube.com", path="/oembed").mock(return_value=httpx.Response(404))
        ctx = deep_ctx(render=None, base=wp_base(), target=WP)
        found = await run(module(), ctx)
    data = ctx.links
    assert data is not None and data.source == "phase1"
    assert [link["url"] for link in found["web.links.broken_internal"].evidence["links"]] == [
        "/2019/05/eski-yazi/"
    ]
    assert found["web.links.broken_external"].evidence["links"][0]["reason"] == "http_5xx"
    assert found["web.meta.og_image_unreachable"].evidence["images"][0]["issues"] == [
        "svg_unsupported"
    ]
    issues = found["web.seo.structured_data_invalid"].evidence["pages"][0]["issues"]
    assert issues == [{"kind": "missing_field", "type": "Question", "field": "acceptedAnswer.text"}]
    assert found["web.links.youtube_unavailable"].evidence["videos"][0]["state"] == "removed"
    # Fırsat sinyalleri render ister: check "geçti" değil, atlandı
    assert "web.seo.structured_data_opportunity" in ctx.meta["checks_skipped"]


async def test_no_pages_at_all_fails_module() -> None:
    with pytest.raises(LinksUnavailableError):
        await module().run(deep_ctx(render=None), ModuleOutput())


async def test_youtube_service_failure_skips_only_its_check() -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_spa(router, [])
        router.route(host="www.youtube.com", path="/oembed").mock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        ctx = deep_ctx(spa_render(), parent=[INSECURE_PARENT])
        found = await run(module(), ctx)
    assert ctx.links is not None and ctx.links.status["youtube"] == PartStatus.UNAVAILABLE
    assert "web.links.youtube_unavailable" not in found
    assert ctx.meta["checks_skipped"] == ["web.links.youtube_unavailable"]
    assert "web.links.broken_internal" in found


async def test_youtube_rate_limited_part() -> None:
    class Deny:
        async def allow(self, service: str) -> bool:
            return service != "youtube"

    mod = LinksModule(settings(), guard=UrlGuard(FakeResolver()), limiter=Deny())  # type: ignore[arg-type]
    with respx.mock(assert_all_called=False) as router:
        mock_spa(router, [])
        ctx = deep_ctx(spa_render())
        await run(mod, ctx)
    assert ctx.links is not None and ctx.links.status["youtube"] == PartStatus.RATE_LIMITED


async def test_spa_router_404s_are_left_to_spa_check() -> None:
    """SPA rotası sunucuda 404 ama tıklayınca açılıyor (render'ın spa_deep_link_404'ü):
    aynı kök neden kırık link olarak tekrar sayılmaz; 5xx yine sayılır."""
    render = RenderData(
        pages=[
            page(f"{SHOP}/", internal_links=[f"{SHOP}/a", f"{SHOP}/b"]),
            page(f"{SHOP}/a", status=404),
        ],
        deep_links=[DeepLinkProbe(f"{SHOP}/a", 404, True)],
    )
    with respx.mock(assert_all_called=False) as router:
        router.route(host="www.shop.test", path="/b").mock(return_value=httpx.Response(500))
        ctx = deep_ctx(render)
        found = await run(module(), ctx)
    assert ctx.links is not None and ctx.links.spa_router_404s_excluded
    assert [link["status"] for link in found["web.links.broken_internal"].evidence["links"]] == [
        500
    ]


async def test_query_strings_are_never_sent() -> None:
    requested: list[str] = []
    render = RenderData(
        pages=[
            page(
                f"{SHOP}/",
                internal_links=[f"{SHOP}/x"],
                external_links={"https://ok.example.org/p": "content"},
            )
        ]
    )
    render.pages[0].internal_links.append(f"{SHOP}/y?token=secret123")

    def record(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200)

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=record)
        await run(module(), deep_ctx(render))
    assert requested and not any("?" in r for r in requested)


# --- yoklama ve sınıflandırma


@pytest.mark.parametrize(
    ("status", "headers", "outcome"),
    [
        (200, {}, LinkOutcome.OK),
        (301, {}, LinkOutcome.OK),
        (404, {}, LinkOutcome.BROKEN),
        (410, {}, LinkOutcome.BROKEN),
        (502, {}, LinkOutcome.BROKEN),
        (403, {}, LinkOutcome.UNKNOWN),  # bot engeli / giriş
        (401, {}, LinkOutcome.UNKNOWN),
        (429, {}, LinkOutcome.UNKNOWN),
        (999, {}, LinkOutcome.UNKNOWN),  # LinkedIn
        (503, {"cf-mitigated": "challenge"}, LinkOutcome.UNKNOWN),
    ],
)
def test_classify_status(status: int, headers: dict[str, str], outcome: str) -> None:
    assert classify_status(status, headers).outcome == outcome


@pytest.mark.parametrize(
    ("code", "detail", "outcome", "reason"),
    [
        ("url_unresolvable", "", LinkOutcome.BROKEN, "dns"),
        ("site_unreachable", "connect", LinkOutcome.BROKEN, "connect"),
        ("site_unreachable", "tls", LinkOutcome.BROKEN, "tls"),
        ("site_unreachable", "timeout", LinkOutcome.UNKNOWN, "timeout"),
        ("url_not_public", "", LinkOutcome.UNKNOWN, "blocked"),
    ],
)
def test_classify_error(code: str, detail: str, outcome: str, reason: str) -> None:
    result = classify_error(FetchError(code, detail))
    assert (result.outcome, result.reason) == (outcome, reason)


def _res(status: int) -> FetchResult:
    return FetchResult(url="u", final_url="u", status=status, headers={}, body=b"")


async def test_probe_falls_back_to_get_when_head_unsupported() -> None:
    calls: list[str] = []

    async def fetch(url: str, max_bytes: int, method: str) -> FetchResult:
        calls.append(method)
        return _res(405 if method == "HEAD" else 200)

    result = await probe_link(fetch, HostPacer(0), "https://a.test/x")
    assert result.outcome == LinkOutcome.OK and calls == ["HEAD", "GET"]


async def test_probe_does_not_retry_dns_failure() -> None:
    calls: list[str] = []

    async def fetch(url: str, max_bytes: int, method: str) -> FetchResult:
        calls.append(method)
        raise FetchError("url_unresolvable")

    result = await probe_link(fetch, HostPacer(0), "https://gone.test/")
    assert result.reason == "dns" and calls == ["HEAD"]


async def test_host_pacer_spaces_requests_to_same_host(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("launchcheck.deep.links.probe.asyncio.sleep", fake_sleep)
    pacer = HostPacer(1.0, own_host="site.test", own_interval_s=0)
    for host in ("a.test", "a.test", "site.test", "site.test", "b.test"):
        await pacer.wait(host)
    assert len(sleeps) == 1 and 0.9 < sleeps[0] <= 1.0


# --- JSON-LD


def test_jsonld_valid_types_have_no_issues() -> None:
    product = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Kupa",
        "offers": {"@type": "Offer", "price": "99.90", "priceCurrency": "TRY"},
        "aggregateRating": {"@type": "AggregateRating", "ratingValue": 4.5, "reviewCount": 12},
    }
    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": "Ne?", "acceptedAnswer": {"@type": "Answer", "text": "X"}}
        ],
    }
    website = {"@context": "http://schema.org", "@type": ["WebSite", "Thing"], "url": "x"}
    result = analyze_blocks("/", [json.dumps(product), json.dumps(faq), json.dumps(website)])
    assert result.issues == []
    assert result.types == ["Product", "FAQPage", "WebSite", "Thing"]


def test_jsonld_missing_fields_and_context() -> None:
    blocks = [
        json.dumps({"@context": "https://schema.org", "@type": "Product", "name": "X"}),
        json.dumps(
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Y",
                "offers": {"@type": "Offer", "price": "10"},
            }
        ),
        json.dumps({"@type": "Organization", "name": "Acme"}),
        json.dumps({"@context": "https://schema.org", "name": "no type"}),
        json.dumps({"@context": "https://schema.org", "@type": "Restaurant", "name": "Lokanta"}),
    ]
    kinds = {(i.kind, i.type, i.field) for i in analyze_blocks("/", blocks).issues}
    assert ("missing_field", "Product", "offers|review|aggregateRating") in kinds
    assert ("missing_field", "Offer", "priceCurrency") in kinds
    assert ("missing_context", "", "") in kinds
    assert ("missing_type", "", "") in kinds
    assert ("missing_field", "Restaurant", "address") in kinds


def test_jsonld_parse_error_and_wrappers() -> None:
    assert analyze_blocks("/", ['{"a": 1,}']).issues[0].kind == "parse_error"
    wrapped = '<!--\n{"@context":"https://schema.org","@type":"Organization","name":"A"}\n-->'
    assert clean_block(wrapped).startswith("{")
    assert analyze_blocks("/", [wrapped]).issues == []
    assert analyze_blocks("/", ["   "]).issues == []


# --- fırsatlar (TR ve EN, en az iki sinyal)


@pytest.mark.parametrize(
    ("signals", "kinds"),
    [
        (SdSignals(actions=["Sepete Ekle"], prices=3), ["product"]),
        (SdSignals(actions=["Add to cart"], prices=1), ["product"]),
        (SdSignals(headings=["Sıkça Sorulan Sorular"], questions=4), ["faq"]),
        (SdSignals(headings=["Help"], questions=5, accordions=5), ["faq"]),
        (SdSignals(map=True, tel=1), ["local_business"]),
        # Yanlış alarm: SaaS fiyat sayfası (fiyat var, sepete ekle yok)
        (SdSignals(actions=["Start free trial", "Abone ol"], prices=3), []),
        # Yanlış alarm: blog yazısında iki soru başlığı
        (SdSignals(headings=["SSS"], questions=2), []),
        # Yanlış alarm: footer'da telefon + adres (harita yok)
        (SdSignals(tel=1, address=True), []),
    ],
)
def test_opportunities(signals: SdSignals, kinds: list[str]) -> None:
    assert [o.kind for o in opportunities("/", signals, set())] == kinds


def test_opportunity_suppressed_when_type_exists_anywhere() -> None:
    signals = SdSignals(actions=["Sepete ekle"], prices=2, map=True, tel=1)
    assert opportunities("/", signals, {"Product", "Organization"}) == []
    assert [o.kind for o in opportunities("/", signals, {"Offer"})] == ["local_business"]


# --- og:image ve YouTube


def og(
    raw: str,
    status: int | None,
    ctype: str = "image/png",
    size: int | None = 1000,
    dims: Any = (1200, 630),
) -> list[str]:
    return og_issues(
        raw,
        status,
        ctype,
        size,
        dims,
        max_bytes=5_000_000,
        min_px=200,
        ratio_min=1.0,
        ratio_max=2.2,
    )


def test_og_issues() -> None:
    assert og("https://a.test/x.png", 200) == []
    assert og("https://a.test/x.png", 200, dims=(1080, 1080)) == []  # kare: WhatsApp'ta iyi
    assert og("/x.png", 200) == ["relative_url"]
    assert og("https://a.test/x", None) == ["unreachable"]
    assert og("https://a.test/x", 404) == ["http_error"]
    assert og("https://a.test/x", 200, "text/html") == ["not_image"]
    assert og("https://a.test/x.svg", 200, "image/svg+xml") == ["svg_unsupported"]
    assert og("https://a.test/x", 200, size=6_000_000) == ["too_large"]
    assert og("https://a.test/x", 200, dims=(150, 80)) == ["too_small"]
    assert og("https://a.test/x", 200, dims=(600, 1200)) == ["bad_ratio"]


def test_image_size_reads_header_of_truncated_file() -> None:
    data = png(1200, 630)
    assert image_size(data) == (1200, 630)
    assert image_size(data[:200]) == (1200, 630)
    assert image_size(b"<svg/>") is None


@pytest.mark.parametrize(
    ("url", "vid"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/videoseries?list=PL1", None),
        ("https://www.youtube.com/@kanal", None),
        ("https://www.youtube.com/watch?v=short", None),  # bozuk: Faz 1'in konusu
        ("https://vimeo.com/123", None),
    ],
)
def test_video_id(url: str, vid: str | None) -> None:
    assert video_id(url) == vid


def test_video_state() -> None:
    assert [video_state(s) for s in (200, 401, 403, 404, 500, None)] == [
        "ok",
        "not_embeddable",
        "not_embeddable",
        "removed",
        "unknown",
        "unknown",
    ]


# --- check'ler (sentetik veri) ve doğrulama kuralı


def data_ctx(data: LinksData, parent: list[ParentFinding] | None = None) -> DeepContext:
    ctx = deep_ctx(parent=parent)
    ctx.links = data
    return ctx


async def checks_on(ctx: DeepContext) -> dict[str, Any]:
    checks = [c for c in load_builtin_checks().all() if c.module == "links"]
    result = await run_checks(ctx, checks)
    assert result.failed == []
    return {f.check_id: f for f in result.findings}


async def test_link_only_video_with_embedding_disabled_is_fine() -> None:
    from launchcheck.deep.links_data import VideoStatus

    data = LinksData(site=SHOP)
    data.videos = [VideoStatus("AAAAAAAAAAA", "u", "/", False, "not_embeddable", 401)]
    assert await checks_on(data_ctx(data)) == {}


async def test_og_small_image_is_low() -> None:
    data = LinksData(site=SHOP)
    data.og_images = [
        OgImage("/", "og:image", "u", "server", 200, "image/png", 10, 100, 100, ["too_small"])
    ]
    found = await checks_on(data_ctx(data))
    assert found["web.meta.og_image_unreachable"].severity == Severity.LOW


def test_verification_rule_confirmed_when_all_targets_support_https() -> None:
    ctx = deep_ctx()
    assert insecure_http_links(ctx, INSECURE_PARENT) is None  # veri yok → karar yok
    ctx.links = LinksData(site=SHOP)
    ctx.links.https_targets = [HttpsTarget("a.test", "https://a.test/", True)]
    assert insecure_http_links(ctx, INSECURE_PARENT) == Verification.CONFIRMED
    ctx.links.https_targets = [HttpsTarget("a.test", "https://a.test/", None, "timeout")]
    assert insecure_http_links(ctx, INSECURE_PARENT) is None


def test_insecure_targets_one_per_host() -> None:
    ctx = deep_ctx(parent=[INSECURE_PARENT])
    assert insecure_targets(ctx) == [
        "http://legacy.partner.test/page",
        "http://docs.vendor.test/guide",
    ]


async def test_texts_render_with_params_in_both_languages() -> None:
    with respx.mock(assert_all_called=False) as router:
        mock_spa(router, [])
        ctx = deep_ctx(spa_render(), parent=[INSECURE_PARENT])
        found = await run(module(), ctx)
    registry = {c.id: c for c in load_builtin_checks().all()}
    for lang in ("tr", "en"):
        for check_id, finding in found.items():
            check = registry[check_id]
            params = check.text_params(finding.evidence, fragment_fn(check_id, lang))
            text = finding_text(check_id, lang, params)
            assert "{" not in text.title + text.description, (check_id, lang)


def test_link_checks_are_phase2_only() -> None:
    registry = load_builtin_checks()
    ids = {c.id for c in registry.all() if c.module == "links"}
    assert ids == LINK_IDS
    assert not ids & {c.id for c in registry.for_target(TargetType.WEB, 1)}


async def test_link_checks_reject_phase1_context() -> None:
    from tests.checks.helpers import make_ctx

    checks = [c for c in load_builtin_checks().all() if c.module == "links"]
    result = await run_checks(make_ctx(), checks)
    assert sorted(result.failed) == sorted(LINK_IDS)


def test_link_status_dataclass_defaults() -> None:
    link = LinkStatus("https://a.test/", LinkOutcome.UNKNOWN)
    assert link.pages == [] and not link.prominent


def test_default_modules_lane_order() -> None:
    """Şerit içi sıra: render → gizlilik (tarayıcı), dış görünürlük → linkler (ağ)."""
    from launchcheck.deep.modules import Lane
    from launchcheck.deep.setup import default_modules, default_verification_rules

    lanes: dict[Lane, list[str]] = {}
    for m in default_modules():
        lanes.setdefault(m.lane, []).append(m.name)
    assert lanes[Lane.BROWSER] == ["render", "privacy"]
    assert lanes[Lane.NETWORK] == ["external", "links"]
    assert "web.links.insecure_http_links" in default_verification_rules()
    s = Settings()
    assert s.deep_budget_external_s + s.deep_budget_links_s <= s.deep_budget_network_s
    assert s.deep_budget_render_s + s.deep_budget_privacy_s <= s.deep_budget_browser_s


async def test_render_module_marks_render_ready_even_on_failure() -> None:
    from launchcheck.deep.render_module import RenderModule

    async def boom(url: str) -> WebContext:
        raise RuntimeError("down")

    ctx = DeepContext(target_type=TargetType.WEB, target_label=SHOP, target_url=SHOP)
    with pytest.raises(RuntimeError):
        await RenderModule(settings(), collect_base=boom).run(ctx, ModuleOutput())
    assert ctx.render_ready.is_set() and ctx.key_urls_ready.is_set()
