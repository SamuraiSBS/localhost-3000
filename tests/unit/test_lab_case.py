"""L18 portable replay and independent arithmetic/privacy checks."""

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from app.contracts import AccountSource, ModelSettings, ServiceSource, SourceSnapshot
from app.domain.lab_case import (CaseValidationError, MAX_CASE_BYTES, canonical_case_json,
                                 case_checksum, export_case, replay_case)

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def source():
    owners = [AccountSource(id=9001, email="private@example.test", balance=0,
                            withdrawBalance=9000, totalReplenished=25000,
                            bonusBalance=500, bonusState="locked", secret="do-not-export"),
              AccountSource(id=9002, email="other@example.test", balance=123,
                            withdrawBalance=0, totalReplenished=0, bonusBalance=0, bonusState="locked")]
    rows = [ServiceSource(id=81, ownerId=9001, name="private-service", price=1000,
                          paymentTerm="month", autoProlong=False, status="active",
                          createdAt=NOW-timedelta(days=60), expiresAt=NOW+timedelta(days=15),
                          token="secret-token")]
    return SourceSnapshot(accounts=owners, services=rows, transactions=[], secret="snapshot-secret")


def operation():
    return {"type": "solve", "goal": {"type": "score_below", "threshold": 35},
            "max_top_up_cents": 1000, "allowed_enable_auto_prolong_ids": [81], "max_switches": 1}


def exported(**kwargs):
    options = dict(customer_id=9001, as_of=NOW, operation=operation())
    options.update(kwargs)
    return export_case(source(), ModelSettings(), **options)


def resign(case):
    case["checksum"] = case_checksum(case)
    return case


def test_l18_arithmetic_and_restart_independence_no_mutation():
    snapshot, settings = source(), ModelSettings()
    before = snapshot.canonical_json(), settings.model_dump_json()
    case = export_case(snapshot, settings, customer_id=9001, as_of=NOW, operation=operation())
    result = replay_case(canonical_case_json(case).encode())
    assert result["matched"] is True
    actual = result["calculated"]
    assert actual["baseline"]["risk_score"] == "70.00"
    assert (actual["status"], actual["top_up_cents"], actual["enable_auto_prolong_ids"]) == ("found", 286, [1])
    assert actual["scenario"]["risk_score"] == "34.99"
    assert actual["scenario"]["funding_gap_cents"] == 714
    assert actual["one_cent_less_result"]["risk_score"] == "35.03"
    assert actual["one_cent_less_satisfies"] is False
    assert before == (snapshot.canonical_json(), settings.model_dump_json())
    # Detached JSON and different caller settings cannot affect the carried model.
    ModelSettings(weights={"renewal_proximity": 100, "manual_renewal": 0, "funding_gap": 0, "payment_wait": 0})
    assert replay_case(json.loads(canonical_case_json(case))) == result


def test_privacy_whitelist_and_deterministic_bytes():
    case = exported()
    document = canonical_case_json(case)
    assert document == canonical_case_json(exported())
    for private in ("private@", "other@", "private-service", "secret", "9001", "9002"):
        assert private not in document
    row = case["snapshot"]["accounts"][0]
    assert (row["id"], row["email"], row["withdrawBalance"], row["totalReplenished"]) == (1, "case@example.invalid", 0, 0)
    assert (row["balance"], row["bonusBalance"], row["bonusState"]) == (0, 500, "locked")
    assert case["snapshot"]["transactions"] == []
    payload = {key: value for key, value in case.items() if key != "checksum"}
    independently_canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert hashlib.sha256(independently_canonical.encode()).hexdigest() == case["checksum"]


def test_equal_expiry_funding_and_solver_id_ties_preserved():
    snapshot = source()
    first = snapshot.services[0].model_copy(update={"id": 55, "price": 600})
    second = snapshot.services[0].model_copy(update={"id": 99, "price": 400})
    snapshot = SourceSnapshot(accounts=snapshot.accounts, services=[second, first], transactions=[])
    case = export_case(snapshot, ModelSettings(), customer_id=9001, as_of=NOW,
                       operation={"type": "solve", "goal": {"type": "cover_selected", "service_ids": [99]},
                                  "max_top_up_cents": 1000})
    assert case["operation"]["goal"]["service_ids"] == [2]
    assert case["expected"]["top_up_cents"] == 1000
    assert case["expected"]["one_cent_less_result"]["services"][1]["funding_gap_cents"] == 1
    assert replay_case(case)["matched"]


@pytest.mark.parametrize("op,expected", [
    ({"type": "model", "settings_overrides": {"weights": {"manual_renewal": 50}}}, "95.00"),
    ({"type": "action", "action": {"top_up_cents": 1000, "enable_auto_prolong_ids": [81]}}, "10.00"),
])
def test_real_preview_operations(op, expected):
    case = exported(operation=op)
    assert case["expected"]["scenario"]["risk_score"] == expected
    assert replay_case(case)["calculated"] == case["expected"]


@pytest.mark.parametrize("field,value,code", [
    ("case_version", 2, "unsupported_case_version"),
    ("case_version", True, "invalid_case_schema"),
    ("engine_version", "rules-v2", "unsupported_case_engine"),
    ("feature_versions", {}, "unsupported_case_features"),
])
def test_versions_have_explicit_errors(field, value, code):
    case = exported()
    case[field] = value
    with pytest.raises(CaseValidationError, match=code):
        replay_case(resign(case))


