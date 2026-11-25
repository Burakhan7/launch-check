"""Kıyas modülü (P9, PHASE2 §7) — tarayıcısız testler.

Site taramaları `anatomy.js` / `funnel.js` çıktısı biçiminde sentetik verilir; kategori tespiti,
anatomi, karşılaştırma, robots.txt, önbellek, modül, referans kütüphanesi ve API burada.
Gerçek Chromium taraması `tests/deep/browser/test_benchmark_collect.py`'de.
"""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.config import Settings
from launchcheck.db.models import ReferenceSite, ReferenceSiteStatus, Scan, utcnow
from launchcheck.deep.assets import AssetStore
from launchcheck.deep.benchmark.anatomy import build_anatomy, classify_section, page_inventory
from launchcheck.deep.benchmark.cache import AnatomyCache, ShotStore
from launchcheck.deep.benchmark.categories import (
    SiteSignals,
    category_keys,
    category_labels,
    detect_category,
)
from launchcheck.deep.benchmark.compare import compare, rebuild_sections, speed
from launchcheck.deep.benchmark.library import (
    majestic_ranks,
    refresh_due_sites,
    refresh_popularity,
    sync_reference_sites,
)
from launchcheck.deep.benchmark.robots import RobotsPolicy, parse_policy
from launchcheck.deep.benchmark_data import PartStatus
from launchcheck.deep.benchmark_module import (
    BenchmarkBlockedError,
    BenchmarkModule,
    ReferenceExample,
)
from launchcheck.deep.browser.benchmark import SiteCrawl
from launchcheck.deep.context import DeepContext
from launchcheck.deep.events import MemoryEventSink
from launchcheck.deep.modules import ModuleOutput
from launchcheck.i18n import finding_text, fragment_fn
from tests.conftest import FakeRedis
from tests.deep.helpers import add_parent

SM = async_sessionmaker[AsyncSession]
SITE = "https://acme.test"
BENCH_IDS = {
    "web.benchmark.common_pattern_missing",
    "web.benchmark.missing_page_types",
    "web.benchmark.slower_than_peers",
}


def webp() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, "WEBP")
    return buf.getvalue()


# --- kategoriler


def test_categories_and_labels() -> None:
    keys = category_keys()
    assert "saas" in keys and "dating_matching" in keys
    tr = {c["key"]: c["label"] for c in category_labels("tr")}
    en = {c["key"]: c["label"] for c in category_labels("en")}
    assert tr["saas"].startswith("SaaS") and en["nonprofit_org"].startswith("Nonprofit")


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (SiteSignals({"is_dating_or_matching": True}, [], []), "dating_matching"),
        (SiteSignals({"is_emotional_support": True}, [], []), "wellbeing_support"),
        (SiteSignals({}, ["Kupa ve tabak"], [], prices=8, add_to_cart=True), "ecommerce_brand"),
        (SiteSignals({}, ["Open source database", "Developer API and SDK"], []), "dev_ai_tools"),
        (SiteSignals({}, ["Bağış yap", "Gönüllü ol, vakfımızı destekle"], []), "nonprofit_org"),
        (
            SiteSignals({}, ["Uygulamamızı indir"], ["apps.apple.com", "play.google.com"]),
            "mobile_app_landing",
        ),
        (
            SiteSignals({"has_login": True}, ["Ekip için iş akışı"], [], has_pricing_page=True),
            "saas",
        ),
        # Belirsiz: sinyal yok veya zayıf → kategori yok (referans örnek kullanılmaz)
        (SiteSignals({}, ["Hoş geldiniz"], []), None),
        (SiteSignals({}, ["API"], []), None),
    ],
)
def test_detect_category(signals: SiteSignals, expected: str | None) -> None:
    assert detect_category(signals)[0] == expected


# --- anatomi


