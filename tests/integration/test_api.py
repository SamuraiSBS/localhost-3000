from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from pwdlib import PasswordHash

from app.config import DEFAULT_SOURCE_PATH, RuntimeConfig
from app.main import create_app

LOGIN = "analyst"
PASSWORD = "b05-test-password-only"
PASSWORD_HASH = PasswordHash.recommended().hash(PASSWORD)
METRICS_TOKEN = "b05-test-metrics-token"
ORIGIN = "http://testserver"
AS_OF = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
AS_OF_TEXT = AS_OF.isoformat().replace("+00:00", "Z")
SCRATCH_ROOT = Path(__file__).resolve().parent / ".b05-api-test-scratch"


def _account(
    customer_id: int,
    email: str,
    *,
    balance: int,
    total_replenished: int,
    **extra: object,
) -> dict[str, object]:
    return {
        "id": customer_id,
        "email": email,
        "balance": balance,
        "withdrawBalance": 0,
        "totalReplenished": total_replenished,
        "bonusBalance": 0,
        "bonusState": "locked",
        **extra,
    }


def _service(
    service_id: int,
    owner_id: int,
    *,
    auto_prolong: bool = False,
    status: str = "active",
    expires_at: str = AS_OF_TEXT,
    price: int = 1000,
    **extra: object,
) -> dict[str, object]:
    return {
        "id": service_id,
        "name": f"Service {service_id}",
        "ownerId": owner_id,
        "paymentTerm": "month",
        "autoProlong": auto_prolong,
        "createdAt": "2026-01-01T00:00:00Z",
        "expiresAt": expires_at,
        "status": status,
        "price": price,
        **extra,
    }


def api_snapshot() -> dict[str, object]:
    return {
        "accounts": [
            _account(2, "shared@example.test", balance=1000, total_replenished=200,
                     externalReference="preserve-π"),
            _account(10, "shared@example.test", balance=1000, total_replenished=200),
            _account(20, "zeta@example.test", balance=0, total_replenished=200),
            _account(100, "user@example.test", balance=900, total_replenished=500),
        ],
        "services": [
            _service(200, 2, upstreamTag={"source": "fixture"}),
            _service(210, 10),
            _service(220, 20, auto_prolong=False),
        ],
        "transactions": [
            {
                "id": 900,
                "amount": 1000,
                "status": "performed",
                "performedAt": "2026-02-01T00:00:00Z",
                "createdAt": "2026-02-01T00:00:00Z",
                "type": "prolong",
                "serviceId": 200,
            },
            {
                "id": 901,
                "amount": 500,
                "status": "not_performed",
                "performedAt": None,
                "createdAt": "2026-03-01T00:00:00Z",
                "type": "order",
                "serviceId": 200,
            },
        ],
        "sourceLabel": "api-test",
    }


def _unsafe_headers(csrf_token: str, *, etag: str | None = None) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf_token}
    if etag is not None:
        headers["If-Match"] = etag
    return headers


def _put_snapshot(client: TestClient, snapshot: dict[str, object], csrf_token: str):
    return client.put(
        "/api/v1/source/snapshot",
        content=json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
        headers={**_unsafe_headers(csrf_token), "Content-Type": "application/json"},
    )


def _reverse_object_keys(value: object) -> object:
    if isinstance(value, dict):
        return {key: _reverse_object_keys(item) for key, item in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reverse_object_keys(item) for item in value]
    return value


def _source_revision(app):
    current = app.state.coordinator.current
    return {key: getattr(current, key) for key in
            ("analysis_revision", "data_revision", "settings_revision")}


def _source_preview(client, app, csrf, candidate=None):
    expected = _source_revision(app)
    response = client.post("/api/v1/source/snapshot/preview", json={
        "expected_revision": expected,
        "candidate_snapshot": candidate if candidate is not None else api_snapshot(),
    }, headers=_unsafe_headers(csrf))
    assert response.status_code == 200, response.text
    data = response.json()
    etag = f'"lab-{expected["analysis_revision"]}-d-{expected["data_revision"]}-s-{expected["settings_revision"]}"'
    return data, {"expected_revision": expected, "candidate_checksum": data["candidate_checksum"]}, etag


