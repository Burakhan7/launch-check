"""Periyodik bakım işleri."""

from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.db.models import FindingRow, Scan, ScanStatus, utcnow


async def delete_expired_scans(
    sessionmaker: async_sessionmaker[AsyncSession], retention_days: int
) -> int:
    """`retention_days`'den eski taramaları ve bulgularını siler. Silinen tarama sayısını döner."""
    cutoff = utcnow() - timedelta(days=retention_days)
    expired = select(Scan.id).where(Scan.created_at < cutoff)
    async with sessionmaker() as session:
        # Bulgular açıkça silinir: FK cascade'e güvenmeden (her DB'de çalışsın).
        await session.execute(delete(FindingRow).where(FindingRow.scan_id.in_(expired)))
        result = await session.execute(delete(Scan).where(Scan.created_at < cutoff))
        await session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def fail_stale_scans(
    sessionmaker: async_sessionmaker[AsyncSession], stale_after_s: int
) -> int:
    """Worker çöktüğü için takılı kalan taramaları `failed` yapar (frontend sonsuz beklemesin)."""
    cutoff = utcnow() - timedelta(seconds=stale_after_s)
    async with sessionmaker() as session:
        result = await session.execute(
            update(Scan)
            .where(
                Scan.status.in_([ScanStatus.QUEUED, ScanStatus.RUNNING]),
                Scan.created_at < cutoff,
            )
            .values(status=ScanStatus.FAILED, error_code="scan_stale", finished_at=utcnow())
        )
        await session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
