"""Hız modülü: Lighthouse rapor ayrıştırma (kayıtlı gerçek raporlar), filmstrip, CrUX istemcisi
(respx), check'ler ve modül akışı (ölçüm ve saha verisi sahte).

Kayıtlı raporlar `tests/deep/record_lighthouse.py` ile üç farklı yapıdaki yerel siteden alındı:
heavy_spa (yoğun SPA), plain_clean (düz HTML, sorunsuz), wp_like (WordPress benzeri).
"""

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.api.report import _passed
from launchcheck.checks.base import Severity, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.config import Settings
from launchcheck.db.models import FindingRow, Scan
from launchcheck.deep.assets import AssetStore, is_webp
from launchcheck.deep.context import DeepContext
from launchcheck.deep.events import MemoryEventSink
from launchcheck.deep.modules import ModuleOutput
from launchcheck.deep.orchestrator import DeepOrchestrator, OrchestratorConfig
from launchcheck.deep.perf.crux import API_KEY_HEADER, CruxClient
from launchcheck.deep.perf.filmstrip import blank_until, content_ratio, strip_webp
from launchcheck.deep.perf.lighthouse import (
    LighthouseConfig,
    LighthouseError,
    _child_env,
    lighthouse_args,
    parse_report,
)
from launchcheck.deep.perf_data import (
    DESKTOP,
    MOBILE,
    FieldData,
    FieldStatus,
    LighthouseRun,
    PerfData,
    ResourceItem,
    ThirdParty,
)
from launchcheck.deep.perf_module import PerfModule, PerfUnavailableError, summary
from launchcheck.i18n import finding_text
from tests.deep.helpers import add_deep, add_parent

LH = Path(__file__).parent.parent / "fixtures" / "deep" / "lighthouse"
SITE = "https://example.com"
HOME = SITE + "/"
CRUX = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
PERF_IDS = {c.id for c in load_builtin_checks().all() if c.module == "perf"}


def report(name: str) -> dict[str, Any]:
    return json.loads((LH / f"{name}.json").read_text("utf-8"))


def run_of(name: str, url: str = HOME, form_factor: str = MOBILE) -> LighthouseRun:
    return parse_report(report(name), url, form_factor, Settings().deep_perf_blank_content_ratio)


def ctx_with(data: PerfData, **meta: Any) -> DeepContext:
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, target_url=SITE, perf=data)
    ctx.meta.update(meta)
    return ctx


async def perf_findings(data: PerfData, **meta: Any) -> dict[str, Any]:
    checks = [c for c in load_builtin_checks().all() if c.module == "perf"]
    result = await run_checks(ctx_with(data, **meta), checks)
    assert result.failed == []
    return {f.check_id: f for f in result.findings}


def data_of(name: str, runs: int = 3, field: FieldData | None = None) -> PerfData:
    return PerfData(
        runs=[run_of(name) for _ in range(runs)],
        field=field,
        field_status=FieldStatus.OK if field else FieldStatus.NO_DATA,
        home_url=HOME,
    )


# --- rapor ayrıştırma


def test_parse_heavy_spa_report() -> None:
    run = run_of("heavy_spa")
    assert run.lcp_ms and run.lcp_ms > 4000
    assert run.cls == 0
    # İçerik 4 sn'lik zamanlayıcıyla geliyor: simüle FCP bunu görmez, filmstrip görür
    assert run.fcp_ms and run.fcp_ms < 1000
    assert run.blank_ms and run.blank_ms > 3000
    assert run.unused_js_bytes > 500 * 1024 and run.total_js_bytes >= run.unused_js_bytes
    assert any(i.third_party for i in run.unused_js) and any(
        not i.third_party for i in run.unused_js
    )
    hero = run.images[0]
    assert hero.url.endswith("/hero.png") and {"format", "oversized"} <= set(hero.reasons)
    assert hero.natural == (1600, 1200) and hero.displayed and hero.displayed[0] < 1600
    assert len(run.unsized_images) >= 3
    assert {u.url.rsplit("/", 1)[-1] for u in run.uncompressed} >= {"widget.js", "index-a1b2c3.js"}
    assert all("cdn.test" not in c.url for c in run.short_cache)  # sadece birinci taraf
    assert run.third_parties[0].entity == "cdn.test"
    assert run.third_parties[0].transfer_bytes > 500 * 1024
    audits = [o.audit for o in run.lcp_opportunities]
    assert "image-delivery-insight" in audits and "cache-insight" not in audits
    assert run.frames and all(isinstance(j, bytes) for _, j in run.frames)
    assert run.scores["performance"] < 0.9