def _confirm_source(client, csrf, data, body, etag):
    return client.post(f'/api/v1/source/snapshot/preview/{data["preview_id"]}/confirm',
                       json=body, headers=_unsafe_headers(csrf, etag=etag))


def test_b24_preview_no_write_confirm_and_reuse(api_environment):
    from app.metrics import render_metrics
    start_app, *_ = api_environment
    client, app, csrf = start_app()
    baseline = app.state.coordinator.current
    state = app.state.repository.load_state()
    metrics = render_metrics(baseline)
    data, body, etag = _source_preview(client, app, csrf)
    assert app.state.coordinator.current is baseline
    assert app.state.repository.load_state() == state
    assert render_metrics(baseline) == metrics
    assert data["diff"]["as_of"] == data["as_of"]
    assert data["diff"]["record_changes"]
    response = _confirm_source(client, csrf, data, body, etag)
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    assert response.json()["data_revision"] == state.data_revision + 1
    assert response.json()["settings_revision"] == state.settings_revision
    assert response.json()["preview_as_of"] == data["as_of"]
    activated = app.state.repository.load_state()
    assert _confirm_source(client, csrf, data, body, etag).status_code == 409
    assert app.state.repository.load_state() == activated
    noop, noop_body, noop_etag = _source_preview(client, app, csrf)
    assert _confirm_source(client, csrf, noop, noop_body, noop_etag).json()["changed"] is False
    assert app.state.repository.load_state() == activated


@pytest.mark.parametrize("failure", ["checksum", "etag", "expiry", "stale", "bytes", "missing_etag"])
def test_b24_confirmation_rejections_do_not_write(api_environment, monkeypatch, failure):
    from app.api import source
    start_app, *_ = api_environment
    client, app, csrf = start_app()
    data, body, etag = _source_preview(client, app, csrf)
    if failure == "checksum":
        body["candidate_checksum"] = "0" * 64
    elif failure == "etag":
        etag = '"wrong"'
    elif failure == "expiry":
        monkeypatch.setattr(source, "monotonic", lambda: float("inf"))
    elif failure == "stale":
        app.state.coordinator.refresh()
    elif failure == "bytes":
        from dataclasses import replace
        entry = app.state.source_previews[data["preview_id"]]
        app.state.source_previews[data["preview_id"]] = replace(entry, document=b"{}")
    elif failure == "missing_etag":
        etag = None
    state = app.state.repository.load_state()
    published = app.state.coordinator.current
    response = _confirm_source(client, csrf, data, body, etag)
    assert response.status_code == (400 if failure == "missing_etag" else 409), response.text
    assert app.state.repository.load_state() == state
    assert app.state.coordinator.current is published


def test_b24_preview_stream_limit_before_decode(api_environment):
    start_app, *_ = api_environment
    client, app, csrf = start_app(max_snapshot_bytes=100_000)
    state = app.state.repository.load_state()
    headers = {**_unsafe_headers(csrf), "Content-Type": "application/json", "Content-Length": "1"}
    response = client.post("/api/v1/source/snapshot/preview", content=iter([b"{" * 100_001]), headers=headers)
    assert response.status_code == 413
    headers["Content-Length"] = "100001"
    assert client.post("/api/v1/source/snapshot/preview", content=b"{}", headers=headers).status_code == 413
    assert app.state.repository.load_state() == state


