"""arq job fonksiyonları. `ctx` arq'nın iş bağlamıdır; `sessionmaker` on_startup'ta eklenir."""

import logging
import uuid
from pathlib import Path
from typing import Any

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.collectors.base import get_collector
from launchcheck.config import get_settings
from launchcheck.security.upload import delete_upload
from launchcheck.worker.cron import (
    delete_expired_scans,
    delete_orphan_uploads,
    fail_stale_scans,
)
from launchcheck.worker.pipeline import PipelineConfig, execute_scan

logger = logging.getLogger(__name__)


def pipeline_config() -> PipelineConfig:
    settings = get_settings()
    return PipelineConfig(
        registry=load_builtin_checks(),
        resolve_collector=get_collector,
        timeouts_s={
            TargetType.WEB: settings.web_scan_timeout_s,
            TargetType.APK: settings.apk_scan_timeout_s,
        },
        score_weights=settings.score_weights,
    )


async def run_scan(ctx: dict[str, Any], scan_id: str, args: dict[str, Any]) -> str | None:
    try:
        status = await execute_scan(
            ctx["sessionmaker"], uuid.UUID(scan_id), args, pipeline_config()
        )
    finally:
        # Collector'a hiç ulaşılmasa bile (ör. tarama zaten işlenmiş) yüklenen APK silinir.
        if "upload_path" in args:
            delete_upload(str(args["upload_path"]), Path(get_settings().upload_dir))
    return str(status) if status else None


async def cleanup_expired(ctx: dict[str, Any]) -> int:
    deleted = await delete_expired_scans(ctx["sessionmaker"], get_settings().report_retention_days)
    logger.info("süresi dolan %d tarama silindi", deleted)
    return deleted


async def cleanup_stale(ctx: dict[str, Any]) -> int:
    settings = get_settings()
    orphans = await delete_orphan_uploads(
        Path(settings.upload_dir), Path(settings.scan_tmp_dir), settings.upload_orphan_after_s
    )
    if orphans:
        logger.warning("%d yetim yükleme/geçici klasör silindi", orphans)
    return await fail_stale_scans(ctx["sessionmaker"], settings.stale_scan_after_s)


async def hello(ctx: dict[str, Any], name: str = "world") -> str:
    """Duman testi işi."""
    return f"hello {name}"
