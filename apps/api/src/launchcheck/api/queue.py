"""arq kuyruğuna erişim. Redis bağlantısı ilk kullanımda açılır (API Redis'siz de ayağa kalkar)."""

import asyncio
from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Request

from launchcheck.config import get_settings

RUN_SCAN_JOB = "run_scan"


class JobQueue(Protocol):
    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any: ...


class LazyArqQueue:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._pool: ArqRedis | None = None
        self._lock = asyncio.Lock()

    async def _get_pool(self) -> ArqRedis:
        async with self._lock:
            if self._pool is None:
                self._pool = await create_pool(RedisSettings.from_dsn(self._redis_url), retry=0)
            return self._pool

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any:
        pool = await self._get_pool()
        return await pool.enqueue_job(function, *args, **kwargs)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


def build_queue() -> LazyArqQueue:
    return LazyArqQueue(get_settings().redis_url)


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.queue
    return queue