@pytest.mark.parametrize("no_op", [False, True])
def test_b24_confirm_compare_and_swap_race_is_atomic(api_environment, monkeypatch, no_op):
    from app.contracts import SourceSnapshot
    start_app, *_ = api_environment
    client, app, csrf = start_app()
    candidate = app.state.coordinator.current.snapshot.model_dump(mode="json") if no_op else None
    data, body, etag = _source_preview(client, app, csrf, candidate)
    repository = app.state.repository
    original = repository.commit_snapshot
    race_state = []
    def raced(document, checksum, *, expected, received_at):
        # Simulate another process winning after coordinator's loaded-state
        # check and calculation, immediately before the SQLite CAS transaction.
        other = SourceSnapshot(accounts=[], services=[], transactions=[])
        from app.storage import snapshot_checksum
        original(other.canonical_json(), snapshot_checksum(other.canonical_json()),
                 expected=expected, received_at=received_at)
        race_state.append(repository.load_state())
        return original(document, checksum, expected=expected, received_at=received_at)
    monkeypatch.setattr(repository, "commit_snapshot", raced)
    baseline = app.state.coordinator.current
    response = _confirm_source(client, csrf, data, body, etag)
    assert response.status_code == 409, response.text
    assert repository.load_state() == race_state[0]
    assert response.json()["error"]["current_revision"]["data_revision"] == race_state[0].data_revision
    assert app.state.coordinator.current is baseline


def test_b24_source_preview_auth_csrf_and_validation(api_environment):
    start_app, *_ = api_environment
    client, app, csrf = start_app()
    path = "/api/v1/source/snapshot/preview"
    state = app.state.repository.load_state()
    payload = {"expected_revision": _source_revision(app), "candidate_snapshot": api_snapshot()}
    assert client.post(path, json=payload).status_code == 403
    headers = _unsafe_headers(csrf)
    assert client.post(path, json=payload, headers={**headers, "Origin": "https://other.invalid"}).status_code == 403
    assert client.post(path, json={}, headers=headers).status_code == 400
    assert client.post(path, content="{", headers={**headers, "Content-Type": "application/json"}).status_code == 400
    assert client.post(path, content="{}", headers=headers).status_code == 415
    payload["candidate_snapshot"]["services"][0]["ownerId"] = 999999
    assert client.post(path, json=payload, headers=headers).status_code == 422
    client.cookies.clear()
    assert client.post(path, json=payload, headers=headers).status_code == 401
    assert app.state.repository.load_state() == state


def test_b24_simultaneous_confirms_apply_once(api_environment):
    start_app, *_ = api_environment
    client, app, csrf = start_app()
    data, body, etag = _source_preview(client, app, csrf)
    before = app.state.repository.load_state()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: _confirm_source(client, csrf, data, body, etag).status_code, range(2)))
    assert sorted(outcomes) == [200, 409]
    assert app.state.repository.load_state().data_revision == before.data_revision + 1


