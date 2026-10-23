"""FastAPI application with a public health check and protected product API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.responses import JSONResponse

from app.api.auth import router as auth_router
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
from app.storage import SQLiteRepository


def create_app(config: RuntimeConfig | None = None) -> FastAPI:
    config = config or RuntimeConfig.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        validate_runtime_credentials(config)
        application.state.config = config
        application.state.source = load_source(config.source_path)
        database_path = config.data_dir / SESSION_DATABASE_NAME
        repository = SQLiteRepository(database_path)
        repository.initialize()
        application.state.repository = repository
        session_store = SessionStore(database_path, ttl_seconds=config.session_ttl_seconds)
        session_store.initialize()
        application.state.session_store = session_store
        application.state.login_rate_limiter = LoginRateLimiter()
        yield

    application = FastAPI(title="localhost3000", version="0.1.0", lifespan=lifespan,
                          docs_url=None, redoc_url=None, openapi_url=None)
    application.include_router(auth_router, prefix="/api/v1/auth")
    application.add_exception_handler(
        AuthError,
        lambda request, error: JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
        ),
    )
    application.middleware("http")(api_auth_middleware)

    @application.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "stage": "runtime-scaffold"}

    return application


app = create_app()