def test_parse_clean_and_wp_reports() -> None:
    clean = run_of("plain_clean")
    assert clean.lcp_ms and clean.lcp_ms < 2500
    assert clean.uncompressed == [] and clean.short_cache == [] and clean.third_parties == []
    assert clean.blank_ms == clean.fcp_ms
    wp = run_of("wp_like")
    assert {u.url.rsplit("/", 1)[-1] for u in wp.uncompressed} >= {"plugins.js", "style.css"}
    assert len(wp.unsized_images) == 4 and wp.image_savings_bytes == 0
    assert wp.short_cache == []  # uzun önbellekli


def test_parse_rejects_runtime_error_and_error_status() -> None:
    broken = copy.deepcopy(report("plain_clean"))
    broken["runtimeError"] = {"code": "NO_FCP", "message": "x"}
    with pytest.raises(LighthouseError) as exc:
        parse_report(broken, HOME, MOBILE, 0.015)
    assert exc.value.code == "runtime:NO_FCP"
    # Bot doğrulaması / hata sayfası (403, 429, 5xx) ölçülmez
    blocked = copy.deepcopy(report("plain_clean"))
    for item in blocked["audits"]["network-requests"]["details"]["items"]:
        if item["resourceType"] == "Document":
            item["statusCode"] = 429
    with pytest.raises(LighthouseError) as exc:
        parse_report(blocked, HOME, MOBILE, 0.015)
    assert exc.value.code == "http_status"


def test_parse_tolerates_missing_audits() -> None:
    run = parse_report({"audits": {}, "categories": {}}, HOME, MOBILE, 0.015)
    assert run.lcp_ms is None and run.images == [] and run.frames == []


# --- filmstrip


def test_blank_until_uses_observed_frames_and_fcp_as_lower_bounds() -> None:
    frames = run_of("heavy_spa").frames
    blank = [f for f in frames if content_ratio(f[1]) < 0.015]
    content = [f for f in frames if content_ratio(f[1]) >= 0.015]
    assert blank and content
    # İçerik iki kare arasında geldi: önceki karenin zamanı (tutucu)
    assert blank_until(frames, 100, 0.015) == blank[-1][0]
    assert blank_until(frames, 9000, 0.015) == 9000  # FCP daha geçse o
    assert blank_until([], 1200, 0.015) == 1200
    # Hiç içerik yoksa son kareye kadar boş
    assert blank_until(blank, 0, 0.015) == blank[-1][0]


def test_strip_webp_respects_width_and_time_window() -> None:
    frames = run_of("heavy_spa").frames
    webp, width, height, times = strip_webp(frames, 5000)
    assert is_webp(webp) and width <= 1440 and height > 0
    assert times == [round(t) for t, _ in frames if t <= 5000][:10]
    _, _, _, early = strip_webp(frames, 1)  # pencerede kare yoksa ilk kare
    assert len(early) == 1


# --- CrUX


def crux_record(lcp: int = 2100, cls: str = "0.05", inp: int = 180) -> dict[str, Any]:
    return {
        "record": {
            "metrics": {
                "largest_contentful_paint": {"percentiles": {"p75": lcp}},
                "cumulative_layout_shift": {"percentiles": {"p75": cls}},
                "interaction_to_next_paint": {"percentiles": {"p75": inp}},
            },
            "collectionPeriod": {"lastDate": {"year": 2026, "month": 11, "day": 20}},
        }
    }


def client() -> CruxClient:
    return CruxClient("test-key", CRUX, 5)


