"""B29 real SQLite publication boundaries and independently mounted history API."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
import shutil
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.observations import router as history_router
from app.api.source import router as source_router
from app.api.workflow import router as workflow_router
from app.auth import SessionStore, api_auth_middleware, csrf_token_for_session
from app.config import RuntimeConfig
from app.contracts import ModelSettings, SourceSnapshot
from app.evaluation import EvaluationCoordinator, SourceRevisionConflict, calculate_evaluation
from app.storage import ConcurrentWriteError, SQLiteRepository, snapshot_checksum
from app.workflow_storage import WorkflowPatch, WorkflowStore

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
HISTORY = "/api/v1/observations"
SOURCE = "/api/v1/source/snapshot"


def snapshot(*, balance=1000, auto=True, status="active", empty=False, expires="2026-10-30T00:00:00Z", email="synthetic@example.invalid"):
    return SourceSnapshot.model_validate(dict(
        accounts=[] if empty else [dict(id=1, email=email, balance=balance, withdrawBalance=0,
            totalReplenished=0, bonusBalance=0, bonusState="locked")],
        services=[] if empty else [dict(id=10, ownerId=1, name="Synthetic", paymentTerm="month",
            autoProlong=auto, status=status, price=1000, createdAt="2026-01-01T00:00:00Z", expiresAt=expires)],
        transactions=[]))


@pytest.fixture
def environment():
    scratch = Path(__file__).parent / (".observations-" + uuid4().hex)
    scratch.mkdir()
    repository = SQLiteRepository(scratch / "app.sqlite3")
    coordinator = EvaluationCoordinator(repository, clock=lambda: NOW)
    coordinator.start()
    workflow = WorkflowStore(repository.database_path, clock=lambda: NOW)
    workflow.initialize()
    app = FastAPI()
    app.state.repository = repository
    app.state.coordinator = coordinator
    app.state.workflow_store = workflow
    app.state.config = RuntimeConfig(login="analyst", public_origin="http://testserver")
    app.state.session_store = SessionStore(repository.database_path, ttl_seconds=3600)
    app.state.session_store.initialize()
    app.middleware("http")(api_auth_middleware)
    for router in (history_router, source_router, workflow_router):
        app.include_router(router, prefix="/api/v1")
    token = secrets.token_urlsafe(32)
    app.state.session_store.create(token)
    with TestClient(app) as client:
        client.cookies.set("session", token)
        headers = {"Origin": "http://testserver", "X-CSRF-Token": csrf_token_for_session(token)}
        yield client, coordinator, repository, workflow, headers, app
    shutil.rmtree(scratch)


def history(client, **params):
    response = client.get(HISTORY, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def revisions(coordinator):
    current = coordinator.current
    return {key: getattr(current, key) for key in ("analysis_revision", "data_revision", "settings_revision")}


def test_first_snapshot_successful_changes_retry_and_file_restart(environment):
    client, coordinator, repository, workflow, headers, _ = environment
    assert history(client)["events"] == []
    assert client.put(SOURCE, json=snapshot().model_dump(mode="json"), headers=headers).status_code == 200
    assert history(client)["events"] == []
    changed = snapshot(balance=0, auto=False, status="suspended")
    response = client.put(SOURCE, json=changed.model_dump(mode="json"), headers=headers)
    assert response.status_code == 200 and response.json()["changed"]
    events = history(client)["events"]
    assert [event["field"] for event in events] == ["balance", "autoProlong", "status"]
    for event in events:
        assert event["kind"] == "source"
        assert (event["previous_data_revision"], event["data_revision"]) == (1, 2)
        assert event["comparison_settings_revision"] == 1
        assert event["comparison_model_version"] == "rules-v1"
        assert event["comparison_as_of"] == event["received_at"] == "2026-10-25T00:00:00.000000Z"
    assert client.put(SOURCE, json=changed.model_dump(mode="json"), headers=headers).json()["changed"] is False
    assert history(client)["events"] == events
    state = repository.load_state()
    restarted = EvaluationCoordinator(SQLiteRepository(repository.database_path), clock=lambda: NOW)
    restarted.start()
    assert history(client)["events"] == events
    assert repository.load_state().data_revision == state.data_revision
    assert repository.load_state().settings_revision == state.settings_revision


def test_preview_rejection_confirmation_noop_and_csrf(environment):
    client, coordinator, repository, _, headers, _ = environment
    coordinator.replace_snapshot(snapshot())
    state = repository.load_state()
    changed = snapshot(balance=0)
    body = dict(expected_revision=revisions(coordinator), candidate_snapshot=changed.model_dump(mode="json"))
    assert client.post(SOURCE + "/preview", json=body).status_code == 403
    preview = client.post(SOURCE + "/preview", json=body, headers=headers)
    assert preview.status_code == 200, preview.text
    assert repository.load_state() == state
    assert history(client)["events"] == []
    rejected = client.post(SOURCE + f"/preview/{preview.json()['preview_id']}/confirm", json={
        "expected_revision": body["expected_revision"], "candidate_checksum": "0" * 64,
    }, headers={**headers, "If-Match": '"lab-a-1-d-1-s-1"'})
    assert rejected.status_code == 409
    assert repository.load_state() == state
    assert history(client)["events"] == []
    noop_body = dict(body, candidate_snapshot=snapshot().model_dump(mode="json"))
    noop = client.post(SOURCE + "/preview", json=noop_body, headers=headers).json()
    confirmed = client.post(SOURCE + f"/preview/{noop['preview_id']}/confirm", json={
        "expected_revision": body["expected_revision"], "candidate_checksum": noop["candidate_checksum"],
    }, headers={**headers, "If-Match": '"lab-a-1-d-1-s-1"'})
    assert confirmed.status_code == 200, confirmed.text
    assert not confirmed.json()["changed"]
    assert history(client)["events"] == []
    assert client.post(SOURCE + f"/preview/{noop['preview_id']}/confirm", json={
        "expected_revision": body["expected_revision"], "candidate_checksum": noop["candidate_checksum"],
    }, headers={**headers, "If-Match": '"lab-a-1-d-1-s-1"'}).status_code == 409
    assert history(client)["events"] == []


def test_disappearance_and_model_refresh_do_not_fabricate_source_history(environment):
    client, coordinator, repository, workflow, _, _ = environment
    coordinator.replace_snapshot(snapshot())
    workflow.update(1, WorkflowPatch(expected_revision=0, status="processed"), author="analyst")
    coordinator.replace_settings(ModelSettings(horizon_days=20), expected_settings_revision=1)
    coordinator.refresh()
    assert workflow.get(1)["status"] == "processed"
    events = history(client)["events"]
    assert sorted(event["kind"] for event in events) == ["model", "workflow"]
    model = next(event for event in events if event["kind"] == "model")
    assert model["previous_settings_revision"] == 1 and model["settings_revision"] == 2
    assert model["after"]["horizon_days"] == 20
    coordinator.replace_snapshot(snapshot(empty=True))
    source = [event for event in history(client)["events"] if event["kind"] == "source"]
    assert [event["field"] for event in source] == ["disappeared", "disappeared"]
    assert {event["entity"] for event in source} == {"account", "service"}


@pytest.mark.parametrize("operation,recorder", [("source", "record_source_observations"),
    ("model", "record_model_event"), ("workflow", "record_workflow_event")])
def test_recorder_failure_rolls_back_publication_and_history(environment, monkeypatch, operation, recorder):
    from app import observation_storage
    client, coordinator, repository, workflow, _, _ = environment
    coordinator.replace_snapshot(snapshot())
    state, published = repository.load_state(), coordinator.current
    original = getattr(observation_storage, recorder)
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected failure after event insert")
    monkeypatch.setattr(observation_storage, recorder, fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        if operation == "source":
            coordinator.replace_snapshot(snapshot(balance=0))
        elif operation == "model":
            coordinator.replace_settings(ModelSettings(horizon_days=20), expected_settings_revision=1)
        else:
            workflow.update(1, WorkflowPatch(expected_revision=0, status="processed"), author="analyst")
    assert repository.load_state() == state
    assert coordinator.current is published
    assert workflow.get(1)["revision"] == 0
    assert history(client)["events"] == []


def test_failed_calculation_stale_revision_and_failed_cas_write_nothing(environment, monkeypatch):
    client, coordinator, repository, workflow, _, _ = environment
    coordinator.replace_snapshot(snapshot())
    state = repository.load_state()
    with pytest.raises(SourceRevisionConflict):
        coordinator.replace_snapshot(snapshot(balance=0), expected_revision=("a-999", 1, 1))
    def fail(*args, **kwargs):
        raise ValueError("failed calculation")
    with monkeypatch.context() as patch:
        patch.setattr(coordinator, "_calculator", fail)
        with pytest.raises(ValueError, match="failed calculation"):
            coordinator.replace_snapshot(snapshot(balance=0))
    coordinator.refresh()
    with pytest.raises(ConcurrentWriteError):
        document = snapshot(balance=0).canonical_json()
        repository.commit_snapshot(document, snapshot_checksum(document), expected=state, received_at=NOW)
    assert history(client)["events"] == []


def test_workflow_events_reopen_deferral_and_atomic_hook_failure(environment, monkeypatch):
    client, coordinator, repository, workflow, _, _ = environment
    coordinator.replace_snapshot(snapshot())
    workflow.update(1, WorkflowPatch(expected_revision=0, status="processed", manual_outcome="topup_reported"), author="analyst")
    state = repository.load_state()
    original = WorkflowStore.observe_published_source
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected reopening failure")
    with monkeypatch.context() as patch:
        patch.setattr(WorkflowStore, "observe_published_source", fail)
        with pytest.raises(RuntimeError, match="reopening failure"):
            coordinator.replace_snapshot(snapshot(balance=0))
    assert repository.load_state() == state
    assert workflow.get(1)["status"] == "processed"
    assert len(history(client)["events"]) == 1
    coordinator.replace_snapshot(snapshot(balance=0))
    reopened = workflow.get(1)
    assert reopened["status"] == "new" and reopened["revision"] == 2
    assert reopened["manual_outcome"] == "topup_reported"
    coordinator.replace_snapshot(snapshot(balance=0))
    assert workflow.get(1) == reopened
    workflow.update(1, WorkflowPatch(expected_revision=2, status="deferred", next_contact_at=NOW + timedelta(days=1)), author="analyst")
    coordinator.replace_snapshot(snapshot(balance=1000))
    coordinator.replace_snapshot(snapshot(balance=0))
    assert workflow.get(1)["status"] == "deferred"
    manual = [event for event in history(client)["events"] if event["kind"] == "workflow"]
    assert len(manual) == 2
    assert manual[0]["changes"]["manual_outcome"]["after"] == "topup_reported"


def test_reopen_compares_both_snapshots_at_one_clock_not_old_published_clock(environment):
    client, coordinator, repository, workflow, _, _ = environment
    old = snapshot(auto=False, expires="2026-12-01T00:00:00Z")
    coordinator.replace_snapshot(old)
    assert coordinator.current.customer(1).risk_level == "low"
    workflow.update(1, WorkflowPatch(expected_revision=0, status="processed"), author="analyst")
    later = datetime(2026, 11, 30, tzinfo=timezone.utc)
    coordinator._clock = lambda: later
    calls = []
    def calculate(source, settings, **kwargs):
        calls.append((source.canonical_json(), canonical(settings), kwargs["as_of"]))
        return calculate_evaluation(source, settings, **kwargs)
    def canonical(settings):
        return settings.model_dump(mode="json")
    coordinator._calculator = calculate
    coordinator.replace_snapshot(snapshot(auto=False, expires="2026-12-01T00:00:00Z", email="changed@example.invalid"))
    assert len(calls) == 2 and calls[0][1:] == calls[1][1:]
    assert calls[0][2] == later
    assert coordinator.current.customer(1).risk_level == "medium"
    assert workflow.get(1)["status"] == "processed"
    assert history(client)["events"][0]["kind"] == "workflow"


def test_history_auth_bounded_pagination_filter_and_readonly(environment):
    client, coordinator, repository, workflow, _, app = environment
    coordinator.replace_snapshot(snapshot())
    coordinator.replace_snapshot(snapshot(balance=0, auto=False, status="suspended"))
    workflow.update(1, WorkflowPatch(expected_revision=0, status="processed"), author="analyst")
    coordinator.replace_settings(ModelSettings(horizon_days=20), expected_settings_revision=1)
    state = repository.load_state()
    with TestClient(app) as guest:
        assert guest.get(HISTORY).status_code == 401
    assert client.post(HISTORY).status_code == 403
    for params in (dict(limit=0), dict(limit=201), dict(offset=-1), dict(offset=1_000_001), dict(customer_id=0)):
        assert client.get(HISTORY, params=params).status_code == 422
    first = history(client, limit=2)
    assert first["has_more"] and first["next_offset"] == 2
    second = history(client, limit=2, offset=2)
    assert not set(event["event_id"] for event in first["events"]) & set(event["event_id"] for event in second["events"])
    all_events = history(client)["events"]
    assert history(client)["events"] == all_events
    assert [event["kind"] for event in history(client, customer_id=999)["events"]] == ["model"]
    assert repository.load_state() == state
    response = client.get(HISTORY)
    assert response.headers["Cache-Control"] == "no-store"
    with closing(repository._connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 3


def test_upgrade_existing_snapshot_installs_baseline_without_prior_events(environment):
    client, coordinator, repository, workflow, _, _ = environment
    coordinator.replace_snapshot(snapshot())
    with closing(repository._connect()) as connection:
        connection.execute("DELETE FROM source_observation_batches")
        connection.execute("DELETE FROM source_observation_baseline")
    repository.initialize()
    assert history(client)["events"] == []
    coordinator.replace_snapshot(snapshot(balance=0))
    assert [event["field"] for event in history(client)["events"]] == ["balance"]
