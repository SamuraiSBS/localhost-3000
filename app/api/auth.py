"""Login, current-user and logout endpoints."""

from __future__ import annotations

import hmac
import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app.auth import (
    MAX_LOGIN_BODY_BYTES,
    SESSION_COOKIE_NAME,
    AuthError,
    AuthenticatedUser,
    LoginRateLimiter,
    SessionStore,
    cookie_is_secure,
    csrf_token_for_session,
    require_same_origin,
    require_session,
    verify_login_password,
    issue_session,
)
from app.oidc import FLOW_COOKIE, FLOW_SECONDS, OIDCFailure, PREFIX, safe_destination

router = APIRouter()


class LoginCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    login: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    login: str


class AuthResponse(BaseModel):
    user: UserResponse
    csrf_token: str


def _source_address(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


async def _read_login_credentials(request: Request) -> LoginCredentials:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise AuthError(415, "unsupported_media_type", "Login requires a JSON request.")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_LOGIN_BODY_BYTES:
                raise AuthError(413, "request_too_large", "Login request is too large.")
        except ValueError:
            raise AuthError(400, "invalid_request", "Invalid login request.") from None

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_LOGIN_BODY_BYTES:
            raise AuthError(413, "request_too_large", "Login request is too large.")
        body.extend(chunk)

    try:
        return LoginCredentials.model_validate_json(bytes(body))
    except ValidationError:
        # Pydantic errors can include the supplied password in their `input`;
        # this deliberately returns a fixed message without validation details.
        raise AuthError(422, "invalid_login_request", "Invalid login request.") from None


@router.post("/login", response_model=AuthResponse)
async def login(request: Request, response: Response) -> AuthResponse:
    require_same_origin(request)
    client_address = _source_address(request)
    limiter: LoginRateLimiter = request.app.state.login_rate_limiter
    credentials = await _read_login_credentials(request)
    reservation = limiter.reserve_attempt(client_address)
    if reservation is None:
        raise AuthError(429, "rate_limited", "Too many login attempts. Try again later.")

    config = request.app.state.config
    configured_login = config.login or ""
    login_matches = hmac.compare_digest(
        credentials.login.encode("utf-8"), configured_login.encode("utf-8")
    )
    password_matches = await run_in_threadpool(
        verify_login_password,
        credentials.password,
        config.password_hash or "",
        login_matches,
    )
    if not login_matches or not password_matches:
        limiter.record_failure(client_address, reservation)
        raise AuthError(401, "authentication_failed", "Invalid login or password.")

    limiter.record_success(client_address, reservation)
    csrf_token = issue_session(request, response)
    return AuthResponse(
        user=UserResponse(id="operator", login=configured_login),
        csrf_token=csrf_token,
    )


@router.get("/me", response_model=AuthResponse)
def me(user: AuthenticatedUser = Depends(require_session)) -> AuthResponse:
    return AuthResponse(
        user=UserResponse(id=user.id, login=user.login),
        csrf_token=user.csrf_token,
    )


@router.get("/demo/config")
def demo_config(request: Request) -> dict[str, bool]:
    return {"enabled": request.app.state.config.demo_enabled}


@router.post("/demo", response_model=AuthResponse)
async def demo_login(request: Request, response: Response) -> AuthResponse:
    require_same_origin(request)
    if not request.app.state.config.demo_enabled:
        raise AuthError(404, "demo_disabled", "Demo access is disabled.")
    limiter = request.app.state.demo_rate_limiter
    client = _source_address(request)
    reservation = limiter.reserve_attempt(client)
    if reservation is None:
        raise AuthError(429, "rate_limited", "Too many demo sessions. Try again later.")
    # Count successful demo creation as well: each creates a separate sandbox.
    limiter.record_failure(client, reservation)
    csrf = await run_in_threadpool(issue_session, request, response, auth_method="demo")
    return AuthResponse(user=UserResponse(id="demo", login="Демо-аккаунт"), csrf_token=csrf)


@router.post("/logout", status_code=204, response_class=Response)
def logout(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(require_session),
) -> Response:
    store: SessionStore = request.app.state.session_store
    method = store.authentication_method(user.session_hash)
    store.revoke_hash(user.session_hash)
    if method == "demo":
        request.app.state.demo_workspaces.release(user.session_hash)
    config = request.app.state.config
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=cookie_is_secure(config.public_origin),
        httponly=True,
        samesite="lax",
    )
    response.status_code = 204
    if method == "oidc" and config.oidc_enabled:
        # Local revocation is complete before any provider request. The browser
        # receives only a bound, one-use same-origin ticket, never an ID token.
        browser = secrets.token_urlsafe(32)
        try:
            ticket = request.app.state.oidc.pending.issue("logout_ticket", browser, {})
            _flow_cookie(request, response, browser)
            response.headers["X-OIDC-Logout-URL"] = PREFIX + "/logout?ticket=" + ticket
        except OIDCFailure:
            response.headers["X-OIDC-Logout-URL"] = "/login?oidc_error=logout_incomplete"
    return response