@respx.mock
async def test_crux_url_level_data_with_key_in_header() -> None:
    route = respx.post(CRUX).mock(return_value=httpx.Response(200, json=crux_record()))
    status, data = await client().field_data(HOME, SITE)
    assert status == FieldStatus.OK and data is not None
    assert (data.scope, data.form_factor, data.lcp_ms, data.cls, data.inp_ms) == (
        "url",
        "phone",
        2100,
        0.05,
        180,
    )
    request = route.calls[0].request
    assert request.headers[API_KEY_HEADER] == "test-key"
    assert "test-key" not in str(request.url)  # anahtar URL'de (ve loglarda) yok
    assert json.loads(request.content)["url"] == HOME


@respx.mock
async def test_crux_falls_back_to_origin_then_all_devices() -> None:
    responses = [httpx.Response(404), httpx.Response(404), httpx.Response(200, json=crux_record())]
    route = respx.post(CRUX).mock(side_effect=responses)
    status, data = await client().field_data(HOME, SITE)
    assert status == FieldStatus.OK and data and (data.scope, data.form_factor) == ("origin", "all")
    bodies = [json.loads(c.request.content) for c in route.calls]
    assert bodies[1]["origin"] == SITE and "formFactor" not in bodies[2]


@respx.mock
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(404), FieldStatus.NO_DATA),
        (httpx.Response(429), FieldStatus.QUOTA),
        (httpx.Response(500), FieldStatus.ERROR),
        (httpx.ConnectTimeout("t"), FieldStatus.ERROR),
        (httpx.Response(200, text="not json"), FieldStatus.ERROR),
    ],
)
async def test_crux_errors_and_missing_data(response: Any, expected: str) -> None:
    respx.post(CRUX).mock(side_effect=[response] * 3)
    status, data = await client().field_data(HOME, SITE)
    assert (status, data) == (expected, None)


@respx.mock
async def test_crux_without_key_sends_nothing() -> None:
    route = respx.post(CRUX)
    status, data = await CruxClient("", CRUX, 5).field_data(HOME, SITE)
    assert (status, data, route.called) == (FieldStatus.NO_KEY, None, False)


# --- check'ler: üç yapı


async def test_heavy_spa_findings() -> None:
    found = await perf_findings(data_of("heavy_spa"))
    assert set(found) == {
        "web.perf.lcp_slow",
        "web.perf.blank_screen_long",
        "web.perf.unused_js",
        "web.perf.oversized_images",
        "web.perf.no_compression_or_cache",
        "web.perf.heavy_third_party",
    }
    lcp = found["web.perf.lcp_slow"]
    assert lcp.severity == Severity.HIGH and lcp.evidence["source"] == "lab"
    assert lcp.evidence["opportunities"][0]["audit"] == "image-delivery-insight"
    assert found["web.perf.no_compression_or_cache"].evidence["problem"] == "both"
    assert found["web.perf.unused_js"].evidence["unused_pct"] > 50
    images = found["web.perf.oversized_images"].evidence["images"]
    assert images[0]["natural"] == [1600, 1200]


async def test_plain_clean_site_has_no_perf_findings() -> None:
    assert await perf_findings(data_of("plain_clean")) == {}


async def test_wp_like_findings() -> None:
    found = await perf_findings(data_of("wp_like"))
    assert set(found) == {"web.perf.no_compression_or_cache", "web.perf.oversized_images"}
    assert found["web.perf.no_compression_or_cache"].evidence["problem"] == "compression"
    assert found["web.perf.oversized_images"].evidence["unsized_count"] == 4


# --- check'ler: saha verisi ve yanlış pozitifler


async def test_field_data_overrides_harsh_lab_result() -> None:
    """Laboratuvar (yavaş 4G benzetimi) kötü, gerçek kullanıcılar iyi → LCP bulgusu yok."""
    good = FieldData("origin", "phone", lcp_ms=1800, cls=0.02, inp_ms=120)
    found = await perf_findings(data_of("heavy_spa", field=good))
    assert "web.perf.lcp_slow" not in found and "web.perf.inp_slow" not in found


