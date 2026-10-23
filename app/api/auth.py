"""Login, current-user and logout endpoints."""

from __future__ import annotations

import hmac
import secrets

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

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
)

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
    token = secrets.token_urlsafe(32)
    csrf_token = csrf_token_for_session(token)
    store: SessionStore = request.app.state.session_store
    store.create(token)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=config.session_ttl_seconds,
        path="/",
        secure=cookie_is_secure(config.public_origin),
        httponly=True,
        samesite="lax",
    )
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


@router.post("/logout", status_code=204, response_class=Response)
def logout(
    request: Request,
    response: Response,
    user: AuthenticatedUser = Depends(require_session),
) -> Response:
    store: SessionStore = request.app.state.session_store
    store.revoke_hash(user.session_hash)
    config = request.app.state.config
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=cookie_is_secure(config.public_origin),
        httponly=True,
        samesite="lax",
    )
    response.status_code = 204
    return response
