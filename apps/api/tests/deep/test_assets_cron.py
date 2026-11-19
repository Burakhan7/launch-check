"""Asset deposu, saklama süresi (Faz 2 verisi dahil) ve takılı Faz 2 taramaları."""

import os
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from launchcheck.db.models import FindingRow, Scan, ScanAsset, ScanEvent, ScanStatus
from launchcheck.deep.assets import AssetError, AssetStore
from launchcheck.worker.cron import StaleLimits, delete_expired_scans, fail_stale_scans
from tests.deep.helpers import add_deep, add_parent

SM = async_sessionmaker[AsyncSession]
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


def make_old(path: Path, seconds: int = 7200) -> None:
    past = time.time() - seconds
    os.utime(path, (past, past))


def test_asset_store_validation(tmp_path: Path) -> None:
    store = AssetStore(tmp_path, 64)
    scan_id = uuid.uuid4()
    with pytest.raises(AssetError):
        store.save(scan_id, "screenshot", "/", b"\x89PNG....", 1, 1)
    with pytest.raises(AssetError):
        store.save(scan_id, "screenshot", "/", WEBP + b"\x00" * 100, 1, 1)
    asset = store.save(scan_id, "screenshot", "/", WEBP, 10, 20)
    assert store.resolve(asset.path) == (tmp_path / asset.path).resolve()
    assert store.resolve("../etc/passwd") is None
    assert store.resolve(f"{scan_id}/missing.webp") is None


def test_sweep_orphans(tmp_path: Path) -> None:
    store = AssetStore(tmp_path, 1024)
    known_scan, gone_scan, fresh_scan = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    kept = store.save(known_scan, "s", "/", WEBP, 1, 1)
    orphan_file = store.save(known_scan, "s", "/", WEBP, 1, 1)
    store.save(gone_scan, "s", "/", WEBP, 1, 1)
    store.save(fresh_scan, "s", "/", WEBP, 1, 1)  # yeni yazılıyor: yaşı küçük, dokunulmaz
    for path in (tmp_path / str(known_scan), tmp_path / str(gone_scan)):
        for file in path.iterdir():
            make_old(file)
        make_old(path)

    known = {str(known_scan): {Path(kept.path).name}}
    assert store.sweep_orphans(known, max_age_s=3600) == 2
    assert (tmp_path / kept.path).exists()
    assert not (tmp_path / orphan_file.path).exists()
    assert not (tmp_path / str(gone_scan)).exists()
    assert (tmp_path / str(fresh_scan)).exists()


async def test_expired_parent_deletes_deep_scan_events_assets_and_files(
    sessionmaker: SM, tmp_path: Path
) -> None:
    store = AssetStore(tmp_path, 1024)
    old_parent = await add_parent(sessionmaker, age=timedelta(days=7, minutes=1))
    deep = await add_deep(sessionmaker, old_parent, age=timedelta(days=6))
    fresh_parent = await add_parent(sessionmaker)
    fresh_deep = await add_deep(sessionmaker, fresh_parent)
    asset = store.save(deep, "s", "/", WEBP, 1, 1)
    fresh_asset = store.save(fresh_deep, "s", "/", WEBP, 1, 1)
    async with sessionmaker() as s:
        s.add_all([asset, fresh_asset])
        s.add(ScanEvent(scan_id=deep, module="core", key="deep.event.started", params={}))
        await s.commit()

    assert await delete_expired_scans(sessionmaker, 7, store) == 2
    async with sessionmaker() as s:
        assert {i for i in (await s.scalars(select(Scan.id))).all()} == {fresh_parent, fresh_deep}
        assert await s.scalar(select(func.count()).select_from(ScanEvent)) == 0
        assert await s.scalar(select(func.count()).select_from(ScanAsset)) == 1
        assert await s.scalar(select(func.count()).select_from(FindingRow)) == 0
    assert not (tmp_path / str(deep)).exists()
    assert (tmp_path / fresh_asset.path).exists()


async def test_fail_stale_deep_scans(sessionmaker: SM) -> None:
    parent = await add_parent(sessionmaker)
    waiting_long = await add_deep(sessionmaker, parent, age=timedelta(hours=7))
    waiting_ok = await add_deep(sessionmaker, parent, age=timedelta(hours=1))
    running_stuck = await add_deep(
        sessionmaker,
        parent,
        status=ScanStatus.RUNNING,
        age=timedelta(hours=2),
        started_ago=timedelta(minutes=30),
    )
    running_ok = await add_deep(
        sessionmaker,
        parent,
        status=ScanStatus.RUNNING,
        age=timedelta(hours=2),
        started_ago=timedelta(minutes=5),
    )
    limits = StaleLimits(quick_s=900, deep_queued_s=6 * 3600, deep_running_s=1500)
    assert await fail_stale_scans(sessionmaker, limits) == 2
    async with sessionmaker() as s:
        status = {
            sid: (await s.get(Scan, sid)).status
            for sid in (waiting_long, waiting_ok, running_stuck, running_ok)  # type: ignore[union-attr]
        }
    assert status[waiting_long] == ScanStatus.FAILED
    assert status[running_stuck] == ScanStatus.FAILED
    assert status[waiting_ok] == ScanStatus.QUEUED  # Faz 1'in 15 dk sınırı uygulanmaz
    assert status[running_ok] == ScanStatus.RUNNING