def _flow_cookie(request: Request, response: Response, browser: str | None = None):
    options = dict(path=PREFIX, secure=cookie_is_secure(request.app.state.config.public_origin), httponly=True, samesite="lax")
    if browser:
        response.set_cookie(FLOW_COOKIE, browser, max_age=FLOW_SECONDS, **options)
    else:
        response.delete_cookie(FLOW_COOKIE, **options)


def _oidc_error(request: Request, code: str):
    response = RedirectResponse("/login?oidc_error=" + code, status_code=303,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    _flow_cookie(request, response)
    return response


@router.get("/oidc/config")
def oidc_configuration(request: Request) -> dict:
    return {"enabled": request.app.state.config.oidc_enabled, "provider_name": "Keycloak"}


@router.get("/oidc/start", response_class=RedirectResponse, status_code=303)
async def oidc_start(request: Request, next: str = "/customers"):
    browser = secrets.token_urlsafe(32)
    try:
        target = await request.app.state.oidc.authorize(browser, safe_destination(next))
    except OIDCFailure as failure:
        return _oidc_error(request, str(failure))
    response = RedirectResponse(target, status_code=303, headers={"Referrer-Policy": "no-referrer"})
    _flow_cookie(request, response, browser)
    return response


@router.get("/oidc/callback", response_class=RedirectResponse, status_code=303)
async def oidc_callback(request: Request):
    service = request.app.state.oidc
    params = request.query_params
    # Reject ambiguous duplicate parameters before exchanging any code.
    if any(len(params.getlist(k)) > 1 for k in ("state", "code", "error", "iss")):
        return _oidc_error(request, "invalid_response")
    data = service.pending.consume(params.get("state"), request.cookies.get(FLOW_COOKIE), "login")
    if data is None:
        return _oidc_error(request, "invalid_state")
    if params.get("error") or not params.get("code"):
        return _oidc_error(request, "access_denied")
    if params.get("iss") and params["iss"] != service.config.oidc_issuer_url:
        return _oidc_error(request, "invalid_response")
    try:
        await service.authenticate(data, params["code"])
    except OIDCFailure as failure:
        return _oidc_error(request, str(failure))
    response = RedirectResponse(data["destination"], status_code=303,
        headers={"Referrer-Policy": "no-referrer"})
    issue_session(request, response, auth_method="oidc")
    _flow_cookie(request, response)
    return response


@router.get("/oidc/logout", response_class=RedirectResponse, status_code=303)
async def oidc_logout(request: Request, ticket: str = ""):
    service = request.app.state.oidc
    browser = request.cookies.get(FLOW_COOKIE)
    if service.pending.consume(ticket, browser, "logout_ticket") is None:
        return _oidc_error(request, "invalid_state")
    try:
        target = await service.logout_url(browser)
        return RedirectResponse(target, status_code=303, headers={"Referrer-Policy": "no-referrer"})
    except OIDCFailure:
        return _oidc_error(request, "logout_incomplete")


@router.get("/oidc/logout-return", response_class=RedirectResponse, status_code=303)
def oidc_logout_return(request: Request, state: str = ""):
    data = request.app.state.oidc.pending.consume(state, request.cookies.get(FLOW_COOKIE), "logout_return")
    if data is None:
        return _oidc_error(request, "invalid_state")
    response = RedirectResponse("/login?oidc_logged_out=1", status_code=303,
        headers={"Referrer-Policy": "no-referrer"})
    _flow_cookie(request, response)
    return response
