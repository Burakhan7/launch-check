"""FastAPI uygulama girişi."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from launchcheck.api.errors import install_error_handlers
from launchcheck.api.queue import build_queue
from launchcheck.api.routes import benchmark, deep, health, scans
from launchcheck.checks.registry import load_builtin_checks
from launchcheck.config import get_settings
from launchcheck.security.ratelimit import RateLimiter
from launchcheck.security.ssrf import UrlGuard


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    load_builtin_checks()
    redis = Redis.from_url(settings.redis_url)
    app.state.queue = build_queue()
    app.state.rate_limiter = RateLimiter(redis, settings.rate_limit_ip_salt)
    app.state.url_guard = UrlGuard(timeout_s=settings.dns_timeout_s)
    try:
        yield
    finally:
        await app.state.queue.close()
        await redis.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    is_dev = settings.env == "dev"
    app = FastAPI(
        title="Launch Check API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if is_dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if is_dev else None,
    )
    install_error_handlers(app)
    app.include_router(health.router, prefix="/api")
    app.include_router(scans.router, prefix="/api")
    app.include_router(deep.router, prefix="/api")
    app.include_router(benchmark.router, prefix="/api")
    return app


app = create_app()
