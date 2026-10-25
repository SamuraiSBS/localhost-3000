"""Server-side cookie sessions and authentication request guards."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
from functools import lru_cache
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Lock
import time
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Request
from pwdlib import PasswordHash
from starlette.responses import JSONResponse, Response

from app.config import RuntimeConfig

SESSION_COOKIE_NAME = "session"
SESSION_DATABASE_NAME = "app.sqlite3"
LOGIN_PATH = "/api/v1/auth/login"
MAX_LOGIN_BODY_BYTES = 4096
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60

_SESSION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AuthError(Exception):
    """An API error whose response contains no request or credential values."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def auth_error_response(error: AuthError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
    )


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: str
    login: str
    csrf_token: str = field(repr=False)
    session_hash: str = field(repr=False)


def _parse_origin(value: str | None) -> tuple[str, str, int] | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.rstrip(".").lower()
    return scheme, host, port or (443 if scheme == "https" else 80)


def is_same_origin(origin: str | None, public_origin: str) -> bool:
    expected = _parse_origin(public_origin)
    return expected is not None and _parse_origin(origin) == expected


def cookie_is_secure(public_origin: str) -> bool:
    parsed = _parse_origin(public_origin)
    return parsed is not None and parsed[0] == "https"


def require_same_origin(request: Request) -> None:
    config: RuntimeConfig = request.app.state.config
    if not is_same_origin(request.headers.get("origin"), config.public_origin):
        raise AuthError(403, "invalid_origin", "Request origin is not allowed.")


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def csrf_token_for_session(token: str) -> str:
    """Derive an unguessable, session-bound token without storing it in SQLite."""
    return hmac.new(token.encode("ascii"), b"localhost3000:csrf:v1", hashlib.sha256).hexdigest()


def _valid_session_token(token: str | None) -> bool:
    return bool(token and _SESSION_TOKEN_PATTERN.fullmatch(token))


@lru_cache(maxsize=1)
def _password_hasher() -> PasswordHash:
    return PasswordHash.recommended()


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return _password_hasher().hash(secrets.token_urlsafe(32))


def validate_runtime_credentials(config: RuntimeConfig) -> None:
    """Fail startup before serving routes if auth configuration is incomplete."""
    config.require_credentials()
    if len(config.login or "") > 128:
        raise ValueError("APP_LOGIN must be at most 128 characters")
    if type(config.session_ttl_seconds) is not int or config.session_ttl_seconds < 1:
        raise ValueError("SESSION_TTL_SECONDS must be a positive integer")
    if _parse_origin(config.public_origin) is None:
        raise ValueError("PUBLIC_ORIGIN must be an absolute HTTP(S) origin")
    try:
        _password_hasher().verify("startup-validation-only", config.password_hash or "")
    except Exception:
        raise ValueError("APP_PASSWORD_HASH must contain a supported password hash") from None


def verify_login_password(password: str, password_hash: str, login_matches: bool) -> bool:
    """Always perform one password verification, including for unknown logins."""
    candidate_hash = password_hash if login_matches else _dummy_password_hash()
    try:
        return bool(_password_hasher().verify(password, candidate_hash))
    except Exception:
        # Startup validates the configured hash; any later verifier failure is
        # treated as an ordinary failed login without exposing its details.
        return False