def section(**kw: Any) -> dict[str, Any]:
    base = {
        "heading": "",
        "classes": "",
        "h1": False,
        "images": 0,
        "logo_like": 0,
        "quotes": 0,
        "stars": 0,
        "prices": 0,
        "details": 0,
        "questions": 0,
        "buttons": 0,
        "words": 50,
    }
    return {**base, **kw}


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        (section(h1=True), "hero"),
        (section(logo_like=6, words=10), "logos"),
        (section(heading="musterilerimiz ne diyor"), "testimonials"),
        (section(quotes=3), "testimonials"),
        (section(classes="testimonials-grid"), "testimonials"),
        (section(prices=3), "pricing"),
        (section(details=5), "faq"),
        (section(heading="frequently asked questions"), "faq"),
        (section(heading="nasil calisir"), "how_it_works"),
        (section(heading="everything you need", images=3), "features"),
        (section(words=500), "unknown"),  # uzun metin bölümü: tür yok
    ],
)
def test_classify_section(raw: dict[str, Any], kind: str) -> None:
    assert classify_section(raw, 1, last=False) == kind


def test_final_cta_only_at_end() -> None:
    raw = section(buttons=1, words=20)
    assert classify_section(raw, 5, last=True) == "final_cta"
    assert classify_section(raw, 2, last=False) == "unknown"


def test_page_inventory_tr_and_en() -> None:
    links = [
        f"{SITE}/fiyatlandirma",
        f"{SITE}/sikca-sorulan-sorular",
        f"{SITE}/blog/yazi-1",
        f"{SITE}/hakkimizda",
        f"{SITE}/case-studies",
        f"{SITE}/docs/getting-started",
        "mailto:",
        f"{SITE}/random",
    ]
    assert set(page_inventory(links)) == {"pricing", "faq", "blog", "about", "customers", "docs"}


def home_raw(**kw: Any) -> dict[str, Any]:
    """anatomy.js çıktısı biçiminde ana sayfa."""
    sections = kw.pop(
        "sections",
        [
            section(h1=True),
            section(logo_like=6, words=10),
            section(heading="features", images=3),
            section(quotes=3),
            section(details=4),
            section(buttons=1, words=20),
        ],
    )
    raw = {
        "perf": {"lcp_ms": kw.pop("lcp", 1500), "cls": 0.01, "js_kb": 300},
        "sections": sections,
        "hero": {
            "headline_words": 6,
            "subheadline": True,
            "cta_above_fold": 1,
            "primary_cta": "X",
            "media": "image",
        },
        "seo": {"title_length": 40, "h1_words": 6, "h2_count": 5, "word_count": 700},
        "claims": ["10.000+ kullanici"],
        "alts": [],
        "footer_text": "acme a.s. mersis no 123",
        "links": [f"{SITE}/pricing", f"{SITE}/blog", f"{SITE}/contact", "mailto:"],
        "external_hosts": [],
        "og_image": True,
        "og_size": [1200, 630],
        "stack": ["nextjs"],
    }
    raw.update(kw)
    return raw


PRICING = {
    "prices": 3,
    "priceContexts": ["free ₺0", "pro ₺99/ay", "yillik ₺990 %20 indirim en populer"],
    "currencies": ["TL"],
    "text": "",
}
SIGNUP = {
    "groups": [
        {
            "kind": "form",
            "fields": [{"type": "email"}, {"type": "password"}],
            "buttons": ["Kayıt ol"],
        }
    ],
    "social": ["google"],
}


def test_build_anatomy_full_schema() -> None:
    a = build_anatomy(
        home_raw(),
        home_inspect={"prices": 0},
        pricing_inspect=PRICING,
        pricing_clicks=1,
        signup_url=f"{SITE}/signup",
        signup_inspect=SIGNUP,
        field_lcp_ms=2100,
    )
    assert a["sections"] == ["hero", "logos", "features", "testimonials", "faq", "final_cta"]
    assert a["trust"] == {
        "logo_band": True,
        "testimonials": 3,
        "user_count_claim": True,
        "security_badges": False,
        "company_info": True,
        "contact": True,
        "case_studies": False,
    }
    assert a["pricing"] == {
        "visible_on_home": False,
        "clicks_to_price": 1,
        "plans": 3,
        "free_tier": True,
        "annual_discount": True,
        "highlighted_plan": True,
        "currency": "TL",
    }
    assert a["signup"] == {
        "fields": 2,
        "required": 0,
        "social_login": ["google"],
        "card_required": False,
    }
    assert a["perf"]["field_data"] and a["perf"]["lcp_ms"] == 1500
    assert a["share"] == {"og_image": True, "og_image_ratio_ok": True}
    # Desen kopyalanır, içerik kopyalanmaz: anatomide örnek sitenin metni yok
    assert "kullanici" not in json.dumps(a) and "mersis" not in json.dumps(a)


