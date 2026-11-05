import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Category, Check, Finding, ScanContext, Severity, TargetType
from launchcheck.checks.registry import Registry
from launchcheck.collectors.base import Collector, CollectorError
from launchcheck.db.models import FindingRow, Scan, ScanStatus
from launchcheck.worker.pipeline import PipelineConfig, execute_scan

W = {"security": 0.7, "prod_readiness": 0.3}


class TrackingContext(ScanContext):
    closed = False

    async def aclose(self) -> None:
        TrackingContext.closed = True


class FakeCollector:
    def __init__(self, error: Exception | None = None, delay: float = 0) -> None:
        self.error = error
        self.delay = delay

    async def collect(self, scan_id: uuid.UUID, args: dict[str, Any]) -> ScanContext:
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return TrackingContext(TargetType.WEB, "https://example.com", meta={"platform": ["vite"]})


class CriticalCheck(Check):
    id = "web.test.critical"
    target = TargetType.WEB
    category = Category.SECURITY
    default_severity = Severity.CRITICAL

    async def run(self, ctx: ScanContext) -> list[Finding]:
        return [self.finding({"masked": "sk-pro…1234"})]


class BoomCheck(Check):
    id = "web.test.boom"
    target = TargetType.WEB
    category = Category.SECURITY
    default_severity = Severity.LOW

    async def run(self, ctx: ScanContext) -> list[Finding]:
        raise RuntimeError


def config(collector: Collector, timeout: float = 5) -> PipelineConfig:
    reg = Registry()
    reg.register(CriticalCheck)
    reg.register(BoomCheck)
    return PipelineConfig(
        registry=reg,
        resolve_collector=lambda _: collector,
        timeouts_s={TargetType.WEB: timeout, TargetType.APK: timeout},
        score_weights=W,
    )


async def new_scan(
    sm: async_sessionmaker[AsyncSession], status: ScanStatus = ScanStatus.QUEUED
) -> uuid.UUID:
    async with sm() as s:
        scan = Scan(target_type=TargetType.WEB, target_label="https://example.com", status=status)
        s.add(scan)
        await s.commit()
        return scan.id


async def load(
    sm: async_sessionmaker[AsyncSession], scan_id: uuid.UUID
) -> tuple[Scan, list[FindingRow]]:
    async with sm() as s:
        scan = await s.get(Scan, scan_id)
        rows = (await s.scalars(select(FindingRow).where(FindingRow.scan_id == scan_id))).all()
        assert scan is not None
        return scan, list(rows)


async def test_successful_scan(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    TrackingContext.closed = False
    scan_id = await new_scan(sessionmaker)
    status = await execute_scan(sessionmaker, scan_id, {}, config(FakeCollector()))
    assert status == ScanStatus.DONE

    scan, rows = await load(sessionmaker, scan_id)
    assert scan.status == ScanStatus.DONE
    assert scan.score == 49  # critical tavanı
    assert scan.category_scores == {"security": 70, "prod_readiness": 100}
    assert scan.meta == {"platform": ["vite"], "checks_failed": ["web.test.boom"]}
    assert scan.finished_at is not None
    assert [r.check_id for r in rows] == ["web.test.critical"]
    assert rows[0].evidence == {"masked": "sk-pro…1234"}
    assert TrackingContext.closed  # geçici kaynaklar her durumda temizlenir


@pytest.mark.parametrize(
    ("collector", "code"),
    [
        (FakeCollector(error=CollectorError("collector_unavailable")), "collector_unavailable"),
        (FakeCollector(error=RuntimeError("içerik sızmasın")), "internal_error"),
        (FakeCollector(delay=1), "scan_timeout"),
    ],
)
async def test_failed_scan(
    sessionmaker: async_sessionmaker[AsyncSession], collector: FakeCollector, code: str
) -> None:
    scan_id = await new_scan(sessionmaker)
    await execute_scan(sessionmaker, scan_id, {}, config(collector, timeout=0.05))
    scan, rows = await load(sessionmaker, scan_id)
    assert scan.status == ScanStatus.FAILED
    assert scan.error_code == code
    assert scan.score is None
    assert rows == []


async def test_non_queued_scan_is_skipped(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    scan_id = await new_scan(sessionmaker, ScanStatus.DONE)
    assert await execute_scan(sessionmaker, scan_id, {}, config(FakeCollector())) is None


async def test_missing_scan_is_skipped(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    assert await execute_scan(sessionmaker, uuid.uuid4(), {}, config(FakeCollector())) is None
