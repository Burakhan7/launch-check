"""Gizlilik ve tedarik zinciri modülü (P6, PHASE2 §6.5) — tarayıcısız testler.

Tarayıcının topladığı ham kayıtlar (`PrivacyRaw`) sentetik verilir; takipçi sınıflandırması,
retire.js eşleşmesi, axe gruplaması, check'ler ve modülün parça durumları burada doğrulanır.
Gerçek Chromium toplaması `tests/deep/browser/test_privacy_collect.py`'de (`-m browser`).
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.collectors.fetcher import SafeFetcher
from launchcheck.config import Settings
from launchcheck.deep.browser.consent import cmp_from_scripts, js_options, load_cmps
from launchcheck.deep.browser.privacy import (
    CookieRecord,
    PrivacyInput,
    PrivacyRaw,
    RequestRecord,
    consent_denied,
)
from launchcheck.deep.context import DeepContext, ParentFinding
from launchcheck.deep.events import MemoryEventSink
from launchcheck.deep.modules import ModuleOutput
from launchcheck.deep.privacy_data import (
    AxeViolation,
    ConsentInfo,
    PartStatus,
    PhaseResult,
    PrivacyData,
    RejectState,
    TrackerHit,
)
from launchcheck.deep.privacy_module import PrivacyBlockedError, PrivacyModule, axe_targets
from launchcheck.deep.render_data import RenderData, RenderedPage
from launchcheck.deep.supply.analysis import (
    classify_phase,
    load_current_retire,
    risky_scripts,
    scan_libraries,
)
from launchcheck.deep.supply.axe import group_violations
from launchcheck.deep.supply.retire import (
    RetireDb,
    clean_version,
    is_at_or_above,
    required_literal,
)
from launchcheck.deep.supply.retire_update import update_retire_db
from launchcheck.deep.supply.trackers import TrackerDb, load_curated, parse_easyprivacy
from launchcheck.i18n import finding_text, fragment_fn
from launchcheck.security.ssrf import UrlGuard
from tests.conftest import FakeResolver

FIXTURES = Path(__file__).parent.parent / "fixtures" / "deep" / "privacy"
SITE = "https://www.acme.test"
PRIVACY_IDS = {
    "web.privacy.trackers_before_consent",
    "web.privacy.trackers_after_reject",
    "web.privacy.consent_banner_missing",
    "web.security.vulnerable_js_library",
    "web.security.risky_third_party_script",
    "web.a11y.axe_violations",
}


def tracker_db() -> TrackerDb:
    return TrackerDb(
        load_curated(), parse_easyprivacy((FIXTURES / "easyprivacy.txt").read_text("utf-8"))
    )


def retire_db() -> RetireDb:
    db = RetireDb.load(FIXTURES / "jsrepository.json")
    assert db is not None
    return db


def req(url: str, kind: str = "script", denied: bool = False) -> RequestRecord:
    host = url.split("://", 1)[1].split("/", 1)[0]
    return RequestRecord(url, host, kind, denied)


# --- takipçi listesi


def test_easyprivacy_takes_only_plain_domain_rules() -> None:
    domains = parse_easyprivacy((FIXTURES / "easyprivacy.txt").read_text("utf-8"))
    assert domains == {
        "track.test",
        "pixel.example-ads.test",
        "google-analytics.com",
        "plausible.io",
    }


def test_curated_list_wins_over_easyprivacy() -> None:
    db = tracker_db()
    assert db.match("www.google-analytics.com").name == "Google Analytics"  # type: ignore[union-attr]
    assert db.match("plausible.io").category == "exempt"  # type: ignore[union-attr]
    assert db.match("cdn.track.test").name == "track.test"  # type: ignore[union-attr]
    assert db.match("nottrack.test") is None
    assert db.match("googletagmanager.com").category == "tag_manager"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("name", "tracker"),
    [
        ("_ga", "Google Analytics"),
        ("_ga_ABC123", "Google Analytics"),
        ("_fbp", "Meta Pixel"),
        ("_hjSessionUser_1", "Hotjar"),
        ("_ym_uid", "Yandex Metrica"),
        ("ph_phc_x_posthog", "PostHog"),
        ("session", None),
        ("cookie_consent", None),
        ("_gallery_pref", None),  # "_ga" ile başlıyor ama kalıp "_ga_*" değil
    ],
)
def test_cookie_patterns(name: str, tracker: str | None) -> None:
    hit = tracker_db().cookie_tracker(name)
    assert (hit.name if hit else None) == tracker


def test_curated_yaml_is_well_formed() -> None:
    items = load_curated()
    assert len({t.name for t in items}) == len(items)
    assert all(t.domains for t in items)
    assert {t.category for t in items} <= {
        "analytics",
        "ads",
        "session_recording",
        "social",
        "tag_manager",
        "exempt",
    }


# --- aşama sınıflandırması


def test_classify_phase_counts_trackers_and_cookies() -> None:
    requests = [
        req(f"{SITE}/app.js"),  # birinci taraf
        req("https://cdn.acme.test/lib.js"),  # aynı kayıtlı alan adı
        req("https://www.googletagmanager.com/gtm.js"),  # sadece yükleyici
        req("https://www.google-analytics.com/g/collect", "ping", denied=True),  # Consent Mode
        req("https://connect.facebook.net/en_US/fbevents.js"),
        req("https://www.facebook.com/tr/", "image"),
        req("https://plausible.io/api/event", "xhr"),  # çerezsiz → takip değil
        req("https://track.test/collect", "xhr"),
    ]
    cookies = [
        CookieRecord("_ga", "acme.test"),
        CookieRecord("_fbp", "acme.test"),
        CookieRecord("cart", "acme.test"),
        CookieRecord("uid", "track.test"),  # üçüncü taraf, listedeki alan adı
    ]
    result = classify_phase(requests, cookies, tracker_db(), "acme.test")
    hits = {t.name: t for t in result.trackers}
    assert set(hits) == {"Meta Pixel", "Google Analytics", "track.test"}
    assert hits["Meta Pixel"].requests == 2 and hits["Meta Pixel"].cookies == ("_fbp",)
    assert hits["Google Analytics"].requests == 0 and hits["Google Analytics"].cookies == ("_ga",)
    assert result.consent_mode_pings == 1
    assert result.tag_managers == ["Google Tag Manager"]
    assert result.requests == len(requests)


def test_classify_phase_false_positives() -> None:
    """GTM yükleyicisi, Consent Mode reddedildi ping'i, çerezsiz ölçüm ve hata izleme tek başına
    takip sayılmaz."""
    requests = [
        req("https://www.googletagmanager.com/gtag/js"),
        req("https://region1.google-analytics.com/g/collect", "ping", denied=True),
        req("https://plausible.io/js/script.js"),
        req("https://static.cloudflareinsights.com/beacon.min.js"),
        req("https://o1.ingest.sentry.io/api/1/envelope/", "xhr"),
        req("https://va.vercel-scripts.com/v1/script.js"),
    ]
    result = classify_phase(
        requests, [CookieRecord("theme", "acme.test")], tracker_db(), "acme.test"
    )
    assert not result.tracking


@pytest.mark.parametrize(
    ("url", "denied"),
    [
        ("https://www.google-analytics.com/g/collect?v=2&gcs=G100&tid=G-X", True),
        ("https://www.google-analytics.com/g/collect?v=2&gcs=G110", True),  # analytics reddedildi
        ("https://www.google-analytics.com/g/collect?v=2&gcs=G111", False),
        ("https://googleads.g.doubleclick.net/pagead/x?gcs=G101", True),  # reklam reddedildi
        ("https://www.google-analytics.com/g/collect?v=2", False),
        ("https://track.test/collect?gcs=G100", False),  # Google değil
    ],
)
def test_consent_mode_denied(url: str, denied: bool) -> None:
    assert consent_denied(url) is denied


# --- retire.js


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("3.5.0", "3.5.0", True),
        ("3.5.1", "3.5.0", True),
        ("3.4.9", "3.5.0", False),
        ("1.10.0", "1.9.0", True),
        ("3.0.0", "3.0.0-beta1", True),
        ("3.0.0-beta1", "3.0.0", False),
        ("1.12.4", "1.12", True),
    ],
)
def test_version_comparison(a: str, b: str, expected: bool) -> None:
    assert is_at_or_above(a, b) is expected


def test_required_literal_is_safe() -> None:
    assert required_literal(r"/\*!? jQuery v()") == " jQuery v"
    assert required_literal(r"a|b") is None  # üst düzey alternatif: ön filtre yok
    assert required_literal(r"(?:Lo-Dash|lodash)\s+v()") is None
    assert required_literal(r"Bootstrap v()") == "Bootstrap v"
    assert required_literal(r"abcde?f") == "abcd"  # isteğe bağlı karakter düşer


def test_clean_version() -> None:
    assert clean_version("1.8.3.min") == "1.8.3"
    assert clean_version("3.4.1-slim.min") == "3.4.1"
    assert clean_version("4.0.0-beta.2") == "4.0.0-beta.2"


def test_detect_by_uri_filename_and_content() -> None:
    db = retire_db()
    by_file = db.detect("https://code.jquery.com/jquery-1.8.3.min.js")
    assert [(d.library, d.version, d.how) for d in by_file] == [("jquery", "1.8.3", "filename")]
    by_uri = db.detect(
        "https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/3.3.7/js/bootstrap.min.js"
    )
    assert [(d.library, d.version) for d in by_uri] == [("bootstrap", "3.3.7")]
    bundle = "var x=1;" * 1000 + "/*! jQuery v2.2.4 | (c) jQuery Foundation */" + "y()" * 1000
    assert [
        (d.library, d.version) for d in db.detect("https://a.test/assets/index-4f.js", bundle)
    ] == [("jquery", "2.2.4")]


def test_detect_false_positives() -> None:
    db = retire_db()
    # Güncel sürüm: tespit edilir ama açığı yok → bulgu değil
    assert scan_libraries(db, {"https://code.jquery.com/jquery-3.7.1.min.js": None}) == []
    # "jquery" kelimesi geçen ama kütüphane olmayan kod / adres
    text = "// uses jQuery-like selectors; see jquery docs v2"
    assert db.detect("https://a.test/js/my-jquery-helpers.js", text) == []


def test_scan_libraries_dedupes_and_orders() -> None:
    found = scan_libraries(
        retire_db(),
        {
            "https://a.test/jquery-1.8.3.min.js": None,
            "https://b.test/js/jquery-1.8.3.js": None,
            "https://cdn.test/3.3.7/js/bootstrap.min.js": None,
        },
    )
    assert [(f.library, f.version) for f in found] == [("jquery", "1.8.3"), ("bootstrap", "3.3.7")]
    jq = found[0]
    assert jq.severity == "medium"
    # Kanıtta en fazla 5 açık (en önemliler önce)
    assert len(jq.vulnerabilities) == 5
    assert any("CVE-2012-6708" in v["cves"] for v in jq.vulnerabilities)
    assert all(v["fixed_in"] for v in jq.vulnerabilities)


def test_retire_db_rejects_unknown_format(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "retire"}', "utf-8")
    assert RetireDb.load(bad) is None
    bad.write_text("not json", "utf-8")
    assert RetireDb.load(bad) is None


def test_load_current_retire_prefers_updated_copy(tmp_path: Path) -> None:
    image = tmp_path / "image.json"
    image.write_text((FIXTURES / "jsrepository.json").read_text("utf-8"), "utf-8")
    update_dir = tmp_path / "upd"
    s = Settings(deep_retire_db_path=str(image), deep_retire_update_dir=str(update_dir))
    db = load_current_retire(s)
    assert db is not None and len(db.libraries) == 5
    update_dir.mkdir()
    (update_dir / "jsrepository.json").write_text(
        json.dumps({"jquery": json.loads(image.read_text("utf-8"))["jquery"]}), "utf-8"
    )
    newer = load_current_retire(s)
    assert newer is not None and [lib.name for lib in newer.libraries] == ["jquery"]
    assert (
        load_current_retire(
            Settings(deep_retire_db_path="", deep_retire_update_dir=str(tmp_path / "x"))
        )
        is None
    )


def _big_db() -> dict[str, Any]:
    real = json.loads((FIXTURES / "jsrepository.json").read_text("utf-8"))
    data = dict(real)
    for i in range(40):
        data[f"lib{i}"] = {
            "vulnerabilities": [],
            "extractors": {"filename": [f"lib{i}-(§§version§§).js"]},
        }
    return data


@pytest.mark.parametrize(
    ("response", "ok"),
    [
        (httpx.Response(200, json=_big_db()), True),
        (
            httpx.Response(
                200, json=json.loads((FIXTURES / "jsrepository.json").read_text("utf-8"))
            ),
            False,
        ),  # eksik
        (httpx.Response(200, text="<html>not json</html>"), False),
        (httpx.Response(503), False),
        (httpx.ConnectTimeout("timeout"), False),
    ],
)
async def test_retire_update(tmp_path: Path, response: Any, ok: bool) -> None:
    s = Settings(deep_retire_update_dir=str(tmp_path / "upd"))
    with respx.mock() as router:
        route = router.get(s.deep_retire_update_url)
        if isinstance(response, Exception):
            route.mock(side_effect=response)
        else:
            route.mock(return_value=response)
        fetcher = SafeFetcher(s, UrlGuard(FakeResolver()))  # type: ignore[arg-type]
        try:
            assert await update_retire_db(s, fetcher) is ok
        finally:
            await fetcher.aclose()
    written = tmp_path / "upd" / "jsrepository.json"
    assert written.is_file() is ok
    assert not list((tmp_path / "upd").glob("*.tmp")) if (tmp_path / "upd").exists() else True


# --- riskli CDN'ler ve axe


def test_risky_scripts_match_domain_and_subdomains_only() -> None:
    domains = Settings().deep_risky_script_domains
    found = risky_scripts(
        [
            "https://cdn.polyfill.io/v3/polyfill.min.js",
            "https://polyfill.io/v3/polyfill.js",
            "https://cdnjs.cloudflare.com/polyfill/v3/polyfill.min.js",  # güvenilir ayna
            "https://polyfill.io.example.com/x.js",  # benzer ad, farklı alan adı
            "https://mypolyfill.io/x.js",
            "https://cdn.bootcdn.net/ajax/libs/jquery/3.6.0/jquery.min.js",
        ],
        domains,
    )
    assert [(r.url.split("/")[2], r.domain) for r in found] == [
        ("cdn.polyfill.io", "polyfill.io"),
        ("polyfill.io", "polyfill.io"),
        ("cdn.bootcdn.net", "bootcdn.net"),
    ]
    assert all(r.source for r in found)


def test_group_axe_violations() -> None:
    runs = [
        (
            "/",
            "desktop",
            [
                {
                    "id": "color-contrast",
                    "impact": "serious",
                    "help": "Kontrast",
                    "helpUrl": "https://dequeuniversity.com/rules/axe/4.14/color-contrast",
                    "count": 4,
                    "targets": [".btn"],
                },
                {"id": "region", "impact": "moderate", "count": 9, "targets": []},
            ],
        ),
        (
            "/",
            "mobile",
            [
                {
                    "id": "color-contrast",
                    "impact": "serious",
                    "count": 2,
                    "targets": [".btn", ".nav a"],
                }
            ],
        ),
        (
            "/pricing",
            "mobile",
            [
                {
                    "id": "button-name",
                    "impact": "critical",
                    "count": 1,
                    "targets": ["button.icon"],
                    "helpUrl": "javascript:alert(1)",
                }
            ],
        ),
    ]
    grouped = group_violations(runs)
    assert [v.rule for v in grouped] == [
        "button-name",
        "color-contrast",
    ]  # kritik önce; moderate yok
    contrast = grouped[1]
    assert contrast.count == 6 and contrast.viewports == ("desktop", "mobile")
    assert contrast.targets == (".btn", ".nav a")
    assert grouped[0].help_url == ""  # https olmayan link kanıta girmez


# --- check'ler


def privacy_ctx(data: PrivacyData, parent: list[ParentFinding] | None = None) -> DeepContext:
    ctx = DeepContext(
        target_type=TargetType.WEB, target_label=SITE, target_url=SITE, parent_findings=parent or []
    )
    ctx.privacy = data
    return ctx


async def checks_on(ctx: DeepContext) -> dict[str, Any]:
    checks = [c for c in load_builtin_checks().all() if c.module == "privacy"]
    result = await run_checks(ctx, checks)
    assert result.failed == []
    return {f.check_id: f for f in result.findings}


GA = TrackerHit("Google Analytics", "google-analytics.com", "analytics", 3, ("_ga",))


async def test_trackers_before_consent_vs_banner_missing_are_exclusive() -> None:
    data = PrivacyData(site=SITE, before=PhaseResult(trackers=[GA]))
    data.consent = ConsentInfo(cmp="CookieYes", banner=True, reject=RejectState.CLICKED)
    found = await checks_on(privacy_ctx(data))
    assert set(found) == {"web.privacy.trackers_before_consent"}
    data.consent = ConsentInfo()
    found = await checks_on(privacy_ctx(data))
    assert set(found) == {"web.privacy.consent_banner_missing"}
    assert found["web.privacy.consent_banner_missing"].severity == Severity.HIGH


async def test_cmp_script_without_visible_banner_counts_as_mechanism() -> None:
    data = PrivacyData(site=SITE, before=PhaseResult(trackers=[GA]))
    data.consent = ConsentInfo(cmp="OneTrust", banner=False)
    found = await checks_on(privacy_ctx(data))
    assert set(found) == {"web.privacy.trackers_before_consent"}
    assert found["web.privacy.trackers_before_consent"].evidence["banner_visible"] is False


async def test_trackers_after_reject() -> None:
    data = PrivacyData(site=SITE, before=PhaseResult(), after=PhaseResult(trackers=[GA]))
    data.consent = ConsentInfo(
        cmp=None, banner=True, reject=RejectState.CLICKED_SECOND_LAYER, reject_label="tumunu reddet"
    )
    found = await checks_on(privacy_ctx(data))
    assert set(found) == {"web.privacy.trackers_after_reject"}
    evidence = found["web.privacy.trackers_after_reject"].evidence
    assert evidence["second_layer"] is True and evidence["trackers"][0]["cookies"] == ["_ga"]


async def test_no_tracking_no_findings() -> None:
    """Yanlış alarm: takip yok (banner olsun olmasın), sadece GTM / Consent Mode ping'i var."""
    for consent in (ConsentInfo(), ConsentInfo(banner=True, reject=RejectState.CLICKED)):
        data = PrivacyData(
            site=SITE,
            before=PhaseResult(consent_mode_pings=4, tag_managers=["Google Tag Manager"]),
            after=PhaseResult(),
            consent=consent,
        )
        assert await checks_on(privacy_ctx(data)) == {}


