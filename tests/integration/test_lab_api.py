"""Lab HTTP boundaries and no-write guarantees using isolated ASGI state."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pwdlib import PasswordHash
import pytest

from app.config import RuntimeConfig
from app.contracts import SourceSnapshot
from app.main import create_app
from app.metrics import render_metrics
from app.storage import snapshot_checksum

AS_OF = datetime(2026, 10, 25, tzinfo=timezone.utc)
PASSWORD = "lab-integration-password"
PASSWORD_HASH = PasswordHash.recommended().hash(PASSWORD)
ORIGIN = "http://testserver"


def snapshot():
    return SourceSnapshot.model_validate({
        "accounts": [{"id": 9001, "email": "fixture@example.invalid", "balance": 1200,
                      "withdrawBalance": 9000, "totalReplenished": 25000,
                      "bonusBalance": 500, "bonusState": "locked"}],
        "services": [{"id": key, "ownerId": 9001, "name": f"Fixture {key}",
                      "paymentTerm": "month", "autoProlong": key == 101,
                      "createdAt": "2026-01-01T00:00:00Z", "expiresAt": expiry,
                      "status": "active", "price": 1000}
                     for key, expiry in [(101, "2026-10-30T00:00:00Z"),
                                         (102, "2026-11-04T00:00:00Z")]],
        "transactions": [],
    })


@pytest.fixture
def lab_environment():
    # mkdir avoids the Windows ACL failure affecting pytest's numbered temp dirs.
    directory = Path(__file__).resolve().parents[2] / ".state" / "lab-api-tests" / uuid4().hex
    directory.mkdir(parents=True)
    app = create_app(RuntimeConfig(data_dir=directory, login="analyst", password_hash=PASSWORD_HASH,
                                   metrics_token="lab-test-metrics", public_origin=ORIGIN))
    with TestClient(app) as client:
        app.state.coordinator._clock = lambda: AS_OF
        app.state.coordinator.replace_snapshot(snapshot())
        login = client.post("/api/v1/auth/login", json={"login": "analyst", "password": PASSWORD},
                            headers={"Origin": ORIGIN})
        assert login.status_code == 200
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        yield client, app, headers


def revision(app):
    current = app.state.coordinator.current
    return {name: getattr(current, name) for name in
            ("analysis_revision", "data_revision", "settings_revision")}


def test_b24_source_preview_schema_and_coherent_diff(lab_environment):
    client, app, headers = lab_environment
    candidate = snapshot().model_dump(mode="json")
    candidate["accounts"][0]["balance"] += 800
    current = revision(app)
    preview = client.post("/api/v1/source/snapshot/preview", json={
        "expected_revision": current, "candidate_snapshot": candidate,
    }, headers=headers)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    customer, = data["diff"]["customer_results"]
    assert customer["before_score"] == 66.33
    assert customer["after_score"] == 38.33
    assert customer["score_delta"] == -28
    assert data["diff"]["before"]["funding_gap_cents"] == 800
    assert data["diff"]["after"]["funding_gap_cents"] == 0
    assert revision(app) == current
    assert preview.headers["cache-control"] == "no-store"
    schema = client.get("/api/v1/openapi.json").json()
    for path, request_model, response_model in (
        ("/api/v1/source/snapshot/preview", "SourcePreviewRequest", "SourcePreviewResponse"),
        ("/api/v1/source/snapshot/preview/{preview_id}/confirm", "SourceConfirmRequest", "SourceConfirmResponse"),
    ):
        operation = schema["paths"][path]["post"]
        assert operation["security"] == [{"SessionCookie": []}]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(request_model)
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(response_model)
        assert {"Origin", "X-CSRF-Token"} <= {item["name"] for item in operation["parameters"]}
    confirm = schema["paths"]["/api/v1/source/snapshot/preview/{preview_id}/confirm"]["post"]
    assert next(item for item in confirm["parameters"] if item["name"] == "If-Match")["required"] is True
    assert schema["components"]["schemas"]["SourcePreviewRequest"]["additionalProperties"] is False


@pytest.mark.parametrize("before_present", [False, True])
def test_b24_preview_distinguishes_absent_and_null_extras(lab_environment, before_present):
    client, app, headers = lab_environment
    absent = snapshot().model_dump(mode="json")
    present = snapshot().model_dump(mode="json")
    present["accounts"][0]["nullable_extra"] = None
    current, candidate = (present, absent) if before_present else (absent, present)
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(current))
    baseline = revision(app)

    response = client.post("/api/v1/source/snapshot/preview", json={
        "expected_revision": baseline, "candidate_snapshot": candidate,
    }, headers=headers)

    assert response.status_code == 200, response.text
    record, = response.json()["diff"]["record_changes"]
    assert record["kind"] == "changed"
    assert record["fields"] == [{
        "name": "nullable_extra", "before": None, "after": None,
        "before_present": before_present, "after_present": not before_present,
    }]
    assert revision(app) == baseline
    assert json.loads(app.state.coordinator.current.snapshot_json) == current


def test_b24_preview_race_discards_candidate_without_publication(lab_environment, monkeypatch):
    from app.domain import lab_source_diff
    client, app, headers = lab_environment
    original = lab_source_diff.diff_snapshots
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        app.state.coordinator.refresh()
        return result
    monkeypatch.setattr(lab_source_diff, "diff_snapshots", race)
    data = client.post("/api/v1/source/snapshot/preview", json={
        "expected_revision": revision(app), "candidate_snapshot": snapshot().model_dump(mode="json"),
    }, headers=headers)
    assert data.status_code == 409
    assert not getattr(app.state, "source_previews", {})


def test_b24_preview_is_bound_to_creating_session(lab_environment):
    client, app, headers = lab_environment
    current = revision(app)
    data = client.post("/api/v1/source/snapshot/preview", json={
        "expected_revision": current, "candidate_snapshot": snapshot().model_dump(mode="json"),
    }, headers=headers).json()
    login = client.post("/api/v1/auth/login", json={"login": "analyst", "password": PASSWORD},
                        headers={"Origin": ORIGIN})
    new_headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"],
                   "If-Match": f'"lab-{current["analysis_revision"]}-d-{current["data_revision"]}-s-{current["settings_revision"]}"'}
    response = client.post(f'/api/v1/source/snapshot/preview/{data["preview_id"]}/confirm', json={
        "expected_revision": current, "candidate_checksum": data["candidate_checksum"],
    }, headers=new_headers)
    assert response.status_code == 409
    assert revision(app) == current


def body(app, **values):
    return {"expected_revision": revision(app), "customer_id": 9001, "mode": "model",
            "settings_overrides": {}, **values}


def test_preview_f1_hand_calculation_and_no_write(lab_environment):
    client, app, headers = lab_environment
    baseline = app.state.coordinator.current
    storage = app.state.repository.load_state()
    metrics = render_metrics(baseline)
    response = client.post("/api/v1/lab/preview", json=body(app), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["baseline"]["risk_score"] == 66.33
    assert data["baseline"]["monetary_risk_cents"] == 830
    assert data["baseline"] == data["model_scenario"]
    assert data["effective_overrides"] == {}
    assert data["source_checksum"] == snapshot_checksum(baseline.snapshot_json)
    assert data["as_of"] == "2026-10-25T00:00:00Z"
    action = {"expected_revision": revision(app), "customer_id": 9001, "mode": "action",
              "action": {"top_up_cents": 800}}
    result = client.post("/api/v1/lab/preview", json=action, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["action_scenario"]["risk_score"] == 38.33
    assert result.json()["action_scenario"]["monetary_risk_cents"] == 550
    assert result.json()["hypothetical"] is True
    assert result.headers["cache-control"] == "no-store"
    assert app.state.coordinator.current is baseline
    assert app.state.repository.load_state() == storage
    assert render_metrics(app.state.coordinator.current) == metrics


@pytest.mark.parametrize("route", ["preview", "sensitivity", "worklist", "solve", "cases/export", "cases/replay", "renewal-calendar"])
def test_auth_and_csrf_cover_every_lab_route(lab_environment, route):
    client, app, headers = lab_environment
    path = f"/api/v1/lab/{route}"
    assert client.post(path, json={}).status_code == 403
    assert client.post(path, json={}, headers={**headers, "Origin": "https://foreign.invalid"}).status_code == 403
    client.cookies.clear()
    response = client.post(path, json={}, headers=headers)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("payload,status", [({}, 400), ({"expected_revision": {}}, 400),
    ({"settings_overrides": {"weights": {"alien": 12}}}, 422),
    ({"settings_overrides": {"weights": {"manual_renewal": True}}}, 422),
    ({"settings_overrides": {"horizon_days": 10}}, 422)])
def test_bad_inputs(lab_environment, payload, status):
    client, app, headers = lab_environment
    request = payload if status == 400 else body(app, **payload)
    response = client.post("/api/v1/lab/preview", json=request, headers=headers)
    assert response.status_code == status, response.text


@pytest.mark.parametrize("field,value", [("analysis_revision", "a-999"), ("data_revision", 999),
                                          ("settings_revision", 999)])
def test_stale_tuple(lab_environment, field, value):
    client, app, headers = lab_environment
    request = body(app)
    request["expected_revision"][field] = value
    response = client.post("/api/v1/lab/preview", json=request, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_evaluation"
    assert response.json()["error"]["current_revision"] == revision(app)


def test_actual_body_size_json_type_and_malformed_json(lab_environment):
    client, app, headers = lab_environment
    path = "/api/v1/lab/preview"
    assert client.post(path, content="{}", headers=headers).status_code == 415
    json_headers = {**headers, "Content-Type": "application/json"}
    assert client.post(path, content="{", headers=json_headers).status_code == 400
    chunks = [json.dumps(body(app)).encode(), b" " * (64 * 1024)]
    response = client.post(path, content=iter(chunks), headers={**json_headers, "Content-Length": "1"})
    assert response.status_code == 413


def test_changed_revision_during_calculation_returns_conflict(lab_environment, monkeypatch):
    from app.api import lab
    client, app, headers = lab_environment
    original = lab.preview_customer
    def racing_preview(*args, **kwargs):
        result = original(*args, **kwargs)
        changed = snapshot().model_dump(mode="json")
        changed["accounts"][0]["balance"] += 1
        app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(changed))
        return result
    monkeypatch.setattr(lab, "preview_customer", racing_preview)
    response = client.post("/api/v1/lab/preview", json=body(app), headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["current_revision"] == revision(app)


def test_worklist_solver_and_sensitivity_are_read_only(lab_environment):
    client, app, headers = lab_environment
    baseline = app.state.coordinator.current
    storage = app.state.repository.load_state()
    metrics = render_metrics(baseline)
    metadata = {"expected_revision": revision(app)}
    queue = client.post("/api/v1/lab/worklist", json={**metadata, "limit": 1}, headers=headers)
    assert queue.status_code == 200, queue.text
    assert queue.json()["rows"][0]["customer_id"] == 9001
    assert queue.json()["matched_count"] == queue.json()["returned_count"] == 1
    solved = client.post("/api/v1/lab/solve", json={**metadata, "customer_id": 9001,
        "goal": {"type": "cover_selected", "service_ids": [102]}, "max_top_up_cents": 1000}, headers=headers)
    assert solved.status_code == 200, solved.text
    assert solved.json()["status"] == "found"
    assert solved.json()["top_up_cents"] == 800
    assert solved.json()["certificate"]["one_cent_less_satisfies"] is False
    sensitivity = client.post("/api/v1/lab/sensitivity", json={**metadata, "customer_id": 9001}, headers=headers)
    assert sensitivity.status_code == 200, sensitivity.text
    assert sensitivity.json()["weights"]["denominator"] > 0
    assert app.state.coordinator.current is baseline
    assert app.state.repository.load_state() == storage
    assert render_metrics(app.state.coordinator.current) == metrics


@pytest.mark.parametrize("route,values", [
    ("worklist", {"limit": 51}),
    ("worklist", {"limit": True}),
    ("solve", {"customer_id": 9001, "goal": {"type": "score_below", "threshold": 35},
               "max_top_up_cents": 1.5}),
    ("solve", {"customer_id": 9001, "goal": {"type": "score_below", "threshold": 35},
               "max_top_up_cents": 1000, "allowed_enable_auto_prolong_ids": [101]}),
    ("sensitivity", {"customer_id": 9001, "top_k": 99}),
])
def test_strict_operation_limits(lab_environment, route, values):
    client, app, headers = lab_environment
    baseline = app.state.coordinator.current
    response = client.post(f"/api/v1/lab/{route}", json={"expected_revision": revision(app), **values},
                           headers=headers)
    assert response.status_code == 422, response.text
    assert app.state.coordinator.current is baseline


@pytest.mark.parametrize("route,values", [
    ("worklist", {"limit": 1}), ("sensitivity", {"customer_id": 9001}),
    ("solve", {"customer_id": 9001, "goal": {"type": "score_below", "threshold": 35},
               "max_top_up_cents": 1000}),
])
def test_all_operations_reject_stale_evaluation(lab_environment, route, values):
    client, app, headers = lab_environment
    old = revision(app)
    old["analysis_revision"] = "a-999"
    response = client.post(f"/api/v1/lab/{route}", json={"expected_revision": old, **values}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["current_revision"] == revision(app)


def test_missing_customer_and_unavailable_evaluation(lab_environment):
    client, app, headers = lab_environment
    response = client.post("/api/v1/lab/preview", json=body(app, customer_id=999), headers=headers)
    assert response.status_code == 404
    request = body(app)
    app.state.coordinator._published = None
    response = client.post("/api/v1/lab/preview", json=request, headers=headers)
    assert response.status_code == 503


@pytest.mark.parametrize("ids", [[9999], [102, 102], [101]])
def test_action_rejects_invalid_ids_without_disclosing_sources(lab_environment, ids):
    client, app, headers = lab_environment
    response = client.post("/api/v1/lab/preview", json={"expected_revision": revision(app),
        "customer_id": 9001, "mode": "action", "action": {"enable_auto_prolong_ids": ids}}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "service_not_allowed"
    assert "9999" not in response.text


def test_openapi_keeps_protected_routes_and_strict_request_schemas(lab_environment):
    client, app, _ = lab_environment
    schema = client.get("/api/v1/openapi.json").json()
    for route in ("preview", "sensitivity", "worklist", "solve"):
        operation = schema["paths"][f"/api/v1/lab/{route}"]["post"]
        assert operation["security"] == [{"SessionCookie": []}]
        assert operation["requestBody"]["required"] is True
        assert {"Origin", "X-CSRF-Token"} <= {p["name"] for p in operation["parameters"]}
    assert "/api/v1/customers" in schema["paths"]


def worklist_snapshot():
    """Real scoring fixtures: four candidates plus five excluded customers."""
    account = snapshot().model_dump(mode="json")["accounts"][0]
    service = snapshot().model_dump(mode="json")["services"][1]
    accounts, services = [], []
    # IDs 20/21/22 have tied dates; money then ID decides the order. ID 10 has
    # more monetary risk, but its later expiry takes precedence.
    specs = [(22, 1, 2500, 0, False, "active"), (10, 2, 8000, 0, False, "active"),
             (21, 1, 2500, 0, False, "active"), (20, 1, 3000, 0, False, "active"),
             (30, 1, 1000, 0, True, "blocked"),
             (31, 31, 1000, 0, False, "active"),
             (32, 30, 1000, 1000, False, "active"),
             (33, 2, 1000, 1000, True, "prolong_wait")]
    for customer_id, days, price, balance, auto, status in specs:
        accounts.append({**account, "id": customer_id, "balance": balance})
        services.append({**service, "id": customer_id, "ownerId": customer_id, "price": price,
                         "expiresAt": (AS_OF + timedelta(days=days)).isoformat(),
                         "autoProlong": auto, "status": status})
    accounts.append({**account, "id": 34})  # no services
    return SourceSnapshot.model_validate({"accounts": accounts, "services": services, "transactions": []})


@pytest.mark.parametrize("limit,expected", [(1, [20]), (5, [20, 21, 22, 10]), (50, [20, 21, 22, 10])])
def test_worklist_real_baseline_order_full_counts_and_exclusions(lab_environment, limit, expected):
    client, app, headers = lab_environment
    app.state.coordinator.replace_snapshot(worklist_snapshot())
    baseline = app.state.coordinator.current
    storage = app.state.repository.load_state()
    metrics = render_metrics(baseline)
    response = client.post("/api/v1/lab/worklist", json={"expected_revision": revision(app), "limit": limit}, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [row["customer_id"] for row in data["rows"]] == expected
    assert data["matched_count"] == 4 and data["returned_count"] == len(expected)
    assert data["excluded_reason_counts"] == {"no_eligible_service": 2, "no_in_horizon_service": 1,
                                              "low_risk": 1, "no_actionable_reason": 1}
    assert data["coverage"] == {"total_customers": 9, "scored_customers": 7, "unknown_customers": 2,
                                "monetary_known_customers": 7, "monetary_unknown_customers": 2}
    assert data["rows"][0]["risk_score"] == 79.33
    assert data["rows"][0]["monetary_risk_cents"] == 2380
    assert data["rows"][0]["funding_gap_cents"] == 3000
    assert data["rows"][0]["reason_codes"] == ["funding_gap", "manual_renewal"]
    assert data["source_checksum"] == snapshot_checksum(baseline.snapshot_json)
    assert {key: data[key] for key in revision(app)} == revision(app)
    assert data["as_of"] in {"2026-10-25T00:00:00Z", "2026-10-25T00:00:00+00:00"}
    assert response.headers["cache-control"] == "no-store"
    assert app.state.coordinator.current is baseline
    assert app.state.repository.load_state() == storage
    assert render_metrics(baseline) == metrics


def test_worklist_f1_affected_service_is_readable_and_uses_baseline_after_draft(lab_environment):
    client, app, headers = lab_environment
    draft = client.post("/api/v1/lab/preview", json=body(app, settings_overrides={"weights": {"manual_renewal": 50}}), headers=headers)
    assert draft.status_code == 200
    assert draft.json()["model_scenario"]["risk_score"] == 91.33
    data = client.post("/api/v1/lab/worklist", json={"expected_revision": revision(app)}, headers=headers).json()
    row, = data["rows"]
    assert (row["risk_score"], row["due_services_price_cents"], row["funding_gap_cents"]) == (66.33, 2000, 800)
    affected, = row["affected_services"]
    assert affected["service_id"] == 102
    assert affected["expires_at"].startswith("2026-11-04T00:00:00")
    assert affected["overdue"] is False and affected["status"] == "active"
    assert affected["price_cents"] == 1000 and affected["funding_gap_cents"] == 800
    assert affected["suggested_actions"] == ["review_funding_gap", "review_manual_renewal"]


def test_worklist_overdue_wait_warning_and_empty_result(lab_environment):
    client, app, headers = lab_environment
    changed = snapshot().model_dump(mode="json")
    changed["accounts"][0]["balance"] = 0
    changed["services"] = [{**changed["services"][1], "status": "prolong_wait", "expiresAt": "2026-10-24T00:00:00Z"}]
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(changed))
    result = client.post("/api/v1/lab/worklist", json={"expected_revision": revision(app)}, headers=headers)
    assert result.status_code == 200, result.text
    row, = result.json()["rows"]
    assert row["affected_services"][0]["overdue"] is True
    assert row["affected_services"][0]["warnings"] == ["prolong_wait"]
    assert row["warnings"] == ["prolong_wait"]
    app.state.coordinator.replace_snapshot(SourceSnapshot(accounts=[], services=[], transactions=[]))
    empty = client.post("/api/v1/lab/worklist", json={"expected_revision": revision(app)}, headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["rows"] == []
    assert empty.json()["matched_count"] == empty.json()["returned_count"] == 0
    assert empty.json()["coverage"]["total_customers"] == 0


@pytest.mark.parametrize("values", [{"limit": 0}, {"limit": "1"}, {"limit": 1.5}, {"limit": None},
                                     {"customer_id": 9001}, {"settings_overrides": {}},
                                     {"action": {"top_up_cents": 800}}])
def test_worklist_rejects_invalid_limits_and_hypothetical_inputs(lab_environment, values):
    client, app, headers = lab_environment
    baseline = app.state.coordinator.current
    response = client.post("/api/v1/lab/worklist", json={"expected_revision": revision(app), **values}, headers=headers)
    assert response.status_code == 422, response.text
    assert app.state.coordinator.current is baseline


def test_worklist_revision_change_during_build_discards_result(lab_environment, monkeypatch):
    from app.api import lab
    client, app, headers = lab_environment
    original = lab.build_worklist
    def race(evaluation, **kwargs):
        result = original(evaluation, **kwargs)
        app.state.coordinator.replace_snapshot(worklist_snapshot())
        return result
    monkeypatch.setattr(lab, "build_worklist", race)
    response = client.post("/api/v1/lab/worklist", json={"expected_revision": revision(app)}, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_evaluation"
    assert response.json()["error"]["current_revision"] == revision(app)
    assert "rows" not in response.json()


def test_worklist_openapi_documents_nested_results_and_bounds(lab_environment):
    client, app, headers = lab_environment
    document = client.get("/api/v1/openapi.json").json()
    schemas = document["components"]["schemas"]
    request = schemas["WorklistRequest"]
    assert request["additionalProperties"] is False
    assert request["properties"]["limit"]["minimum"] == 1
    assert request["properties"]["limit"]["maximum"] == 50
    response = schemas["WorklistResponse"]
    assert response["properties"]["rows"]["items"]["$ref"].endswith("/WorklistRowResult")
    assert schemas["WorklistServiceResult"]["properties"]["expires_at"]["format"] == "date-time"
    assert "overdue" in schemas["WorklistServiceResult"]["required"]
    checked_in = json.loads((Path(__file__).resolve().parents[2] / "openapi.json").read_text(encoding="utf-8"))
    assert checked_in == document


def solver_body(app, **values):
    return {"expected_revision": revision(app), "customer_id": 9001,
            "goal": {"type": "score_below", "threshold": 35}, "max_top_up_cents": 1000,
            "allowed_enable_auto_prolong_ids": [102], "max_switches": 1, **values}


def install_solver_l15(app, **service_values):
    document = snapshot().model_dump(mode="json")
    document["accounts"][0]["balance"] = 0
    document["services"] = [{**document["services"][1], "expiresAt": "2026-11-09T00:00:00Z",
                              **service_values}]
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))


def test_solver_l15_exact_cent_certificate_full_explanation_and_no_write(lab_environment):
    client, app, headers = lab_environment
    install_solver_l15(app)
    baseline = app.state.coordinator.current
    storage, metrics = app.state.repository.load_state(), render_metrics(baseline)
    expected_revision = revision(app)
    response = client.post("/api/v1/lab/solve", json=solver_body(app), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["status"], data["top_up_cents"], data["enable_auto_prolong_ids"]) == ("found", 286, [102])
    assert data["baseline"]["risk_score"] == 70
    assert data["result"]["risk_score"] == 34.99
    assert data["baseline"]["input"]["balance"] == 0
    assert data["result"]["input"]["balance"] == 286
    assert data["result"]["covered_cents"] == 286
    assert data["result"]["funding_gap_cents"] == 714
    assert data["result"]["services"][0]["input"]["autoProlong"] is True
    assert data["result"]["services"][0]["features"]
    assert data["effective_action"] == {"top_up_cents": 286, "enable_auto_prolong_ids": [102]}
    assert data["effective_goal"] == {"type": "score_below", "threshold": 35}
    assert data["effective_constraints"] == {"max_top_up_cents": 1000,
        "allowed_enable_auto_prolong_ids": [102], "max_switches": 1}
    assert data["hypothetical"] is True
    assert data["delta"]["label"] == "изменение модельного индекса"
    assert data["delta"]["risk_score"] == -35.01
    certificate = data["certificate"]
    assert certificate["complete"] is True
    assert certificate["ordering"] == ["top_up_cents", "switch_count", "service_ids"]
    assert certificate["one_cent_less_satisfies"] is False
    assert certificate["one_cent_less_result"]["risk_score"] == 35.03
    assert certificate["subset_minima"] == [
        {"enable_auto_prolong_ids": [], "top_up_cents": None},
        {"enable_auto_prolong_ids": [102], "top_up_cents": 286}]
    assert {key: data[key] for key in expected_revision} == expected_revision
    assert data["source_checksum"] == snapshot_checksum(baseline.snapshot_json)
    assert data["as_of"] == "2026-10-25T00:00:00Z"
    assert response.headers["cache-control"] == "no-store"
    assert app.state.coordinator.current is baseline
    assert app.state.repository.load_state() == storage
    assert render_metrics(baseline) == metrics


def test_solver_coverage_consumes_full_basket_and_one_cent_less(lab_environment):
    client, app, headers = lab_environment
    response = client.post("/api/v1/lab/solve", json=solver_body(app,
        goal={"type": "cover_selected", "service_ids": [102]}), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["status"], data["top_up_cents"], data["enable_auto_prolong_ids"]) == ("found", 800, [])
    assert data["result"]["spendable_cents"] == data["result"]["due_services_price_cents"] == 2000
    assert data["certificate"]["one_cent_less_result"]["funding_gap_cents"] == 1


def test_b21_solver_adaptive_full_source_budget_is_not_unreachable(lab_environment, monkeypatch):
    from app.domain import lab_solver
    client, app, headers = lab_environment
    # Keep genuine scoring and adaptive source-row accounting; lower only the
    # internal work budget so exhaustion is deterministic with a small fixture.
    monkeypatch.setattr(lab_solver, "MAX_SERVICE_EVALUATIONS", 2)
    baseline = app.state.coordinator.current
    storage, metrics = app.state.repository.load_state(), render_metrics(baseline)
    response = client.post("/api/v1/lab/solve", json=solver_body(app), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "search_limit"
    assert data["evaluations"] == 1
    assert data["certificate"]["complete"] is False
    assert data["result"] is data["top_up_cents"] is data["effective_action"] is None
    assert app.state.coordinator.current is baseline
    assert app.state.repository.load_state() == storage
    assert render_metrics(baseline) == metrics


def test_solver_already_satisfied_unreachable_and_budget_guard(lab_environment, monkeypatch):
    from app.api import lab
    client, app, headers = lab_environment
    install_solver_l15(app)
    already = client.post("/api/v1/lab/solve", json=solver_body(app,
        goal={"type": "score_below", "threshold": 71}), headers=headers).json()
    assert (already["status"], already["top_up_cents"], already["enable_auto_prolong_ids"]) == ("already_satisfied", 0, [])
    assert already["baseline"] == already["result"]
    assert already["evaluations"] == 0 and already["certificate"]["complete"] is True
    unreachable = client.post("/api/v1/lab/solve", json=solver_body(app, max_top_up_cents=285), headers=headers).json()
    assert unreachable["status"] == "unreachable" and unreachable["certificate"]["complete"] is True
    assert unreachable["result"] is unreachable["top_up_cents"] is unreachable["delta"] is None
    original = lab.solve
    monkeypatch.setattr(lab, "solve", lambda *args, **kwargs: original(*args, **kwargs, evaluation_limit=1))
    limited = client.post("/api/v1/lab/solve", json=solver_body(app), headers=headers).json()
    assert limited["status"] == "search_limit" and limited["certificate"]["complete"] is False
    assert limited["evaluations"] == 1
    assert limited["result"] is limited["top_up_cents"] is limited["effective_action"] is None


@pytest.mark.parametrize("values", [
    {"max_top_up_cents": True}, {"max_top_up_cents": -1}, {"max_top_up_cents": 10000001},
    {"max_top_up_cents": "12"}, {"max_switches": True}, {"max_switches": 2},
    {"allowed_enable_auto_prolong_ids": [102, 102]},
    {"allowed_enable_auto_prolong_ids": list(range(1, 10))},
    {"goal": {"type": "cover_selected", "service_ids": [102, 102]}},
    {"goal": {"type": "cover_selected", "service_ids": []}},
    {"goal": {"type": "cover_selected", "service_ids": [102], "threshold": 35}},
    {"goal": {"type": "score_below", "threshold": True}},
    {"goal": {"type": "score_below", "threshold": 0}},
    {"goal": {"type": "score_below", "threshold": "35"}},
    {"settings_overrides": {"weights": {"manual_renewal": 0}}},
    {"action": {"top_up_cents": 1000}}, {"evaluation_limit": 1},
])
def test_solver_strict_goals_constraints_and_independence(lab_environment, values):
    client, app, headers = lab_environment
    before = app.state.coordinator.current
    response = client.post("/api/v1/lab/solve", json=solver_body(app, **values), headers=headers)
    assert response.status_code == 422, response.text
    assert app.state.coordinator.current is before


@pytest.mark.parametrize("service_values", [{"status": "suspended"}, {"status": "prolong_wait"},
    {"status": "blocked"}, {"status": "deleted"}, {"autoProlong": True},
    {"expiresAt": "2026-11-25T00:00:00Z"}, {"id": 777}])
def test_solver_disallowed_switches_do_not_disclose_sources(lab_environment, service_values):
    client, app, headers = lab_environment
    install_solver_l15(app, **service_values)
    response = client.post("/api/v1/lab/solve", json=solver_body(app), headers=headers)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "service_not_allowed"
    assert "777" not in response.text


def test_solver_foreign_service_service_cap_and_money_overflow(lab_environment):
    client, app, headers = lab_environment
    document = snapshot().model_dump(mode="json")
    document["accounts"].append({**document["accounts"][0], "id": 9002})
    document["services"].append({**document["services"][1], "id": 777, "ownerId": 9002})
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))
    for values in [{"allowed_enable_auto_prolong_ids": [777]},
                   {"goal": {"type": "cover_selected", "service_ids": [777]}}]:
        response = client.post("/api/v1/lab/solve", json=solver_body(app, **values), headers=headers)
        assert response.status_code == 422 and response.json()["error"]["code"] == "service_not_allowed"
        assert "777" not in response.text
    document = snapshot().model_dump(mode="json")
    document["services"] = [{**document["services"][1], "id": key} for key in range(1, 102)]
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))
    response = client.post("/api/v1/lab/solve", json=solver_body(app,
        allowed_enable_auto_prolong_ids=[], max_switches=0), headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "solver_service_limit"
    document = snapshot().model_dump(mode="json")
    document["accounts"][0]["balance"] = 2**53 - 1
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))
    response = client.post("/api/v1/lab/solve", json=solver_body(app), headers=headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "money_out_of_range"


def test_solver_race_unsupported_registry_and_missing_customer(lab_environment, monkeypatch):
    from app.api import lab
    client, app, headers = lab_environment
    original = lab.solve
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        install_solver_l15(app)
        return result
    monkeypatch.setattr(lab, "solve", race)
    response = client.post("/api/v1/lab/solve", json=solver_body(app), headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["current_revision"] == revision(app)
    assert "result" not in response.json()
    def unsupported(*args, **kwargs):
        raise ValueError("solver_unsupported_registry")
    monkeypatch.setattr(lab, "solve", unsupported)
    response = client.post("/api/v1/lab/solve", json=solver_body(app), headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "solver_unsupported_registry"
    response = client.post("/api/v1/lab/solve", json=solver_body(app, customer_id=999), headers=headers)
    assert response.status_code == 404


def test_solver_matches_exhaustive_small_oracle(lab_environment):
    from itertools import combinations
    from app.domain.scoring import score_customer
    client, app, headers = lab_environment
    document = snapshot().model_dump(mode="json")
    document["accounts"][0]["balance"] = 0
    document["services"] = [{**row, "price": 3, "autoProlong": False} for row in document["services"]]
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))
    evaluation = app.state.coordinator.current
    owner = evaluation.snapshot.accounts[0]
    rows = evaluation.snapshot.services
    candidates = []
    for cents in range(7):
        for count in range(3):
            for switches in combinations((101, 102), count):
                scored = score_customer(owner.model_copy(update={"balance": cents}),
                    [row.model_copy(update={"autoProlong": row.id in switches}) for row in rows],
                    settings=evaluation.settings, as_of=evaluation.evaluated_at)
                if scored.risk_score < 35:
                    candidates.append((cents, count, switches))
    response = client.post("/api/v1/lab/solve", json=solver_body(app,
        max_top_up_cents=6, allowed_enable_auto_prolong_ids=[102, 101], max_switches=2), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data["top_up_cents"], len(data["enable_auto_prolong_ids"]), tuple(data["enable_auto_prolong_ids"])) == min(candidates)


def test_solver_openapi_documents_goals_typed_certificate_and_bounds(lab_environment):
    client, app, _ = lab_environment
    schemas = client.get("/api/v1/openapi.json").json()["components"]["schemas"]
    request = schemas["SolveRequest"]
    assert request["additionalProperties"] is False
    assert request["properties"]["goal"]["discriminator"]["propertyName"] == "type"
    assert request["properties"]["max_top_up_cents"]["maximum"] == 10000000
    assert request["properties"]["allowed_enable_auto_prolong_ids"]["maxItems"] == 8
    response = schemas["SolveResponse"]["properties"]
    assert response["baseline"]["$ref"].endswith("/CustomerPreview")
    assert response["certificate"]["$ref"].endswith("/SolverCertificate")
    assert response["evaluations"]["maximum"] == 6144
    assert schemas["SolverCertificate"]["properties"]["subset_minima"]["maxItems"] == 256


# B25: the transport retains untouched case text and never publishes a sandbox.
def case_export_body(app, operation=None, **values):
    return {"expected_revision": revision(app), "customer_id": 9001,
            "operation": operation or {"type": "action", "action": {"top_up_cents": 800}}, **values}


def export_api_case(client, app, headers, operation=None):
    response = client.post("/api/v1/lab/cases/export",
                           json=case_export_body(app, operation), headers=headers)
    assert response.status_code == 200, response.text
    return response


def replay_api_case(client, app, headers, document):
    text = document if isinstance(document, str) else json.dumps(document)
    wrapper = json.dumps({"expected_revision": revision(app), "case_document": text}, ensure_ascii=True)
    return client.post("/api/v1/lab/cases/replay", content=wrapper.encode("ascii"),
                       headers={**headers, "Content-Type": "application/json"})


@pytest.mark.parametrize("operation,score", [
    ({"type": "model", "settings_overrides": {"weights": {"manual_renewal": 50}}}, "91.33"),
    ({"type": "action", "action": {"top_up_cents": 800}}, "38.33"),
    ({"type": "solve", "goal": {"type": "cover_selected", "service_ids": [102]},
      "max_top_up_cents": 1000}, "38.33"),
])
def test_case_export_attachment_privacy_all_operations_and_no_write(lab_environment, operation, score):
    from app.domain.lab_case import case_checksum
    client, app, headers = lab_environment
    document = snapshot().model_dump(mode="json")
    document["private_extra"] = "snapshot-secret"
    document["accounts"][0]["password"] = "account-secret"
    document["accounts"].append({**document["accounts"][0], "id": 777, "email": "other@example.invalid"})
    document["services"][0]["token"] = "service-secret"
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))
    baseline = app.state.coordinator.current
    state, metrics = app.state.repository.load_state(), render_metrics(baseline)
    response = export_api_case(client, app, headers, operation)
    assert response.headers["content-disposition"] == 'attachment; filename="decision-case-v1.json"'
    assert response.headers["cache-control"] == "no-store"
    case = response.json()
    assert case["checksum"] == case_checksum(case)
    assert case["expected"]["scenario"]["risk_score"] == score
    assert len(response.content) <= 65536
    for value in ("fixture@example", "Fixture", "other@example", "snapshot-secret", "account-secret", "service-secret", "9001"):
        assert value not in response.text
    owner, = case["snapshot"]["accounts"]
    assert (owner["id"], owner["email"], owner["withdrawBalance"], owner["totalReplenished"]) == (1, "case@example.invalid", 0, 0)
    assert [row["id"] for row in case["snapshot"]["services"]] == [1, 2]
    assert case["snapshot"]["transactions"] == []
    replay = replay_api_case(client, app, headers, response.text)
    assert replay.status_code == 200, replay.text
    assert replay.json()["matched"] is replay.json()["sandbox"] is True
    assert replay.json()["calculated"] == case["expected"]
    assert replay.headers["cache-control"] == "no-store"
    assert app.state.coordinator.current is baseline
    assert app.state.repository.load_state() == state
    assert render_metrics(baseline) == metrics


def test_case_l18_cent_certificate_survives_settings_change_and_real_restart(lab_environment):
    from app.contracts import SettingsPatch
    client, app, headers = lab_environment
    install_solver_l15(app)
    operation = {key: value for key, value in solver_body(app).items()
                 if key not in {"expected_revision", "customer_id"}}
    operation["type"] = "solve"
    case = export_api_case(client, app, headers, operation).json()
    app.state.coordinator.patch_settings(SettingsPatch(horizon_days=20),
        expected_settings_revision=app.state.coordinator.current.settings_revision)
    # A new application/coordinator loads the same SQLite state, while the case
    # still carries its original fixed settings, source and evaluation time.
    restarted = create_app(app.state.config)
    with TestClient(restarted) as fresh:
        login = fresh.post("/api/v1/auth/login", json={"login": "analyst", "password": PASSWORD},
                           headers={"Origin": ORIGIN})
        new_headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        before = restarted.state.repository.load_state()
        baseline = restarted.state.coordinator.current
        metrics = render_metrics(baseline)
        replay = replay_api_case(fresh, restarted, new_headers, case)
        assert replay.status_code == 200, replay.text
        data = replay.json()
        assert data["case_as_of"] == "2026-10-25T00:00:00Z"
        assert data["case_horizon_days"] == 30 and data["horizon_days"] == 20
        calculated = data["calculated"]
        assert calculated == case["expected"]
        assert calculated["top_up_cents"] == 286
        assert calculated["scenario"]["risk_score"] == "34.99"
        assert calculated["one_cent_less_result"]["risk_score"] == "35.03"
        assert calculated["one_cent_less_satisfies"] is False
        assert restarted.state.repository.load_state() == before
        assert restarted.state.coordinator.current is baseline
        assert render_metrics(baseline) == metrics


@pytest.mark.parametrize("field,value,code,resign", [
    ("checksum", "0" * 64, "case_checksum_mismatch", False),
    ("case_version", 2, "unsupported_case_version", False),
    ("engine_version", "rules-v2", "unsupported_case_engine", False),
    ("feature_versions", {}, "unsupported_case_features", False),
    ("unexpected", "private-source-value", "invalid_case_schema", True),
    ("expected", {}, "invalid_case_schema", True),
])
def test_case_replay_distinct_safe_errors_without_write(lab_environment, field, value, code, resign):
    from app.domain.lab_case import case_checksum
    client, app, headers = lab_environment
    case = export_api_case(client, app, headers).json()
    case[field] = value
    if resign:
        case["checksum"] = case_checksum(case)
    before, baseline = app.state.repository.load_state(), app.state.coordinator.current
    response = replay_api_case(client, app, headers, case)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert "private-source-value" not in response.text
    assert app.state.repository.load_state() == before
    assert app.state.coordinator.current is baseline


def test_case_replay_recomputes_expected_and_rejects_duplicate_keys(lab_environment):
    from app.domain.lab_case import case_checksum
    client, app, headers = lab_environment
    exported = export_api_case(client, app, headers)
    case = exported.json()
    case["expected"]["scenario"]["funding_gap_cents"] += 1
    case["checksum"] = case_checksum(case)
    mismatch = replay_api_case(client, app, headers, case)
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "case_result_mismatch"
    duplicate = '{"case_version":1,' + exported.text[1:]
    rejected = replay_api_case(client, app, headers, duplicate)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_case_schema"
    for document in ('not json', '[]', '{"case_version":true}', '\ud800'):
        invalid = replay_api_case(client, app, headers, document)
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["error"]["code"] == "invalid_case_schema"


@pytest.mark.parametrize("route", ["cases/export", "cases/replay"])
def test_cases_tuple_guards_and_streaming_request_cap(lab_environment, route):
    client, app, headers = lab_environment
    document = export_api_case(client, app, headers).text
    values = (case_export_body(app) if route.endswith("export") else
              {"expected_revision": revision(app), "case_document": document})
    old = dict(values["expected_revision"])
    app.state.coordinator.replace_snapshot(SourceSnapshot(accounts=[], services=[], transactions=[]))
    stale = client.post('/api/v1/lab/'+route, json={**values, "expected_revision": old}, headers=headers)
    assert stale.status_code == 409
    assert stale.json()["error"]["current_revision"] == revision(app)
    assert client.post('/api/v1/lab/'+route, json={}, headers=headers).status_code == 400
    oversized = client.post('/api/v1/lab/'+route, content=iter([b'{' + b' ' * 32768, b' ' * 32768 + b'}']),
                            headers={**headers, "Content-Type": "application/json"})
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request_too_large"


@pytest.mark.parametrize("route,function", [("export", "export_case"), ("replay", "replay_case")])
def test_cases_drop_results_when_revision_changes_during_calculation(lab_environment, monkeypatch, route, function):
    from app.api import lab
    client, app, headers = lab_environment
    document = export_api_case(client, app, headers).text
    original = getattr(lab, function)
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        app.state.coordinator.replace_snapshot(SourceSnapshot(accounts=[], services=[], transactions=[]))
        return result
    monkeypatch.setattr(lab, function, race)
    values = case_export_body(app) if route == "export" else {"expected_revision": revision(app), "case_document": document}
    response = client.post('/api/v1/lab/cases/'+route, json=values, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["current_revision"] == revision(app)
    assert "calculated" not in response.json()
    assert "content-disposition" not in response.headers


def test_case_export_missing_customer_bounds_and_replay_wrapper_schema(lab_environment):
    client, app, headers = lab_environment
    missing = client.post('/api/v1/lab/cases/export', json=case_export_body(app, customer_id=777), headers=headers)
    assert missing.status_code == 404
    document = snapshot().model_dump(mode="json")
    document["services"] = [{**document["services"][0], "id": key} for key in range(1, 102)]
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))
    over = client.post('/api/v1/lab/cases/export', json=case_export_body(app), headers=headers)
    assert over.status_code == 422 and over.json()["error"]["code"] == "case_service_limit"
    for value in ({}, [], 123, None):
        response = client.post('/api/v1/lab/cases/replay', json={"expected_revision": revision(app), "case_document": value}, headers=headers)
        assert response.status_code == 422
    wrong_operation = client.post('/api/v1/lab/cases/export',
        json=case_export_body(app, {"type": "action", "action": {"top_up_cents": True}}), headers=headers)
    assert wrong_operation.status_code == 422


def test_case_openapi_wrapper_operations_attachment_and_checked_in_schema(lab_environment):
    client, app, _ = lab_environment
    document = client.get('/api/v1/openapi.json').json()
    schemas = document["components"]["schemas"]
    assert schemas["CaseReplayRequest"]["additionalProperties"] is False
    field = schemas["CaseReplayRequest"]["properties"]["case_document"]
    assert field["type"] == "string" and field["maxLength"] == 65536
    assert "Untouched UTF-8" in field["description"]
    assert schemas["CaseExportRequest"]["properties"]["operation"]["discriminator"]["propertyName"] == "type"
    for name in ('export', 'replay'):
        operation = document["paths"]['/api/v1/lab/cases/'+name]["post"]
        assert operation["security"] == [{"SessionCookie": []}]
        assert {'Origin', 'X-CSRF-Token'} <= {item["name"] for item in operation["parameters"]}
        assert {"409", "413", "415", "422"} <= operation["responses"].keys()
    assert "Content-Disposition" in document["paths"]['/api/v1/lab/cases/export']["post"]["responses"]["200"]["headers"]
    checked_in = json.loads((Path(__file__).resolve().parents[2] / "openapi.json").read_text(encoding="utf-8"))
    assert checked_in == document



def test_case_transport_preserves_service_and_switch_caps(lab_environment):
    from app.domain.lab_case import case_checksum
    client, app, headers = lab_environment
    case = export_api_case(client, app, headers).json()
    template = case["snapshot"]["services"][1]
    case["snapshot"]["services"] = [{**template, "id": key, "name": f"case-service-{key}"} for key in range(1, 102)]
    case["checksum"] = case_checksum(case)
    before = app.state.repository.load_state()
    response = replay_api_case(client, app, headers, case)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "case_service_limit"
    assert app.state.repository.load_state() == before
    source = snapshot().model_dump(mode="json")
    source["services"] = [{**source["services"][1], "id": key} for key in range(1, 10)]
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(source))
    response = client.post('/api/v1/lab/cases/export', json=case_export_body(app,
        {"type": "action", "action": {"enable_auto_prolong_ids": list(range(1, 10))}}), headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "case_switch_limit"


def calendar_body(app, **values):
    return {"expected_revision": revision(app), "customer_id": 9001, **values}


def test_renewal_calendar_shared_balance_provenance_and_unchanged_baseline(lab_environment):
    client, app, headers = lab_environment
    baseline = app.state.coordinator.current
    state, metrics = app.state.repository.load_state(), render_metrics(baseline)
    original = client.post("/api/v1/lab/preview", json=body(app), headers=headers).json()["baseline"]
    response = client.post("/api/v1/lab/renewal-calendar",
                           json=calendar_body(app, horizon_days=60), headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["baseline"] == original
    assert data["hypothetical"] is True
    assert data["source_checksum"] == snapshot_checksum(baseline.snapshot_json)
    assert {name: data[name] for name in revision(app)} == revision(app)
    assert data["as_of"] == "2026-10-25T00:00:00Z"
    assert data["horizon_days"] == 30
    scenario = data["scenario"]
    assert scenario["horizon_days"] == 60
    assert scenario["horizon_end"] == "2026-12-24T00:00:00Z"
    assert [e["service_id"] for e in scenario["events"]] == [101, 102, 101, 102]
    assert [e["covered_cents"] for e in scenario["events"]] == [1000, 200, 0, 0]
    assert scenario["spendable_cents"] == scenario["total_covered_cents"] == 1200
    assert scenario["funding_gap_cents"] == 2800
    assert scenario["first_shortfall_at"] == "2026-11-04T00:00:00Z"
    assert response.headers["cache-control"] == "no-store"
    assert app.state.repository.load_state() == state
    assert app.state.coordinator.current is baseline
    assert render_metrics(baseline) == metrics


def test_renewal_calendar_explicit_assumptions_cap_and_default_horizon(lab_environment):
    client, app, headers = lab_environment
    response = client.post("/api/v1/lab/renewal-calendar", json=calendar_body(app,
        max_events=3, period_assumptions=[{"service_id": 101, "payment_term": "hour"}]), headers=headers)
    assert response.status_code == 200, response.text
    scenario = response.json()["scenario"]
    assert scenario["horizon_days"] == app.state.coordinator.current.horizon_days
    assert scenario["period_assumptions"] == [{"service_id": 101, "payment_term": "hour"}]
    assert scenario["max_events"] == len(scenario["events"]) == 3
    assert scenario["truncated"] is True
    assert [e["occurrence_index"] for e in scenario["events"]] == [0, 1, 2]


@pytest.mark.parametrize("values", [
    {"horizon_days": 0}, {"horizon_days": 91}, {"horizon_days": True}, {"horizon_days": None},
    {"max_events": 0}, {"max_events": 1001}, {"max_events": True}, {"max_events": 1.5},
    {"as_of": "2026-10-25T00:00:00Z"}, {"period_assumptions": [{"service_id": 101, "payment_term": "day"}]},
    {"period_assumptions": [{"service_id": 101, "payment_term": "hour"}] * 2},
    {"period_assumptions": [{"service_id": 777, "payment_term": "hour"}]},
])
def test_renewal_calendar_strict_inputs(lab_environment, values):
    client, app, headers = lab_environment
    state = app.state.repository.load_state()
    response = client.post("/api/v1/lab/renewal-calendar", json=calendar_body(app, **values), headers=headers)
    assert response.status_code == 422, response.text
    assert app.state.repository.load_state() == state


def test_renewal_calendar_missing_customer_stale_race_and_transport_limits(lab_environment, monkeypatch):
    from app.api import lab
    client, app, headers = lab_environment
    path = "/api/v1/lab/renewal-calendar"
    missing = client.post(path, json=calendar_body(app, customer_id=777), headers=headers)
    assert missing.status_code == 404
    assert client.post(path, json={}, headers=headers).status_code == 400
    assert client.post(path, content="{}", headers={**headers, "Content-Type": "text/plain"}).status_code == 415
    oversized = client.post(path, content=b"{" + b" " * 65536 + b"}",
                            headers={**headers, "Content-Type": "application/json"})
    assert oversized.status_code == 413
    old = calendar_body(app)
    app.state.coordinator.refresh()
    assert client.post(path, json=old, headers=headers).status_code == 409
    original = lab.build_renewal_calendar
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        app.state.coordinator.refresh()
        return result
    monkeypatch.setattr(lab, "build_renewal_calendar", race)
    response = client.post(path, json=calendar_body(app), headers=headers)
    assert response.status_code == 409
    assert "scenario" not in response.json()


def test_renewal_calendar_unlocked_bonus_and_typed_openapi(lab_environment):
    client, app, headers = lab_environment
    document = snapshot().model_dump(mode="json")
    document["accounts"][0]["bonusState"] = "unlocked"
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(document))
    response = client.post("/api/v1/lab/renewal-calendar", json=calendar_body(app), headers=headers)
    assert response.status_code == 200, response.text
    scenario = response.json()["scenario"]
    assert scenario["spendable_cents"] == scenario["total_covered_cents"] == 1700
    schema = client.get("/api/v1/openapi.json").json()
    operation = schema["paths"]["/api/v1/lab/renewal-calendar"]["post"]
    assert operation["security"] == [{"SessionCookie": []}]
    assert {"Origin", "X-CSRF-Token"} <= {item["name"] for item in operation["parameters"]}
    models = schema["components"]["schemas"]
    assert models["RenewalCalendarRequest"]["additionalProperties"] is False
    assert models["RenewalCalendarRequest"]["properties"]["max_events"]["maximum"] == 1000
    assert models["RenewalCalendarScenario"]["properties"]["events"]["maxItems"] == 1000
