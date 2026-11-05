"""Tutarlı hata formatı: {"error": {"code": "...", "message": "..."}} (mesaj i18n)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from launchcheck.i18n import error_message, normalize_lang


class ApiError(Exception):
    def __init__(
        self, code: str, status_code: int = 400, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.headers = headers or {}


def request_lang(request: Request) -> str:
    return normalize_lang(
        request.query_params.get("lang") or request.headers.get("accept-language")
    )


def error_response(
    request: Request, code: str, status_code: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    message = error_message(code, request_lang(request))
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


async def _api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(request, exc.code, exc.status_code, exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    return error_response(request, "invalid_request", 422)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = "not_found" if exc.status_code == 404 else "invalid_request"
    if exc.status_code >= 500:
        code = "internal_error"
    return error_response(request, code, exc.status_code)


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    return error_response(request, "internal_error", 500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