async def test_library_severity_is_capped_at_high() -> None:
    from launchcheck.deep.privacy_data import LibraryFinding

    data = PrivacyData(site=SITE)
    data.libraries = [
        LibraryFinding("lodash", "4.17.4", "https://a.test/lodash.js", "critical", ()),
        LibraryFinding("jquery", "1.8.3", "https://a.test/jq.js", "medium", ()),
    ]
    found = await checks_on(privacy_ctx(data))
    assert found["web.security.vulnerable_js_library"].severity == Severity.HIGH


async def test_axe_rules_covered_by_phase1_are_not_double_counted() -> None:
    def violation(rule: str, impact: str) -> AxeViolation:
        return AxeViolation(rule, impact, "h", "", 1, ("/",), ("mobile",), ())

    data = PrivacyData(site=SITE)
    data.axe = [violation("image-alt", "critical"), violation("color-contrast", "serious")]
    parent = [ParentFinding("web.a11y.img_missing_alt", Severity.LOW, Category.CONVERSION, {})]
    found = await checks_on(privacy_ctx(data, parent))
    axe = found["web.a11y.axe_violations"]
    assert axe.severity == Severity.MEDIUM  # kritik olan Faz 1'de zaten var
    assert [r["rule"] for r in axe.evidence["rules"]] == ["color-contrast"]
    assert axe.evidence["covered_by_phase1"] == ["image-alt"]
    data.axe = [violation("image-alt", "critical")]
    assert await checks_on(privacy_ctx(data, parent)) == {}
    found = await checks_on(privacy_ctx(data))
    assert found["web.a11y.axe_violations"].severity == Severity.HIGH


