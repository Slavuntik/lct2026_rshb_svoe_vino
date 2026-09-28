"""Единый формат ошибок по contracts/openapi.yaml v0.2:
{"error": {"code": "<snake_case>", "message": "<человеческое, по-русски>"}}

Словарь допустимых code (шапка openapi.yaml, единственный источник):
validation_error | invalid_credentials | unauthorized | age_restricted
| consent_required | not_found | rate_limited | unknown_event | llm_unavailable
| not_implemented

ApiError обязан использовать только эти коды. Единственное осознанное
отступление — internal_error для необработанных исключений (см.
_unhandled_error_handler): без него сорвался бы сам JSON error-envelope на
любой баг, что хуже, чем код вне словаря. Вынесено в отчёт как предложение
к контракту (добавить internal_error в словарь).
"""
from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

VALID_CODES = {
    "validation_error",
    "invalid_credentials",
    "unauthorized",
    "age_restricted",
    "consent_required",
    "not_found",
    "rate_limited",
    "unknown_event",
    "llm_unavailable",
    "not_implemented",
}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        assert code in VALID_CODES or code == "internal_error", (
            f"код {code!r} вне словаря contracts/openapi.yaml"
        )
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_envelope(exc.code, exc.message))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
        # Сеть путей/методов FastAPI (404 на неизвестный route, 405 на неверный
        # verb) — единственные случаи, где ошибку поднимает не наш код.
        code = {
            status.HTTP_404_NOT_FOUND: "not_found",
            status.HTTP_401_UNAUTHORIZED: "unauthorized",
            status.HTTP_403_FORBIDDEN: "consent_required",
            status.HTTP_405_METHOD_NOT_ALLOWED: "validation_error",
            status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
        }.get(exc.status_code, "validation_error")
        message = exc.detail if isinstance(exc.detail, str) else "Ошибка запроса"
        return JSONResponse(status_code=exc.status_code, content=_envelope(code, message), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_envelope("validation_error", "Некорректные данные запроса"),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        # internal_error НЕ входит в словарь контракта — см. докстринг модуля
        # и reports/b-report.md, раздел «Предложения к контрактам».
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal_error", "Внутренняя ошибка сервиса"),
        )
