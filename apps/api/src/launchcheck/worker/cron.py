"""Periyodik bakım işleri."""

import asyncio
import shutil
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.db.models import FindingRow, Scan, ScanAsset, ScanEvent, ScanStatus, utcnow
from launchcheck.deep.assets import AssetStore


async def delete_expired_scans(
    sessionmaker: async_sessionmaker[AsyncSession],
    retention_days: int,
    assets: AssetStore | None = None,
) -> int:
    """`retention_days`'den eski taramaları ve tüm verilerini siler. Dayandığı Faz 1 taraması
    silinen Faz 2 taramaları da silinir (FK CASCADE ile aynı davranış). Asset dosyaları DB
    kayıtlarından ÖNCE diskten silinir. Silinen tarama sayısını döner."""
    cutoff = utcnow() - timedelta(days=retention_days)
    async with sessionmaker() as session:
        ids = set((await session.scalars(select(Scan.id).where(Scan.created_at < cutoff))).all())
        children = set(
            (await session.scalars(select(Scan.id).where(Scan.parent_scan_id.in_(ids)))).all()
        )
        ids |= children
        if not ids:
            return 0
        if assets is not None:
            for scan_id in ids:
                assets.delete_scan(scan_id)
        # Bağlı kayıtlar açıkça silinir: FK cascade'e güvenmeden (her DB'de çalışsın).
        for model in (ScanEvent, ScanAsset, FindingRow):
            await session.execute(delete(model).where(model.scan_id.in_(ids)))
        await session.execute(
            update(Scan).where(Scan.compare_to_id.in_(ids)).values(compare_to_id=None)
        )
        await session.execute(delete(Scan).where(Scan.id.in_(children)))
        result = await session.execute(delete(Scan).where(Scan.id.in_(ids - children)))
        await session.commit()
        return int(result.rowcount or 0) + len(children)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class StaleLimits:
    """Faz 1: oluşturulmadan bu yana süre. Faz 2: kuyrukta bekleme ve çalışma süreleri ayrı
    (Faz 2 kuyruğu uzun olabilir; 15 dk'lık Faz 1 sınırı bekleyen derin taramaları öldürürdü)."""

    quick_s: int
    deep_queued_s: int | None = None
    deep_running_s: int | None = None


async def fail_stale_scans(
    sessionmaker: async_sessionmaker[AsyncSession], stale_after_s: int | StaleLimits
) -> int:
    """Worker çöktüğü için takılı kalan taramaları `failed` yapar (frontend sonsuz beklemesin)."""
    limits = (
        stale_after_s
        if isinstance(stale_after_s, StaleLimits)
        else StaleLimits(quick_s=stale_after_s)
    )
    now = utcnow()
    active = Scan.status.in_([ScanStatus.QUEUED, ScanStatus.RUNNING])
    conditions = [
        and_(active, Scan.phase == 1, Scan.created_at < now - timedelta(seconds=limits.quick_s))
    ]
    if limits.deep_queued_s is not None:
        conditions.append(
            and_(
                Scan.phase == 2,
                Scan.status == ScanStatus.QUEUED,
                Scan.created_at < now - timedelta(seconds=limits.deep_queued_s),
            )
        )
    if limits.deep_running_s is not None:
        conditions.append(
            and_(
                Scan.phase == 2,
                Scan.status == ScanStatus.RUNNING,
                Scan.started_at < now - timedelta(seconds=limits.deep_running_s),
            )
        )
    async with sessionmaker() as session:
        result = await session.execute(
            update(Scan)
            .where(or_(*conditions))
            .values(status=ScanStatus.FAILED, error_code="scan_stale", finished_at=now)
        )
        await session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


def _delete_old_entries(directory: Path, max_age_s: int) -> int:
    if not directory.is_dir():
        return 0
    cutoff = time.time() - max_age_s
    deleted = 0
    for entry in directory.iterdir():
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            continue
    return deleted


async def delete_orphan_uploads(upload_dir: Path, tmp_dir: Path, max_age_s: int) -> int:
    """Worker çökmesi gibi durumlarda geride kalan APK'ları ve geçici klasörleri siler."""

    def _run() -> int:
        return _delete_old_entries(upload_dir, max_age_s) + _delete_old_entries(tmp_dir, max_age_s)

    return await asyncio.to_thread(_run)
