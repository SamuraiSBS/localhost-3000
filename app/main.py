"""FastAPI application with a public health check and protected product API."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, HTMLResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from starlette.staticfiles import StaticFiles

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
    require_session,
    validate_runtime_credentials,
)
from app.config import RuntimeConfig
from app.contracts import load_source
from app.evaluation import EvaluationCoordinator
from app.storage import SQLiteRepository
from app.metrics import render_metrics, require_metrics_token


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

    @application.middleware("http")
    async def product_asset_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    project_root = Path(__file__).resolve().parents[1]
    web_root = project_root / "web"
    application.mount("/assets", StaticFiles(directory=web_root / "assets"), name="web-assets")
    application.mount("/design", StaticFiles(directory=project_root / "design"), name="design")

    @application.get("/metrics", dependencies=[Depends(require_metrics_token)], response_class=Response)
    def metrics(request: Request):
        evaluation = request.app.state.coordinator.current
        if evaluation is None:
            raise HTTPException(503, detail={"code": "evaluation_unavailable",
                                             "message": "No active source snapshot is available."})
        return Response(render_metrics(evaluation), headers={"Content-Type": CONTENT_TYPE_LATEST,
                                                            "Cache-Control": "no-store"})

    @application.get("/api/v1/openapi.json", include_in_schema=False,
                     dependencies=[Depends(require_session)])
    def protected_spec():
        return JSONResponse(application.openapi())

    @application.get("/api/v1/docs/assets/{filename}", include_in_schema=False,
                     dependencies=[Depends(require_session)])
    def swagger_asset(filename: str):
        if filename not in {"swagger-ui-bundle.js", "swagger-ui.css"}:
            raise HTTPException(404)
        return FileResponse(web_root / "vendor" / "swagger" / filename)

    @application.get("/api/v1/docs", response_class=HTMLResponse,
                     dependencies=[Depends(require_session)])
    def swagger_docs():
        return HTMLResponse('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>localhost3000 API</title><link rel="stylesheet" href="/api/v1/docs/assets/swagger-ui.css"></head>
<body><div id="swagger-ui"></div><script src="/api/v1/docs/assets/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url: "/api/v1/openapi.json", dom_id: "#swagger-ui", validatorUrl: null,
  queryConfigEnabled: false, persistAuthorization: false, withCredentials: true,
  requestInterceptor: function(request) {
    const target = new URL(request.url, window.location.origin);
    if (target.origin !== window.location.origin) throw new Error("Only local API requests are allowed");
    return request;
  }
});</script></body></html>''', headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        })

    def page(name: str) -> FileResponse:
        return FileResponse(web_root / name, headers={"Cache-Control": "no-store"})

    @application.get("/", include_in_schema=False)
    def entry_page():
        return page("index.html")

    @application.get("/login", include_in_schema=False)
    def login_page():
        return page("login.html")

    def protected_page(request: Request, name: str):
        try:
            require_session(request)
        except AuthError:
            # Only fixed internal destinations are accepted by the login page.
            destination = request.url.path
            return RedirectResponse("/login?next=" + destination, status_code=303,
                                    headers={"Cache-Control": "no-store"})
        return page(name)

    @application.get("/customers", include_in_schema=False)
    def customers_page(request: Request):
        return protected_page(request, "customers.html")

    @application.get("/customers/{customer_id}", include_in_schema=False)
    def customer_page(request: Request, customer_id: str):
        return protected_page(request, "customer.html")

    @application.get("/settings", include_in_schema=False)
    def settings_page(request: Request):
        return protected_page(request, "settings.html")

    @application.get("/healthz", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "stage": "runtime-scaffold"}

    def contract_openapi():
        if application.openapi_schema is None:
            schema = get_openapi(title=application.title, version=application.version,
                                 routes=application.routes)
            _document_contract(schema)
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = contract_openapi
    return application


def _document_contract(schema: dict) -> None:
    """Document middleware security and dictionary DTOs without changing runtime auth."""
    from app.contracts import MAX_MONEY_CENTS, SourceSnapshot, Thresholds

    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        "SessionCookie": {"type": "apiKey", "in": "cookie", "name": "session"},
        "MetricsBearer": {"type": "http", "scheme": "bearer",
                          "description": "Separate collector METRICS_TOKEN; grants no customer API access."},
    }
    models = components.setdefault("schemas", {})
    for dto in (SourceSnapshot, Thresholds):
        document = dto.model_json_schema(ref_template="#/components/schemas/{model}")
        models.update(document.pop("$defs", {}))
        models[dto.__name__] = document
    ref = lambda name: {"$ref": "#/components/schemas/" + name}
    integer = {"type": "integer", "minimum": 0}
    cents = {**integer, "maximum": MAX_MONEY_CENTS, "description": "Integer euro cents (EUR)."}
    string = {"type": "string"}
    score = {"anyOf": [{"type": "number", "minimum": 0, "maximum": 100,
                         "multipleOf": 0.01}, {"type": "null"}]}
    level = {"type": "string", "enum": ["low", "medium", "high", "unknown"]}
    array = lambda item: {"type": "array", "items": item}
    obj = lambda props: {"type": "object", "properties": props, "required": list(props)}
    metadata = {"analysis_revision": string, "data_revision": integer,
                "settings_revision": integer, "model_version": string,
                "evaluated_at": {"type": "string", "format": "date-time"},
                "horizon_days": {"type": "integer", "minimum": 1},
                "currency": {"type": "string", "const": "EUR"}}
    customer = {"id": {"type": "string", "pattern": "^[1-9][0-9]*$"}, "email": string,
                "balance_cents": cents, "total_replenished_cents": cents,
                "risk_score": score, "risk_level": level,
                "monetary_risk_cents": {"anyOf": [cents, {"type": "null"}]},
                "top_reasons": array(string), "data_quality_warnings": array(string)}
    models["CustomerListItem"] = obj(customer)
    models["CustomerList"] = obj({**metadata, "total": integer, "limit": integer,
                                   "offset": integer, "items": array(ref("CustomerListItem"))})
    models["CustomerDetail"] = obj({**metadata, **customer,
        "spendable_cents": cents, "due_services_price_cents": cents, "funding_gap_cents": cents,
        "unscored_reason": {"anyOf": [string, {"type": "null"}]},
        "services": array({"type": "object", "additionalProperties": True}),
        "transactions": array(ref("TransactionSource"))})
    models["AnalyticsSummary"] = obj({**metadata, "customers_total": integer,
        "customers_evaluated": integer, "risk_counts": obj({key: integer for key in level["enum"]}),
        "monetary_risk_cents": cents,
        "monetary_coverage": obj({"evaluated": integer, "unknown": integer}),
        "due_services_price_cents": cents, "funding_gap_cents": cents,
        "data_quality_warnings": integer, "mean_risk_score": score})
    models["SettingsResponse"] = obj({
        "settings_revision": integer, "model_version": string, "horizon_days": integer,
        "thresholds": ref("Thresholds"),
        "weights": {"type": "object", "additionalProperties": {"type": "number"}},
        "features": array({"type": "object", "additionalProperties": True})})
    models["SettingsUpdateResponse"] = obj({**models["SettingsResponse"]["properties"],
                                            "analysis_revision": string})
    models["SnapshotResponse"] = obj({"data_revision": integer, "analysis_revision": string,
        "received_at": {"type": "string", "format": "date-time"}, "checksum": string,
        "changed": {"type": "boolean"}, "counts": obj({key: integer for key in
            ("accounts", "services", "transactions")}), "warnings": array({"type": "object"})})
    models["APIError"] = obj({"error": {"type": "object", "required": ["code", "message"],
        "properties": {"code": string, "message": string, "details": array({"type": "object"}),
                       "current_revision": integer}}})
    models["CustomerDetail"]["properties"]["top_reasons"] = array({"type": "object"})
    nullable_string = {"anyOf": [string, {"type": "null"}]}
    number = {"type": "number"}
    models["FeatureContribution"] = obj({"id": string, "version": string, "title": string,
        "value": {"anyOf": [number, {"type": "null"}]}, "weight": number,
        "contribution": {"anyOf": [number, {"type": "null"}]},
        "available": {"type": "boolean"}, "explanation": string,
        "evidence": {"type": "object", "additionalProperties": True}})
    service = dict(models["ServiceSource"]["properties"])
    service.update({"id": string, "ownerId": string, "price_cents": cents,
        "eligible": {"type": "boolean"}, "in_horizon": {"type": "boolean"},
        "overdue": {"type": "boolean"}, "excluded_reason": nullable_string,
        "covered_cents": {"anyOf": [cents, {"type": "null"}],
                          "description": "Null when no allocation is performed outside the horizon or for an excluded service."},
        "funding_gap_cents": {"anyOf": [cents, {"type": "null"}],
                              "description": "Null when no allocation is performed outside the horizon or for an excluded service."},
        "raw_score": {"anyOf": [number, {"type": "null"}]}, "risk_score": score,
        "risk_level": level, "score_clamped": {"type": "boolean"},
        "monetary_risk_cents": customer["monetary_risk_cents"],
        "features": array(ref("FeatureContribution")), "top_reasons": array(ref("FeatureContribution")),
        "unscored_reason": nullable_string, "data_quality_warnings": array(string)})
    models["ServiceResponse"] = obj(service)
    detail = models["CustomerDetail"]["properties"]
    detail.update({**models["AccountSource"]["properties"], "id": customer["id"],
        "withdraw_balance_cents": cents, "bonus_balance_cents": cents,
        "top_service_id": nullable_string, "top_reasons": array(ref("FeatureContribution")),
        "services": array(ref("ServiceResponse"))})
    models["CustomerDetail"]["required"] = list(detail)
    # Response DTOs preserve source fields but stringify identifiers.
    transaction = dict(models["TransactionSource"]["properties"])
    transaction.update({"id": string, "serviceId": string, "amount_cents": cents})
    models["TransactionResponse"] = obj(transaction)
    models["CustomerDetail"]["properties"]["transactions"] = array(ref("TransactionResponse"))
    schema["paths"]["/api/v1/source/snapshot"]["put"]["requestBody"] = {
        "required": True, "content": {"application/json": {"schema": ref("SourceSnapshot"),
            "example": {"accounts": [], "services": [], "transactions": []}}}}
    models["CustomerListItem"]["example"] = {
        "id": "1001", "email": "manual-renewal@example.test", "balance_cents": 1000,
        "total_replenished_cents": 10000, "risk_score": 45, "risk_level": "medium",
        "monetary_risk_cents": 450, "top_reasons": ["Автопродление отключено"],
        "data_quality_warnings": []}
    responses = {
        ("/api/v1/customers", "get"): "CustomerList",
        ("/api/v1/customers/{customer_id}", "get"): "CustomerDetail",
        ("/api/v1/analytics/summary", "get"): "AnalyticsSummary",
        ("/api/v1/model/settings", "get"): "SettingsResponse",
        ("/api/v1/model/settings", "patch"): "SettingsUpdateResponse",
        ("/api/v1/source/snapshot", "put"): "SnapshotResponse",
    }
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "patch", "put"}:
                continue
            login = path == "/api/v1/auth/login"
            operation["security"] = ([] if login else
                [{"MetricsBearer": []}] if path == "/metrics" else [{"SessionCookie": []}])
            if not login and method in {"post", "patch", "put"}:
                operation.setdefault("parameters", []).extend([
                    {"name": "X-CSRF-Token", "in": "header", "required": True, "schema": string,
                     "description": "Session-bound CSRF token from login or auth/me."},
                    {"name": "Origin", "in": "header", "required": True, "schema": string}])
            if login:
                operation.setdefault("parameters", []).append(
                    {"name": "Origin", "in": "header", "required": True, "schema": string})
            response_name = responses.get((path, method))
            if response_name:
                operation["responses"]["200"] = {"description": "Current evaluation/configuration",
                    "content": {"application/json": {"schema": ref(response_name)}}}
            for status in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503):
                operation["responses"][str(status)] = {"description": "Structured API error",
                    "content": {"application/json": {"schema": ref("APIError"),
                        "example": {"error": {"code": "request_failed", "message": "Request failed."}}}}}
            if path == "/metrics":
                operation["responses"]["200"] = {"description": "Prometheus exposition from one Evaluation",
                    "content": {"text/plain": {"schema": string}}}
    schema["info"]["description"] = (
        "Cookie-session protected API. Monetary values are integer euro cents; unknown score/money is null. "
        "All evaluation metadata belongs to one immutable Evaluation. Mutations require CSRF and Origin. "
        "Metrics uses a separate collector bearer. Documentation and schema require a session.")


app = create_app()
