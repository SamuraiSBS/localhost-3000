"""Financial/type integrity at the source boundary (U09, U10, U11)."""

from copy import deepcopy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import DEFAULT_SOURCE_PATH, RuntimeConfig
from app.contracts import (
    MAX_MONEY_CENTS, MAX_SOURCE_ID, ModelSettings, SettingsPatch,
    SourceSnapshot, checked_money, load_source,
)
from app.main import create_app


@pytest.fixture
def source():
    return {
        "accounts": [{"id": 1, "email": "<em>клиент</em>@example.test", "balance": 100,
                      "withdrawBalance": 200, "totalReplenished": 0,
                      "bonusBalance": 10, "bonusState": "locked"}],
        "services": [{"id": 2, "ownerId": 1, "name": "任意 услуга <script>",
                      "paymentTerm": "month", "autoProlong": False,
                      "createdAt": "2026-01-01T00:00:00Z", "expiresAt": "2026-02-01T01:00:00+01:00",
                      "status": "active", "price": 2000}],
        "transactions": [{"id": 3, "serviceId": 2, "amount": 1001,
                          "status": "not_performed", "performedAt": None,
                          "createdAt": "2026-01-01T00:00:00Z", "type": "order"}],
    }


def test_actual_seed_readonly_and_complete():
    before = DEFAULT_SOURCE_PATH.read_bytes()
    snapshot = load_source(DEFAULT_SOURCE_PATH)
    assert (len(snapshot.accounts), len(snapshot.services), len(snapshot.transactions)) == (10, 30, 100)
    assert DEFAULT_SOURCE_PATH.read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == hashlib.sha256(DEFAULT_SOURCE_PATH.read_bytes()).hexdigest()


def test_unicode_amount_and_extras_survive(source):
    source["additional"] = {"note": "не терять"}
    source["accounts"][0]["upstream"] = [1, 2]
    snapshot = SourceSnapshot.model_validate(source)
    result = json.loads(snapshot.canonical_json())
    assert result["additional"] == source["additional"]
    assert result["accounts"][0]["upstream"] == [1, 2]
    assert snapshot.services[0].name == "任意 услуга <script>"
    assert snapshot.transactions[0].amount == 1001
    assert result["services"][0]["expiresAt"] == "2026-02-01T00:00:00Z"
    assert SourceSnapshot.model_validate_json(snapshot.canonical_json()) == snapshot


@pytest.mark.parametrize("table,field,value", [
    ("accounts", "balance", True), ("accounts", "balance", 1.0),
    ("accounts", "balance", "1"), ("accounts", "balance", -1),
    ("accounts", "balance", MAX_MONEY_CENTS + 1),
    ("accounts", "id", 0), ("accounts", "id", MAX_SOURCE_ID + 1),
    ("accounts", "id", True), ("accounts", "bonusState", "other"),
    ("accounts", "email", 1), ("services", "name", 7),
    ("services", "paymentTerm", "day"), ("services", "status", "other"),
    ("services", "autoProlong", 1), ("transactions", "type", "refund"),
    ("transactions", "status", "other"), ("transactions", "amount", 1001.0),
    ("services", "expiresAt", "2026-02-01T00:00:00"),
    ("services", "expiresAt", 1790000000),
    ("services", "expiresAt", "2026-02-30T00:00:00Z"),
    ("services", "expiresAt", "2025-02-01T00:00:00Z"),
    ("transactions", "status", "performed"),
    ("services", "ownerId", 99), ("transactions", "serviceId", 99),
])
def test_invalid_fields_rejected(source, table, field, value):
    source[table][0][field] = value
    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate(source)


@pytest.mark.parametrize("table", ["accounts", "services", "transactions"])
def test_duplicate_ids_rejected(source, table):
    source[table].append(deepcopy(source[table][0]))
    with pytest.raises(ValidationError, match="duplicate_id"):
        SourceSnapshot.model_validate(source)


def test_performed_transaction_date_order(source):
    transaction = source["transactions"][0]
    transaction.update(status="performed", performedAt="2025-01-01T00:00:00Z")
    with pytest.raises(ValidationError, match="performed_before_created"):
        SourceSnapshot.model_validate(source)
    transaction["performedAt"] = transaction["createdAt"]
    assert SourceSnapshot.model_validate(source).transactions[0].performedAt is not None


@pytest.mark.parametrize("offset", ["+00:60", "-00:60", "+24:00", "-24:00"])
@pytest.mark.parametrize("table,field", [
    ("services", "createdAt"), ("services", "expiresAt"),
    ("transactions", "createdAt"), ("transactions", "performedAt"),
])
def test_json_timestamp_rejects_invalid_offset(source, table, field, offset):
    source[table][0][field] = f"2026-01-02T00:00:00{offset}"
    with pytest.raises(ValidationError, match="timezone_required_rfc3339"):
        SourceSnapshot.model_validate_json(json.dumps(source))


@pytest.mark.parametrize("value", [
    "2026-02-01T24:00:00Z", "2026-02-01T00:60:00Z",
    "2026-02-01T00:00:60Z", "2026-02-30T00:00:00Z",
])
def test_json_timestamp_rejects_invalid_clock_and_date(source, value):
    source["services"][0]["expiresAt"] = value
    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate_json(json.dumps(source))


