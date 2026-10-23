from __future__ import annotations

import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
import pytest
from pwdlib import PasswordHash

from app.auth import AuthenticatedUser, hash_session_token, require_session
from app.config import DEFAULT_SOURCE_PATH, RuntimeConfig
from app.main import create_app

LOGIN = "analyst"
PASSWORD = "test-only-password-not-for-public-use"
METRICS_TOKEN = "test-only-metrics-token"
ORIGIN = "http://testserver"
PASSWORD_HASH = PasswordHash.recommended().hash(PASSWORD)


@pytest.fixture
def auth_data_dir():
    path = Path(__file__).resolve().parent / f".b04-auth-test-{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        database_path = path / "app.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        path.rmdir()


def configured_app_config(data_dir, *, public_origin: str = ORIGIN, **overrides) -> RuntimeConfig:
    return RuntimeConfig(
        source_path=DEFAULT_SOURCE_PATH,
        data_dir=data_dir,
        login=LOGIN,
        password_hash=PASSWORD_HASH,
        metrics_token=METRICS_TOKEN,
        public_origin=public_origin,
        **overrides,
    )


def login(client: TestClient, *, password: str = PASSWORD, login_name: str = LOGIN):
    return client.post(
        "/api/v1/auth/login",
        json={"login": login_name, "password": password},
        headers={"Origin": ORIGIN},
    )


@pytest.mark.parametrize(
    "missing_credentials",
    [
        {"login": None, "password_hash": PASSWORD_HASH, "metrics_token": METRICS_TOKEN},
        {"login": LOGIN, "password_hash": None, "metrics_token": METRICS_TOKEN},
        {"login": LOGIN, "password_hash": PASSWORD_HASH, "metrics_token": None},
    ],
)
def test_startup_fails_closed_when_a_required_credential_is_missing(
    auth_data_dir, missing_credentials
):
    config = RuntimeConfig(
        source_path=DEFAULT_SOURCE_PATH,
        data_dir=auth_data_dir,
        public_origin=ORIGIN,
        **missing_credentials,
    )
    try:
        with TestClient(create_app(config)):
            raise AssertionError("app started without credentials")
    except ValueError as error:
        assert "APP_LOGIN" in str(error)


def test_login_me_and_neutral_failed_login(auth_data_dir, caplog):
    with TestClient(create_app(configured_app_config(auth_data_dir))) as client:
        guest = client.get("/api/v1/auth/me")
        assert guest.status_code == 401

        failed = login(client, login_name="unknown-operator", password="incorrect-secret")
        assert failed.status_code == 401
        assert failed.json() == {
            "error": {"code": "authentication_failed", "message": "Invalid login or password."}
        }
        assert "incorrect-secret" not in failed.text
        assert "unknown-operator" not in failed.text

        response = login(client)
        assert response.status_code == 200
        payload = response.json()
        assert payload["user"] == {"id": "operator", "login": LOGIN}
        assert payload["csrf_token"]
        session_token = response.cookies.get("session")
        assert session_token
        assert session_token not in response.text
        assert PASSWORD not in response.text
        assert PASSWORD_HASH not in response.text

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json() == payload
        assert me.headers["cache-control"] == "no-store"
        assert "incorrect-secret" not in caplog.text

        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert "path=/" in cookie
        assert "secure" not in cookie

        with closing(sqlite3.connect(auth_data_dir / "app.sqlite3")) as connection:
            rows = connection.execute("SELECT token_hash FROM auth_sessions").fetchall()
            application_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        assert rows == [(hash_session_token(session_token),)]
        assert session_token not in str(rows)
        assert {"settings", "source_snapshots", "auth_sessions"} <= application_tables


def test_session_expiration_revokes_access(auth_data_dir):
    with TestClient(create_app(configured_app_config(auth_data_dir))) as client:
        response = login(client)
        assert response.status_code == 200
        session_token = response.cookies.get("session")
        assert session_token

        with closing(sqlite3.connect(auth_data_dir / "app.sqlite3")) as connection:
            connection.execute(
                "UPDATE auth_sessions SET created_at = 1, expires_at = 2 WHERE token_hash = ?",
                (hash_session_token(session_token),),
            )
            connection.commit()

        expired = client.get("/api/v1/auth/me")
        assert expired.status_code == 401
        with closing(sqlite3.connect(auth_data_dir / "app.sqlite3")) as connection:
            assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0


