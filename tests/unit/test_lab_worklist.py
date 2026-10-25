"""Worklist ordering, manual contract cases and immutable baseline coverage."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.contracts import AccountSource, ModelSettings, ServiceSource, SourceSnapshot
from app.domain.lab_worklist import build_worklist
from app.evaluation import calculate_evaluation


AS_OF = datetime(2026, 10, 25, tzinfo=timezone.utc)


def account(customer_id=1, *, balance=0):
    return AccountSource(id=customer_id, email=f"{customer_id}@example.test",
                         balance=balance, withdrawBalance=0, totalReplenished=0,
                         bonusBalance=0, bonusState="locked")


def service(service_id=101, owner=1, *, days=10, auto=False, status="active", price=1000):
    return ServiceSource(id=service_id, ownerId=owner, name="Service",
                         paymentTerm="month", autoProlong=auto, price=price,
                         status=status, createdAt=AS_OF - timedelta(days=100),
                         expiresAt=AS_OF + timedelta(days=days))


def evaluate(accounts=(), services=(), *, settings=None):
    return calculate_evaluation(
        SourceSnapshot(accounts=accounts, services=services, transactions=()),
        settings or ModelSettings(), analysis_revision=12, data_revision=7,
        settings_revision=3, as_of=AS_OF,
    )


def test_f1_baseline_gap_and_manual_renewal_are_both_explained():
    baseline = evaluate((account(9001, balance=1200),), (
        service(101, 9001, days=5, auto=True), service(102, 9001),
    ))
    result = build_worklist(baseline, limit=1)
    row, = result.rows
    assert row.customer_id == 9001
    assert row.risk_score == Decimal("66.33")
    assert row.monetary_risk_cents == 830
    assert row.due_services_price_cents == 2000
    assert row.funding_gap_cents == 800
    assert row.reason_codes == ("funding_gap", "manual_renewal")
    affected, = row.affected_services
    assert affected.service_id == 102
    assert affected.funding_gap_cents == 800
    assert affected.suggested_actions == ("review_funding_gap", "review_manual_renewal")
    assert result.matched_count == result.returned_count == 1


@pytest.mark.parametrize("limit,expected", [(1, [20]), (5, [20, 21, 22, 10, 12]),
                                            (50, [20, 21, 22, 10, 12, 13])])
def test_l10_strict_sort_and_limit_apply_after_full_matching(limit, expected):
    specs = [(13, 4, 1000, 65), (22, 1, 2500, 50), (10, 2, 8000, 80),
             (12, 3, 7000, 70), (21, 1, 2500, 50), (20, 1, 3000, 60)]
    baseline = evaluate(tuple(account(cid) for cid, *_ in specs),
                        tuple(service(cid, cid, days=days) for cid, days, *_ in specs))
    expectations = {cid: (money, score) for cid, _, money, score in specs}
    baseline = replace(baseline, customers=tuple(
        replace(customer, monetary_risk_cents=expectations[customer.customer_id][0],
                risk_score=Decimal(expectations[customer.customer_id][1]))
        for customer in baseline.customers
    ))
    result = build_worklist(baseline, limit=limit)
    assert [row.customer_id for row in result.rows] == expected
    assert result.matched_count == 6
    assert result.returned_count == len(expected)
    assert sum(result.excluded_reason_counts.values()) == 0


def test_equal_date_money_uses_score_then_customer_id():
    baseline = evaluate(tuple(account(cid) for cid in (3, 2, 1)),
                        tuple(service(cid, cid) for cid in (3, 2, 1)))
    baseline = replace(baseline, customers=tuple(
        replace(customer, monetary_risk_cents=100,
                risk_score=Decimal(60 if customer.customer_id == 3 else 50))
        for customer in baseline.customers
    ))
    assert [row.customer_id for row in build_worklist(baseline).rows] == [3, 1, 2]


def test_exclusions_partition_full_input_and_unknown_is_not_zero():
    baseline = evaluate(tuple(account(cid, balance=10000 if cid == 5 else 0)
                              for cid in range(1, 7)), (
        service(2, 2, status="blocked"), service(22, 2, status="deleted"),
        service(3, 3, days=31), service(4, 4),
        service(5, 5, auto=True), service(6, 6, auto=True),
    ))
    baseline = replace(baseline, customers=tuple(
        replace(customer, risk_score=None, risk_level="unknown") if customer.customer_id == 4
        else replace(customer, risk_score=Decimal(40), risk_level="medium")
        if customer.customer_id == 5 else customer
        for customer in baseline.customers
    ))
    result = build_worklist(baseline)
    assert [row.customer_id for row in result.rows] == [6]
    assert dict(result.excluded_reason_counts) == {
        "no_eligible_service": 2, "no_in_horizon_service": 1,
        "unknown_score": 1, "no_actionable_reason": 1,
    }
    assert result.coverage.total_customers == 6
    assert result.coverage.unknown_customers == 3
    assert sum(result.excluded_reason_counts.values()) + result.matched_count == 6


def test_low_score_is_excluded_even_with_manual_renewal():
    baseline = evaluate((account(balance=1000),), (service(days=30),))
    result = build_worklist(baseline)
    assert result.rows == ()
    assert dict(result.excluded_reason_counts) == {"low_risk": 1}


def test_covered_manual_service_and_uncovered_horizon_boundary_are_candidates():
    baseline = evaluate((account(1, balance=1000), account(2)), (
        service(1, 1), service(2, 2, days=30, auto=True),
    ))
    result = build_worklist(baseline)
    first, second = result.rows
    assert first.reason_codes == ("manual_renewal",)
    assert first.funding_gap_cents == 0
    assert second.reason_codes == ("funding_gap",)
    assert second.risk_score == Decimal("35.00")


def test_affected_expiry_ignores_earlier_fully_covered_automatic_service():
    baseline = evaluate((account(balance=1000),), (
        service(101, days=-2, auto=True), service(103, days=2), service(102, days=2),
    ))
    row, = build_worklist(baseline).rows
    assert row.expires_at == AS_OF + timedelta(days=2)
    assert [item.service_id for item in row.affected_services] == [102, 103]


def test_overdue_wait_and_suspended_are_retained_with_status_warning():
    baseline = evaluate((account(),), (
        service(101, days=-2, status="prolong_wait"), service(102, status="suspended"),
    ))
    row, = build_worklist(baseline).rows
    assert row.affected_services[0].overdue is True
    assert row.affected_services[1].status == "suspended"
    assert row.warnings == ("prolong_wait",)


def test_empty_source_and_no_mutation_deterministic_frozen_results():
    empty = build_worklist(evaluate())
    assert empty.rows == ()
    assert empty.matched_count == empty.returned_count == 0
    assert empty.coverage.total_customers == 0
    baseline = evaluate((account(),), (service(),))
    before = (baseline.snapshot_json, baseline.settings_json, baseline.customers,
              baseline.analysis_revision, baseline.data_revision, baseline.settings_revision)
    result = build_worklist(baseline)
    assert result == build_worklist(baseline)
    assert before == (baseline.snapshot_json, baseline.settings_json, baseline.customers,
                      baseline.analysis_revision, baseline.data_revision, baseline.settings_revision)
    with pytest.raises(FrozenInstanceError):
        result.rows[0].customer_id = 2
    with pytest.raises(TypeError):
        result.excluded_reason_counts["other"] = 1


def test_covered_wait_still_warns_when_another_service_is_affected():
    baseline = evaluate((account(balance=1000),), (
        service(101, days=1, status="prolong_wait", auto=True), service(102),
    ))
    row, = build_worklist(baseline).rows
    assert [item.service_id for item in row.affected_services] == [102]
    assert row.warnings == ("prolong_wait",)


@pytest.mark.parametrize("limit", [0, 51, -1, True, 1.0, "1", None])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError, match="worklist_limit_out_of_range"):
        build_worklist(evaluate(), limit=limit)


def test_unknown_money_stays_unknown_and_sorts_after_known_zero():
    baseline = evaluate((account(1), account(2)), (service(1, 1), service(2, 2)))
    baseline = replace(baseline, customers=tuple(
        replace(customer, monetary_risk_cents=None if customer.customer_id == 1 else 0)
        for customer in baseline.customers
    ))
    result = build_worklist(baseline)
    assert [row.customer_id for row in result.rows] == [2, 1]
    assert result.rows[1].monetary_risk_cents is None
    assert result.coverage.monetary_unknown_customers == 1
