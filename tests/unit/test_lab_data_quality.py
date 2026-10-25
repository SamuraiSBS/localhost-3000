"""Quality facts stay on one evaluation and an explicit receipt/clock pair."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from app.contracts import ModelSettings, SourceSnapshot
from app.data_quality import data_quality_report
from app.evaluation import calculate_evaluation

AS_OF = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
THRESHOLD = timedelta(hours=24)


def evaluation():
    accounts = [dict(id=identifier, email=f"{identifier}@example.test", balance=1000,
                     withdrawBalance=0, totalReplenished=1000, bonusBalance=0, bonusState="locked")
                for identifier in (9, 2, 7)]
    services = [dict(id=identifier, name="Service", ownerId=owner, paymentTerm="month",
                     autoProlong=True, createdAt="2026-01-01T00:00:00Z",
                     expiresAt=(AS_OF + timedelta(days=days)).isoformat(), status="active", price=100)
                for identifier, owner, days in ((1, 2, -7), (2, 2, -8), (3, 7, 60))]
    return calculate_evaluation(
        SourceSnapshot.model_validate(dict(accounts=accounts, services=services, transactions=[])),
        ModelSettings(), analysis_revision=8, data_revision=3, settings_revision=5, as_of=AS_OF,
    )


def report(current=None, **kwargs):
    options = dict(received_at=AS_OF - timedelta(hours=1), now=AS_OF, freshness_threshold=THRESHOLD)
    options.update(kwargs)
    return data_quality_report(current if current is not None else evaluation(), **options)


@pytest.mark.parametrize(("age", "state"), [
    (timedelta(0), "fresh"), (THRESHOLD, "fresh"),
    (THRESHOLD + timedelta(microseconds=1), "stale"),
    (-timedelta(microseconds=1), "future_timestamp"),
])
def test_receipt_freshness_threshold_boundary_and_future(age, state):
    result = report(received_at=AS_OF - age)
    assert result["freshness"] == {
        "state": state, "threshold_seconds": 86400.0,
        "age_seconds": age.total_seconds() if age >= timedelta(0) else None,
    }


def test_no_receipt_timestamp_does_not_substitute_evaluation_time():
    result = report(received_at=None)
    assert result["received_at"] is None
    assert result["as_of"] == "2026-10-25T12:00:00Z"
    assert result["freshness"] == {
        "state": "no_timestamp", "age_seconds": None, "threshold_seconds": 86400.0,
    }


def test_polling_and_recalculation_cannot_refresh_receipt():
    current = evaluation()
    receipt = AS_OF - timedelta(days=2)
    before = report(current, received_at=receipt)
    after = report(replace(current, evaluated_at=AS_OF + timedelta(hours=1)),
                   received_at=receipt, now=AS_OF + timedelta(hours=1))
    assert before["received_at"] == after["received_at"]
    assert before["freshness"]["state"] == after["freshness"]["state"] == "stale"
    assert after["freshness"]["age_seconds"] == before["freshness"]["age_seconds"] + 3600
    assert before["as_of"] != after["as_of"]


def test_offsets_normalize_to_utc_for_all_instants():
    moscow = timezone(timedelta(hours=3))
    current = replace(evaluation(), evaluated_at=AS_OF.astimezone(moscow))
    assert report(current, now=AS_OF.astimezone(moscow),
                  received_at=(AS_OF - timedelta(hours=1)).astimezone(moscow)) == report()


@pytest.mark.parametrize("field", ["now", "received_at", "as_of"])
def test_naive_timestamps_are_rejected(field):
    naive = AS_OF.replace(tzinfo=None)
    with pytest.raises(ValueError, match=f"{field}_must_be_timezone_aware"):
        if field == "as_of":
            report(replace(evaluation(), evaluated_at=naive))
        else:
            report(**{field: naive})


def test_required_now_and_explicit_threshold_have_no_clock_defaults():
    with pytest.raises(TypeError):
        data_quality_report(evaluation(), received_at=None, freshness_threshold=THRESHOLD)
    with pytest.raises(TypeError):
        data_quality_report(evaluation(), received_at=None, now=AS_OF)
    with pytest.raises(ValueError, match="now_must_be_timezone_aware"):
        report(now=None)
    with pytest.raises(ValueError, match="freshness_threshold_must_be_nonnegative"):
        report(freshness_threshold=-timedelta(microseconds=1))
    with pytest.raises(TypeError, match="freshness_threshold_must_be_timedelta"):
        report(freshness_threshold=86400)
    assert report(freshness_threshold=timedelta(0), received_at=AS_OF)["freshness"]["state"] == "fresh"


def test_coverage_unknown_and_warning_counts_match_captured_customers():
    current = evaluation()
    result = report(current)
    assert (result["analysis_revision"], result["data_revision"], result["settings_revision"]) == ("a-8", 3, 5)
    assert result["score_coverage"] == {
        "numerator": current.customers_evaluated, "denominator": 3, "unknown": 1, "state": "partial",
    }
    assert result["monetary_coverage"]["numerator"] == current.monetary_coverage_evaluated
    assert result["monetary_coverage"]["unknown"] == current.monetary_coverage_unknown
    assert result["unknown_reasons"] == [
        {"code": "no_eligible_services", "count": 1, "customer_ids": [9]},
    ]
    assert result["field_warnings"] == [
        {"code": "active_expired_over_five_days", "count": 2, "customer_count": 1, "customer_ids": [2]},
    ]
    assert result["warning_count"] == current.data_quality_warnings == 2
    assert result["source_completeness"] == "not_established"


def test_empty_snapshot_has_zero_denominator_without_claiming_complete_source():
    current = calculate_evaluation(SourceSnapshot(accounts=[], services=[], transactions=[]),
                                   ModelSettings(), analysis_revision=1, data_revision=1,
                                   settings_revision=1, as_of=AS_OF)
    result = report(current)
    assert result["score_coverage"] == {"numerator": 0, "denominator": 0, "unknown": 0, "state": "empty"}
    assert result["unknown_reasons"] == result["field_warnings"] == []
    assert result["source_completeness"] == "not_established"


def test_sorting_fallback_unknown_reason_and_zero_score():
    current = evaluation()
    a, b, c = current.customers
    customers = (
        replace(c, risk_score=None, unscored_reason=None, data_quality_warnings=("z", "a", "z")),
        replace(b, risk_score=None, unscored_reason="z_reason", data_quality_warnings=("a",)),
        replace(a, risk_score=0, data_quality_warnings=("z",)),
    )
    result = report(replace(current, customers=customers))
    assert result == report(replace(current, customers=tuple(reversed(customers))))
    assert result["score_coverage"]["numerator"] == 1
    assert result["unknown_reasons"] == [
        {"code": "reason_unavailable", "count": 1, "customer_ids": [9]},
        {"code": "z_reason", "count": 1, "customer_ids": [7]},
    ]
    assert result["field_warnings"] == [
        {"code": "a", "count": 2, "customer_count": 2, "customer_ids": [7, 9]},
        {"code": "z", "count": 3, "customer_count": 2, "customer_ids": [2, 9]},
    ]


def test_deterministic_json_and_result_mutation_do_not_touch_input():
    current = evaluation()
    before = (current.snapshot_json, current.settings_json, current.customers,
              dict(current.risk_counts), dict(current.customers_by_id))
    expected = report(current)
    assert json.dumps(expected, allow_nan=False) == json.dumps(report(current), allow_nan=False)
    changed = report(current)
    changed["unknown_reasons"][0]["customer_ids"].append(999)
    changed["field_warnings"][0]["customer_ids"].clear()
    changed["limitations"].clear()
    assert report(current) == expected
    assert before == (current.snapshot_json, current.settings_json, current.customers,
                      dict(current.risk_counts), dict(current.customers_by_id))