@pytest.mark.parametrize(
    ("field", "check_id", "severity"),
    [
        (FieldData("url", "phone", lcp_ms=3200), "web.perf.lcp_slow", Severity.MEDIUM),
        (FieldData("url", "phone", lcp_ms=5200), "web.perf.lcp_slow", Severity.HIGH),
        (FieldData("origin", "all", cls=0.3), "web.perf.cls_high", Severity.HIGH),
        (FieldData("origin", "phone", cls=0.15), "web.perf.cls_high", Severity.MEDIUM),
        (FieldData("url", "phone", inp_ms=350), "web.perf.inp_slow", Severity.MEDIUM),
        (FieldData("url", "phone", inp_ms=650), "web.perf.inp_slow", Severity.HIGH),
    ],
)
async def test_field_vitals_severity(field: FieldData, check_id: str, severity: Severity) -> None:
    found = await perf_findings(data_of("plain_clean", field=field))
    assert found[check_id].severity == severity
    assert found[check_id].evidence["source"] == "field"


async def test_small_unused_js_and_light_third_party_not_flagged() -> None:
    """Oran yüksek ama miktar küçük (< 200 KB) → bulgu yok; üçüncü taraf sınırların altında."""
    run = LighthouseRun(
        url=HOME,
        form_factor=MOBILE,
        lcp_ms=1200,
        cls=0,
        fcp_ms=800,
        blank_ms=800,
        total_js_bytes=150 * 1024,
        unused_js_bytes=120 * 1024,
        unused_js=[ResourceItem("https://example.com/a.js", 150 * 1024, 120 * 1024)],
        third_parties=[ThirdParty("Google Tag Manager", 90 * 1024, 250)],
    )
    found = await perf_findings(PerfData(runs=[run], home_url=HOME))
    assert found == {}


async def test_short_cache_on_tiny_files_not_flagged() -> None:
    run = run_of("heavy_spa")
    tiny = [c for c in run.short_cache if c.total_bytes < 2048]
    assert tiny
    small = LighthouseRun(url=HOME, form_factor=MOBILE, lcp_ms=1000, short_cache=tiny)
    found = await perf_findings(PerfData(runs=[small], home_url=HOME))
    assert "web.perf.no_compression_or_cache" not in found


async def test_lab_checks_use_worst_page_and_desktop_is_ignored() -> None:
    key = SITE + "/pricing"
    data = PerfData(
        runs=[
            run_of("plain_clean"),
            run_of("heavy_spa", url=key),
            run_of("heavy_spa", HOME, DESKTOP),
        ],
        home_url=HOME,
        key_url=key,
    )
    found = await perf_findings(data)
    assert found["web.perf.lcp_slow"].evidence["page"] == "/pricing"
    assert found["web.perf.unused_js"].evidence["page"] == "/pricing"
    # Üçüncü taraf ve filmstrip sadece ana sayfa: ana sayfa temiz
    assert "web.perf.heavy_third_party" not in found


async def test_blank_screen_links_home_filmstrip() -> None:
    found = await perf_findings(
        data_of("heavy_spa"), perf_filmstrip={"asset_id": "abc", "frames_ms": [1, 2]}
    )
    assert found["web.perf.blank_screen_long"].evidence["filmstrip_asset"] == "abc"


def test_perf_checks_are_phase2_only() -> None:
    registry = load_builtin_checks()
    assert len(PERF_IDS) == 8
    assert not PERF_IDS & {c.id for c in registry.for_target(TargetType.WEB, 1)}
    assert all(c.phase == 2 for c in registry.all() if c.id in PERF_IDS)


async def test_perf_checks_reject_phase1_context() -> None:
    from tests.checks.helpers import make_ctx

    checks = [c for c in load_builtin_checks().all() if c.module == "perf"]
    result = await run_checks(make_ctx(), checks)
    assert sorted(result.failed) == sorted(PERF_IDS)


@pytest.mark.parametrize("lang", ["tr", "en"])
async def test_perf_texts_render_without_placeholders(lang: str) -> None:
    registry = load_builtin_checks()
    found = await perf_findings(
        data_of("heavy_spa", field=FieldData("url", "phone", inp_ms=600, cls=0.3))
    )
    from launchcheck.i18n import fragment_fn

    for check_id, finding in found.items():
        check = registry.get(check_id)
        params = check.text_params(finding.evidence, fragment_fn(check_id, lang))
        text = finding_text(check_id, lang, params)
        for part in (text.title, text.description, *text.fix_steps):
            assert "{" not in part, (check_id, part)
    lcp = found["web.perf.lcp_slow"]
    params = registry.get("web.perf.lcp_slow").text_params(
        lcp.evidence, fragment_fn("web.perf.lcp_slow", "tr")
    )
    assert "sn" in params["value"] and "görsellerin küçültülmesi" in params["top_fix"]


