"""B28 quality API provenance, freshness and authenticated read-only contract."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api import data_quality
from app.api.customers import router as customers_router
from app.auth import SessionStore, api_auth_middleware
from app.config import RuntimeConfig
from app.contracts import SourceSnapshot
from app.evaluation import EvaluationCoordinator
from app.storage import SQLiteRepository

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
URL = "/api/v1/data-quality"


def source():
    return SourceSnapshot.model_validate({
        "accounts": [dict(id=key, email=f"{key}@example.invalid", balance=1000,
                          withdrawBalance=0, totalReplenished=1000, bonusBalance=0,
                          bonusState="locked") for key in (9, 2, 7)],
        "services": [dict(id=key, ownerId=owner, name="Fixture", paymentTerm="month",
                          autoProlong=True, status="active", price=100,
                          createdAt="2026-01-01T00:00:00Z",
                          expiresAt=(NOW + timedelta(days=days)).isoformat())
                     for key, owner, days in ((1, 2, -7), (2, 2, -8), (3, 7, 60))],
        "transactions": [],
    })


@pytest.fixture
def environment():
    # Explicit workspace directory avoids Windows numbered-temp-directory ACL failures.
    directory = Path(__file__).resolve().parents[2] / ".state" / "quality-api-tests" / uuid4().hex
    directory.mkdir(parents=True)
    repository = SQLiteRepository(directory / "app.sqlite3")
    coordinator = EvaluationCoordinator(repository, clock=lambda: NOW - timedelta(hours=1))
    coordinator.start()
    coordinator.replace_snapshot(source())
    sessions = SessionStore(repository.database_path, ttl_seconds=3600, clock=lambda: NOW.timestamp())
    sessions.initialize()
    token = "Q" * 43
    sessions.create(token)
    app = FastAPI()
    app.state.repository = repository
    app.state.coordinator = coordinator
    app.state.session_store = sessions
    app.state.config = RuntimeConfig(data_dir=directory, login="operator")
    app.middleware("http")(api_auth_middleware)
    app.include_router(data_quality.router, prefix="/api/v1")
    app.include_router(customers_router, prefix="/api/v1")
    app.dependency_overrides[data_quality.quality_now] = lambda: NOW
    with TestClient(app) as client:
        client.cookies.set("session", token)
        yield client, app


def test_authentication_and_documented_contract(environment):
    client, app = environment
    result = client.get(URL)
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    operation = app.openapi()["paths"][URL]["get"]
    assert operation["security"] == [{"SessionCookie": []}]
    assert "24-hour" in operation["description"]
    assert "expected-count manifest" in operation["description"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("DataQualityResponse")
    client.cookies.clear()
    assert client.get(URL).status_code == 401
    client.cookies.set("session", "not-a-valid-session")
    assert client.get(URL).status_code == 401
    client.cookies.set("session", "X" * 43)
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize("age,state", [
    (timedelta(hours=1), "fresh"),
    (timedelta(hours=24), "fresh"),
    (timedelta(hours=24, microseconds=1), "stale"),
    (None, "no_timestamp"),
    (-timedelta(seconds=1), "future_timestamp"),
])
def test_receipt_freshness_and_inclusive_boundary(environment, monkeypatch, age, state):
    client, app = environment
    persisted = app.state.repository.load_state()
    receipt = NOW - age if age is not None else None
    monkeypatch.setattr(app.state.repository, "load_state", lambda: replace(persisted, received_at=receipt))
    result = client.get(URL).json()
    assert result["freshness"] == {
        "state": state, "threshold_seconds": 86400.0,
        "age_seconds": age.total_seconds() if age is not None and age >= timedelta(0) else None,
    }
    assert result["received_at"] == (receipt.isoformat().replace("+00:00", "Z") if receipt else None)
    assert result["as_of"] == "2026-10-25T11:00:00Z"
    assert result["checked_at"] == "2026-10-25T12:00:00Z"


def test_coverage_unknown_warning_navigation_matches_customer_list(environment):
    client, app = environment
    response = client.get(URL)
    assert response.status_code == 200, response.text
    result = response.json()
    listing = client.get("/api/v1/customers").json()
    assert result["analysis_revision"] == listing["analysis_revision"]
    assert result["data_revision"] == listing["data_revision"]
    assert result["settings_revision"] == listing["settings_revision"]
    assert result["score_coverage"] == {"numerator": 2, "denominator": 3, "unknown": 1, "state": "partial"}
    assert result["monetary_coverage"]["numerator"] == app.state.coordinator.current.monetary_coverage_evaluated
    assert result["monetary_coverage"]["denominator"] == 3
    assert result["unknown_reasons"] == [{"code": "no_eligible_services", "count": 1, "customer_ids": [9]}]
    assert result["field_warnings"] == [{"code": "active_expired_over_five_days", "count": 2,
                                          "customer_count": 1, "customer_ids": [2]}]
    assert result["warning_count"] == 2
    assert result["source_completeness"] == "not_established"
    assert any("expected-count manifest" in text for text in result["limitations"])
    unknown = client.get("/api/v1/customers", params={"risk_level": "unknown"}).json()
    assert [int(row["id"]) for row in unknown["items"]] == result["unknown_reasons"][0]["customer_ids"]
    for group in result["field_warnings"]:
        for key in group["customer_ids"]:
            assert client.get(f"/api/v1/customers/{key}").status_code == 200


def test_polling_has_no_writes_or_evaluation_clock_refresh(environment, monkeypatch):
    client, app = environment
    repository = app.state.repository
    original = repository.load_state()
    evaluation = app.state.coordinator.current
    database_before = repository.database_path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Quality polling attempted a write or calculation clock refresh")

    monkeypatch.setattr(app.state.coordinator, "_clock", forbidden)
    for method in ("commit_snapshot", "commit_settings", "commit_refresh"):
        monkeypatch.setattr(repository, method, forbidden)
    before = client.get(URL).json()
    app.dependency_overrides[data_quality.quality_now] = lambda: NOW + timedelta(days=2)
    after = client.get(URL).json()
    assert before["freshness"]["state"] == "fresh"
    assert after["freshness"]["state"] == "stale"
    assert after["freshness"]["age_seconds"] == before["freshness"]["age_seconds"] + 172800
    assert before["as_of"] == after["as_of"]
    assert before["received_at"] == after["received_at"]
    assert app.state.coordinator.current is evaluation
    assert repository.load_state() == original
    assert repository.database_path.read_bytes() == database_before


@pytest.mark.parametrize("field,value", [
    ("data_revision", 999), ("settings_revision", 999), ("analysis_revision", 999),
    ("snapshot_json", "{}"), ("settings_json", "{}"),
])
def test_mismatch_retries_then_returns_service_unavailable(environment, monkeypatch, field, value):
    client, app = environment
    state = app.state.repository.load_state()
    calls = []

    def raced():
        calls.append(1)
        return replace(state, **{field: value}, received_at=NOW)

    monkeypatch.setattr(app.state.repository, "load_state", raced)
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "evaluation_revision_conflict"
    assert response.headers["Retry-After"] == "1"
    assert len(calls) == 3


def test_race_retries_and_uses_timestamp_from_matching_read_only(environment, monkeypatch):
    client, app = environment
    state = app.state.repository.load_state()
    states = iter([replace(state, data_revision=999, received_at=NOW), state])
    monkeypatch.setattr(app.state.repository, "load_state", lambda: next(states))
    result = client.get(URL).json()
    assert result["data_revision"] == state.data_revision
    assert result["received_at"] == "2026-10-25T11:00:00Z"
    assert result["freshness"]["age_seconds"] == 3600


def test_absent_evaluation_is_clear_service_unavailable(environment, monkeypatch):
    client, app = environment
    monkeypatch.setattr(app.state.coordinator, "_published", None)
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "evaluation_unavailable"