def test_privacy_checks_are_phase2_only() -> None:
    registry = load_builtin_checks()
    ids = {c.id for c in registry.all() if c.module == "privacy"}
    assert ids == PRIVACY_IDS
    assert not ids & {c.id for c in registry.for_target(TargetType.WEB, 1)}


async def test_privacy_checks_reject_phase1_context() -> None:
    from tests.checks.helpers import make_ctx

    checks = [c for c in load_builtin_checks().all() if c.module == "privacy"]
    result = await run_checks(make_ctx(), checks)
    assert sorted(result.failed) == sorted(PRIVACY_IDS)


# --- modül (sahte tarayıcı toplaması)


def raw_for(**kw: Any) -> PrivacyRaw:
    base: dict[str, Any] = {
        "consent": ConsentInfo(),
        "before_requests": [],
        "before_cookies": [],
    }
    return PrivacyRaw(**{**base, **kw})


def mod(raw: PrivacyRaw, tmp_path: Path, axe: bool = True, retire: bool = True) -> PrivacyModule:
    axe_file = tmp_path / "axe.min.js"
    axe_file.write_text("window.axe={run:async()=>({violations:[]})};", "utf-8")
    s = Settings(
        deep_tracker_list_path=str(FIXTURES / "easyprivacy.txt"),
        deep_retire_db_path=str(FIXTURES / "jsrepository.json") if retire else "",
        deep_retire_update_dir=str(tmp_path / "none"),
        deep_axe_path=str(axe_file) if axe else "",
    )
    seen: list[PrivacyInput] = []

    async def collect(ctx: DeepContext, data_in: PrivacyInput) -> PrivacyRaw:
        seen.append(data_in)
        return raw

    module = PrivacyModule(s, collect=collect)
    module.seen = seen  # type: ignore[attr-defined]
    return module