# --- modül akışı (ölçüm ve saha verisi sahte)


class FakeLighthouse:
    def __init__(self, reports: dict[str, str], fail: set[str] | None = None) -> None:
        self.reports = reports  # url → fixture adı
        self.fail = fail or set()
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, url: str, form_factor: str, remaining: float) -> dict[str, Any]:
        self.calls.append((url, form_factor))
        if url in self.fail:
            raise LighthouseError("timeout")
        return report(self.reports.get(url, "plain_clean"))


def field_query(status: str, data: FieldData | None = None) -> Any:
    async def query(page: str, origin: str) -> tuple[str, FieldData | None]:
        return status, data

    return query


def perf_settings(**kw: Any) -> Settings:
    return Settings(deep_key_page_wait_s=1, **kw)


async def test_module_measures_home_desktop_then_key_page(tmp_path: Path) -> None:
    key = SITE + "/pricing"
    fake = FakeLighthouse({HOME: "heavy_spa", key: "wp_like"})
    sink = MemoryEventSink()
    ctx = DeepContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        target_url=SITE,
        events=sink,
        assets=AssetStore(tmp_path, 3 * 1024 * 1024),
    )
    ctx.set_key_urls([key])
    out = ModuleOutput()
    module = PerfModule(perf_settings(), measure=fake, field_query=field_query(FieldStatus.NO_DATA))
    await module.run(ctx, out)
    assert fake.calls == [(HOME, MOBILE)] * 3 + [(HOME, DESKTOP)] + [(key, MOBILE)] * 3
    assert ctx.perf is not None and ctx.perf.key_url == key
    ids = {f.check_id for f in out.findings}
    assert "web.perf.lcp_slow" in ids and "web.perf.unused_js" in ids
    # Saha verisi yok → INP değerlendirilmedi (raporda "geçti" sayılmaz)
    assert ctx.meta["checks_skipped"] == ["web.perf.inp_slow"]
    assert ctx.meta["perf"]["field_status"] == FieldStatus.NO_DATA
    assert ctx.meta["perf"]["home_mobile"]["runs"] == 3
    assert ctx.meta["perf"]["key_mobile"]["url"] == key
    assert ctx.meta["perf"]["home_desktop"]["runs"] == 1
    # Filmstrip ana sayfanın ortanca ölçümünden, WebP asset olarak
    assert [a.kind for a in out.assets] == ["filmstrip"]
    assert ctx.meta["perf_filmstrip"]["asset_id"] == str(out.assets[0].id)
    assert (tmp_path / out.assets[0].path).is_file()
    keys = [e[1] for e in sink.events]
    assert keys.count("deep.event.lighthouse_run") == 7
    assert "deep.event.field_data_missing" in keys


async def test_module_skips_key_page_when_render_finds_none() -> None:
    fake = FakeLighthouse({HOME: "plain_clean"})
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, target_url=SITE)
    ctx.key_urls_ready.set()  # render bitti, kilit sayfa yok
    await PerfModule(
        perf_settings(), measure=fake, field_query=field_query(FieldStatus.NO_KEY)
    ).run(ctx, ModuleOutput())
    assert len(fake.calls) == 4 and ctx.perf and ctx.perf.key_url is None


async def test_module_stops_retrying_failing_url_and_uses_field_data() -> None:
    fake = FakeLighthouse({}, fail={HOME})
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, target_url=SITE)
    ctx.key_urls_ready.set()
    out = ModuleOutput()
    field = FieldData("origin", "phone", lcp_ms=4500, cls=0.01, inp_ms=150)
    await PerfModule(
        perf_settings(), measure=fake, field_query=field_query(FieldStatus.OK, field)
    ).run(ctx, out)
    assert len(fake.calls) == 2  # iki başarısızlıktan sonra aynı adres denenmez
    assert ctx.meta["perf_errors"] == ["timeout", "timeout"]
    assert [f.check_id for f in out.findings] == ["web.perf.lcp_slow"]
    assert set(ctx.meta["checks_skipped"]) == {
        "web.perf.blank_screen_long",
        "web.perf.unused_js",
        "web.perf.oversized_images",
        "web.perf.no_compression_or_cache",
        "web.perf.heavy_third_party",
    }


