"""CSV selection, text safety, provenance and protected read-only boundaries."""

import csv
from io import StringIO
from decimal import Decimal

import pytest

from test_lab_api import lab_environment, revision, snapshot
from app.contracts import SourceSnapshot
from app.export_csv import text_cell
from app.metrics import render_metrics

URL = "/api/v1/exports/customers.csv"


def download(environment, **values):
    client, app, headers = environment
    return client.post(URL, headers=headers, json={"scope": "filtered", "expected_revision": revision(app), **values})


def rows(response):
    assert response.status_code == 200, response.text
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"), newline="")))


def test_full_filter_order_unknown_and_no_writes(lab_environment):
    client, app, _ = lab_environment
    source = snapshot().model_dump(mode="json")
    source["accounts"] = [dict(source["accounts"][0], id=i, email=f"клиент-{i}@example.invalid") for i in range(1, 122)]
    source["services"] = [dict(source["services"][0], id=i, ownerId=i) for i in range(1, 121)]
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(source))
    evaluation = app.state.coordinator.current
    metrics = render_metrics(app.state.coordinator.current)
    response = download(lab_environment)
    records = rows(response)
    expected = client.get("/api/v1/customers?limit=100").json()
    assert len(records) == 121
    assert [row["customer_id"] for row in records[:100]] == [row["id"] for row in expected["items"]]
    assert records[-1]["risk_score"] == "unknown"
    assert records[-1]["risk_level"] == "unknown"
    assert records[-1]["email"] == "клиент-121@example.invalid"
    assert all(row["row_count"] == "121" and row["analysis_revision"] == evaluation.analysis_revision for row in records)
    assert response.headers["X-Export-Row-Count"] == "121"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Export-As-Of"] == records[0]["as_of"]
    assert app.state.coordinator.current is evaluation
    assert render_metrics(app.state.coordinator.current) == metrics
    for filters in ({"q": "клиент-12"}, {"q": "121"}, {"risk_level": "unknown"},
                    {"sort_by": "email", "sort_order": "asc"}, {"sort_by": "balance", "sort_order": "desc"}):
        exported = rows(download(lab_environment, filters=filters))
        listing = client.get("/api/v1/customers", params={**filters, "limit": 100}).json()
        assert [row["customer_id"] for row in exported[:100]] == [row["id"] for row in listing["items"]]


def test_csv_quoting_formulas_decimal_and_empty(lab_environment):
    _, app, _ = lab_environment
    source = snapshot().model_dump(mode="json")
    dangerous = '\t\r\n =HYPERLINK("bad", "x")\nкириллица'
    source["accounts"][0]["email"] = dangerous
    app.state.coordinator.replace_snapshot(SourceSnapshot.model_validate(source))
    response = download(lab_environment)
    record, = rows(response)
    assert record["email"] == "'" + dangerous
    assert record["risk_score"] == str(app.state.coordinator.current.customer(9001).risk_score)
    assert record["monetary_risk_cents"] == str(app.state.coordinator.current.customer(9001).monetary_risk_cents)
    assert record["expires_at"] == "2026-10-30T00:00:00Z"
    assert len(record["source_checksum"]) == 64
    empty = download(lab_environment, filters={"q": "absent"})
    assert rows(empty) == []
    assert empty.headers["X-Export-Row-Count"] == "0"
    assert empty.headers["X-Analysis-Revision"]
    assert text_cell(Decimal("12.30")) == "12.30"


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "  =1", "\x00=1", "\tvalue", "\rvalue", "\nvalue", "\ufeff=1", "\u00a0=1"])
def test_text_prefix_preserves_dangerous_values(value):
    assert text_cell(value) == "'" + value


def test_selected_worklist_and_invalid_selection(lab_environment):
    client, app, headers = lab_environment
    queue = client.post("/api/v1/lab/worklist", headers=headers, json={"expected_revision": revision(app), "limit": 50}).json()
    ids = [int(row["customer_id"]) for row in queue["rows"]]
    assert ids
    saved = client.patch(f"/api/v1/customers/{ids[0]}/workflow", headers=headers, json={
        "expected_revision": 0, "status": "in_progress", "note": "private contact note",
        "manual_outcome": "contacted",
    })
    assert saved.status_code == 200, saved.text
    exported = rows(download(lab_environment, scope="worklist", customer_ids=ids))
    assert [int(row["customer_id"]) for row in exported] == ids
    assert exported[0]["scope"] == "worklist"
    assert exported[0]["workflow_status"] == "in_progress"
    assert exported[0]["workflow_revision"] == "1"
    assert exported[0]["workflow_manual_outcome"] == "contacted"
    assert "workflow_next_contact_at" in exported[0]
    assert "note" not in exported[0] and "private contact note" not in str(exported[0])
    assert rows(download(lab_environment, scope="worklist", customer_ids=[])) == []
    assert download(lab_environment, scope="worklist", customer_ids=[99999]).status_code == 422
    assert download(lab_environment, scope="worklist", customer_ids=ids * 2).status_code == 422


def test_auth_csrf_revision_limits_and_schema(lab_environment, monkeypatch):
    client, app, headers = lab_environment
    body = {"scope": "filtered", "expected_revision": revision(app)}
    assert client.post(URL, json=body).status_code == 403
    assert client.post(URL, headers={**headers, "Origin": "http://foreign.invalid"}, json=body).status_code == 403
    assert download(lab_environment, expected_revision={**revision(app), "analysis_revision": "a-999999"}).status_code == 409
    assert download(lab_environment, filters={"limit": 1}).status_code == 422
    assert client.post(URL, headers={**headers, "Content-Type": "application/json"}, content='{"padding":"' + 'x' * 65536 + '"}').status_code == 413
    schema = client.get("/api/v1/openapi.json").json()["paths"][URL]["post"]
    assert schema["security"] == [{"SessionCookie": []}]
    assert "text/csv" in schema["responses"]["200"]["content"]
    import app.api.exports as exports_api
    with monkeypatch.context() as patch:
        patch.setattr(exports_api, "MAX_EXPORT_ROWS", 0)
        assert download(lab_environment).status_code == 413
    assert client.post(URL, headers=headers, json={"scope": "filtered"}).status_code == 400
    import app.export_csv as serializer
    monkeypatch.setattr(serializer, "MAX_EXPORT_BYTES", 50)
    assert download(lab_environment).status_code == 413
    client.cookies.clear()
    assert client.post(URL, headers=headers, json=body).status_code == 401