def test_anatomy_unknowns() -> None:
    a = build_anatomy(home_raw(sections=[section(h1=True)]))
    assert a["signup"] is None and a["pricing"]["clicks_to_price"] is None
    assert a["share"]["og_image_ratio_ok"] is True


# --- karşılaştırma


def anatomy(**kw: Any) -> dict[str, Any]:
    base = build_anatomy(
        home_raw(),
        pricing_inspect=PRICING,
        pricing_clicks=1,
        signup_url=f"{SITE}/signup",
        signup_inspect=SIGNUP,
    )
    for path, value in kw.items():
        keys = path.split("__")
        target = base
        for k in keys[:-1]:
            target = target[k]
        target[keys[-1]] = value
    return base


def user_poor() -> dict[str, Any]:
    return anatomy(
        sections=["hero", "features"],
        trust__testimonials=0,
        trust__logo_band=False,
        pricing__clicks_to_price=3,
        page_inventory=["blog"],
        signup=None,
        perf__lcp_ms=5200,
    )


def test_compare_patterns_and_missing_pages() -> None:
    examples = [anatomy(), anatomy(), anatomy(trust__logo_band=False)]
    result = compare(user_poor(), examples)
    patterns = {p["key"]: p for p in result.patterns}
    assert patterns["testimonials"]["share"] == 1.0
    assert patterns["logo_band"]["have"] == 2 and patterns["logo_band"]["total"] == 3
    assert "price_one_click" in patterns and "faq_section" in patterns
    # Kullanıcının kayıt formu bilinmiyor → kayıt desenleri "sende yok" denmez
    assert "social_login" not in patterns and "short_signup" not in patterns
    assert {p["type"] for p in result.missing_pages} == {"pricing", "contact"}
    assert result.rebuild_sections[0] == "hero" and "testimonials" in result.rebuild_sections
    assert result.speed == {"method": "lab", "user_ms": 5200, "median_ms": 1500, "peers": 3}


def test_compare_false_positives() -> None:
    """Yanlış alarm: örneklerin azında olan desen (<%60) ve bilinmeyen değerler sayılmaz."""
    examples = [
        anatomy(trust__security_badges=True),
        anatomy(),
        anatomy(),
        anatomy(signup=None),
        anatomy(signup=None),
    ]
    result = compare(anatomy(), examples)
    assert result.patterns == []
    assert result.missing_pages == []
    # Sadece 3 örnekte kayıt formu biliniyor: payda 3
    row = next(r for r in result.matrix if r["key"] == "social_login")
    assert row["known"] == 3


def test_not_enough_examples() -> None:
    result = compare(user_poor(), [anatomy()])
    assert result.patterns == [] and result.matrix == [] and result.speed is None


def test_speed_never_mixes_field_and_lab() -> None:
    user = anatomy(perf__field_lcp_ms=3000)
    # Örneklerden sadece biri saha verisine sahip → laboratuvar ölçümüne düşülür
    peers = [anatomy(perf__field_lcp_ms=1000), anatomy(), anatomy()]
    assert speed(user, peers)["method"] == "lab"  # type: ignore[index]
    peers = [anatomy(perf__field_lcp_ms=1000), anatomy(perf__field_lcp_ms=1200)]
    assert speed(user, peers) == {
        "method": "field",
        "user_ms": 3000,
        "median_ms": 1100.0,
        "peers": 2,
    }


def test_ahead_areas() -> None:
    user = anatomy(perf__lcp_ms=600, trust__security_badges=True)
    user["signup"]["fields"] = 1
    examples = [
        anatomy(signup={"fields": 5, "required": 4, "social_login": [], "card_required": True})
        for _ in range(3)
    ]
    keys = {a["key"] for a in compare(user, examples).ahead}
    assert {"faster", "fewer_signup_fields", "security_badges"} <= keys


