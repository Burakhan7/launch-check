"""Dış servis istemcisi (crt.sh, Cert Spotter, Wayback CDX, RDAP).

- İstekler SSRF korumalı `SafeFetcher` ile gider (RDAP yönlendirmeleri dahil), yanıt boyutu sınırlı.
- Global hız sınırı: tüm taramalar için servis başına sayaç (Redis, PHASE2 §12.4). Sınır dolarsa
  veya servis hata verirse çağıran parça atlanır; tarama durmaz.
- Servislere sadece alan adı / origin gönderilir; kullanıcıya ait başka veri gitmez.
"""

import asyncio
import json
import logging
from typing import Any, Protocol

from launchcheck.collectors.fetcher import FetchError, SafeFetcher
from launchcheck.deep.external_data import PartStatus
from launchcheck.security.ratelimit import RateLimiter, RedisLike

logger = logging.getLogger(__name__)
GLOBAL_KEY = "global"  # IP değil: sayaç tüm taramalar için ortak
DEFAULT_TIMEOUT_S = 20.0


class ServiceError(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class ServiceLimiter(Protocol):
    async def allow(self, service: str) -> bool: ...


class NoLimit:
    async def allow(self, service: str) -> bool:
        return True


class RedisServiceLimiter:
    """`limits`: servis → [istek sayısı, pencere sn]. Listede olmayan servis sınırsız."""

    def __init__(self, redis: RedisLike, limits: dict[str, list[int]]) -> None:
        self._limiter = RateLimiter(redis, salt="svc")
        self._limits = limits

    async def allow(self, service: str) -> bool:
        limit = self._limits.get(service)
        if not limit:
            return True
        count, window_s = int(limit[0]), int(limit[1])
        result = await self._limiter.hit(f"svc:{service}", GLOBAL_KEY, count, window_s)
        return result.allowed


class ServiceClient:
    def __init__(
        self,
        fetcher: SafeFetcher,
        limiter: ServiceLimiter,
        max_bytes: int,
        timeouts_s: dict[str, float],
    ) -> None:
        self._fetcher = fetcher
        self._limiter = limiter
        self._max_bytes = max_bytes
        self._timeouts_s = timeouts_s

    async def get_json(self, service: str, url: str) -> Any | None:
        """JSON gövde; 404 → None (servisin bu alan adı için verisi yok)."""
        if not await self._limiter.allow(service):
            raise ServiceError(PartStatus.RATE_LIMITED)
        timeout = self._timeouts_s.get(service, DEFAULT_TIMEOUT_S)
        try:
            async with asyncio.timeout(timeout):
                res = await self._fetcher.fetch(url, self._max_bytes)
        except (FetchError, TimeoutError) as exc:
            logger.info("%s isteği başarısız (%s)", service, type(exc).__name__)
            raise ServiceError(PartStatus.UNAVAILABLE) from exc
        if res.status == 404:
            return None
        if res.status == 429:
            raise ServiceError(PartStatus.RATE_LIMITED)
        if res.status != 200 or res.truncated:
            logger.info("%s yanıtı kullanılamadı (%s)", service, res.status)
            raise ServiceError(PartStatus.UNAVAILABLE)
        try:
            return json.loads(res.body)
        except ValueError as exc:
            raise ServiceError(PartStatus.UNAVAILABLE) from exc

    async def aclose(self) -> None:
        await self._fetcher.aclose()
