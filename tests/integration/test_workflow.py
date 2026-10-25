"""B27 standalone router proof; mounting is intentionally owned by integration."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import secrets
import shutil
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.workflow import router
from app.auth import SessionStore, api_auth_middleware, csrf_token_for_session
from app.config import RuntimeConfig
from app.storage import SQLiteRepository
from app.workflow_storage import WorkflowPatch, WorkflowRevisionConflict, WorkflowStore

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
URL = "/api/v1/customers/1/workflow"


@pytest.fixture
def environment():
    scratch = Path(__file__).parent / (".workflow-" + uuid4().hex)
    scratch.mkdir()
    database = scratch / "app.sqlite3"
    repository = SQLiteRepository(database)
    repository.initialize()
    store = WorkflowStore(database, clock=lambda: NOW)
    store.initialize()
    app = FastAPI()
    app.state.config = RuntimeConfig(login="analyst", public_origin="http://testserver")
    app.state.workflow_store = store
    app.state.session_store = SessionStore(database, ttl_seconds=3600)
    app.state.session_store.initialize()
    current = SimpleNamespace(customer=lambda customer_id: object() if customer_id == 1 else None)
    app.state.coordinator = SimpleNamespace(current=current)
    app.middleware("http")(api_auth_middleware)
    app.include_router(router, prefix="/api/v1")
    token = secrets.token_urlsafe(32)
    app.state.session_store.create(token)
    with TestClient(app) as client:
        client.cookies.set("session", token)
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token_for_session(token)}
        yield client, store, database, headers, repository, app
    shutil.rmtree(scratch)


def test_workflow_persistence_cas_author_and_no_source_mutations(environment):
    client, store, database, headers, repository, app = environment
    source = repository.load_state()
    evaluation = app.state.coordinator.current
    assert client.get(URL).json()["revision"] == 0
    note = '<script>alert("x")</script>\nordinary plain text'
    patch = dict(expected_revision=0, status="deferred", note=note,
                 next_contact_at="2026-10-26T12:00:00Z", manual_outcome="topup_reported")
    response = client.patch(URL, json=patch, headers=headers)
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["author"] == "analyst"
    assert record["updated_at"] == "2026-10-25T12:00:00.000000Z"
    assert record["note"] == note
    assert record["manual_outcome"] == "topup_reported"
    assert "confirmed" not in record
    restarted = WorkflowStore(database)
    restarted.initialize()
    assert restarted.get(1) == record
    assert client.patch(URL, json=patch, headers=headers).status_code == 409
    patch["expected_revision"] = 1
    assert client.patch(URL, json=patch, headers=headers).json() == record
    for _ in range(3):
        assert client.get(URL).json() == record
    assert restarted.get_many([1, 2])["1"] == record
    assert restarted.get_many([1, 2])["2"]["status"] == "new"
    assert repository.load_state() == source
    assert app.state.coordinator.current is evaluation
    # Reaching the contact date never silently ends the manual hold, and
    # operators can still amend notes without moving the scheduled date.
    store.clock = lambda: datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert client.get(URL).json()["status"] == "deferred"
    amended = client.patch(URL, json={"expected_revision": 1, "note": "follow up"}, headers=headers)
    assert amended.status_code == 200
    assert amended.json()["status"] == "deferred"


def test_auth_csrf_limits_and_validation(environment):
    client, store, database, headers, *_ = environment
    patch = dict(expected_revision=0, status="in_progress")
    with TestClient(client.app) as guest:
        assert guest.get(URL).status_code == 401
        assert guest.patch(URL, json=patch).status_code == 401
    assert client.patch(URL, json=patch).status_code == 403
    assert client.patch(URL, json=patch, headers={**headers, "Origin": "https://evil.test"}).status_code == 403
    for invalid in [dict(patch, author="spoofed"), dict(patch, updated_at="yesterday"),
                    dict(patch, expected_revision=True), dict(patch, note="x" * 2001),
                    dict(patch, note="bad\x00text"), dict(patch, status="deferred"),
                    dict(patch, next_contact_at="2026-10-26T12:00:00"),
                    dict(patch, status="deferred", next_contact_at="2026-10-24T12:00:00Z"),
                    dict(patch, manual_outcome="payment_confirmed"), dict(patch, note=None),
                    dict(expected_revision=0)]:
        assert client.patch(URL, json=invalid, headers=headers).status_code == 422
    assert client.patch(URL, content="x" * 17000, headers={**headers, "Content-Type": "application/json"}).status_code == 413
    assert client.patch(URL, content="hello", headers=headers).status_code == 415
    assert client.get("/api/v1/customers/2/workflow").status_code == 404
    assert store.get(1)["revision"] == 0


def test_parallel_writers_have_exactly_one_winner(environment):
    _, store, _, _, *_ = environment
    def write(note):
        try:
            return store.update(1, WorkflowPatch(expected_revision=0, note=note), author="analyst")
        except WorkflowRevisionConflict as conflict:
            return conflict.current_revision
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["first", "second"]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 1 in results
    assert store.get(1)["revision"] == 1


def test_publication_boundary_dedup_deferral_and_model_changes(environment):
    client, store, database, headers, *_ = environment
    store.update(1, WorkflowPatch(expected_revision=0, status="processed", manual_outcome="renewal_reported"), author="analyst")
    kwargs = dict(customer_id=1, previous_data_revision=1, data_revision=2,
                  before_risk="low", after_risk="high", before_gap_cents=0, after_gap_cents=100,
                  received_at=NOW, comparison_settings_revision=1,
                  comparison_model_version="v1", comparison_as_of=NOW)
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        with pytest.raises(ValueError):
            store.observe_published_source(connection, **kwargs)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO source_snapshots VALUES (1, 2, '{}', 'fixture', '2026-10-25T12:00:00Z')")
        # A model refresh retaining the source revision is rejected.
        with pytest.raises(ValueError):
            store.observe_published_source(connection, **{**kwargs, "previous_data_revision": 2})
        assert store.observe_published_source(connection, **kwargs) is True
        assert store.observe_published_source(connection, **kwargs) is False
        connection.rollback()
        assert store.get(1)["status"] == "processed"  # rollback is atomic
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO source_snapshots VALUES (1, 2, '{}', 'fixture', '2026-10-25T12:00:00Z')")
        assert store.observe_published_source(connection, **kwargs)
        connection.commit()
    reopened = store.get(1)
    assert reopened["status"] == "new"
    assert reopened["manual_outcome"] == "renewal_reported"
    assert reopened["author"] == "system:source"
    store.update(1, WorkflowPatch(expected_revision=2, status="deferred", next_contact_at=datetime(2026, 10, 26, tzinfo=timezone.utc)), author="analyst")
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE source_snapshots SET data_revision=3")
        assert not store.observe_published_source(connection, **{**kwargs, "previous_data_revision": 2, "data_revision": 3})
        connection.commit()
    assert store.get(1)["status"] == "deferred"


@pytest.mark.parametrize("before,after,gap_before,gap_after,reopens", [
    ("high", "high", 100, 200, False), ("unknown", "high", 0, 0, False),
    ("medium", "low", 0, 100, True), ("low", "medium", 0, 0, True),
    ("high", "low", 100, 0, False),
])
def test_exact_reopen_rules(environment, before, after, gap_before, gap_after, reopens):
    _, store, database, *_ = environment
    store.update(1, WorkflowPatch(expected_revision=0, status="processed"), author="analyst")
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO source_snapshots VALUES (1, 2, '{}', 'fixture', '2026-10-25T12:00:00Z')")
        assert store.observe_published_source(connection, customer_id=1, previous_data_revision=1, data_revision=2,
            before_risk=before, after_risk=after, before_gap_cents=gap_before, after_gap_cents=gap_after,
            received_at=NOW, comparison_settings_revision=1, comparison_model_version="v1", comparison_as_of=NOW) is reopens
        connection.commit()
