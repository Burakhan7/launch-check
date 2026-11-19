"""Faz 2 orkestratörü: şeritler, bütçeler, modül yaşam döngüsü, olay yayını.

Akış: taramayı al (queued → running) → Faz 1 bulgularını kopyala → şeritleri paralel çalıştır
(her modül bittikçe bulguları DB'ye yazılır) → Faz 1 doğrulama kuralları → skor → done.
Bir modülün hatası veya zaman aşımı taramayı durdurmaz. Sert sınır dolarsa tarama elindekiyle
tamamlanır (bitmeyen modüller `partial_modules`).
"""

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Finding
from launchcheck.config import Settings
from launchcheck.db.models import FindingRow, Scan, ScanStatus, Verification, utcnow
from launchcheck.deep.assets import AssetStore
from launchcheck.deep.context import DeepContext, ParentFinding
from launchcheck.deep.events import DbEventSink, EventSink
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput, VerificationRule, lane_budget_s
from launchcheck.scoring import compute_score, escalate_for_sensitive_data

logger = logging.getLogger(__name__)

CORE = "core"


class ModuleStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class OrchestratorConfig:
    settings: Settings
    modules: Sequence[DeepModule]
    verification_rules: Mapping[str, VerificationRule] = field(default_factory=dict)
    # Önemi özel nitelikli veriyi zaten hesaba katan check'ler (çift yükseltme olmasın)
    special_data_aware: Sequence[str] = ()
    assets: AssetStore | None = None
    events_factory: Callable[[uuid.UUID], EventSink] | None = None


