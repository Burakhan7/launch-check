"""Faz 2 orkestratörü: şeritler, bütçeler, hata/zaman aşımı, olaylar, Faz 1 kopyası, skor."""

import asyncio
import time
import uuid
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Category, Finding, Severity
from launchcheck.config import Settings
from launchcheck.db.models import FindingRow, Scan, ScanEvent, ScanStatus, Verification
from launchcheck.deep.context import DeepContext, ParentFinding
from launchcheck.deep.events import MemoryEventSink
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput
from launchcheck.deep.orchestrator import DeepOrchestrator, OrchestratorConfig
from tests.deep.helpers import add_deep, add_parent

SM = async_sessionmaker[AsyncSession]


def settings(**overrides: float) -> Settings:
    base: dict[str, float] = {
        "deep_scan_timeout_s": 5,
        "deep_budget_browser_s": 2,
        "deep_budget_lighthouse_s": 2,
        "deep_budget_network_s": 2,
        "deep_budget_llm_s": 2,
        "deep_budget_module_s": 2,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def finding(check_id: str, severity: Severity = Severity.MEDIUM) -> Finding:
    return Finding(check_id, severity, Category.SECURITY, {"page": "/"})


class Quick(DeepModule):
    name = "quick"
    lane = Lane.NETWORK

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        out.findings.append(finding("web.deep.quick"))


class Sleepy(DeepModule):
    """Önce bir bulgu ekler, sonra uzun süre bekler: zaman aşımında bulgu korunmalı."""

    name = "sleepy"
    lane = Lane.BROWSER
    sleep_s: ClassVar[float] = 10

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        out.findings.append(finding("web.deep.sleepy", Severity.LOW))
        await asyncio.sleep(self.sleep_s)


class Broken(DeepModule):
    name = "broken"
    lane = Lane.LLM

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        raise RuntimeError("gizli sayfa içeriği")


class Pause(DeepModule):
    lane = Lane.LIGHTHOUSE

    def __init__(self, name: str, lane: Lane, seconds: float) -> None:
        self.name = name  # type: ignore[misc]
        self.lane = lane  # type: ignore[misc]
        self.seconds = seconds

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        await asyncio.sleep(self.seconds)


def orchestrator(
    sm: SM, modules: list[DeepModule], **kw: object
) -> tuple[DeepOrchestrator, MemoryEventSink]:
    sink = MemoryEventSink()
    cfg = OrchestratorConfig(
        settings=kw.pop("settings", settings()),  # type: ignore[arg-type]
        modules=modules,
        events_factory=lambda _id: sink,
        **kw,  # type: ignore[arg-type]
    )
    return DeepOrchestrator(sm, cfg), sink


async def rows(sm: SM, scan_id: uuid.UUID) -> list[FindingRow]:
    async with sm() as s:
        return list(
            (await s.scalars(select(FindingRow).where(FindingRow.scan_id == scan_id))).all()
        )


async def get_scan(sm: SM, scan_id: uuid.UUID) -> Scan:
    async with sm() as s:
        scan = await s.get(Scan, scan_id)
        assert scan is not None
        return scan


async def test_end_to_end_with_no_modules_copies_phase1(sessionmaker: SM) -> None:
    parent = await add_parent(
        sessionmaker,
        findings=[("web.files.env_exposed", Severity.CRITICAL, Category.SECURITY)],
        meta={"platform": ["vite"], "checks_failed": ["web.x.y"]},
    )
    deep = await add_deep(sessionmaker, parent)
    orch, sink = orchestrator(sessionmaker, [])

    assert await orch.run(deep) == ScanStatus.DONE

    scan = await get_scan(sessionmaker, deep)
    assert scan.status == ScanStatus.DONE and scan.started_at is not None
    assert scan.score is not None and scan.score <= 49  # Faz 1'deki critical kopyalandı
    assert scan.meta["platform"] == ["vite"]
    assert scan.meta["checks_failed"] == ["web.x.y"]
    assert scan.meta["deep"]["partial_modules"] == [] and scan.meta["deep"]["failed_modules"] == []
    (copy,) = await rows(sessionmaker, deep)
    assert (copy.source_phase, copy.verification) == (1, Verification.NOT_VERIFIABLE)
    assert copy.check_id == "web.files.env_exposed"
    assert [k for _, k, _ in sink.events] == [
        "deep.event.started",
        "deep.event.phase1_copied",
        "deep.event.done",
    ]


async def test_modules_write_findings_and_progress(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    orch, sink = orchestrator(sessionmaker, [Quick()])
    await orch.run(deep)

    (row,) = await rows(sessionmaker, deep)
    assert (row.check_id, row.source_phase, row.module) == ("web.deep.quick", 2, "quick")
    scan = await get_scan(sessionmaker, deep)
    assert scan.progress == {"modules": {"quick": {"status": "done", "found": 1}}}
    assert ("quick", "deep.event.module_done", {"module": "quick", "found": 1}) in sink.events


async def test_module_timeout_keeps_collected_findings(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    orch, sink = orchestrator(
        sessionmaker, [Sleepy(), Quick()], settings=settings(deep_budget_module_s=0.2)
    )
    started = time.monotonic()
    assert await orch.run(deep) == ScanStatus.DONE
    assert time.monotonic() - started < 2

    scan = await get_scan(sessionmaker, deep)
    assert scan.meta["deep"]["partial_modules"] == ["sleepy"]
    assert scan.progress["modules"]["sleepy"] == {"status": "partial", "found": 1}
    assert scan.progress["modules"]["quick"]["status"] == "done"
    assert {r.check_id for r in await rows(sessionmaker, deep)} == {
        "web.deep.sleepy",
        "web.deep.quick",
    }
    assert any(k == "deep.event.module_partial" for _, k, _ in sink.events)


async def test_failing_module_does_not_stop_scan(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    orch, sink = orchestrator(sessionmaker, [Broken(), Quick()])
    assert await orch.run(deep) == ScanStatus.DONE
    scan = await get_scan(sessionmaker, deep)
    assert scan.meta["deep"]["failed_modules"] == ["broken"]
    assert scan.progress["modules"]["broken"]["status"] == "failed"
    assert scan.progress["modules"]["quick"]["status"] == "done"
    # İstisna mesajı (sayfa içeriği olabilir) hiçbir yere yazılmaz
    assert "gizli" not in str(sink.events) and "gizli" not in str(scan.meta)


async def test_lanes_run_in_parallel(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    modules: list[DeepModule] = [
        Pause("a", Lane.BROWSER, 0.3),
        Pause("b", Lane.LIGHTHOUSE, 0.3),
        Pause("c", Lane.NETWORK, 0.3),
    ]
    orch, _ = orchestrator(sessionmaker, modules)
    started = time.monotonic()
    await orch.run(deep)
    assert time.monotonic() - started < 0.8  # sıralı olsaydı ≥ 0.9 sn


async def test_lane_budget_exhausted_skips_rest_of_lane(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    modules: list[DeepModule] = [Pause("first", Lane.NETWORK, 5), Pause("second", Lane.NETWORK, 0)]
    orch, _ = orchestrator(
        sessionmaker, modules, settings=settings(deep_budget_network_s=0.2, deep_budget_module_s=5)
    )
    await orch.run(deep)
    scan = await get_scan(sessionmaker, deep)
    assert scan.progress["modules"]["first"]["status"] == "partial"
    assert scan.progress["modules"]["second"]["status"] == "partial"  # hiç başlamadı
    assert scan.meta["deep"]["partial_modules"] == ["first", "second"]


async def test_hard_timeout_finishes_with_partial(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    orch, _ = orchestrator(
        sessionmaker,
        [Sleepy(), Pause("later", Lane.BROWSER, 0)],
        settings=settings(
            deep_scan_timeout_s=0.3, deep_budget_browser_s=10, deep_budget_module_s=10
        ),
    )
    assert await orch.run(deep) == ScanStatus.DONE
    scan = await get_scan(sessionmaker, deep)
    assert set(scan.meta["deep"]["partial_modules"]) == {"sleepy", "later"}
    assert {r.check_id for r in await rows(sessionmaker, deep)} == {"web.deep.sleepy"}


async def test_refuted_phase1_findings_excluded_from_score(sessionmaker: SM) -> None:
    parent = await add_parent(
        sessionmaker,
        findings=[("web.render.empty_server_html", Severity.CRITICAL, Category.SECURITY)],
    )
    deep = await add_deep(sessionmaker, parent)

    def refute(ctx: DeepContext, finding: ParentFinding) -> Verification:
        return Verification.REFUTED

    orch, _ = orchestrator(
        sessionmaker, [], verification_rules={"web.render.empty_server_html": refute}
    )
    await orch.run(deep)
    scan = await get_scan(sessionmaker, deep)
    (copy,) = await rows(sessionmaker, deep)
    assert copy.verification == Verification.REFUTED
    assert scan.score == 100


async def test_deep_scores_use_deep_weights(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    orch, _ = orchestrator(sessionmaker, [Quick()])
    await orch.run(deep)
    scan = await get_scan(sessionmaker, deep)
    assert set(scan.category_scores) >= {"security", "prod_readiness", "performance", "conversion"}


async def test_sensitive_data_escalates_deep_findings(sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker, meta={"collects_special_category_data": True})
    deep = await add_deep(sessionmaker, parent)
    orch, _ = orchestrator(sessionmaker, [Quick()])
    await orch.run(deep)
    (row,) = await rows(sessionmaker, deep)
    assert row.severity == Severity.HIGH
    assert row.evidence["original_severity"] == "medium"


async def test_claim_is_idempotent_and_phase1_ignored(sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker)
    deep = await add_deep(sessionmaker, parent)
    orch, _ = orchestrator(sessionmaker, [])
    assert await orch.run(deep) == ScanStatus.DONE
    assert await orch.run(deep) is None  # tekrar çalışan job
    assert await orch.run(parent) is None  # Faz 1 taraması Faz 2 kuyruğunda çalışmaz


async def test_missing_parent_fails(sessionmaker: SM) -> None:
    deep = await add_deep(sessionmaker, None)
    orch, _ = orchestrator(sessionmaker, [])
    assert await orch.run(deep) is None
    scan = await get_scan(sessionmaker, deep)
    assert (scan.status, scan.error_code) == (ScanStatus.FAILED, "parent_not_found")


async def test_db_event_sink_writes_events(sessionmaker: SM) -> None:
    """events_factory verilmezse olaylar scan_events tablosuna yazılır."""
    deep = await add_deep(sessionmaker, await add_parent(sessionmaker))
    cfg = OrchestratorConfig(settings=settings(), modules=[Quick()])
    await DeepOrchestrator(sessionmaker, cfg).run(deep)
    async with sessionmaker() as s:
        keys = list(
            (
                await s.scalars(
                    select(ScanEvent.key).where(ScanEvent.scan_id == deep).order_by(ScanEvent.id)
                )
            ).all()
        )
    assert keys[0] == "deep.event.started" and keys[-1] == "deep.event.done"
    assert "deep.event.module_done" in keys