def test_rebuild_sections_order() -> None:
    ex = [
        {"sections": ["hero", "logos", "features", "faq"]},
        {"sections": ["hero", "logos", "testimonials", "faq"]},
        {"sections": ["hero", "features", "final_cta"]},
    ]
    assert rebuild_sections(ex) == ["hero", "logos", "features", "faq"]


# --- robots.txt ve önbellek


def test_robots_policy() -> None:
    body = "User-agent: LaunchCheckBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    blocked = parse_policy(SITE, 200, body)
    assert blocked.reachable and not blocked.allowed(f"{SITE}/")
    partial = parse_policy(SITE, 200, "User-agent: *\nDisallow: /pricing\n")
    assert partial.allowed(f"{SITE}/") and not partial.allowed(f"{SITE}/pricing")
    assert parse_policy(SITE, 404, "").allowed(f"{SITE}/x")  # robots.txt yok → izinli
    assert not parse_policy(SITE, 503, "").allowed(f"{SITE}/")  # RFC 9309: 5xx → yasak
    assert not parse_policy(SITE, None, "").allowed(f"{SITE}/")


def test_shot_store_roundtrip_and_traversal(tmp_path: Path) -> None:
    store = ShotStore(tmp_path, 1_000_000)
    shot = store.save("https://rakip.test", "mobile", webp(), 40, 30)
    assert shot is not None and shot.path.startswith("_benchmark/")
    assert store.read({"path": shot.path, "width": 40, "height": 30}) is not None
    assert store.read({"path": "../etc/passwd"}) is None
    assert store.read({"path": "_benchmark/../secret.webp"}) is None
    assert store.save("https://rakip.test", "mobile", b"not webp", 1, 1) is None
    assert store.save("https://rakip.test", "../../x", webp(), 1, 1) is None


def test_shot_store_sweep(tmp_path: Path) -> None:
    import os
    import time

    store = ShotStore(tmp_path, 1_000_000)
    old = store.save("https://old.test", "mobile", webp(), 1, 1)
    fresh = store.save("https://fresh.test", "mobile", webp(), 1, 1)
    ref = store.save("https://ref.test", "mobile", webp(), 1, 1, reference=True)
    assert old and fresh and ref
    past = time.time() - 2 * 86400
    old_dir = (tmp_path / old.path).parent
    os.utime(old_dir, (past, past))
    assert store.sweep(86400, 8 * 86400) == 1
    assert (
        not old_dir.exists() and (tmp_path / fresh.path).exists() and (tmp_path / ref.path).exists()
    )


async def test_anatomy_cache_roundtrip_and_failures() -> None:
    cache = AnatomyCache(FakeRedis(), 3600)
    await cache.put("https://rakip.test", {"anatomy": {"sections": []}})
    assert await cache.get("https://rakip.test") == {"anatomy": {"sections": []}}
    assert await cache.get("https://other.test") is None
    assert await AnatomyCache(None, 1).get("https://rakip.test") is None
    broken = AnatomyCache(FakeRedis(fail=True), 1)
    await broken.put("https://x.test", {})  # hata yutulur
    assert await broken.get("https://x.test") is None


def test_orphan_sweeper_skips_shared_cache(tmp_path: Path) -> None:
    import os
    import time

    store = ShotStore(tmp_path, 1_000_000)
    saved = store.save("https://rakip.test", "mobile", webp(), 1, 1)
    assert saved is not None
    past = time.time() - 7200
    os.utime(tmp_path / "_benchmark", (past, past))
    assert AssetStore(tmp_path, 1_000_000).sweep_orphans({}, 3600) == 0
    assert (tmp_path / saved.path).exists()


# --- modül


def crawl(origin: str, *, ok: bool = True, reason: str = "", **kw: Any) -> SiteCrawl:
    return SiteCrawl(
        origin=origin,
        ok=ok,
        reason=reason,
        home=kw.get("home") or home_raw(),
        home_inspect={"prices": 0},
        pricing_url=f"{origin}/pricing",
        pricing_inspect=PRICING,
        signup_url=f"{origin}/signup",
        signup_inspect=SIGNUP,
        shots={"mobile": (webp(), 40, 30), "desktop": (webp(), 40, 30)},
    )


