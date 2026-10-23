"""Health-only scaffold. Protected product routes are added in later slices."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import RuntimeConfig
from app.contracts import load_source


def create_app(config: RuntimeConfig | None = None) -> FastAPI:
    config = config or RuntimeConfig.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.config = config
        application.state.source = load_source(config.source_path)
        yield

    application = FastAPI(title="localhost3000", version="0.1.0", lifespan=lifespan,
                          docs_url=None, redoc_url=None, openapi_url=None)

    @application.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "stage": "runtime-scaffold"}

    return application


app = create_app()
