"""IP başına saatlik rate limit. Sadece Redis'te, TTL'li sayaç; IP tuzlanmış hash olarak tutulur.
IP adresi DB'ye veya loga yazılmaz."""

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

WINDOW_S = 3600
KEY_PREFIX = "lc:rl"
HASH_HEX_LEN = 32


class RedisLike(Protocol):
    def pipeline(self, transaction: bool = True) -> Any: ...


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_s: int = 0


def hash_ip(ip: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:HASH_HEX_LEN]


class RateLimiter:
    def __init__(self, redis: RedisLike, salt: str, window_s: int = WINDOW_S) -> None:
        self._redis = redis
        self._salt = salt
        self._window_s = window_s

    async def hit(self, kind: str, ip: str, limit: int) -> RateLimitResult:
        key = f"{KEY_PREFIX}:{kind}:{hash_ip(ip, self._salt)}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, self._window_s, nx=True)
                pipe.ttl(key)
                count, _, ttl = await pipe.execute()
        except Exception as exc:
            # Redis yoksa kuyruk da yoktur; isteği engellemek yerine enqueue hatasına bırak.
            logger.warning("rate limit kontrol edilemedi (%s)", type(exc).__name__)
            return RateLimitResult(allowed=True)
        if int(count) > limit:
            return RateLimitResult(allowed=False, retry_after_s=max(int(ttl), 1))
        return RateLimitResult(allowed=True)