def render_ctx() -> DeepContext:
    home = RenderedPage(
        url=f"{SITE}/",
        final_url=f"{SITE}/",
        status=200,
        priority_links=[f"{SITE}/pricing", f"{SITE}/about", f"{SITE}/broken"],
    )
    pages = [
        home,
        RenderedPage(url=f"{SITE}/pricing", final_url=f"{SITE}/pricing", status=200),
        RenderedPage(url=f"{SITE}/about", final_url=f"{SITE}/about", status=200),
        RenderedPage(url=f"{SITE}/broken", final_url=f"{SITE}/broken", status=404),
    ]
    ctx = DeepContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        target_url=SITE,
        render=RenderData(pages=pages),
        events=MemoryEventSink(),
    )
    ctx.key_urls = [f"{SITE}/pricing"]
    return ctx


async def test_module_end_to_end_with_fake_browser(tmp_path: Path) -> None:
    raw = raw_for(
        consent=ConsentInfo(banner=True, reject=RejectState.CLICKED, reject_label="reddet"),
        before_requests=[req("https://www.google-analytics.com/g/collect?v=2", "ping")],
        before_cookies=[CookieRecord("_ga", "acme.test")],
        after_requests=[req("https://track.test/collect", "xhr")],
        after_cookies=[CookieRecord("consent", "acme.test")],
        scripts={
            "https://code.jquery.com/jquery-1.8.3.min.js": None,
            "https://cdn.polyfill.io/v3/polyfill.min.js": None,
        },
        axe_runs=[("/", "desktop", [{"id": "button-name", "impact": "critical", "count": 2}])],
    )
    module = mod(raw, tmp_path)
    ctx = render_ctx()
    out = ModuleOutput()
    await module.run(ctx, out)
    found = {f.check_id: f for f in out.findings}
    assert set(found) == PRIVACY_IDS - {"web.privacy.consent_banner_missing"}
    assert found["web.security.risky_third_party_script"].severity == Severity.CRITICAL
    assert ctx.privacy is not None and all(ctx.privacy.ok(p) for p in ctx.privacy.status)
    assert "checks_skipped" not in ctx.meta
    assert ctx.meta["privacy"]["trackers_after"] == ["track.test"]
    # axe: ana sayfa + kilit sayfa + menü linkleri (404 veren sayfa hariç)
    assert module.seen[0].axe_urls == [f"{SITE}/", f"{SITE}/pricing", f"{SITE}/about"]  # type: ignore[attr-defined]