def test_checksum_tamper_and_expected_forgery_are_distinct():
    case = exported()
    case["snapshot"]["accounts"][0]["balance"] = 1
    with pytest.raises(CaseValidationError, match="case_checksum_mismatch"):
        replay_case(case)
    case = exported()
    case["expected"]["top_up_cents"] = 285
    with pytest.raises(CaseValidationError, match="case_result_mismatch"):
        replay_case(resign(case))


@pytest.mark.parametrize("document", [b"{", b"[]", b"{\"case_version\":1,\"case_version\":1}",
                                       b"{\"case_version\":NaN}", b"\xff", b"null"])
def test_malformed_json(document):
    with pytest.raises(CaseValidationError, match="invalid_case_schema"):
        replay_case(document)


def test_size_is_utf8_byte_cap_before_parsing():
    with pytest.raises(CaseValidationError) as error:
        replay_case(b" " * (MAX_CASE_BYTES + 1))
    assert (error.value.code, error.value.status_code) == ("case_too_large", 413)
    with pytest.raises(CaseValidationError) as error:
        replay_case("я" * (MAX_CASE_BYTES // 2 + 1))
    assert error.value.status_code == 413


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(extra="unused"),
    lambda c: c["snapshot"]["accounts"][0].update(secret="cookie"),
    lambda c: c["snapshot"]["services"][0].update(name="<script>"),
    lambda c: c["snapshot"]["accounts"][0].update(withdrawBalance=1),
    lambda c: c.update(horizon_days=31),
    lambda c: c["operation"].update(max_top_up_cents=True),
    lambda c: c["operation"].update(allowed_enable_auto_prolong_ids=[1, 1]),
    lambda c: c["operation"].update(goal={"type": "score_below", "threshold": 35, "service_ids": [1]}),
])
def test_strict_schema_neutral_source_and_operation(mutate):
    case = exported()
    mutate(case)
    with pytest.raises(CaseValidationError, match="invalid_case_schema"):
        replay_case(resign(case))


def test_services_cap_even_if_engine_would_ignore_blocked_rows():
    snapshot = source()
    rows = [snapshot.services[0].model_copy(update={"id": index + 1, "status": "blocked"}) for index in range(101)]
    many = SourceSnapshot(accounts=snapshot.accounts, services=rows, transactions=[])
    with pytest.raises(CaseValidationError, match="case_service_limit"):
        export_case(many, ModelSettings(), customer_id=9001, as_of=NOW,
                    operation={"type": "model", "settings_overrides": {}})


def test_foreign_action_ids_rejected_without_mutation():
    with pytest.raises(CaseValidationError, match="service_not_allowed"):
        exported(operation={"type": "action", "action": {"enable_auto_prolong_ids": [999]}})


def test_case_with_no_services_replays_unknown_without_history():
    snapshot = source().model_copy(update={"services": ()})
    case = export_case(snapshot, ModelSettings(), customer_id=9001, as_of=NOW,
                       operation={"type": "model", "settings_overrides": {}})
    assert replay_case(case)["calculated"]["baseline"]["risk_score"] is None


def test_replay_rejects_invalid_expected_before_calculation(monkeypatch):
    import app.domain.lab_case as domain
    case = exported()
    case["expected"]["scenario"]["risk_score"] = "<script>"
    monkeypatch.setattr(domain, "_calculate", lambda _: pytest.fail("invalid expected reached engine"))
    with pytest.raises(CaseValidationError, match="invalid_case_schema"):
        replay_case(resign(case))


def test_replay_caps_and_full_snapshot_rejection():
    case = exported()
    case["snapshot"]["services"] = [
        {**case["snapshot"]["services"][0], "id": i, "name": f"case-service-{i}"}
        for i in range(1, 102)]
    with pytest.raises(CaseValidationError, match="case_service_limit"):
        replay_case(resign(case))
    case = exported()
    case["snapshot"]["accounts"].append({**case["snapshot"]["accounts"][0], "id": 2})
    with pytest.raises(CaseValidationError, match="invalid_case_schema"):
        replay_case(resign(case))
    case = exported(operation={"type": "action", "action": {}})
    case["operation"]["action"]["enable_auto_prolong_ids"] = list(range(1, 10))
    with pytest.raises(CaseValidationError, match="case_switch_limit"):
        replay_case(resign(case))


def test_original_input_order_does_not_change_export_and_earlier_dates_still_win():
    snapshot = source()
    rows = [snapshot.services[0].model_copy(update={"id": 90, "price": 600, "expiresAt": NOW+timedelta(days=1)}),
            snapshot.services[0].model_copy(update={"id": 10, "price": 400, "expiresAt": NOW+timedelta(days=2)})]
    operation = {"type": "solve", "goal": {"type": "cover_selected", "service_ids": [10]}, "max_top_up_cents": 1000}
    def make(services):
        return export_case(SourceSnapshot(accounts=snapshot.accounts, services=services, transactions=[]),
                           ModelSettings(), customer_id=9001, as_of=NOW, operation=operation)
    case = make(rows)
    assert canonical_case_json(case) == canonical_case_json(make(list(reversed(rows))))
    assert case["operation"]["goal"]["service_ids"] == [1]
    assert case["expected"]["top_up_cents"] == 1000
    assert replay_case(case)["matched"]