@pytest.mark.parametrize("offset,expected", [
    ("Z", "2026-02-01T00:00:00Z"),
    ("+00:59", "2026-01-31T23:01:00Z"),
    ("-00:59", "2026-02-01T00:59:00Z"),
    ("+23:59", "2026-01-31T00:01:00Z"),
    ("-23:59", "2026-02-01T23:59:00Z"),
    ("+05:30", "2026-01-31T18:30:00Z"),
])
def test_json_timestamp_valid_offsets_normalize_to_utc(source, offset, expected):
    source["services"][0]["expiresAt"] = f"2026-02-01T00:00:00{offset}"
    result = SourceSnapshot.model_validate_json(json.dumps(source))
    assert json.loads(result.canonical_json())["services"][0]["expiresAt"] == expected


def test_empty_snapshot_and_no_services_are_valid(source):
    empty = SourceSnapshot.model_validate({"accounts": [], "services": [], "transactions": []})
    assert empty.accounts == ()
    source["services"] = []
    source["transactions"] = []
    assert len(SourceSnapshot.model_validate(source).accounts) == 1


def test_money_exact_limits_and_spendable_overflow(source):
    source["accounts"][0]["balance"] = MAX_MONEY_CENTS
    assert SourceSnapshot.model_validate(source).accounts[0].balance == MAX_MONEY_CENTS
    source["accounts"][0]["bonusState"] = "unlocked"
    with pytest.raises(ValidationError, match="money_out_of_range"):
        SourceSnapshot.model_validate(source)
    assert checked_money(MAX_MONEY_CENTS) == MAX_MONEY_CENTS
    with pytest.raises(ValueError):
        checked_money(MAX_MONEY_CENTS + 1)


def test_id_bigint_not_rounded(source):
    source["accounts"][0]["id"] = MAX_SOURCE_ID
    source["services"][0]["ownerId"] = MAX_SOURCE_ID
    assert SourceSnapshot.model_validate(source).accounts[0].id == MAX_SOURCE_ID


@pytest.mark.parametrize("value", [True, "25", float("nan"), float("inf"), -1, 101])
def test_invalid_weights(source, value):
    with pytest.raises(ValidationError):
        SettingsPatch.model_validate({"weights": {"manual_renewal": value}})


@pytest.mark.parametrize("patch", [
    {"horizon_days": True}, {"horizon_days": 0}, {"horizon_days": 91},
    {"horizon_days": 30.0}, {"thresholds": {"medium": 65, "high": 35}},
    {"thresholds": {"medium": 0, "high": 65}}, {"weights": {"unknown": 1}},
    {"unknown": 1}, {"weights": None},
])
def test_invalid_settings_patch(patch):
    with pytest.raises(ValidationError):
        SettingsPatch.model_validate(patch)


def test_partial_settings_merge_preserves_others():
    settings = ModelSettings()
    updated = SettingsPatch(weights={"manual_renewal": 50}).apply(settings)
    assert updated.weights["manual_renewal"] == 50
    assert updated.weights["funding_gap"] == 35
    assert settings.weights["manual_renewal"] == 25
    with pytest.raises(ValidationError, match="all_be_zero"):
        SettingsPatch(weights=dict.fromkeys(settings.weights, 0)).apply(settings)


def test_settings_json_roundtrip_uses_numbers():
    settings = SettingsPatch(weights={"manual_renewal": 25.5}).apply(ModelSettings())
    document = json.loads(settings.model_dump_json())
    assert document["weights"]["manual_renewal"] == 25.5
    assert document["thresholds"]["medium"] == 35
    assert ModelSettings.model_validate_json(settings.model_dump_json()) == settings


def test_partial_threshold_merge_uses_current_values():
    settings = ModelSettings(thresholds={"medium": 40, "high": 90})
    updated = SettingsPatch(thresholds={"medium": 70}).apply(settings)
    assert updated.thresholds.medium == 70
    assert updated.thresholds.high == 90
    with pytest.raises(ValidationError, match="ordered"):
        SettingsPatch(thresholds={"high": 30}).apply(settings)


def test_health_only_and_docs_not_exposed():
    with TestClient(create_app()) as client:
        assert client.get("/healthz").json() == {"status": "ok", "stage": "runtime-scaffold"}
        for path in ("/docs", "/redoc", "/openapi.json", "/api/v1/customers"):
            assert client.get(path).status_code == 404


def test_missing_seed_fails_startup():
    missing = DEFAULT_SOURCE_PATH.with_name("__missing_seed_for_contract_test__.json")
    assert not missing.exists()
    with pytest.raises(FileNotFoundError):
        with TestClient(create_app(RuntimeConfig(source_path=missing))):
            pass


def test_credentials_have_no_public_defaults():
    config = RuntimeConfig()
    with pytest.raises(ValueError, match="APP_LOGIN"):
        config.require_credentials()
    assert "password_hash=" not in repr(config)
    assert "metrics_token=" not in repr(config)