async def test_module_skips_parts_without_data(tmp_path: Path) -> None:
    """Banner yok → "Reddet" denenemez; retire DB ve axe dosyası yok → ilgili check'ler atlanır
    ("geçti" sayılmaz)."""
    module = mod(raw_for(), tmp_path, axe=False, retire=False)
    ctx = render_ctx()
    await module.run(ctx, ModuleOutput())
    assert ctx.privacy is not None
    assert ctx.privacy.status["reject"] == PartStatus.NOT_APPLICABLE
    assert set(ctx.meta["checks_skipped"]) == {
        "web.privacy.trackers_after_reject",
        "web.security.vulnerable_js_library",
        "web.a11y.axe_violations",
    }


async def test_module_does_not_run_on_blocked_site(tmp_path: Path) -> None:
    ctx = render_ctx()
    ctx.meta["deep_blocked"] = "site_blocked_bot"
    with pytest.raises(PrivacyBlockedError):
        await mod(raw_for(), tmp_path).run(ctx, ModuleOutput())


async def test_module_uses_phase1_bundles_for_library_scan(tmp_path: Path) -> None:
    from launchcheck.collectors.web_collector import ScriptFile, WebContext

    ctx = render_ctx()
    ctx.base = WebContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        scripts=[ScriptFile(f"{SITE}/assets/chunk-9a.js", "/*! jQuery v1.12.4 | (c) jQuery */")],
    )
    out = ModuleOutput()
    await mod(raw_for(), tmp_path).run(ctx, out)
    libs = next(f for f in out.findings if f.check_id == "web.security.vulnerable_js_library")
    assert libs.evidence["libraries"][0]["version"] == "1.12.4"


