from dataclasses import replace
from contextlib import closing
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from pwdlib import PasswordHash
import pytest

from app.auth import SessionStore
from app.config import RuntimeConfig
from app.main import create_app
from app.oidc import FLOW_COOKIE, PREFIX

ORIGIN = "http://testserver"
PASSWORD = "test-oidc-local-password"
PASSWORD_HASH = PasswordHash.recommended().hash(PASSWORD)


@pytest.fixture
def local_config():
    directory = Path(__file__).parent / (".b13-test-" + uuid4().hex)
    directory.mkdir()
    yield RuntimeConfig(data_dir=directory, login="analyst", password_hash=PASSWORD_HASH,
        metrics_token="unit-metrics", public_origin=ORIGIN)
    for suffix in ("", "-shm", "-wal"):
        (directory / ("app.sqlite3" + suffix)).unlink(missing_ok=True)
    directory.rmdir()


def oidc_config(local):
    return replace(local, oidc_enabled=True, oidc_issuer_url="http://127.0.0.1:1/realms/unavailable",
        oidc_client_id="unit-client", oidc_client_secret="unit-secret",
        oidc_redirect_uri=ORIGIN + PREFIX + "/callback", oidc_allowed_subjects=("unit-subject",))


def login(client):
    result = client.post("/api/v1/auth/login", json={"login": "analyst", "password": PASSWORD}, headers={"Origin": ORIGIN})
    assert result.status_code == 200
    return result


def test_disabled_config_and_exact_public_routes(local_config):
    with TestClient(create_app(local_config)) as client:
        assert client.get(PREFIX + "/config").json() == {"enabled": False, "provider_name": "Keycloak"}
        start = client.get(PREFIX + "/start", follow_redirects=False)
        assert start.headers["location"] == "/login?oidc_error=disabled"
        assert start.headers["cache-control"] == "no-store"
        assert start.headers["referrer-policy"] == "no-referrer"
        for path in (PREFIX + "/other", PREFIX + "/config/extra", "/api/v1/customers", "/api/v1/openapi.json"):
            assert client.get(path).status_code == 401
        assert client.post(PREFIX + "/config").status_code == 401


def test_idp_unavailable_does_not_block_startup_health_or_basic_login(local_config):
    with TestClient(create_app(oidc_config(local_config))) as client:
        assert client.get("/healthz").status_code == 200
        failed = client.get(PREFIX + "/start", follow_redirects=False)
        assert failed.headers["location"] == "/login?oidc_error=provider_unavailable"
        result = login(client)
        assert client.get("/api/v1/customers").status_code == 200
        out = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": result.json()["csrf_token"]})
        assert out.status_code == 204 and not out.content
        assert "X-OIDC-Logout-URL" not in out.headers
        assert client.get("/api/v1/auth/me").status_code == 401


def test_callback_invalid_state_duplicate_and_replay_fail_closed(local_config, monkeypatch):
    app = create_app(oidc_config(local_config))
    with TestClient(app) as client:
        for query in ("", "?state=invalid&code=bad", "?state=a&state=b&code=x"):
            result = client.get(PREFIX + "/callback" + query, follow_redirects=False)
            assert result.status_code == 303 and "oidc_error=" in result.headers["location"]
            assert "session=" not in result.headers.get("set-cookie", "")
        browser = secrets.token_urlsafe(32)
        state = app.state.oidc.pending.issue("login", browser, {"destination": "/customers"})
        client.cookies.set(FLOW_COOKIE, browser, path=PREFIX)
        async def authenticated(data, code):
            assert code == "unit-code"
        monkeypatch.setattr(app.state.oidc, "authenticate", authenticated)
        first = client.get(PREFIX + f"/callback?state={state}&code=unit-code", follow_redirects=False)
        assert first.headers["location"] == "/customers"
        assert client.get("/api/v1/auth/me").status_code == 200
        replay = client.get(PREFIX + f"/callback?state={state}&code=unit-code", follow_redirects=False)
        assert replay.headers["location"] == "/login?oidc_error=invalid_state"


def test_oidc_login_rotates_old_session_and_logout_revokes_before_provider(local_config, monkeypatch):
    app = create_app(oidc_config(local_config))
    with TestClient(app) as client:
        login(client)
        old = client.cookies.get("session")
        browser = secrets.token_urlsafe(32)
        state = app.state.oidc.pending.issue("login", browser, {"destination": "/settings"})
        client.cookies.set(FLOW_COOKIE, browser, path=PREFIX)
        async def accepted(data, code):
            return None
        monkeypatch.setattr(app.state.oidc, "authenticate", accepted)
        result = client.get(PREFIX + f"/callback?state={state}&code=unit", follow_redirects=False)
        assert result.headers["location"] == "/settings"
        current = client.get("/api/v1/auth/me").json()
        from app.auth import hash_session_token
        assert app.state.session_store.get_expiration(old) is None
        assert client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
        result = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": current["csrf_token"]})
        assert result.status_code == 204 and result.content == b""
        ticket = result.headers["X-OIDC-Logout-URL"]
        assert ticket.startswith(PREFIX + "/logout?ticket=")
        assert client.get("/api/v1/auth/me").status_code == 401
        failed = client.get(ticket, follow_redirects=False)
        assert failed.headers["location"] == "/login?oidc_error=logout_incomplete"
        assert client.get(ticket, follow_redirects=False).headers["location"] == "/login?oidc_error=invalid_state"


def test_logout_return_is_browser_bound_single_use(local_config):
    app = create_app(local_config)
    with TestClient(app) as client:
        browser = secrets.token_urlsafe(32)
        state = app.state.oidc.pending.issue("logout_return", browser, {})
        client.cookies.set(FLOW_COOKIE, secrets.token_urlsafe(32), path=PREFIX)
        assert "invalid_state" in client.get(PREFIX + "/logout-return?state=" + state, follow_redirects=False).headers["location"]
        client.cookies.set(FLOW_COOKIE, browser, path=PREFIX)
        assert client.get(PREFIX + "/logout-return?state=" + state, follow_redirects=False).headers["location"] == "/login?oidc_logged_out=1"
        assert "invalid_state" in client.get(PREFIX + "/logout-return?state=" + state, follow_redirects=False).headers["location"]


def test_legacy_sessions_migrate_without_losing_primary_login(local_config):
    path = local_config.data_dir / "app.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE auth_sessions(token_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)")
        connection.commit()
    store = SessionStore(path, ttl_seconds=300)
    store.initialize()
    token = secrets.token_urlsafe(32)
    store.create(token, auth_method="oidc")
    from app.auth import hash_session_token
    assert SessionStore(path, ttl_seconds=300).authentication_method(hash_session_token(token)) == "oidc"
