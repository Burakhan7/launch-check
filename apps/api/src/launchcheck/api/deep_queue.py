"""Faz 2 kuyruk sırası ve tahmini süre. Sadece DB'deki tarama zamanlarından hesaplanır
(kişisel veri yok)."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from launchcheck.db.models import Scan, ScanStatus, utcnow

ETA_SAMPLE = 20  # son bu kadar tamamlanan Faz 2 taramasının ortalaması


@dataclass(frozen=True)
class QueueInfo:
    position: int  # 0: çalışıyor; 1: sıradaki; ...
    eta_s: int


async def waiting_count(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(Scan)
        .where(Scan.phase == 2, Scan.status == ScanStatus.QUEUED)
    )
    return int((await session.scalar(stmt)) or 0)


async def average_duration_s(session: AsyncSession, default_s: int) -> int:
    rows = (
        await session.execute(
            select(Scan.started_at, Scan.finished_at)
            .where(
                Scan.phase == 2,
                Scan.status == ScanStatus.DONE,
                Scan.started_at.is_not(None),
                Scan.finished_at.is_not(None),
            )
            .order_by(Scan.finished_at.desc())
            .limit(ETA_SAMPLE)
        )
    ).all()
    durations = [(f - s).total_seconds() for s, f in rows if s and f and f >= s]
    return round(sum(durations) / len(durations)) if durations else default_s


def _aware(value: datetime) -> datetime:
    # SQLite saat dilimini saklamaz; karşılaştırma için UTC varsayılır
    return value if value.tzinfo else value.replace(tzinfo=utcnow().tzinfo)


async def queue_info(session: AsyncSession, scan: Scan, default_s: int) -> QueueInfo | None:
    """Sadece bekleyen/çalışan Faz 2 taramaları için."""
    if scan.phase != 2 or scan.status not in (ScanStatus.QUEUED, ScanStatus.RUNNING):
        return None
    average = await average_duration_s(session, default_s)
    if scan.status == ScanStatus.RUNNING:
        elapsed = (utcnow() - _aware(scan.started_at or scan.created_at)).total_seconds()
        return QueueInfo(0, max(0, round(average - elapsed)))
    ahead = await session.scalar(
        select(func.count())
        .select_from(Scan)
        .where(
            Scan.phase == 2,
            Scan.status.in_([ScanStatus.QUEUED, ScanStatus.RUNNING]),
            Scan.created_at < scan.created_at,
            Scan.id != scan.id,
        )
    )
    position = int(ahead or 0) + 1
    return QueueInfo(position, average * position)