def test_axe_targets_without_render() -> None:
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, target_url=SITE)
    assert axe_targets(ctx, f"{SITE}/", 5) == [f"{SITE}/"]


# --- banner yardımcıları


def test_cmp_list_and_js_options() -> None:
    cmps = load_cmps()
    assert len({c.name for c in cmps}) == len(cmps) and all(c.banner for c in cmps)
    options = js_options("reject")
    assert options["target"] == "reject"
    assert "reddet*" in options["words"]["reject"] and "reject*" in options["words"]["reject"]
    assert cmp_from_scripts(["https://cdn-cookieyes.com/client_data/x/script.js"]) == "CookieYes"
    assert cmp_from_scripts(["https://a.test/app.js"]) is None


async def test_texts_render_with_params_in_both_languages(tmp_path: Path) -> None:
    raw = raw_for(
        consent=ConsentInfo(banner=True, reject=RejectState.CLICKED, reject_label="reddet"),
        before_cookies=[CookieRecord("_ga", "acme.test")],
        after_requests=[req("https://track.test/collect", "xhr")],
        scripts={
            "https://code.jquery.com/jquery-1.8.3.min.js": None,
            "https://polyfill.io/v3/p.js": None,
        },
        axe_runs=[("/", "desktop", [{"id": "button-name", "impact": "critical", "count": 2}])],
    )
    out = ModuleOutput()
    await mod(raw, tmp_path).run(render_ctx(), out)
    registry = {c.id: c for c in load_builtin_checks().all()}
    assert len(out.findings) == 5
    for lang in ("tr", "en"):
        for finding in out.findings:
            check = registry[finding.check_id]
            params = check.text_params(finding.evidence, fragment_fn(finding.check_id, lang))
            text = finding_text(finding.check_id, lang, params)
            assert "{" not in text.title + text.description, (finding.check_id, lang)