@pytest.fixture
def api_environment():
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    case_dir = SCRATCH_ROOT / uuid4().hex
    case_dir.mkdir()
    data_dir = case_dir / "state"
    live_clients: list[TestClient] = []

    def start_app(
        *,
        source_path: Path = DEFAULT_SOURCE_PATH,
        max_snapshot_bytes: int = 100_000,
    ):
        config = RuntimeConfig(
            source_path=source_path,
            data_dir=data_dir,
            login=LOGIN,
            password_hash=PASSWORD_HASH,
            metrics_token=METRICS_TOKEN,
            public_origin=ORIGIN,
            max_snapshot_bytes=max_snapshot_bytes,
        )
        app = create_app(config)
        client = TestClient(app)
        client.__enter__()
        live_clients.append(client)
        app.state.coordinator._clock = lambda: AS_OF
        login_response = client.post(
            "/api/v1/auth/login",
            json={"login": LOGIN, "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert login_response.status_code == 200
        return client, app, login_response.json()["csrf_token"]

    def close_app(client: TestClient) -> None:
        if client in live_clients:
            live_clients.remove(client)
            client.close()

    try:
        yield start_app, close_app, data_dir, case_dir
    finally:
        for client in tuple(live_clients):
            close_app(client)
        # Cleanup is limited to the unique directory created by this fixture.
        database_path = data_dir / "app.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{database_path}{suffix}")
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                pass
        for path in (data_dir, case_dir):
            try:
                path.rmdir()
            except (FileNotFoundError, PermissionError, OSError):
                pass


@pytest.mark.parametrize(
    ("sort_by", "sort_order", "expected"),
    [
        ("id", "asc", ["2", "10", "20", "100"]),
        ("id", "desc", ["100", "20", "10", "2"]),
        ("email", "asc", ["2", "10", "100", "20"]),
        ("email", "desc", ["20", "100", "2", "10"]),
        ("balance", "asc", ["20", "100", "2", "10"]),
        ("balance", "desc", ["2", "10", "100", "20"]),
        ("totalReplenished", "asc", ["2", "10", "20", "100"]),
        ("totalReplenished", "desc", ["100", "2", "10", "20"]),
    ],
)
def test_i04_all_required_sorts_are_deterministic_and_keep_id_ties_ascending(
    api_environment, sort_by, sort_order, expected
):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200

    response = client.get(
        "/api/v1/customers",
        params={"sort_by": sort_by, "sort_order": sort_order},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == expected


def test_i04_score_sort_keeps_unknown_last_in_both_directions(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200

    for order, expected in (
        ("asc", ["2", "10", "20", "100"]),
        ("desc", ["20", "2", "10", "100"]),
    ):
        response = client.get(
            "/api/v1/customers",
            params={"sort_by": "risk_score", "sort_order": order},
        )
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == expected
        assert response.json()["items"][-1]["risk_level"] == "unknown"


def test_i05_filter_paging_unknown_and_numeric_id_search(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200

    page = client.get(
        "/api/v1/customers",
        params={"sort_by": "id", "sort_order": "asc", "limit": 2, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert [item["id"] for item in page.json()["items"]] == ["10", "20"]

    unknown = client.get("/api/v1/customers", params={"risk_level": "unknown"})
    assert unknown.json()["total"] == 1
    assert [item["id"] for item in unknown.json()["items"]] == ["100"]

    exact_id = client.get("/api/v1/customers", params={"q": "10"})
    assert [item["id"] for item in exact_id.json()["items"]] == ["10"]
    email_substring = client.get("/api/v1/customers", params={"q": "SHARED"})
    assert [item["id"] for item in email_substring.json()["items"]] == ["2", "10"]
    empty = client.get("/api/v1/customers", params={"q": "%"})
    assert empty.status_code == 200
    assert empty.json()["total"] == 0
    assert empty.json()["items"] == []

    missing = client.get("/api/v1/customers/999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "customer_not_found"


def test_i05_detail_contains_source_features_evidence_and_related_transactions(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200

    response = client.get("/api/v1/customers/2")
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == "2"
    assert detail["email"] == "shared@example.test"
    assert detail["source_extra"]["externalReference"] == "preserve-π"
    assert detail["balance_cents"] == 1000
    assert detail["spendable_cents"] == 1000
    assert detail["risk_score"] == 45
    assert detail["risk_level"] == "medium"
    assert len(detail["services"]) == 1
    service = detail["services"][0]
    assert service["id"] == "200"
    assert service["ownerId"] == "2"
    assert service["source_extra"]["upstreamTag"] == {"source": "fixture"}
    assert service["eligible"] is True
    assert service["in_horizon"] is True
    assert service["raw_score"] == 45
    assert service["score_clamped"] is False
    assert len(service["features"]) == 4
    assert all(feature["available"] and feature["evidence"] for feature in service["features"])
    assert all(feature["explanation"] for feature in service["features"])
    assert {transaction["id"] for transaction in detail["transactions"]} == {"900", "901"}
    assert {transaction["status"] for transaction in detail["transactions"]} == {
        "performed", "not_performed"
    }


def test_i05_account_extras_cannot_override_evaluation_metadata_or_score(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    snapshot = api_snapshot()
    conflicts = {
        "analysis_revision": "forged",
        "data_revision": 987,
        "settings_revision": 876,
        "model_version": "fake",
        "evaluated_at": "fake",
        "horizon_days": 999,
        "currency": "USD",
        "risk_score": -999,
        "risk_level": "forged",
        "monetary_risk_cents": -1,
        "spendable_cents": -1,
        "top_reasons": "forged",
        "services": "forged",
    }
    snapshot["accounts"][0].update(conflicts)
    assert _put_snapshot(client, snapshot, csrf).status_code == 200

    listing = client.get("/api/v1/customers", params={"sort_by": "id", "sort_order": "asc"}).json()
    summary = client.get("/api/v1/analytics/summary").json()
    detail = client.get("/api/v1/customers/2").json()
    metadata_keys = (
        "analysis_revision", "data_revision", "settings_revision", "model_version",
        "evaluated_at", "horizon_days", "currency",
    )
    evaluation_metadata = {key: listing[key] for key in metadata_keys}
    assert {key: detail[key] for key in metadata_keys} == evaluation_metadata
    assert {key: summary[key] for key in metadata_keys} == evaluation_metadata
    assert detail["id"] == "2"
    assert detail["risk_score"] == 45
    assert detail["risk_level"] == "medium"
    assert detail["monetary_risk_cents"] == 450
    assert detail["spendable_cents"] == 1000
    assert isinstance(detail["top_reasons"], list)
    assert isinstance(detail["services"], list)
    assert detail["source_extra"] == {
        "externalReference": "preserve-π",
        **conflicts,
    }


def test_i10_malformed_json_is_400_and_leaves_snapshot_and_settings_unchanged(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    before_summary = client.get("/api/v1/analytics/summary").json()
    before_list = client.get("/api/v1/customers", params={"limit": 100}).json()
    before_settings = client.get("/api/v1/model/settings")
    malformed_json = b'{"broken":'
    json_headers = {
        **_unsafe_headers(csrf),
        "Content-Type": "application/json",
    }

    bad_snapshot = client.put(
        "/api/v1/source/snapshot",
        content=malformed_json,
        headers=json_headers,
    )
    bad_patch = client.patch(
        "/api/v1/model/settings",
        content=malformed_json,
        headers={**json_headers, "If-Match": before_settings.headers["etag"]},
    )

    for response in (bad_snapshot, bad_patch):
        assert response.status_code == 400, response.text
        assert response.json() == {
            "error": {
                "code": "bad_request",
                "message": "Request body must contain valid JSON.",
            }
        }

    after_summary = client.get("/api/v1/analytics/summary").json()
    after_list = client.get("/api/v1/customers", params={"limit": 100}).json()
    after_settings = client.get("/api/v1/model/settings")
    assert after_summary == before_summary
    assert after_list == before_list
    assert after_settings.status_code == 200
    assert after_settings.headers["etag"] == before_settings.headers["etag"]
    assert after_settings.json() == before_settings.json()


def test_i11_summary_is_global_and_metadata_matches_list_and_detail(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200

    summary = client.get("/api/v1/analytics/summary").json()
    listing = client.get("/api/v1/customers", params={"risk_level": "high"}).json()
    detail = client.get("/api/v1/customers/2").json()
    metadata_keys = (
        "analysis_revision", "data_revision", "settings_revision", "model_version",
        "evaluated_at", "horizon_days", "currency",
    )
    summary_metadata = {key: summary[key] for key in metadata_keys}
    assert {key: listing[key] for key in metadata_keys} == summary_metadata
    assert {key: detail[key] for key in metadata_keys} == summary_metadata
    assert summary["customers_total"] == 4
    assert summary["customers_evaluated"] == 3
    assert summary["risk_counts"] == {"low": 0, "medium": 2, "high": 1, "unknown": 1}
    assert sum(summary["risk_counts"].values()) == summary["customers_total"]
    assert summary["monetary_risk_cents"] == 1700
    assert summary["monetary_coverage"] == {"evaluated": 3, "unknown": 1}
    assert summary["due_services_price_cents"] == 3000
    assert summary["funding_gap_cents"] == 1000
    assert listing["total"] == 1  # Filtered table does not narrow global summary.


def test_i06_settings_patch_noop_validation_conflict_and_etag(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200

    before = client.get("/api/v1/model/settings")
    assert before.status_code == 200
    assert before.headers["etag"] == '"settings-1"'
    before_payload = before.json()
    assert set(before_payload["weights"]) == {
        "renewal_proximity", "manual_renewal", "funding_gap", "payment_wait"
    }
    assert {item["id"] for item in before_payload["features"]} == set(before_payload["weights"])
    before_analysis_revision = client.get("/api/v1/analytics/summary").json()["analysis_revision"]

    patched = client.patch(
        "/api/v1/model/settings",
        json={"weights": {"manual_renewal": 50}},
        headers=_unsafe_headers(csrf, etag='"settings-1"'),
    )
    assert patched.status_code == 200
    assert patched.headers["etag"] == '"settings-2"'
    assert patched.json()["settings_revision"] == 2
    assert patched.json()["weights"]["manual_renewal"] == 50
    assert patched.json()["weights"]["renewal_proximity"] == 20
    assert patched.json()["weights"]["funding_gap"] == 35
    assert patched.json()["analysis_revision"] != before_analysis_revision
    assert client.get("/api/v1/customers/2").json()["risk_score"] == 70

    no_op = client.patch(
        "/api/v1/model/settings",
        json={"weights": {"manual_renewal": 50}},
        headers=_unsafe_headers(csrf, etag='"settings-2"'),
    )
    assert no_op.status_code == 200
    assert no_op.headers["etag"] == '"settings-2"'
    assert no_op.json()["analysis_revision"] == patched.json()["analysis_revision"]

    stale = client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 40},
        headers=_unsafe_headers(csrf, etag='"settings-1"'),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["current_revision"] == 2

    missing = client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 40},
        headers=_unsafe_headers(csrf),
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "if_match_required"
    malformed = client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 40},
        headers=_unsafe_headers(csrf, etag="W/\"settings-2\""),
    )
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "if_match_invalid"
    unsupported_encoding = client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 40},
        headers=_unsafe_headers(csrf, etag='"settings-2-br"'),
    )
    assert unsupported_encoding.status_code == 400
    assert unsupported_encoding.json()["error"]["code"] == "if_match_invalid"

    for invalid in (
        {"weights": {"unknown_feature": 10}},
        {"weights": {"manual_renewal": True}},
        {"weights": {"renewal_proximity": 0, "manual_renewal": 0,
                      "funding_gap": 0, "payment_wait": 0}},
        {"thresholds": {"high": 30}},
        {"horizon_days": 91},
    ):
        rejected = client.patch(
            "/api/v1/model/settings",
            json=invalid,
            headers=_unsafe_headers(csrf, etag='"settings-2"'),
        )
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["details"]
        assert rejected.json()["error"]["details"][0]["path"]


@pytest.mark.parametrize("encoding", ("zstd", "gzip"))
def test_i06_settings_patch_accepts_caddy_encoded_etag_variants(api_environment, encoding):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200

    first = client.patch(
        "/api/v1/model/settings",
        json={"weights": {"manual_renewal": 50}},
        headers=_unsafe_headers(csrf, etag=f'"settings-1-{encoding}"'),
    )
    assert first.status_code == 200, first.text
    assert first.headers["etag"] == '"settings-2"'
    assert first.json()["settings_revision"] == 2
    assert first.json()["weights"]["manual_renewal"] == 50
    assert client.get("/api/v1/customers/2").json()["risk_score"] == 70

    second = client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 31},
        headers=_unsafe_headers(csrf, etag=f'"settings-2-{encoding}"'),
    )
    assert second.status_code == 200, second.text
    assert second.json()["settings_revision"] == 3

    stale = client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 40},
        headers=_unsafe_headers(csrf, etag=f'"settings-2-{encoding}"'),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["current_revision"] == 3


def test_i07_settings_and_replaced_snapshot_survive_app_restart_without_seed_import(api_environment):
    start_app, close_app, _, case_dir = api_environment
    client, _, csrf = start_app()
    custom = api_snapshot()
    custom["accounts"] = [
        _account(42, "persisted-only@example.test", balance=1000, total_replenished=700)
    ]
    custom["services"] = [_service(420, 42)]
    custom["transactions"] = []
    assert _put_snapshot(client, custom, csrf).status_code == 200
    patched = client.patch(
        "/api/v1/model/settings",
        json={"horizon_days": 45, "weights": {"manual_renewal": 50}},
        headers=_unsafe_headers(csrf, etag='"settings-1"'),
    )
    assert patched.status_code == 200
    close_app(client)

    missing_seed = case_dir / "must-not-be-read.json"
    restarted, _, _ = start_app(source_path=missing_seed)
    settings = restarted.get("/api/v1/model/settings")
    customers = restarted.get("/api/v1/customers", params={"sort_by": "id"})
    assert settings.status_code == 200
    assert settings.json()["settings_revision"] == 2
    assert settings.json()["horizon_days"] == 45
    assert settings.json()["weights"]["manual_renewal"] == 50
    assert settings.headers["etag"] == '"settings-2"'
    assert [item["email"] for item in customers.json()["items"]] == [
        "persisted-only@example.test"
    ]
    assert customers.json()["data_revision"] == 2


def test_i08_full_snapshot_replace_removes_disappeared_customer(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200
    assert client.get("/api/v1/customers/2").status_code == 200

    replacement = {
        "accounts": [_account(10, "only-current@example.test", balance=500, total_replenished=900)],
        "services": [_service(210, 10)],
        "transactions": [],
    }
    response = _put_snapshot(client, replacement, csrf)
    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["counts"] == {"accounts": 1, "services": 1, "transactions": 0}
    assert client.get("/api/v1/customers/2").status_code == 404
    current = client.get("/api/v1/customers", params={"sort_by": "id"})
    assert [item["id"] for item in current.json()["items"]] == ["10"]


def test_i09_snapshot_noop_preserves_extras_and_invalid_replace_is_atomic(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    original = api_snapshot()
    first = _put_snapshot(client, original, csrf)
    assert first.status_code == 200
    assert first.json()["changed"] is True
    assert first.json()["counts"] == {"accounts": 4, "services": 3, "transactions": 2}
    before = client.get("/api/v1/analytics/summary").json()
    detail = client.get("/api/v1/customers/2").json()
    assert detail["source_extra"]["externalReference"] == "preserve-π"
    assert detail["services"][0]["source_extra"]["upstreamTag"] == {"source": "fixture"}

    reordered = _put_snapshot(client, _reverse_object_keys(original), csrf)
    assert reordered.status_code == 200
    assert reordered.json()["changed"] is False
    assert reordered.json()["data_revision"] == first.json()["data_revision"]
    assert reordered.json()["checksum"] == first.json()["checksum"]

    invalid = api_snapshot()
    invalid["accounts"] = list(invalid["accounts"]) + [invalid["accounts"][0]]
    rejected = _put_snapshot(client, invalid, csrf)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["details"]
    after = client.get("/api/v1/analytics/summary").json()
    assert after["analysis_revision"] == before["analysis_revision"]
    assert after["data_revision"] == before["data_revision"]
    assert after["customers_total"] == before["customers_total"]


def test_i09_snapshot_limit_checks_content_length_and_streamed_body(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app(max_snapshot_bytes=100_000)
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200
    before = client.get("/api/v1/analytics/summary").json()
    headers = {**_unsafe_headers(csrf), "Content-Type": "application/json"}

    declared_large = client.build_request(
        "PUT", "/api/v1/source/snapshot", content=b"{}", headers=headers
    )
    declared_large.headers["content-length"] = "100001"
    declared_rejected = client.send(declared_large)
    assert declared_rejected.status_code == 413

    oversized = json.dumps(
        {**api_snapshot(), "largeUnrelatedExtra": "x" * 100_100}, ensure_ascii=False
    ).encode("utf-8")
    streamed = client.build_request(
        "PUT", "/api/v1/source/snapshot", content=oversized, headers=headers
    )
    streamed.headers.pop("content-length", None)
    streamed_rejected = client.send(streamed)
    assert streamed_rejected.status_code == 413

    after = client.get("/api/v1/analytics/summary").json()
    assert after["analysis_revision"] == before["analysis_revision"]
    assert after["data_revision"] == before["data_revision"]


def test_i03_patch_and_put_require_origin_and_csrf(api_environment):
    start_app, _, _, _ = api_environment
    client, _, csrf = start_app()
    settings_etag = '"settings-1"'
    for method, path, body in (
        ("patch", "/api/v1/model/settings", {"horizon_days": 45}),
        ("put", "/api/v1/source/snapshot", api_snapshot()),
    ):
        missing_origin = getattr(client, method)(
            path,
            json=body,
            headers={"If-Match": settings_etag} if method == "patch" else {},
        )
        assert missing_origin.status_code == 403
        assert missing_origin.json()["error"]["code"] == "invalid_origin"

        missing_token = getattr(client, method)(
            path,
            json=body,
            headers={"Origin": ORIGIN, **({"If-Match": settings_etag} if method == "patch" else {})},
        )
        assert missing_token.status_code == 403
        assert missing_token.json()["error"]["code"] == "csrf_failed"

    assert csrf


def test_i10_concurrent_patch_and_snapshot_publish_only_latest_consistent_view(api_environment):
    start_app, _, _, _ = api_environment
    client, app, csrf = start_app()
    assert _put_snapshot(client, api_snapshot(), csrf).status_code == 200
    coordinator = app.state.coordinator
    calculator = coordinator._calculator
    patch_calculation_started = Event()
    release_patch_calculation = Event()

    def delayed_calculator(snapshot, settings, **kwargs):
        if settings.weights["manual_renewal"] == Decimal(50):
            patch_calculation_started.set()
            if not release_patch_calculation.wait(timeout=10):
                raise TimeoutError("test did not release delayed settings calculation")
        return calculator(snapshot, settings, **kwargs)

    coordinator._calculator = delayed_calculator
    patch_headers = _unsafe_headers(csrf, etag='"settings-1"')
    newest_snapshot = {
        "accounts": [_account(42, "newest@example.test", balance=1000, total_replenished=5)],
        "services": [_service(420, 42)],
        "transactions": [],
    }
    put_started = Event()

    def send_newest_snapshot():
        put_started.set()
        return _put_snapshot(client, newest_snapshot, csrf)

    with ThreadPoolExecutor(max_workers=2) as pool:
        patch_future = pool.submit(
            client.patch,
            "/api/v1/model/settings",
            json={"weights": {"manual_renewal": 50}},
            headers=patch_headers,
        )
        assert patch_calculation_started.wait(timeout=5)
        put_future = pool.submit(send_newest_snapshot)
        try:
            assert put_started.wait(timeout=5)
            assert not put_future.done()
        finally:
            release_patch_calculation.set()
        patch_response = patch_future.result(timeout=10)
        put_response = put_future.result(timeout=10)

    assert patch_response.status_code == 200
    assert put_response.status_code == 200
    assert patch_response.json()["settings_revision"] == 2
    assert put_response.json()["data_revision"] == 3
    summary = client.get("/api/v1/analytics/summary").json()
    listing = client.get("/api/v1/customers", params={"sort_by": "id"}).json()
    detail = client.get("/api/v1/customers/42").json()
    metadata = (
        "analysis_revision", "data_revision", "settings_revision", "model_version",
        "evaluated_at", "horizon_days", "currency",
    )
    expected = {key: summary[key] for key in metadata}
    assert {key: listing[key] for key in metadata} == expected
    assert {key: detail[key] for key in metadata} == expected
    assert summary["data_revision"] == 3
    assert summary["settings_revision"] == 2
    assert [item["email"] for item in listing["items"]] == ["newest@example.test"]
