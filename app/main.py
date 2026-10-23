"""FastAPI application with a public health check and protected product API."""

from contextlib import asynccontextmanager

from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.customers import router as customers_router
from app.api.settings import router as settings_router
from app.api.source import router as source_router
from app.auth import (
    SESSION_DATABASE_NAME,
    AuthError,
    LoginRateLimiter,
    SessionStore,
    api_auth_middleware,
    validate_runtime_credentials,
)
from app.config import RuntimeConfig
from app.contracts import load_source
from app.evaluation import EvaluationCoordinator
from app.storage import SQLiteRepository


def _request_validation_response(request: object, error: RequestValidationError) -> JSONResponse:
    errors = error.errors()
    if any(item.get("type") == "json_invalid" for item in errors):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "bad_request",
                    "message": "Request body must contain valid JSON.",
                }
            },
        )

    details: list[dict[str, str]] = []
    for item in errors:
        location = list(item.get("loc", ()))
        if location and location[0] in {"body", "query", "path", "header", "cookie"}:
            location.pop(0)
        path = ".".join(str(part) for part in location) or "body"
        details.append({"path": path, "reason": str(item.get("type", "invalid_value"))})
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed.",
                "details": details,
            }
        },
    )


def _http_error_response(request: object, error: StarletteHTTPException) -> JSONResponse:
    detail = error.detail
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        payload = {
            "code": detail["code"],
            "message": str(detail.get("message", "Request failed.")),
        }
        for key in ("details", "current_revision"):
            if key in detail:
                payload[key] = detail[key]
    else:
        codes = {
            400: ("bad_request", "The request is invalid."),
            401: ("authentication_required", "Authentication required."),
            403: ("forbidden", "The request is not allowed."),
            404: ("not_found", "The requested resource was not found."),
            409: ("revision_conflict", "The current revision has changed."),
            413: ("request_too_large", "The request is too large."),
            415: ("unsupported_media_type", "The request content type is not supported."),
            422: ("validation_error", "Request validation failed."),
            503: ("service_unavailable", "The requested service is unavailable."),
        }
        code, message = codes.get(error.status_code, ("request_failed", "The request failed."))
        payload = {"code": code, "message": message}
    return JSONResponse(status_code=error.status_code, content={"error": payload}, headers=error.headers)


def create_app(config: RuntimeConfig | None = None) -> FastAPI:
    config = config or RuntimeConfig.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        validate_runtime_credentials(config)
        if type(config.max_snapshot_bytes) is not int or config.max_snapshot_bytes < 1:
            raise ValueError("MAX_SNAPSHOT_BYTES must be a positive integer")
        application.state.config = config
        database_path = config.data_dir / SESSION_DATABASE_NAME
        repository = SQLiteRepository(database_path)
        coordinator = EvaluationCoordinator(repository)
        evaluation = coordinator.start()
        if evaluation is None:
            source_path = config.source_path
            if source_path.stat().st_size > config.max_snapshot_bytes:
                raise ValueError("initial_source_snapshot_too_large")
            coordinator.replace_snapshot(load_source(source_path))
        application.state.coordinator = coordinator
        application.state.repository = repository
        session_store = SessionStore(database_path, ttl_seconds=config.session_ttl_seconds)
        session_store.initialize()
        application.state.session_store = session_store
        application.state.login_rate_limiter = LoginRateLimiter()
        yield

    application = FastAPI(title="localhost3000", version="0.1.0", lifespan=lifespan,
                          docs_url=None, redoc_url=None, openapi_url=None)
    application.include_router(auth_router, prefix="/api/v1/auth")
    application.include_router(customers_router, prefix="/api/v1")
    application.include_router(analytics_router, prefix="/api/v1")
    application.include_router(settings_router, prefix="/api/v1")
    application.include_router(source_router, prefix="/api/v1")
    application.add_exception_handler(
        AuthError,
        lambda request, error: JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
        ),
    )
    application.add_exception_handler(RequestValidationError, _request_validation_response)
    application.add_exception_handler(StarletteHTTPException, _http_error_response)
    application.middleware("http")(api_auth_middleware)

    @application.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "stage": "runtime-scaffold"}

    return application


app = create_app()