def test_logout_requires_origin_and_csrf_then_invalidates_session(auth_data_dir):
    with TestClient(create_app(configured_app_config(auth_data_dir))) as client:
        response = login(client)
        assert response.status_code == 200
        csrf_token = response.json()["csrf_token"]

        for method in ("patch", "put"):
            missing_origin = getattr(client, method)("/api/v1/future-write")
            assert missing_origin.status_code == 403
            assert missing_origin.json()["error"]["code"] == "invalid_origin"
            missing_csrf = getattr(client, method)(
                "/api/v1/future-write", headers={"Origin": ORIGIN}
            )
            assert missing_csrf.status_code == 403
            assert missing_csrf.json()["error"]["code"] == "csrf_failed"

        no_csrf = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
        assert no_csrf.status_code == 403
        assert no_csrf.json()["error"]["code"] == "csrf_failed"
        wrong_origin = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "https://attacker.example", "X-CSRF-Token": csrf_token},
        )
        assert wrong_origin.status_code == 403
        assert wrong_origin.json()["error"]["code"] == "invalid_origin"
        wrong_csrf = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong-token"},
        )
        assert wrong_csrf.status_code == 403

        logged_out = client.post(
            "/api/v1/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
        )
        assert logged_out.status_code == 204
        assert logged_out.content == b""
        assert "max-age=0" in logged_out.headers["set-cookie"].lower()
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.post(
            "/api/v1/auth/logout",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
        ).status_code == 401


def test_login_checks_origin_and_limits_failed_attempts(auth_data_dir):
    with TestClient(create_app(configured_app_config(auth_data_dir))) as client:
        rejected_origin = client.post(
            "/api/v1/auth/login",
            json={"login": LOGIN, "password": PASSWORD},
            headers={"Origin": "https://attacker.example"},
        )
        assert rejected_origin.status_code == 403
        assert PASSWORD not in rejected_origin.text

        for _ in range(4):
            assert login(client, password="wrong-password").status_code == 401
        assert login(client).status_code == 200

        for _ in range(5):
            assert login(client, password="wrong-password").status_code == 401
        limited = login(client, password="wrong-password")
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "rate_limited"
        assert "wrong-password" not in limited.text


def test_concurrent_failed_logins_are_reserved_before_password_verification(
    auth_data_dir, monkeypatch
):
    verification_gate = Event()
    verification_lock = Lock()
    verification_count = 0

    def synchronized_failure(password: str, password_hash: str, login_matches: bool) -> bool:
        nonlocal verification_count
        with verification_lock:
            verification_count += 1
            if verification_count == 5:
                verification_gate.set()
        assert verification_gate.wait(timeout=10)
        return False

    monkeypatch.setattr("app.api.auth.verify_login_password", synchronized_failure)
    with TestClient(create_app(configured_app_config(auth_data_dir))) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            requests = [
                pool.submit(login, client, password=f"wrong-password-{index}")
                for index in range(8)
            ]
            responses = [request.result(timeout=15) for request in requests]

        statuses = [response.status_code for response in responses]
        assert statuses.count(401) == 5
        assert statuses.count(429) == 3
        assert verification_count == 5
        assert login(client, password="another-wrong-password").status_code == 429


def test_https_login_sets_secure_cookie(auth_data_dir):
    origin = "https://testserver"
    config = configured_app_config(auth_data_dir, public_origin=origin)
    with TestClient(create_app(config), base_url=origin) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"login": LOGIN, "password": PASSWORD},
            headers={"Origin": origin},
        )
        assert response.status_code == 200
        assert "secure" in response.headers["set-cookie"].lower()


def test_metrics_bearer_does_not_authorize_guarded_customer_route(auth_data_dir):
    app = create_app(configured_app_config(auth_data_dir))

    @app.get("/api/v1/customers")
    def customers(user: AuthenticatedUser = Depends(require_session)):
        return {"operator": user.login}

    with TestClient(app) as client:
        guest = client.get(
            "/api/v1/customers",
            headers={"Authorization": f"Bearer {METRICS_TOKEN}"},
        )
        assert guest.status_code == 401
        assert guest.json()["error"]["code"] == "authentication_required"

        assert login(client).status_code == 200
        authorized = client.get("/api/v1/customers")
        assert authorized.status_code == 200
        assert authorized.json() == {"operator": LOGIN}