class SessionStore:
    """SQLite session records keyed by a hash of the opaque cookie value."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
        busy_timeout_ms: int = 5000,
    ):
        if type(ttl_seconds) is not int or ttl_seconds < 1:
            raise ValueError("session_ttl_must_be_positive")
        if type(busy_timeout_ms) is not int or busy_timeout_ms < 1:
            raise ValueError("busy_timeout_must_be_positive")
        self.database_path = Path(database_path).expanduser().resolve()
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.busy_timeout_ms = busy_timeout_ms

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS auth_sessions ("
                "token_hash TEXT PRIMARY KEY CHECK(length(token_hash) = 64), "
                "created_at INTEGER NOT NULL, "
                "expires_at INTEGER NOT NULL CHECK(expires_at > created_at)"
                ") WITHOUT ROWID"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS auth_sessions_expires_at_idx "
                "ON auth_sessions(expires_at)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(auth_sessions)")}
            if "auth_method" not in columns:
                connection.execute("ALTER TABLE auth_sessions ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'local'")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(self, token: str, *, auth_method: str = "local") -> int:
        if auth_method not in {"local", "oidc", "demo"}:
            raise ValueError("invalid_auth_method")
        now = int(self.clock())
        expires_at = now + self.ttl_seconds
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                "INSERT INTO auth_sessions(token_hash, created_at, expires_at, auth_method) VALUES (?, ?, ?, ?)",
                (hash_session_token(token), now, expires_at, auth_method),
            )
            connection.commit()
            return expires_at
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_expiration(self, token: str) -> int | None:
        token_hash = hash_session_token(token)
        now = int(self.clock())
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT expires_at FROM auth_sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            expires_at = int(row[0])
            if expires_at <= now:
                connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
                return None
            return expires_at
        finally:
            connection.close()

    def revoke_hash(self, token_hash: str) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        finally:
            connection.close()

    def authentication_method(self, token_hash: str) -> str:
        connection = self._connect()
        try:
            row = connection.execute("SELECT auth_method FROM auth_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
            return str(row[0]) if row else "local"
        finally:
            connection.close()


def issue_session(request: Request, response: Response, *, auth_method: str = "local") -> str:
    """Rotate any old browser session; expose only an opaque HttpOnly cookie."""
    store: SessionStore = request.app.state.session_store
    old = request.cookies.get(SESSION_COOKIE_NAME)
    if _valid_session_token(old):
        if store.authentication_method(hash_session_token(old)) == "demo":
            request.app.state.demo_workspaces.release(hash_session_token(old))
        store.revoke_hash(hash_session_token(old))
    token = secrets.token_urlsafe(32)
    store.create(token, auth_method=auth_method)
    if auth_method == "demo":
        try:
            request.app.state.demo_workspaces.create(hash_session_token(token))
        except BaseException:
            store.revoke_hash(hash_session_token(token))
            raise
    config = request.app.state.config
    response.set_cookie(SESSION_COOKIE_NAME, token, max_age=config.session_ttl_seconds, path="/",
        secure=cookie_is_secure(config.public_origin), httponly=True, samesite="lax")
    return csrf_token_for_session(token)


class LoginRateLimiter:
    """Small per-process failed-login limit for the single-process MVP runtime."""

    def __init__(
        self,
        *,
        max_attempts: int = MAX_LOGIN_ATTEMPTS,
        window_seconds: int = LOGIN_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 2048,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.clock = clock
        self.max_clients = max_clients
        self._failures: dict[str, deque[float]] = {}
        self._reservations: dict[str, dict[object, float]] = {}
        self._lock = Lock()

    def _prune(self, client: str, now: float) -> deque[float]:
        attempts = self._failures.setdefault(client, deque())
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            self._failures.pop(client, None)
            return deque()
        return attempts

    def _prune_reservations(self, client: str, now: float) -> dict[object, float]:
        reservations = self._reservations.get(client)
        if reservations is None:
            return {}
        cutoff = now - self.window_seconds
        for reservation, reserved_at in list(reservations.items()):
            if reserved_at <= cutoff:
                del reservations[reservation]
        if not reservations:
            self._reservations.pop(client, None)
            return {}
        return reservations

    def reserve_attempt(self, client: str) -> object | None:
        """Atomically reserve one of the per-client guesses in the current window."""
        now = self.clock()
        with self._lock:
            attempts = self._prune(client, now)
            reservations = self._prune_reservations(client, now)
            clients = set(self._failures) | set(self._reservations)
            if client not in clients and len(clients) >= self.max_clients:
                idle_clients = [key for key in clients if not self._reservations.get(key)]
                if not idle_clients:
                    return None
                oldest_client = min(
                    idle_clients,
                    key=lambda key: self._failures[key][0]
                    if self._failures.get(key)
                    else now,
                )
                self._failures.pop(oldest_client, None)
                self._reservations.pop(oldest_client, None)
            if len(attempts) + len(reservations) >= self.max_attempts:
                return None
            reservation = object()
            reservations[reservation] = now
            self._reservations[client] = reservations
            return reservation

    def record_failure(self, client: str, reservation: object) -> None:
        """Convert an admitted in-flight attempt into a failed guess."""
        now = self.clock()
        with self._lock:
            reservations = self._reservations.get(client)
            reserved_at = reservations.pop(reservation, None) if reservations else None
            if reservations is not None and not reservations:
                self._reservations.pop(client, None)
            if reserved_at is None or reserved_at <= now - self.window_seconds:
                self._prune(client, now)
                return
            attempts = self._failures.setdefault(client, deque())
            attempts.append(reserved_at)
            self._failures[client] = deque(sorted(attempts))
            self._prune(client, now)

    def record_success(self, client: str, reservation: object) -> None:
        """Release this reservation and reset earlier failures after valid login."""
        with self._lock:
            reservations = self._reservations.get(client)
            if reservations is not None:
                reservations.pop(reservation, None)
                if not reservations:
                    self._reservations.pop(client, None)
            self._failures.pop(client, None)


def require_session(request: Request) -> AuthenticatedUser:
    """Require the session cookie and validate Origin/CSRF on unsafe methods."""
    cached = getattr(request.state, "authenticated_user", None)
    if cached is not None:
        return cached

    config: RuntimeConfig = request.app.state.config
    store: SessionStore | None = getattr(request.app.state, "session_store", None)
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if store is None or not _valid_session_token(token):
        raise AuthError(401, "authentication_required", "Authentication required.")

    expires_at = store.get_expiration(token)
    if expires_at is None:
        raise AuthError(401, "authentication_required", "Authentication required.")

    csrf_token = csrf_token_for_session(token)
    is_demo = store.authentication_method(hash_session_token(token)) == "demo"
    if is_demo and not config.demo_enabled:
        raise AuthError(401, "authentication_required", "Authentication required.")
    user = AuthenticatedUser(
        id="demo" if is_demo else "operator",
        login="Демо-аккаунт" if is_demo else (config.login or ""),
        csrf_token=csrf_token,
        session_hash=hash_session_token(token),
    )
    if request.method.upper() not in _SAFE_METHODS:
        require_same_origin(request)
        provided_token = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(provided_token, csrf_token):
            raise AuthError(403, "csrf_failed", "A valid CSRF token is required.")

    request.state.authenticated_user = user
    return user


async def api_auth_middleware(request: Request, call_next) -> Response:
    """Default-deny the product API; only primary login is public."""
    if not request.url.path.startswith("/api/v1/"):
        return await call_next(request)

    from app.oidc import PUBLIC_ROUTES
    is_public_auth = (request.method.upper() == "POST" and request.url.path in {LOGIN_PATH, "/api/v1/auth/demo"}) or (request.method.upper(), request.url.path) in PUBLIC_ROUTES or (request.method.upper(), request.url.path) == ("GET", "/api/v1/auth/demo/config")
    if not is_public_auth:
        try:
            user = require_session(request)
            if user.id == "demo" and not request.url.path.startswith("/api/v1/auth/"):
                # Request-local application view: never swap global state between users.
                request.scope["app"] = request.app.state.demo_workspaces.application_for(
                    request.app, user.session_hash
                )
        except AuthError as error:
            response = auth_error_response(error)
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    response.headers["Cache-Control"] = "no-store"
    if (request.method.upper(), request.url.path) in PUBLIC_ROUTES:
        response.headers["Referrer-Policy"] = "no-referrer"
    return response
