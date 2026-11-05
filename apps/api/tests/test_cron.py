import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.db.models import FindingRow, Scan, ScanStatus, utcnow
from launchcheck.worker.cron import delete_expired_scans, fail_stale_scans


async def add_scan(
    sm: async_sessionmaker[AsyncSession], age: timedelta, status: ScanStatus = ScanStatus.DONE
) -> uuid.UUID:
    async with sm() as s:
        scan = Scan(
            target_type=TargetType.WEB,
            target_label="https://example.com",
            status=status,
            created_at=utcnow() - age,
        )
        s.add(scan)
        await s.flush()
        s.add(
            FindingRow(
                scan_id=scan.id,
                check_id="web.a.b",
                severity=Severity.LOW,
                category=Category.SECURITY,
                evidence={},
            )
        )
        await s.commit()
        return scan.id


async def test_delete_expired(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    old = await add_scan(sessionmaker, timedelta(days=7, minutes=1))
    fresh = await add_scan(sessionmaker, timedelta(days=6, hours=23))

    assert await delete_expired_scans(sessionmaker, retention_days=7) == 1

    async with sessionmaker() as s:
        assert await s.get(Scan, old) is None
        assert await s.get(Scan, fresh) is not None
        orphan = await s.scalar(
            select(func.count()).select_from(FindingRow).where(FindingRow.scan_id == old)
        )
        assert orphan == 0
        assert await s.scalar(select(func.count()).select_from(FindingRow)) == 1


async def test_fail_stale(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    stuck = await add_scan(sessionmaker, timedelta(minutes=20), ScanStatus.RUNNING)
    recent = await add_scan(sessionmaker, timedelta(minutes=1), ScanStatus.RUNNING)
    done = await add_scan(sessionmaker, timedelta(hours=2), ScanStatus.DONE)

    assert await fail_stale_scans(sessionmaker, stale_after_s=900) == 1

    async with sessionmaker() as s:
        stuck_scan = await s.get(Scan, stuck)
        assert stuck_scan is not None
        assert (stuck_scan.status, stuck_scan.error_code) == (ScanStatus.FAILED, "scan_stale")
        assert (await s.get(Scan, recent)).status == ScanStatus.RUNNING  # type: ignore[union-attr]
        assert (await s.get(Scan, done)).status == ScanStatus.DONE  # type: ignore[union-attr]