async def test_module_fails_without_any_data() -> None:
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, target_url=SITE)
    ctx.key_urls_ready.set()
    module = PerfModule(
        perf_settings(),
        measure=FakeLighthouse({}, fail={HOME}),
        field_query=field_query(FieldStatus.QUOTA),
    )
    with pytest.raises(PerfUnavailableError):
        await module.run(ctx, ModuleOutput())


async def test_module_does_not_start_runs_past_budget() -> None:
    fake = FakeLighthouse({HOME: "plain_clean"})
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, target_url=SITE)
    ctx.key_urls_ready.set()
    # Bütçe (check payı düşülünce) ilk ölçüm tahmininden kısa → hiç ölçüm başlamaz
    module = PerfModule(
        perf_settings(deep_budget_lighthouse_s=20),
        measure=fake,
        field_query=field_query(FieldStatus.OK, FieldData("url", "phone", lcp_ms=1000)),
    )
    await module.run(ctx, ModuleOutput())
    assert fake.calls == []


def test_summary_rounds_medians() -> None:
    data = data_of("heavy_spa", field=FieldData("url", "phone", lcp_ms=2100.4, cls=0.0512))
    s = summary(data)
    assert s["field"] == {
        "scope": "url",
        "form_factor": "phone",
        "lcp_ms": 2100,
        "cls": 0.051,
        "inp_ms": None,
    }
    assert s["home_mobile"]["runs"] == 3 and isinstance(s["home_mobile"]["lcp_ms"], int)
    assert s["home_desktop"] is None and s["key_mobile"] is None


# --- çalıştırıcı (komut satırı ve ortam; gerçek çalıştırma tarayıcı testinde)


def test_lighthouse_args_and_child_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LC_GOOGLE_API_KEY", "secret-value")
    cfg = LighthouseConfig.from_settings(Settings(deep_chrome_path="/x/chrome"))
    mobile = lighthouse_args(cfg, HOME, MOBILE, 9222, "/tmp/r.json")
    desktop = lighthouse_args(cfg, HOME, DESKTOP, 9222, "/tmp/r.json")
    assert "--port=9222" in mobile and "--hostname=127.0.0.1" in mobile
    assert "--form-factor=mobile" in mobile and "--preset=desktop" in desktop
    assert any(a.startswith("--emulated-user-agent=") and "LaunchCheckBot" in a for a in mobile)
    assert not any(a.startswith("--chrome-flags") for a in mobile)  # Chromium'u biz başlatırız
    env = _child_env("/tmp/w")
    assert "secret-value" not in json.dumps(env) and not any(k.startswith("LC_") for k in env)


# --- orkestratör + rapor


async def test_orchestrator_runs_perf_lane_and_report_hides_skipped(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    deep_id = await add_deep(sessionmaker, await add_parent(sessionmaker))
    module = PerfModule(
        perf_settings(),
        measure=FakeLighthouse({}),  # plain_clean: bulgu yok
        field_query=field_query(FieldStatus.NO_DATA),
    )
    cfg = OrchestratorConfig(settings=Settings(deep_scan_timeout_s=60), modules=[module])
    orch = DeepOrchestrator(sessionmaker, cfg)
    # Render modülü yok: kilit sayfa beklemesi kısa sürer (deep_key_page_wait_s=1)
    await orch.run(deep_id)
    async with sessionmaker() as s:
        scan = await s.get(Scan, deep_id)
        rows = list(
            (await s.scalars(select(FindingRow).where(FindingRow.scan_id == deep_id))).all()
        )
    assert scan is not None and scan.progress["modules"]["perf"]["status"] == "done"
    assert scan.meta["perf"]["field_status"] == "no_data"
    passed = {p.check_id for p in _passed(scan, rows, load_builtin_checks(), "tr")}
    assert "web.perf.lcp_slow" in passed and "web.perf.unused_js" in passed
    assert "web.perf.inp_slow" not in passed  # saha verisi yok → kontrol edilmedi
    assert uuid.UUID(str(scan.id)) == deep_id