def poor_home() -> dict[str, Any]:
    return home_raw(
        sections=[section(h1=True), section(heading="features", images=3)],
        links=[f"{SITE}/blog"],
        lcp=6000,
        claims=[],
        footer_text="",
    )


class Fakes:
    def __init__(
        self, blocked: set[str] | None = None, references: list[ReferenceExample] | None = None
    ) -> None:
        self.crawled: list[str] = []
        self.blocked = blocked or set()
        self.refs = references or []

    async def crawl(
        self, origins: list[str], policies: dict[str, RobotsPolicy]
    ) -> dict[str, SiteCrawl]:
        out = {}
        for origin in origins:
            self.crawled.append(origin)
            policy = policies.get(origin)
            if policy is not None and not policy.allowed(origin + "/"):
                out[origin] = SiteCrawl(origin=origin, reason="robots_blocked")
            elif origin == SITE:
                out[origin] = crawl(origin, home=poor_home())
            else:
                out[origin] = crawl(origin)
        return out

    async def robots(self, origin: str) -> RobotsPolicy:
        body = "User-agent: *\nDisallow: /\n" if origin in self.blocked else ""
        return parse_policy(origin, 200 if body else 404, body)

    async def field(self, origin: str) -> float | None:
        return None

    async def references(self, category: str) -> list[ReferenceExample]:
        return self.refs


def bench_module(
    fakes: Fakes, tmp_path: Path, cache: AnatomyCache | None = None
) -> BenchmarkModule:
    return BenchmarkModule(
        Settings(assets_dir=str(tmp_path)),
        crawl=fakes.crawl,
        robots=fakes.robots,
        field_lcp=fakes.field,
        references=fakes.references,
        cache=cache or AnatomyCache(FakeRedis(), 3600),
        shots=ShotStore(tmp_path, 1_000_000),
    )


def ctx_for(tmp_path: Path, **options: Any) -> DeepContext:
    return DeepContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        target_url=SITE,
        options={"competitors": [], "use_reference": True, **options},
        parent_meta={"has_login": True},
        events=MemoryEventSink(),
        assets=AssetStore(tmp_path, 1_000_000),
    )


def ref(origin: str) -> ReferenceExample:
    return ReferenceExample(
        origin,
        build_anatomy(home_raw(), pricing_inspect=PRICING, pricing_clicks=1),
        {"crux": True},
        {},
    )


async def test_module_end_to_end(tmp_path: Path) -> None:
    fakes = Fakes(
        blocked={"https://blocked.test"},
        references=[ref("https://ref1.test"), ref("https://ref2.test"), ref(SITE)],
    )
    ctx = ctx_for(
        tmp_path, competitors=["https://rakip.test", "https://blocked.test"], category="saas"
    )
    out = ModuleOutput()
    await bench_module(fakes, tmp_path).run(ctx, out)
    found = {f.check_id: f for f in out.findings}
    assert set(found) == BENCH_IDS
    data = ctx.benchmark
    assert data is not None and data.category == "saas" and data.category_source == "user"
    by_origin = {e.origin: e for e in data.examples}
    assert (
        by_origin["https://blocked.test"].reason == "robots_blocked"
        and not by_origin["https://blocked.test"].ok
    )
    assert SITE not in by_origin  # kendi sitesi referans listesinde olsa bile örnek değil
    assert [e.source for e in data.valid] == ["competitor", "reference", "reference"]
    # Kullanıcının sitesi örneklerle aynı yöntemle tarandı; robots politikası sadece rakipler için
    assert fakes.crawled[0] == SITE and "https://ref1.test" not in fakes.crawled
    assert found["web.benchmark.common_pattern_missing"].severity == Severity.MEDIUM
    assert found["web.benchmark.slower_than_peers"].evidence["ratio"] == 4.0
    meta = ctx.meta["benchmark"]
    assert meta["status"] == {"compare": "ok", "speed": "ok"}
    assert set(meta["user_assets"]) == {"mobile", "desktop"}
    # Yan yana ilk ekranlar: rakibin görüntüsü taramanın kendi asset'i olarak kopyalandı
    rakip = next(e for e in meta["examples"] if e["origin"] == "https://rakip.test")
    assert set(rakip["assets"]) == {"mobile", "desktop"}
    kinds = {a.kind for a in out.assets}
    assert {"benchmark_self_mobile", "benchmark_example_mobile"} <= kinds
    assert "deep.event.benchmark_started" in [e[1] for e in ctx.events.events]  # type: ignore[attr-defined]


