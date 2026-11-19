"""Uygulama genelinde paylaşılan bağımlılıklar (app.state üzerinden; testlerde değiştirilir)."""

from collections.abc import Awaitable, Callable

from fastapi import Request

from launchcheck.api.errors import ApiError
from launchcheck.security.ratelimit import RateLimiter
from launchcheck.security.ssrf import UrlGuard

UNKNOWN_CLIENT = "unknown"


def get_url_guard(request: Request) -> UrlGuard:
    guard: UrlGuard = request.app.state.url_guard
    return guard


def client_ip(request: Request) -> str:
    # Caddy arkasında uvicorn --proxy-headers ile X-Forwarded-For'dan gelir.
    return request.client.host if request.client else UNKNOWN_CLIENT


def rate_limit(
    kind: str,
    limit_of: Callable[[], int],
    window_s: int | None = None,
    error_code: str = "rate_limited",
) -> Callable[[Request], Awaitable[None]]:
    async def dependency(request: Request) -> None:
        await enforce_rate_limit(request, kind, limit_of(), window_s, error_code)

    return dependency


async def enforce_rate_limit(
    request: Request,
    kind: str,
    limit: int,
    window_s: int | None = None,
    error_code: str = "rate_limited",
) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    result = await limiter.hit(kind, client_ip(request), limit, window_s)
    if not result.allowed:
        raise ApiError(error_code, 429, headers={"Retry-After": str(result.retry_after_s)})
