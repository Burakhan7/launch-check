"""arq job fonksiyonları. `ctx` arq'nın iş bağlamıdır; `sessionmaker` on_startup'ta eklenir."""

import logging
import uuid
from typing import Any

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.collectors.base import get_collector
from launchcheck.config import get_settings
from launchcheck.worker.cron import delete_expired_scans, fail_stale_scans
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
    status = await execute_scan(ctx["sessionmaker"], uuid.UUID(scan_id), args, pipeline_config())
    return str(status) if status else None


async def cleanup_expired(ctx: dict[str, Any]) -> int:
    deleted = await delete_expired_scans(ctx["sessionmaker"], get_settings().report_retention_days)
    logger.info("süresi dolan %d tarama silindi", deleted)
    return deleted


async def cleanup_stale(ctx: dict[str, Any]) -> int:
    return await fail_stale_scans(ctx["sessionmaker"], get_settings().stale_scan_after_s)


async def hello(ctx: dict[str, Any], name: str = "world") -> str:
    """Duman testi işi."""
    return f"hello {name}"
