"""Faz 2 rapor verisi (P10, PHASE2 §8): test edilmemiş kapılar, önizleme, etki kartları, fark
paneli, öncelik planı, yeniden tarama farkı, asset listesi."""

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.collectors.web_collector import PageSnapshot, ScriptFile, WebContext
from launchcheck.db.models import FindingRow, Scan, ScanAsset, ScanStatus, Verification, utcnow
from launchcheck.deep.context import DeepContext, ParentFinding
from launchcheck.deep.funnel_data import FunnelData, SignupForm
from launchcheck.deep.gates import build_gates, build_preview, template_path
from launchcheck.deep.render_data import AdminProbe, RenderData, RenderedPage
from tests.deep.helpers import SITE, add_deep, add_parent

SM = async_sessionmaker[AsyncSession]


# --- kapılar ve önizleme


def test_template_path_replaces_ids() -> None:
    assert template_path("/api/users/42/orders/9f8e7d6c5b4a39281706f5e4d3c2b1a0") == (
        "/api/users/{id}/orders/{id}"
    )
    assert template_path("/rest/v1/profiles") == "/rest/v1/profiles"
    assert template_path("/api/items/3fa85f64-5717-4562-b3fc-2c963f66afa6") == "/api/items/{id}"


def gates_ctx(**kw: Any) -> DeepContext:
    home = RenderedPage(
        url=f"{SITE}/",
        final_url=f"{SITE}/",
        status=200,
        api_calls=[
            ("GET", f"{SITE}/api/users/42"),
            ("GET", f"{SITE}/api/users/77"),
            ("POST", f"{SITE}/api/auth/login"),
            ("POST", f"{SITE}/api/stripe/webhook"),
            ("GET", "https://abcd.supabase.co/rest/v1/profiles"),
            ("GET", "https://www.google-analytics.com/g/collect"),
            ("GET", f"{SITE}/assets/app.js"),
        ],
    )
    render = RenderData(
        pages=[home],
        admin=[
            AdminProbe("/admin", 200, ["admin_heading"], False, False),
            AdminProbe("/yonetim", 200, [], False, False),  # sinyal yok
            AdminProbe("/panel", 200, ["data_table"], True, False),  # giriş formu var
        ],
    )
    bundle = (
        "supabase.from('profiles').select();"
        "supabase.storage.from('avatars').upload();"
        "Array.from(items);"
        "fetch('/api/hooks/github-webhook');"
    )
    ctx = DeepContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        target_url=SITE,
        render=render,
        parent_meta=kw.get("meta", {"supabase_urls": ["https://abcd.supabase.co"]}),
        parent_findings=[
            ParentFinding(
                "web.privacy.form_without_consent",
                Severity.MEDIUM,
                Category.SECURITY,
                {"forms": [{"page": f"{SITE}/iletisim"}]},
            )
        ],
    )
    ctx.base = WebContext(
        target_type=TargetType.WEB,
        target_label=SITE,
        scripts=[ScriptFile(f"{SITE}/assets/app.js", bundle)],
        pages=[
            PageSnapshot(
                f"{SITE}/",
                200,
                {},
                '<html><head><title>Acme</title><meta name="description" content="Açıklama">'
                '<meta property="og:image" content="/og.png"></head></html>',
            )
        ],
    )
    funnel = FunnelData(site=SITE)
    funnel.signup = SignupForm("/kayit")
    ctx.funnel = funnel
    return ctx


