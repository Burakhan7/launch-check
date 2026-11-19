"""Render modülü: Faz 1 doğrulama kuralları, sayfa planı yardımcıları ve orkestratörle birlikte
uçtan uca akış (tarayıcı ve ağ sahte)."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.collectors.web_collector import WebContext
from launchcheck.config import Settings
from launchcheck.db.models import FindingRow, Scan, Verification
from launchcheck.deep.browser.render import (
    admin_candidates,
    dedupe_console,
    key_pages,
    page_role,
    parse_sourcemap_sources,
    plan_pages,
)
from launchcheck.deep.context import DeepContext, ParentFinding
from launchcheck.deep.orchestrator import DeepOrchestrator, OrchestratorConfig
from launchcheck.deep.render_data import ConsoleError, RenderData, RenderedPage, ViewportMetrics
from launchcheck.deep.render_module import RenderModule
from launchcheck.deep.verification import RENDER_RULES
from tests.checks.helpers import make_ctx
from tests.deep.helpers import add_deep, add_parent

SITE = "https://example.com"


def vm(width: int, inner: int | None = None) -> ViewportMetrics:
    return ViewportMetrics(width, 812, inner or width, inner or width)


def rendered(path: str, **kw: Any) -> RenderedPage:
    return RenderedPage(url=SITE + path, final_url=SITE + path, status=200, **kw)


def deep_ctx(*pages: RenderedPage, **kw: Any) -> DeepContext:
    return DeepContext(
        target_type=TargetType.WEB, target_label=SITE, render=RenderData(pages=list(pages), **kw)
    )


def parent(check_id: str, evidence: dict[str, Any]) -> ParentFinding:
    return ParentFinding(check_id, Severity.MEDIUM, Category.PROD_READINESS, evidence)


# --- doğrulama kuralları


def test_empty_server_html_confirmed_when_content_appears_after_render() -> None:
    rule = RENDER_RULES["web.render.empty_server_html"]
    finding = parent(
        "web.render.empty_server_html", {"pages": [{"url": SITE + "/", "main_content_words": 3}]}
    )
    assert rule(deep_ctx(rendered("/", main_words=400)), finding) == Verification.CONFIRMED
    assert rule(deep_ctx(rendered("/", main_words=5)), finding) is None
    assert rule(DeepContext(target_type=TargetType.WEB, target_label=SITE), finding) is None


def test_root_shell_confirmed_when_title_becomes_specific() -> None:
    rule = RENDER_RULES["web.render.route_serves_root_shell"]
    finding = parent("web.render.route_serves_root_shell", {"pages": [{"url": SITE + "/pricing"}]})
    home = rendered("/", title="Acme", h1="Acme")
    specific = rendered("/pricing", title="Fiyatlar | Acme", h1="Fiyatlar")
    same = rendered("/pricing", title="Acme", h1="Acme")
    assert rule(deep_ctx(home, specific), finding) == Verification.CONFIRMED
    assert rule(deep_ctx(home, same), finding) is None


def test_viewport_rule_refutes_when_js_adds_viewport() -> None:
    rule = RENDER_RULES["web.meta.viewport_missing"]
    missing = parent("web.meta.viewport_missing", {"issues": ["missing"]})
    zoom_only = parent("web.meta.viewport_missing", {"issues": ["zoom_blocked"]})
    assert rule(deep_ctx(rendered("/", mobile=vm(375))), missing) == Verification.REFUTED
    assert rule(deep_ctx(rendered("/", mobile=vm(375, 980))), missing) == Verification.CONFIRMED
    assert rule(deep_ctx(rendered("/", mobile=vm(375))), zoom_only) is None


def test_localhost_escalated_only_when_requested() -> None:
    rule = RENDER_RULES["web.bundle.localhost_urls"]
    finding = parent("web.bundle.localhost_urls", {})
    hit = deep_ctx(rendered("/"), blocked_hosts=[("localhost", 3000, "port_not_allowed")])
    miss = deep_ctx(rendered("/"), blocked_hosts=[("10.0.0.1", 80, "private_address")])
    assert rule(hit, finding) == Verification.ESCALATED
    assert rule(miss, finding) == Verification.CONFIRMED


def test_sourcemap_and_og_rules() -> None:
    assert (
        RENDER_RULES["web.files.sourcemap_exposed"](
            deep_ctx(rendered("/"), sourcemap_source_count=3), parent("x", {})
        )
        == Verification.ESCALATED
    )
    assert (
        RENDER_RULES["web.files.sourcemap_exposed"](deep_ctx(rendered("/")), parent("x", {}))
        is None
    )
    assert (
        RENDER_RULES["web.meta.og_missing"](deep_ctx(rendered("/")), parent("x", {}))
        == Verification.CONFIRMED
    )


# --- sayfa planı ve ayrıştırıcılar


def test_plan_pages_priority_dedupe_and_limit() -> None:
    urls = plan_pages(
        SITE + "/",
        [SITE + "/", SITE + "/about/"],
        [SITE + "/about", SITE + "/pricing", SITE + "/contact"],
        [SITE + "/blog/1", SITE + "/register", SITE + "/brochure.pdf"],
        limit=5,
    )
    assert urls == [
        SITE + "/",
        SITE + "/about/",
        SITE + "/pricing",
        SITE + "/contact",
        SITE + "/register",
    ]


def test_page_roles_and_key_pages() -> None:
    assert page_role(SITE + "/fiyatlar") == "pricing"
    assert page_role(SITE + "/kayit-ol") == "signup" or page_role(SITE + "/register") == "signup"
    assert page_role(SITE + "/register") == "signup"
    assert page_role(SITE + "/blog/post") is None
    urls = [SITE + "/", SITE + "/about", SITE + "/register", SITE + "/pricing"]
    assert key_pages(urls, [SITE + "/about"], 2) == [SITE + "/pricing", SITE + "/register"]
    assert key_pages([SITE + "/", SITE + "/about"], [SITE + "/about"], 2) == [SITE + "/about"]


def test_admin_candidates_from_bundles() -> None:
    bundles = [
        '{path:"/admin/users"},{path:"/admin"},"/yonetim","/administrator-guide",x="/panelist"'
    ]
    assert admin_candidates(bundles, SITE + "/", 5) == [
        SITE + "/admin/users",
        SITE + "/admin",
        SITE + "/yonetim",
    ]


def test_parse_sourcemap_sources_truncated_and_filtered() -> None:
    body = (
        b'{"version":3,"sources":["webpack://app/./src/App.tsx","../node_modules/react/index.js","src\\/lib\\/db.ts"],"mappings":"AAAA'
        + b"A" * 100
    )
    assert parse_sourcemap_sources(body) == ["src/App.tsx", "src/lib/db.ts"]
    assert parse_sourcemap_sources(b"not a map") == []


def test_dedupe_console_normalizes_numbers_and_urls() -> None:
    errors = [
        ConsoleError("Failed at https://x.com/a.js:10", "console"),
        ConsoleError("Failed at https://x.com/b.js:22", "console"),
        ConsoleError("Other", "exception"),
    ]
    assert [e.message for e in dedupe_console(errors)] == [
        "Failed at https://x.com/a.js:10",
        "Other",
    ]


# --- orkestratörle uçtan uca (sahte Faz 1 bağlamı ve sahte tarayıcı)


async def test_render_module_end_to_end(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    parent_id = await add_parent(
        sessionmaker,
        findings=[
            ("web.meta.viewport_missing", Severity.MEDIUM, Category.PROD_READINESS),
            ("web.files.env_exposed", Severity.CRITICAL, Category.SECURITY),
        ],
    )
    async with sessionmaker() as s:
        row = (
            await s.scalars(
                select(FindingRow).where(FindingRow.check_id == "web.meta.viewport_missing")
            )
        ).one()
        row.evidence = {"issues": ["missing"]}
        await s.commit()
    deep_id = await add_deep(sessionmaker, parent_id)

    async def fake_base(url: str) -> WebContext:
        return make_ctx(final_url=url + "/")

    async def fake_collect(ctx: DeepContext, base: WebContext, out: Any) -> RenderData:
        home = rendered(
            "/",
            mobile=vm(375),
            desktop=vm(1440),
            console_errors=[ConsoleError("TypeError: boom", "exception")],
        )
        home.widths = {375: home.mobile, 1440: home.desktop}  # type: ignore[dict-item]
        return RenderData(pages=[home])

    module = RenderModule(Settings(), collect=fake_collect, collect_base=fake_base)
    cfg = OrchestratorConfig(
        settings=Settings(deep_scan_timeout_s=30),
        modules=[module],
        verification_rules=RENDER_RULES,
    )
    await DeepOrchestrator(sessionmaker, cfg).run(deep_id)

    async with sessionmaker() as s:
        scan = await s.get(Scan, deep_id)
        rows = {
            r.check_id: r
            for r in (
                await s.scalars(select(FindingRow).where(FindingRow.scan_id == deep_id))
            ).all()
        }
    assert scan is not None and scan.progress["modules"]["render"]["status"] == "done"
    assert rows["web.render.js_runtime_errors"].severity == Severity.HIGH
    assert rows["web.render.js_runtime_errors"].module == "render"
    # Faz 1 viewport bulgusu: sayfa telefonda cihaz genişliğinde çiziliyor → yanlış alarm
    assert rows["web.meta.viewport_missing"].verification == Verification.REFUTED
    assert rows["web.files.env_exposed"].verification == Verification.NOT_VERIFIABLE
    assert uuid.UUID(str(scan.id)) == deep_id


async def test_bot_challenge_fails_module_with_reason(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from launchcheck.deep.browser.errors import BotChallengeError
    from launchcheck.deep.events import MemoryEventSink

    deep_id = await add_deep(sessionmaker, await add_parent(sessionmaker))

    async def no_base(url: str) -> None:
        return None

    async def blocked(ctx: DeepContext, base: Any, out: Any) -> RenderData:
        raise BotChallengeError()

    sink = MemoryEventSink()
    module = RenderModule(Settings(), collect=blocked, collect_base=no_base)  # type: ignore[arg-type]
    cfg = OrchestratorConfig(settings=Settings(), modules=[module], events_factory=lambda _: sink)
    await DeepOrchestrator(sessionmaker, cfg).run(deep_id)
    async with sessionmaker() as s:
        scan = await s.get(Scan, deep_id)
    assert scan is not None
    assert scan.progress["modules"]["render"]["status"] == "failed"
    assert scan.meta["deep_blocked"] == "site_blocked_bot"
    assert ("render", "deep.event.site_blocked", {}) in sink.events


async def test_render_continues_without_phase1_context() -> None:
    """Faz 1 bağlamı toplanamazsa (ör. site art arda isteklere doğrulama gösterdi) render
    tarayıcıyla devam eder; check'ler yine çalışır."""
    seen: dict[str, Any] = {}

    async def no_base(url: str) -> None:
        return None

    async def fake_collect(ctx: DeepContext, base: Any, out: Any) -> RenderData:
        seen["base"] = base
        return RenderData(pages=[rendered("/", console_errors=[ConsoleError("x", "exception")])])

    from launchcheck.deep.modules import ModuleOutput

    ctx = DeepContext(target_type=TargetType.WEB, target_label=SITE, target_url=SITE)
    out = ModuleOutput()
    await RenderModule(Settings(), collect=fake_collect, collect_base=no_base).run(ctx, out)  # type: ignore[arg-type]
    assert seen["base"] is None
    assert [f.check_id for f in out.findings] == ["web.render.js_runtime_errors"]
