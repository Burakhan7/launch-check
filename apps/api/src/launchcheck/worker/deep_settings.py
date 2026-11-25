"""Faz 2 worker'ı. Çalıştırma: `arq launchcheck.worker.deep_settings.DeepWorkerSettings`.

Sadece `deep` kuyruğunu dinler; Faz 1 worker'ı ve kuyruğu bundan etkilenmez.
"""

import logging
import uuid
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from launchcheck.api.queue import DEEP_QUEUE
from launchcheck.config import get_settings
from launchcheck.db.models import ScanAsset
from launchcheck.db.session import get_engine, get_sessionmaker
from launchcheck.deep.orchestrator import DeepOrchestrator
from launchcheck.deep.setup import asset_store, orchestrator_config

logger = logging.getLogger(__name__)
_settings = get_settings()
JOB_TIMEOUT_MARGIN_S = 120


async def startup(ctx: dict[str, Any]) -> None:
    ctx["sessionmaker"] = get_sessionmaker()


async def shutdown(ctx: dict[str, Any]) -> None:
    await get_engine().dispose()


async def run_deep_scan(ctx: dict[str, Any], scan_id: str) -> str | None:
    orchestrator = DeepOrchestrator(ctx["sessionmaker"], orchestrator_config(get_settings()))
    status = await orchestrator.run(uuid.UUID(scan_id))
    return str(status) if status else None


async def sweep_orphan_assets(ctx: dict[str, Any]) -> int:
    """DB'de karşılığı olmayan asset dosyalarını siler (tarama silinmiş, worker çökmüş...)."""
    settings = get_settings()
    async with ctx["sessionmaker"]() as session:
        rows = (await session.execute(select(ScanAsset.scan_id, ScanAsset.path))).all()
    known: dict[str, set[str]] = {}
    for scan_id, path in rows:
        known.setdefault(str(scan_id), set()).add(path.rsplit("/", 1)[-1])
    deleted = asset_store(settings).sweep_orphans(known, settings.deep_asset_orphan_after_s)
    if deleted:
        logger.info("%d yetim asset silindi", deleted)
    return deleted


async def refresh_retire_db(ctx: dict[str, Any]) -> bool:
    """retire.js veritabanını haftalık günceller (açıklı kütüphane check'i). Başarısız olursa
    imajdaki kopya kullanılmaya devam eder; tarama etkilenmez."""
    from launchcheck.deep.supply.retire_update import update_retire_db

    try:
        return await update_retire_db(get_settings())
    except Exception as exc:
        logger.warning("retire güncellemesi başarısız (%s)", type(exc).__name__)
        return False


class DeepWorkerSettings:
    functions: ClassVar[list[Any]] = [run_deep_scan]
    cron_jobs: ClassVar[list[Any]] = [
        cron(sweep_orphan_assets, minute=30, run_at_startup=True),
        # Pazartesi 04:10 (UTC) + başlangıçta: güncel kopya tmpfs'te, yeniden başlayınca kaybolur
        cron(refresh_retire_db, weekday=0, hour=4, minute=10, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = DEEP_QUEUE
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.deep_max_concurrent
    job_timeout = int(_settings.deep_scan_timeout_s) + JOB_TIMEOUT_MARGIN_S
    max_tries = 1
