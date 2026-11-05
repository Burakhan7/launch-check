"""FastAPI uygulama girişi."""

import logging

from fastapi import FastAPI

from launchcheck.api.routes import health
from launchcheck.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(
        title="Launch Check API",
        version="0.1.0",
        docs_url="/api/docs" if settings.env == "dev" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.env == "dev" else None,
    )
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()
