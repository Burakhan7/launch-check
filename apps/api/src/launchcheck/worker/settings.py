"""arq worker ayarları. Çalıştırma: `arq launchcheck.worker.settings.WorkerSettings`."""

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from launchcheck.checks.base import TargetType
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.collectors.base import register_collector
from launchcheck.collectors.web_collector import WebCollector
from launchcheck.config import get_settings
from launchcheck.db.session import get_engine, get_sessionmaker
from launchcheck.security.ssrf import UrlGuard
from launchcheck.worker.tasks import cleanup_expired, cleanup_stale, hello, run_scan

_settings = get_settings()
STALE_CHECK_MINUTES = set(range(0, 60, 5))
# Job zaman aşımı tarama zaman aşımından büyük olmalı; tarama kendi timeout'unu yönetir.
JOB_TIMEOUT_MARGIN_S = 60


async def startup(ctx: dict[str, Any]) -> None:
    load_builtin_checks()
    register_collector(
        TargetType.WEB, WebCollector(_settings, UrlGuard(timeout_s=_settings.dns_timeout_s))
    )
    ctx["sessionmaker"] = get_sessionmaker()


async def shutdown(ctx: dict[str, Any]) -> None:
    await get_engine().dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [run_scan, hello]
    cron_jobs: ClassVar[list[Any]] = [
        cron(cleanup_expired, minute=0, run_at_startup=True),
        cron(cleanup_stale, minute=STALE_CHECK_MINUTES),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_max_jobs
    job_timeout = int(max(_settings.web_scan_timeout_s, _settings.apk_scan_timeout_s)) + (
        JOB_TIMEOUT_MARGIN_S
    )
    max_tries = 1  # tarama yarıda kalırsa tekrar denemek yerine kullanıcı yeniden tarar
