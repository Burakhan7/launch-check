"""arq worker ayarları. Çalıştırma: `arq launchcheck.worker.settings.WorkerSettings`."""

from typing import Any, ClassVar

from arq.connections import RedisSettings

from launchcheck.config import get_settings
from launchcheck.worker.tasks import hello


class WorkerSettings:
    functions: ClassVar[list[Any]] = [hello]
    cron_jobs: ClassVar[list[Any]] = []
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    job_timeout = 600