@dataclass
class _RunState:
    progress: dict[str, dict[str, Any]]
    partial: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DeepOrchestrator:
    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], cfg: OrchestratorConfig
    ) -> None:
        self._sm = sessionmaker
        self._cfg = cfg

    async def run(self, scan_id: uuid.UUID) -> ScanStatus | None:
        started = time.monotonic()
        claimed = await self._claim(scan_id)
        if claimed is None:
            return None
        scan, parent, parent_rows = claimed
        events = (
            self._cfg.events_factory(scan_id)
            if self._cfg.events_factory
            else DbEventSink(self._sm, scan_id)
        )
        ctx = self._context(scan, parent, parent_rows, events)
        state = _RunState(
            progress={m.name: {"status": ModuleStatus.PENDING, "found": 0} for m in self._modules}
        )
        try:
            await events.emit(CORE, "deep.event.started")
            await self._copy_parent_findings(scan_id, parent_rows)
            await events.emit(CORE, "deep.event.phase1_copied", count=len(parent_rows))
            await self._save_progress(scan_id, state)
            try:
                async with asyncio.timeout(self._cfg.settings.deep_scan_timeout_s):
                    await asyncio.gather(
                        *(
                            self._run_lane(ctx, lane, mods, state)
                            for lane, mods in self._lanes().items()
                        )
                    )
            except TimeoutError:
                self._mark_unfinished_partial(state)
            await self._verify(ctx)
            status = await self._finish(scan_id, ctx, state, time.monotonic() - started)
            await events.emit(CORE, "deep.event.done")
            return status
        except Exception as exc:
            logger.error("derin tarama başarısız (%s)", type(exc).__name__)
            await self._fail(scan_id, "internal_error")
            return ScanStatus.FAILED
        finally:
            await ctx.aclose()

    # --- hazırlık

    @property
    def _modules(self) -> Sequence[DeepModule]:
        return self._cfg.modules

    def _lanes(self) -> dict[Lane, list[DeepModule]]:
        lanes: dict[Lane, list[DeepModule]] = defaultdict(list)
        for module in self._modules:
            lanes[module.lane].append(module)
        return lanes

    async def _claim(self, scan_id: uuid.UUID) -> tuple[Scan, Scan, list[FindingRow]] | None:
        """Sadece `queued` Faz 2 taramasını `running`'e alır (tekrar çalışan job'a karşı)."""
        async with self._sm() as session:
            scan = await session.get(Scan, scan_id, with_for_update=True)
            if scan is None or scan.status != ScanStatus.QUEUED or scan.phase != 2:
                return None
            parent = await session.get(Scan, scan.parent_scan_id) if scan.parent_scan_id else None
            if parent is None:
                scan.status = ScanStatus.FAILED
                scan.error_code = "parent_not_found"
                scan.finished_at = utcnow()
                await session.commit()
                return None
            scan.status = ScanStatus.RUNNING
            scan.started_at = utcnow()
            rows = list(
                (
                    await session.scalars(select(FindingRow).where(FindingRow.scan_id == parent.id))
                ).all()
            )
            await session.commit()
            return scan, parent, rows

    def _context(
        self, scan: Scan, parent: Scan, rows: list[FindingRow], events: EventSink
    ) -> DeepContext:
        return DeepContext(
            target_type=scan.target_type,
            target_label=scan.target_label,
            scan_id=scan.id,
            parent_scan_id=parent.id,
            target_url=parent.target_label,
            parent_meta=dict(parent.meta or {}),
            parent_findings=[
                ParentFinding(r.check_id, r.severity, r.category, dict(r.evidence or {}))
                for r in rows
            ],
            options=dict(scan.options or {}),
            events=events,
            assets=self._cfg.assets,
        )

    async def _copy_parent_findings(self, scan_id: uuid.UUID, rows: list[FindingRow]) -> None:
        """Faz 2 raporu tek başına eksiksiz olsun: Faz 1 bulgularının kopyaları
        (`source_phase=1`). Doğrulama kuralı yoksa `not_verifiable` kalır."""
        async with self._sm() as session:
            session.add_all(
                FindingRow(
                    scan_id=scan_id,
                    check_id=r.check_id,
                    severity=r.severity,
                    category=r.category,
                    evidence=dict(r.evidence or {}),
                    source_phase=1,
                    verification=Verification.NOT_VERIFIABLE,
                )
                for r in rows
            )
            await session.commit()

    # --- şeritler

    async def _run_lane(
        self, ctx: DeepContext, lane: Lane, modules: list[DeepModule], state: _RunState
    ) -> None:
        deadline = time.monotonic() + lane_budget_s(lane, self._cfg.settings)
        for module in modules:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                state.partial.append(module.name)
                await self._set_module(ctx, state, module.name, ModuleStatus.PARTIAL, 0)
                continue
            await self._run_module(
                ctx, module, min(remaining, module.budget_s(self._cfg.settings)), state
            )

    async def _run_module(
        self, ctx: DeepContext, module: DeepModule, budget_s: float, state: _RunState
    ) -> None:
        out = ModuleOutput()
        await self._set_module(ctx, state, module.name, ModuleStatus.RUNNING, 0)
        await ctx.events.emit(module.name, "deep.event.module_started", module=module.name)
        status = ModuleStatus.DONE
        try:
            async with asyncio.timeout(budget_s):
                await module.run(ctx, out)
        except TimeoutError:
            status = ModuleStatus.PARTIAL
        except asyncio.CancelledError:
            # Sert sınır: eldekiler yazılır, iptal yukarı iletilir
            await self._store_findings(ctx, module.name, out.findings)
            state.partial.append(module.name)
            await self._set_module(ctx, state, module.name, ModuleStatus.PARTIAL, len(out.findings))
            raise
        except Exception as exc:
            # İstisna mesajı sayfa içeriği taşıyabilir: sadece tipi logla
            logger.warning("modül başarısız: %s (%s)", module.name, type(exc).__name__)
            status = ModuleStatus.FAILED
        found = await self._store_findings(ctx, module.name, out.findings)
        if status == ModuleStatus.PARTIAL:
            state.partial.append(module.name)
        elif status == ModuleStatus.FAILED:
            state.failed.append(module.name)
        await self._set_module(ctx, state, module.name, status, found)
        key = {
            ModuleStatus.DONE: "deep.event.module_done",
            ModuleStatus.PARTIAL: "deep.event.module_partial",
            ModuleStatus.FAILED: "deep.event.module_failed",
        }[status]
        await ctx.events.emit(module.name, key, module=module.name, found=found)

    async def _store_findings(self, ctx: DeepContext, module: str, findings: list[Finding]) -> int:
        """Modül bittiği anda yazılır: rapor sayfası bulguları erken gösterir."""
        if not findings:
            return 0
        escalated = escalate_for_sensitive_data(
            findings, ctx.parent_meta, skip=list(self._cfg.special_data_aware)
        )
        async with self._sm() as session:
            session.add_all(
                FindingRow(
                    scan_id=ctx.scan_id,
                    check_id=f.check_id,
                    severity=f.severity,
                    category=f.category,
                    evidence=f.evidence,
                    source_phase=2,
                    module=module,
                )
                for f in escalated
            )
            await session.commit()
        return len(escalated)

    async def _set_module(
        self, ctx: DeepContext, state: _RunState, name: str, status: str, found: int
    ) -> None:
        async with state.lock:
            state.progress[name] = {"status": status, "found": found}
        await self._save_progress(ctx.scan_id, state)

    async def _save_progress(self, scan_id: uuid.UUID, state: _RunState) -> None:
        async with state.lock, self._sm() as session:
            scan = await session.get(Scan, scan_id)
            if scan is not None:
                scan.progress = {"modules": {k: dict(v) for k, v in state.progress.items()}}
                await session.commit()

    def _mark_unfinished_partial(self, state: _RunState) -> None:
        for name, info in state.progress.items():
            if info["status"] in (ModuleStatus.PENDING, ModuleStatus.RUNNING):
                info["status"] = ModuleStatus.PARTIAL
                if name not in state.partial:
                    state.partial.append(name)

    # --- doğrulama, skor, bitiş

    async def _verify(self, ctx: DeepContext) -> None:
        rules = self._cfg.verification_rules
        if not rules:
            return
        async with self._sm() as session:
            copies = (
                await session.scalars(
                    select(FindingRow).where(
                        FindingRow.scan_id == ctx.scan_id, FindingRow.source_phase == 1
                    )
                )
            ).all()
            for row in copies:
                rule = rules.get(row.check_id)
                if rule is None:
                    continue
                parent = ParentFinding(row.check_id, row.severity, row.category, row.evidence)
                try:
                    result = rule(ctx, parent)
                except Exception as exc:
                    logger.warning(
                        "doğrulama kuralı başarısız: %s (%s)", row.check_id, type(exc).__name__
                    )
                    continue
                if result is not None:
                    row.verification = result
            await session.commit()

    async def _finish(
        self, scan_id: uuid.UUID, ctx: DeepContext, state: _RunState, duration_s: float
    ) -> ScanStatus:
        async with self._sm() as session:
            scan = await session.get(Scan, scan_id)
            if scan is None:
                return ScanStatus.FAILED
            rows = (
                await session.scalars(select(FindingRow).where(FindingRow.scan_id == scan_id))
            ).all()
            # Yanlış alarm çıkan Faz 1 bulguları skora girmez
            scored = [
                Finding(r.check_id, r.severity, r.category, r.evidence)
                for r in rows
                if r.verification != Verification.REFUTED
            ]
            score = compute_score(scored, self._cfg.settings.score_weights_deep)
            scan.score = score.overall
            scan.category_scores = score.categories
            scan.meta = {
                **ctx.parent_meta,
                **ctx.meta,
                "deep": {
                    "partial_modules": state.partial,
                    "failed_modules": state.failed,
                    "duration_s": round(duration_s),
                },
            }
            scan.progress = {"modules": state.progress}
            scan.status = ScanStatus.DONE
            scan.finished_at = utcnow()
            await session.commit()
            return ScanStatus.DONE

    async def _fail(self, scan_id: uuid.UUID, code: str) -> None:
        async with self._sm() as session:
            scan = await session.get(Scan, scan_id)
            if scan is not None:
                scan.status = ScanStatus.FAILED
                scan.error_code = code
                scan.finished_at = utcnow()
                await session.commit()