async def test_cached_competitor_is_not_crawled_again(tmp_path: Path) -> None:
    cache = AnatomyCache(FakeRedis(), 3600)
    first = Fakes(references=[ref("https://ref1.test")])
    await bench_module(first, tmp_path, cache).run(
        ctx_for(tmp_path, competitors=["https://rakip.test"]), ModuleOutput()
    )
    assert "https://rakip.test" in first.crawled
    second = Fakes(references=[ref("https://ref1.test")])
    ctx = ctx_for(tmp_path, competitors=["https://rakip.test"], category="saas")
    await bench_module(second, tmp_path, cache).run(ctx, ModuleOutput())
    assert second.crawled == [SITE]
    assert ctx.benchmark is not None and ctx.benchmark.examples[0].cached


async def test_not_enough_examples_skips_checks(tmp_path: Path) -> None:
    ctx = ctx_for(tmp_path, competitors=["https://rakip.test"], use_reference=False)
    out = ModuleOutput()
    await bench_module(Fakes(), tmp_path).run(ctx, out)
    assert out.findings == []
    assert set(ctx.meta["checks_skipped"]) == BENCH_IDS
    assert ctx.meta["benchmark"]["status"]["compare"] == PartStatus.NOT_ENOUGH


async def test_no_category_means_no_references(tmp_path: Path) -> None:
    fakes = Fakes(references=[ref("https://ref1.test"), ref("https://ref2.test")])
    ctx = ctx_for(tmp_path)
    ctx.parent_meta = {}
    await bench_module(fakes, tmp_path).run(ctx, ModuleOutput())
    assert ctx.benchmark is not None and ctx.benchmark.category is None
    assert ctx.benchmark.examples == []


async def test_blocked_site_does_not_run(tmp_path: Path) -> None:
    ctx = ctx_for(tmp_path)
    ctx.meta["deep_blocked"] = "site_blocked_bot"
    with pytest.raises(BenchmarkBlockedError):
        await bench_module(Fakes(), tmp_path).run(ctx, ModuleOutput())


async def test_texts_render_with_params_in_both_languages(tmp_path: Path) -> None:
    fakes = Fakes(references=[ref("https://ref1.test"), ref("https://ref2.test")])
    out = ModuleOutput()
    await bench_module(fakes, tmp_path).run(ctx_for(tmp_path, category="saas"), out)
    registry = {c.id: c for c in load_builtin_checks().all()}
    assert {f.check_id for f in out.findings} == BENCH_IDS
    for lang in ("tr", "en"):
        for f in out.findings:
            params = registry[f.check_id].text_params(f.evidence, fragment_fn(f.check_id, lang))
            text = finding_text(f.check_id, lang, params)
            body = text.title + text.description
            assert "{" not in body and "pattern_" not in body and "page_" not in body, (
                f.check_id,
                lang,
                body,
            )


def test_benchmark_checks_are_phase2_only() -> None:
    registry = load_builtin_checks()
    ids = {c.id for c in registry.all() if c.module == "benchmark"}
    assert ids == BENCH_IDS
    assert not ids & {c.id for c in registry.for_target(TargetType.WEB, 1)}


async def test_benchmark_checks_reject_phase1_context() -> None:
    from tests.checks.helpers import make_ctx

    checks = [c for c in load_builtin_checks().all() if c.module == "benchmark"]
    result = await run_checks(make_ctx(), checks)
    assert sorted(result.failed) == sorted(BENCH_IDS)


# --- referans kütüphanesi


async def test_sync_reference_sites(sessionmaker: SM) -> None:
    async with sessionmaker() as s:
        s.add(ReferenceSite(category="saas", origin="https://removed.example"))
        await s.commit()
    added = await sync_reference_sites(sessionmaker)
    async with sessionmaker() as s:
        rows = (await s.scalars(select(ReferenceSite))).all()
    origins = {(r.category, r.origin) for r in rows}
    assert ("saas", "https://linear.app") in origins and (
        "saas",
        "https://removed.example",
    ) not in origins
    assert added == len(rows)
    assert await sync_reference_sites(sessionmaker) == 0