def test_build_gates() -> None:
    gates = build_gates(gates_ctx())
    endpoints = [(e["method"], e["path"]) for e in gates["api_endpoints"]]
    # Kendi API'si önce, sonra backend (Supabase), sonra diğerleri; kimlikler {id}; statik yok
    assert endpoints[:3] == [
        ("GET", "/api/users/{id}"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/stripe/webhook"),
    ]
    assert ("GET", "/rest/v1/profiles") in endpoints
    assert not any(p.endswith(".js") for _, p in endpoints)
    assert gates["supabase"] == {
        "projects": ["https://abcd.supabase.co"],
        "tables": ["profiles"],
        "buckets": ["avatars"],
    }
    assert gates["auth_endpoints"] == ["POST /api/auth/login"]
    assert "POST /api/stripe/webhook" in gates["webhooks"]
    assert "/api/hooks/github-webhook" in gates["webhooks"]
    assert gates["personal_data_forms"] == ["/iletisim", "/kayit"]
    assert gates["admin_unverified"] == ["/admin"]
    assert gates["total"] >= 8


def test_gates_without_supabase_lists_no_tables() -> None:
    """Yanlış alarm: Supabase kullanmayan sitede `.from('x')` tablo sayılmaz (Array.from vb.)."""
    ctx = gates_ctx(meta={})
    home = ctx.render.pages[0]  # type: ignore[union-attr]
    home.api_calls = [c for c in home.api_calls if "supabase" not in c[1]]
    gates = build_gates(ctx)
    assert gates["supabase"] == {"projects": [], "tables": [], "buckets": []}
    # API isteklerinde supabase.co görülürse proje ve koddaki tablolar listelenir
    gates = build_gates(gates_ctx(meta={}))
    assert gates["supabase"]["projects"] == ["https://abcd.supabase.co"]
    assert gates["supabase"]["tables"] == ["profiles"]


def test_preview_prefers_server_html() -> None:
    preview = build_preview(gates_ctx())
    assert preview["source"] == "server"
    assert preview["title"] == "Acme" and preview["description"] == "Açıklama"
    assert preview["og_image"] == f"{SITE}/og.png"
    ctx = gates_ctx()
    ctx.base = None
    ctx.render.pages[0].og = {"og:title": "JS ile"}  # type: ignore[union-attr]
    assert build_preview(ctx)["source"] == "rendered"


# --- rapor API'si


async def _deep_with_findings(
    sm: SM,
    rows: list[tuple[str, Severity, Category, int, Verification | None, dict[str, Any]]],
    *,
    score: int = 41,
    compare_to: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    parent = await add_parent(sm)
    deep = await add_deep(sm, parent, status=ScanStatus.DONE)
    async with sm() as s:
        scan = await s.get(Scan, deep)
        assert scan is not None
        scan.score = score
        scan.finished_at = utcnow()
        scan.compare_to_id = compare_to
        scan.meta = meta or {"gates": {"total": 14}}
        scan.progress = {"modules": {}}
        for check_id, sev, cat, phase, verification, evidence in rows:
            s.add(
                FindingRow(
                    scan_id=deep,
                    check_id=check_id,
                    severity=sev,
                    category=cat,
                    evidence=evidence,
                    source_phase=phase,
                    verification=verification,
                )
            )
        s.add(
            ScanAsset(
                scan_id=deep,
                kind="screenshot_mobile",
                page_url=f"{SITE}/",
                path=f"{deep}/x.webp",
                width=375,
                height=812,
                bytes=100,
            )
        )
        await s.commit()
    return parent, deep


ROWS = [
    ("web.files.env_exposed", Severity.CRITICAL, Category.SECURITY, 1, Verification.CONFIRMED, {}),
    ("web.meta.viewport_missing", Severity.LOW, Category.CONVERSION, 1, Verification.REFUTED, {}),
    (
        "web.external.forgotten_subdomain",
        Severity.HIGH,
        Category.SECURITY,
        2,
        None,
        {"host": "staging.example.com"},
    ),
    (
        "web.external.email_spoofing",
        Severity.HIGH,
        Category.SECURITY,
        2,
        None,
        {"domain": "example.com", "problems": []},
    ),
    ("web.perf.lcp_slow", Severity.MEDIUM, Category.PERFORMANCE, 2, None, {}),
    ("web.render.broken_images", Severity.LOW, Category.PROD_READINESS, 2, None, {}),
    ("web.links.http_target_no_https", Severity.INFO, Category.SECURITY, 2, None, {}),
]


async def test_deep_report_sections(client: AsyncClient, sessionmaker: SM) -> None:
    _, deep = await _deep_with_findings(sessionmaker, ROWS)
    body = (await client.get(f"/api/scans/{deep}?lang=tr")).json()
    by_id = {f["check_id"]: f for f in body["findings"]}
    # Etki kartı sadece medium ve üstünde
    env = by_id["web.files.env_exposed"]["impact"]
    assert env["ease"] == "easy" and env["fix_minutes"] == 15
    assert [t["when"] for t in env["timeline"]] == ["today", "week", "month"]
    assert by_id["web.render.broken_images"]["impact"] is None
    assert all(f["id"] and f["fingerprint"] for f in body["findings"])
    assert by_id["web.external.forgotten_subdomain"]["fingerprint"] == (
        "web.external.forgotten_subdomain|staging.example.com"
    )
    s = body["sections"]
    diff = s["phase_diff"]
    assert diff["phase1"] == {"findings": 2, "score": 80}
    assert diff["phase2"]["new_findings"] == 4  # info hariç
    assert diff["phase2"]["verification"]["confirmed"] == 1
    assert diff["phase2"]["verification"]["refuted"] == 1
    assert diff["phase2"]["forgotten_subdomains"] == 1 and diff["phase2"]["untested_gates"] == 14
    assert diff["score_dropped"] is True
    plan = s["plan"]
    ids = {f["id"]: f["check_id"] for f in body["findings"]}
    # Bugün: critical/high ve ≤ 30 dk (env 15 dk, e-posta 30 dk)
    assert {ids[i] for i in plan["today"]} == {
        "web.files.env_exposed",
        "web.external.email_spoofing",
    }
    # Bu hafta: kalan high (alt alan adı 45 dk) + ≤ 120 dk medium
    assert {ids[i] for i in plan["week"]} == {
        "web.external.forgotten_subdomain",
        "web.perf.lcp_slow",
    }
    assert {ids[i] for i in plan["month"]} == {"web.render.broken_images"}
    # Yanlış alarm ve bilgi bulguları plana girmez
    flat = [ids[i] for group in plan.values() for i in group]
    assert "web.meta.viewport_missing" not in flat and "web.links.http_target_no_https" not in flat
    assert s["assets"][0]["kind"] == "screenshot_mobile" and "path" not in s["assets"][0]
    assert s["gates"] == {"total": 14} and s["rescan"] is None
    # Faz 1 raporunda etki kartı ve bölüm yok
    parent = body["parent_scan_id"]
    p1 = (await client.get(f"/api/scans/{parent}?lang=tr")).json()
    assert p1["sections"] is None


async def test_rescan_diff(client: AsyncClient, sessionmaker: SM) -> None:
    before_rows = [
        (
            "web.files.env_exposed",
            Severity.CRITICAL,
            Category.SECURITY,
            1,
            Verification.CONFIRMED,
            {},
        ),
        (
            "web.external.forgotten_subdomain",
            Severity.HIGH,
            Category.SECURITY,
            2,
            None,
            {"host": "old.example.com"},
        ),
        ("web.perf.lcp_slow", Severity.MEDIUM, Category.PERFORMANCE, 2, None, {}),
    ]
    _, before = await _deep_with_findings(sessionmaker, before_rows, score=30)
    after_rows = [
        ("web.perf.lcp_slow", Severity.MEDIUM, Category.PERFORMANCE, 2, None, {}),
        (
            "web.external.forgotten_subdomain",
            Severity.HIGH,
            Category.SECURITY,
            2,
            None,
            {"host": "staging.example.com"},
        ),
    ]
    _, after = await _deep_with_findings(sessionmaker, after_rows, score=72, compare_to=before)
    rescan = (await client.get(f"/api/scans/{after}")).json()["sections"]["rescan"]
    assert (rescan["score_before"], rescan["score_after"]) == (30, 72)
    assert {c["check_id"] for c in rescan["closed"]} == {
        "web.files.env_exposed",
        "web.external.forgotten_subdomain",  # old.example.com kapandı
    }
    assert [n["check_id"] for n in rescan["new"]] == ["web.external.forgotten_subdomain"]
    assert [p["check_id"] for p in rescan["persisting"]] == ["web.perf.lcp_slow"]


async def test_running_deep_scan_has_no_sections(client: AsyncClient, sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker)
    deep = await add_deep(sessionmaker, parent, status=ScanStatus.RUNNING)
    body = (await client.get(f"/api/scans/{deep}")).json()
    assert body["sections"] is None


async def test_finalizer_failure_does_not_fail_scan() -> None:
    from launchcheck.config import Settings
    from launchcheck.deep.orchestrator import DeepOrchestrator, OrchestratorConfig

    calls: list[str] = []

    def boom(ctx: DeepContext) -> None:
        calls.append("boom")
        raise RuntimeError("x")

    def ok(ctx: DeepContext) -> None:
        calls.append("ok")
        ctx.meta["gates"] = {"total": 0}

    orch = DeepOrchestrator(None, OrchestratorConfig(Settings(), [], finalizers=[boom, ok]))  # type: ignore[arg-type]
    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE)
    orch._finalize(ctx)
    assert calls == ["boom", "ok"] and ctx.meta["gates"] == {"total": 0}
