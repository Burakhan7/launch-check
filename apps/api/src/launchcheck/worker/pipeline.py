"""Tek bir taramanın uçtan uca yürütülmesi: collect → check'ler → skor → DB."""

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import ScanContext, TargetType
from launchcheck.checks.registry import CheckRunResult, Registry, run_checks
from launchcheck.collectors.base import Collector, CollectorError
from launchcheck.db.models import FindingRow, Scan, ScanStatus, utcnow
from launchcheck.scoring import compute_score, escalate_for_sensitive_data

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineConfig:
    registry: Registry
    resolve_collector: Callable[[TargetType], Collector]
    timeouts_s: Mapping[TargetType, float]
    score_weights: Mapping[str, float]


async def _claim(session: AsyncSession, scan_id: uuid.UUID) -> Scan | None:
    """Sadece `queued` taramayı `running`'e alır (tekrar çalıştırılan job'a karşı idempotent)."""
    scan = await session.get(Scan, scan_id, with_for_update=True)
    if scan is None or scan.status != ScanStatus.QUEUED:
        return None
    scan.status = ScanStatus.RUNNING
    await session.commit()
    return scan


async def _collect_and_check(
    scan: Scan, args: dict[str, Any], cfg: PipelineConfig
) -> tuple[ScanContext, CheckRunResult]:
    collector = cfg.resolve_collector(scan.target_type)
    ctx = await collector.collect(scan.id, args)
    try:
        checks = cfg.registry.for_target(scan.target_type, scan.phase)
        return ctx, await run_checks(ctx, checks)
    finally:
        await ctx.aclose()


def _apply_result(
    session: AsyncSession,
    scan: Scan,
    ctx: ScanContext,
    result: CheckRunResult,
    cfg: PipelineConfig,
) -> None:
    aware = [c.id for c in cfg.registry.all() if c.considers_special_data]
    findings = escalate_for_sensitive_data(result.findings, ctx.meta, skip=aware)
    score = compute_score(findings, cfg.score_weights)
    session.add_all(
        FindingRow(
            scan_id=scan.id,
            check_id=f.check_id,
            severity=f.severity,
            category=f.category,
            evidence=f.evidence,
        )
        for f in findings
    )
    scan.target_label = ctx.target_label or scan.target_label
    scan.score = score.overall
    scan.category_scores = score.categories
    scan.meta = {**ctx.meta, "checks_failed": result.failed}
    scan.status = ScanStatus.DONE


def _fail(scan: Scan, code: str) -> None:
    scan.status = ScanStatus.FAILED
    scan.error_code = code


async def execute_scan(
    sessionmaker: async_sessionmaker[AsyncSession],
    scan_id: uuid.UUID,
    args: dict[str, Any],
    cfg: PipelineConfig,
) -> ScanStatus | None:
    async with sessionmaker() as session:
        scan = await _claim(session, scan_id)
        if scan is None:
            return None
        try:
            async with asyncio.timeout(cfg.timeouts_s[scan.target_type]):
                ctx, result = await _collect_and_check(scan, args, cfg)
            _apply_result(session, scan, ctx, result, cfg)
        except TimeoutError:
            _fail(scan, "scan_timeout")
        except CollectorError as exc:
            _fail(scan, exc.code)
        except Exception as exc:
            logger.error("tarama başarısız (%s)", type(exc).__name__)
            _fail(scan, "internal_error")
        scan.finished_at = utcnow()
        await session.commit()
        return scan.status