async def test_refresh_due_sites(sessionmaker: SM, tmp_path: Path) -> None:
    async with sessionmaker() as s:
        s.add_all(
            [
                ReferenceSite(category="saas", origin="https://ok.test"),
                ReferenceSite(category="saas", origin="https://blocked.test"),
                ReferenceSite(category="saas", origin="https://down.test"),
                ReferenceSite(
                    category="saas",
                    origin="https://fresh.test",
                    last_scanned_at=utcnow() - timedelta(days=1),
                ),
            ]
        )
        await s.commit()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def robots(origin: str) -> RobotsPolicy:
        return parse_policy(
            origin, 200, "User-agent: *\nDisallow: /\n" if "blocked" in origin else ""
        )

    async def crawl_site(origin: str, policy: RobotsPolicy) -> SiteCrawl:
        return crawl(origin, ok="down" not in origin, reason="unreachable")

    async def field(origin: str) -> float | None:
        return None

    s = Settings(deep_reference_sites_per_run=10, google_api_key="test-key")
    done = await refresh_due_sites(
        sessionmaker, s, crawl_site, robots, field, ShotStore(tmp_path, 10**6), sleep=fake_sleep
    )
    assert set(done) == {"https://ok.test", "https://blocked.test", "https://down.test"}
    assert sleeps == [s.deep_reference_gap_s] * 2  # siteler arası nezaket aralığı
    async with sessionmaker() as session:
        rows = {r.origin: r for r in (await session.scalars(select(ReferenceSite))).all()}
    assert rows["https://ok.test"].status == ReferenceSiteStatus.NO_FIELD_DATA  # CrUX'ta yok
    assert rows["https://ok.test"].anatomy["sections"][0] == "hero"
    assert set(rows["https://ok.test"].anatomy["_shots"]) == {"mobile", "desktop"}
    assert rows["https://blocked.test"].status == ReferenceSiteStatus.ROBOTS_BLOCKED
    assert rows["https://down.test"].status == ReferenceSiteStatus.FAILED
    assert (
        rows["https://fresh.test"].last_error is None and rows["https://fresh.test"].anatomy is None
    )


async def test_majestic_ranks_stream() -> None:
    async def lines():  # type: ignore[no-untyped-def]
        for line in [
            "GlobalRank,TldRank,Domain,TLD",
            "1,1,google.com,com",
            "57,40,linear.app,app",
            "99,2,karaca.com.tr,tr",
            "100,3,notion.com,com",
        ]:
            yield line

    assert await majestic_ranks(lines(), {"linear.app", "notion.com"}) == {
        "linear.app": 57,
        "notion.com": 100,
    }


async def test_refresh_popularity(sessionmaker: SM) -> None:
    async with sessionmaker() as s:
        s.add(
            ReferenceSite(
                category="saas", origin="https://www.linear.app", popularity={"crux": True}
            )
        )
        await s.commit()

    async def lines():  # type: ignore[no-untyped-def]
        yield "GlobalRank,TldRank,Domain,TLD"
        yield "57,40,linear.app,app"

    assert await refresh_popularity(sessionmaker, lines) == 1
    async with sessionmaker() as s:
        row = (await s.scalars(select(ReferenceSite))).one()
    assert row.popularity == {"crux": True, "majestic_rank": 57}


# --- API


async def test_categories_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/api/benchmark/categories?lang=en")
    assert resp.status_code == 200
    cats = resp.json()["categories"]
    assert {"key": "saas", "label": "SaaS / web app"} in cats
    assert "sites" not in json.dumps(cats) and "linear" not in json.dumps(cats)


async def test_deep_scan_category_option(client: AsyncClient, sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker)
    bad = await client.post(f"/api/scans/{parent}/deep", json={"category": "casino"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_category"
    ok = await client.post(f"/api/scans/{parent}/deep", json={"category": "saas"})
    assert ok.status_code == 202, ok.text
    async with sessionmaker() as s:
        scan = await s.get(Scan, __import__("uuid").UUID(ok.json()["scan_id"]))
    assert scan is not None and scan.options["category"] == "saas"
