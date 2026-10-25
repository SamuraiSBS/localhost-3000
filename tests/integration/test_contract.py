"""B08 HTTP/contract checks; these do not establish browser rendering proof."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client import CONTENT_TYPE_LATEST
from pwdlib import PasswordHash
import pytest

from app.config import RuntimeConfig
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://testserver"
TOKEN = "b08-fixture-collector-token"
PASSWORD = "b08-fixture-login-password"
PASSWORD_HASH = PasswordHash.recommended().hash(PASSWORD)
OPERATIONS = {
    ("/api/v1/auth/login", "post"), ("/api/v1/auth/me", "get"),
    ("/api/v1/auth/logout", "post"), ("/api/v1/customers", "get"),
    ("/api/v1/customers/{customer_id}", "get"), ("/api/v1/analytics/summary", "get"),
    ("/api/v1/model/settings", "get"), ("/api/v1/model/settings", "patch"),
    ("/api/v1/source/snapshot", "put"), ("/metrics", "get"), ("/api/v1/docs", "get"),
}


@pytest.fixture
def environment():
    scratch = ROOT / ".state" / ("b08-" + uuid4().hex)
    scratch.mkdir(parents=True)
    try:
        app = create_app(RuntimeConfig(data_dir=scratch, login="analyst",
            password_hash=PASSWORD_HASH, metrics_token=TOKEN, public_origin=ORIGIN))
        with TestClient(app) as client:
            app.state.coordinator._clock = lambda: datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
            yield app, client
    finally:
        for name in ("app.sqlite3", "app.sqlite3-wal", "app.sqlite3-shm"):
            (scratch / name).unlink(missing_ok=True)
        scratch.rmdir()


def login(client):
    response = client.post("/api/v1/auth/login", json={"login": "analyst", "password": PASSWORD},
                           headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def scrape(client):
    response = client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN},
                          follow_redirects=False)
    assert response.status_code == 200
    assert "location" not in response.headers
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    samples = {}
    for family in text_string_to_metric_families(response.text):
        assert family.type == "gauge"
        for sample in family.samples:
            assert set(sample.labels) <= {"risk_level"}
            if sample.labels:
                assert sample.labels["risk_level"] in {"low", "medium", "high", "unknown"}
            samples[(sample.name, sample.labels.get("risk_level"))] = sample.value
    return samples


def assert_consistent(app, client):
    evaluation = app.state.coordinator.current
    summary = client.get("/api/v1/analytics/summary").json()
    samples = scrape(client)
    counts = {key: samples[("churn_customers", key)] for key in ("low", "medium", "high", "unknown")}
    assert counts == summary["risk_counts"]
    assert sum(counts.values()) == summary["customers_total"] == len(evaluation.customers)
    for metric, field in (("monetary_risk_cents", "monetary_risk_cents"),
                          ("due_services_price_cents", "due_services_price_cents"),
                          ("funding_gap_cents", "funding_gap_cents"),
                          ("evaluated_customers", "customers_evaluated"),
                          ("data_revision", "data_revision"), ("settings_revision", "settings_revision")):
        assert samples[("churn_" + metric, None)] == summary[field]
    assert samples[("churn_analysis_revision", None)] == int(summary["analysis_revision"][2:])
    assert samples[("churn_evaluation_timestamp_seconds", None)] == evaluation.evaluated_at.timestamp()
    assert samples[("churn_risk_score_mean", None)] == (summary["mean_risk_score"] or 0)
    assert summary["monetary_risk_cents"] == sum(row.monetary_risk_cents or 0 for row in evaluation.customers)
    return samples


def test_bearer_session_boundaries_and_local_docs(environment):
    app, client = environment
    for path in ("/api/v1/customers", "/api/v1/model/settings", "/api/v1/docs",
                 "/api/v1/openapi.json", "/api/v1/docs/assets/swagger-ui-bundle.js"):
        for headers in ({}, {"Authorization": "Bearer " + TOKEN}):
            response = client.get(path, headers=headers, follow_redirects=False)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics?token=" + TOKEN).status_code == 401
    scrape(client)  # collector works without any customer session
    headers = login(client)
    assert client.get("/metrics").status_code == 401  # cookie alone is not collector access
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, follow_redirects=False).status_code == 404
    docs = client.get("/api/v1/docs")
    assert docs.status_code == 200
    assert 'validatorUrl: null' in docs.text
    assert 'queryConfigEnabled: false' in docs.text
    assert 'connect-src \'self\'' in docs.headers["content-security-policy"]
    assert "https://" not in docs.text and "http://" not in docs.text
    for filename in ("swagger-ui-bundle.js", "swagger-ui.css"):
        response = client.get("/api/v1/docs/assets/" + filename)
        assert response.status_code == 200
        assert response.content == (ROOT / "web/vendor/swagger" / filename).read_bytes()
    assert client.get("/api/v1/openapi.json").json() == app.openapi()
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    for path in ("/api/v1/docs", "/api/v1/openapi.json"):
        assert client.get(path).status_code == 401
    scrape(client)  # logout cannot revoke the independent collector credential


def test_metrics_real_counts_updates_empty_and_single_evaluation(environment):
    app, client = environment
    headers = login(client)
    assert_consistent(app, client)
    account = {"id": 1, "email": "fixture@example.test", "balance": 1000,
               "withdrawBalance": 0, "totalReplenished": 2000, "bonusBalance": 0, "bonusState": "locked"}
    service = {"id": 1, "ownerId": 1, "name": "Fixture", "paymentTerm": "month",
               "autoProlong": False, "createdAt": "2026-01-01T00:00:00Z",
               "expiresAt": "2026-10-23T12:00:00Z", "status": "active", "price": 1000}
    payload = {"accounts": [account], "services": [service], "transactions": []}
    changed = client.put("/api/v1/source/snapshot", json=payload, headers=headers)
    assert changed.status_code == 200
    assert changed.json()["counts"] == {"accounts": 1, "services": 1, "transactions": 0}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert changed.json()["checksum"] == hashlib.sha256(canonical.encode()).hexdigest()
    samples = assert_consistent(app, client)
    assert samples[("churn_customers", "medium")] == 1
    assert samples[("churn_monetary_risk_cents", None)] == 450
    assert samples[("churn_risk_score_mean", None)] == 45
    settings = client.get("/api/v1/model/settings")
    changed = client.patch("/api/v1/model/settings", json={"weights": {"manual_renewal": 50}},
        headers={**headers, "If-Match": settings.headers["etag"]})
    assert changed.status_code == 200
    assert assert_consistent(app, client)[("churn_customers", "high")] == 1
    for accounts in ([account], []):
        response = client.put("/api/v1/source/snapshot",
            json={"accounts": accounts, "services": [], "transactions": []}, headers=headers)
        assert response.status_code == 200
        samples = assert_consistent(app, client)
        assert samples[("churn_customers", "high")] == 0
        assert samples[("churn_customers", "medium")] == 0
        assert samples[("churn_risk_score_mean", None)] == 0
        assert samples[("churn_evaluated_customers", None)] == 0
    # The route must read the published pointer once, even if a writer publishes
    # a new evaluation immediately afterwards.
    current = app.state.coordinator.current
    class OneRead:
        reads = 0
        @property
        def current(self):
            self.reads += 1
            assert self.reads == 1
            return current
    app.state.coordinator = OneRead()
    scrape(client)


def test_openapi_export_operations_security_and_references(environment):
    app, client = environment
    schema = app.openapi()
    assert json.loads((ROOT / "openapi.json").read_text(encoding="utf-8")) == schema
    assert len(OPERATIONS) == 11
    for path, method in OPERATIONS:
        operation = schema["paths"][path][method]
        expected = [] if path.endswith("/login") else [{"MetricsBearer": []}] if path == "/metrics" else [{"SessionCookie": []}]
        assert operation["security"] == expected
        assert "401" in operation["responses"]
        if method in {"post", "put", "patch"} and not path.endswith("/login"):
            assert any(p["name"] == "X-CSRF-Token" and p["required"] for p in operation["parameters"])
    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                current = schema
                for segment in value["$ref"].removeprefix("#/").split("/"):
                    current = current[segment]
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(schema)
    assert schema["components"]["schemas"]["CustomerListItem"]["properties"]["risk_score"]["anyOf"][1] == {"type": "null"}
    assert schema["paths"]["/api/v1/source/snapshot"]["put"]["requestBody"]["required"]


def test_real_service_allocations_match_documented_nullability(environment):
    app, client = environment
    headers = login(client)
    account = {"id": 1, "email": "allocation-contract@example.test", "balance": 1000,
               "withdrawBalance": 0, "totalReplenished": 2000, "bonusBalance": 0,
               "bonusState": "locked"}
    base_service = {"ownerId": 1, "name": "Allocation fixture", "paymentTerm": "month",
                    "autoProlong": False, "createdAt": "2026-01-01T00:00:00Z", "price": 1000}
    services = [
        {**base_service, "id": 1, "status": "active", "expiresAt": "2026-10-23T12:00:00Z"},
        {**base_service, "id": 2, "status": "active", "expiresAt": "2027-01-01T00:00:00Z"},
        {**base_service, "id": 3, "status": "blocked", "expiresAt": "2026-10-23T12:00:00Z"},
        {**base_service, "id": 4, "status": "deleted", "expiresAt": "2026-10-23T12:00:00Z"},
    ]
    assert client.put("/api/v1/source/snapshot", headers=headers,
        json={"accounts": [account], "services": services, "transactions": []}).status_code == 200
    response = client.get("/api/v1/customers/1")
    assert response.status_code == 200
    actual = {row["id"]: row for row in response.json()["services"]}
    definitions = app.openapi()["components"]["schemas"]
    documented = definitions["ServiceResponse"]["properties"]
    exported = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    assert documented == exported["components"]["schemas"]["ServiceResponse"]["properties"]
    assert actual["1"]["in_horizon"] is True
    assert actual["2"]["eligible"] is True and actual["2"]["in_horizon"] is False
    assert actual["3"]["eligible"] is False and actual["4"]["eligible"] is False
    for field in ("covered_cents", "funding_gap_cents"):
        branches = documented[field]["anyOf"]
        assert {branch["type"] for branch in branches} == {"integer", "null"}
        money = next(branch for branch in branches if branch["type"] == "integer")
        for service_id, row in actual.items():
            value = row[field]
            if service_id == "1":
                assert type(value) is int
                assert money["minimum"] <= value <= money["maximum"]
            else:
                assert value is None  # actual ordinary GET, not a schema-only assertion
        # Whole-customer totals still have a known integer result.
        if field == "funding_gap_cents":
            assert definitions["CustomerDetail"]["properties"][field]["type"] == "integer"
            assert type(response.json()[field]) is int


def test_upstream_asset_manifest():
    vendor = ROOT / "web/vendor/swagger"
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    assert "v5.33.1" in readme and "Apache-2.0" in readme
    for name in ("swagger-ui-bundle.js", "swagger-ui.css", "LICENSE"):
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() in readme
    license_text = (vendor / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
