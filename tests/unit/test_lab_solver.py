"""Hand calculated solver fixtures and a small exhaustive independent oracle."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import combinations

import pytest

from app.contracts import MAX_MONEY_CENTS, AccountSource, ModelSettings, ServiceSource
from app.domain.features import DEFAULT_FEATURE_REGISTRY, FeatureRegistry
from app.domain import lab_solver
from app.domain.lab_solver import solve
from app.domain.scoring import score_customer

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def account(balance=0, bonus=0, bonus_state="locked"):
    return AccountSource(id=1, email="synthetic@example.test", balance=balance,
                         withdrawBalance=100000, totalReplenished=100000,
                         bonusBalance=bonus, bonusState=bonus_state)


def service(service_id=81, price=1000, days=15, auto=False, status="active"):
    return ServiceSource(id=service_id, ownerId=1, name="synthetic", price=price,
                         paymentTerm="month", autoProlong=auto, status=status,
                         createdAt=NOW-timedelta(days=60), expiresAt=NOW+timedelta(days=days))


def run(rows=None, **kwargs):
    options = dict(as_of=NOW, settings=ModelSettings(),
                   goal={"type": "score_below", "threshold": 35},
                   max_top_up_cents=1000, allowed_enable_auto_prolong_ids=(81,), max_switches=1)
    options.update(kwargs)
    return solve(options.pop("account", account()), [service()] if rows is None else rows, **options)


def test_l15_strict_threshold_and_one_cent_certificate_no_mutation():
    owner, rows = account(), [service()]
    before = owner.model_dump(), [row.model_dump() for row in rows]
    result = run(rows, account=owner)
    assert result.status == "found"
    assert (result.top_up_cents, result.enable_auto_prolong_ids) == (286, (81,))
    assert result.baseline.risk_score == Decimal("70.00")
    assert result.result.risk_score == Decimal("34.99")
    assert result.result.services[0].funding_gap_cents == 714
    assert result.certificate.one_cent_less_result.risk_score == Decimal("35.03")
    assert result.certificate.one_cent_less_satisfies is False
    assert [(row.enable_auto_prolong_ids, row.top_up_cents) for row in result.certificate.subset_minima] == [((), None), ((81,), 286)]
    assert before == (owner.model_dump(), [row.model_dump() for row in rows])


def test_coverage_consumes_earlier_unselected_funds_and_switches_cannot_help():
    rows = [service(81, 600, 1), service(82, 400, 2)]
    result = run(rows, goal={"type": "cover_selected", "service_ids": [82]})
    assert (result.status, result.top_up_cents, result.enable_auto_prolong_ids) == ("found", 1000, ())
    assert result.certificate.one_cent_less_result.services[1].funding_gap_cents == 1
    assert run(rows, goal={"type": "cover_selected", "service_ids": [82]}, max_top_up_cents=999).status == "unreachable"


def test_zero_price_selected_is_already_covered_without_funding_earlier_rows():
    result = run([service(81, 1000, 1), service(82, 0, 2)],
                 goal={"type": "cover_selected", "service_ids": [82]})
    assert (result.status, result.top_up_cents) == ("already_satisfied", 0)


def test_l16_irreducible_wait_is_unreachable_and_limit_is_not_proof():
    result = run([service(auto=True, status="prolong_wait")],
                 goal={"type": "score_below", "threshold": 15},
                 allowed_enable_auto_prolong_ids=(), max_switches=0)
    assert result.status == "unreachable" and result.certificate.complete
    limited = run(evaluation_limit=1)
    assert limited.status == "search_limit"
    assert limited.evaluations == 1 and not limited.certificate.complete
    assert limited.top_up_cents is None and limited.result is None


@pytest.mark.parametrize("extra", [service(82, days=31), service(82, status="blocked")])
@pytest.mark.parametrize("evaluation_limit,expected_evaluations", [(6144, 15), (2, 2)])
def test_all_source_rows_reduce_candidate_budget(monkeypatch, extra, evaluation_limit, expected_evaluations):
    # Scale the production row budget, retaining real scoring and search on tiny
    # fixtures. Both a future row and an excluded row cost candidate work.
    monkeypatch.setattr(lab_solver, "MAX_SERVICE_EVALUATIONS", 30)
    assert run().status == "found"
    rows = [service(), extra]
    calls = []

    def counted_score(owner, source, **kwargs):
        calls.append(len(source))
        return score_customer(owner, source, **kwargs)

    monkeypatch.setattr(lab_solver, "score_customer", counted_score)
    result = run(rows, evaluation_limit=evaluation_limit)
    assert result.status == "search_limit"
    assert result.evaluations == expected_evaluations
    assert calls == [2] * (expected_evaluations + 1)  # One complete baseline.
    assert result.evaluations * len(rows) <= lab_solver.MAX_SERVICE_EVALUATIONS
    assert not result.certificate.complete
    assert result.top_up_cents is None and result.result is None
    assert len(result.baseline.services) == len(rows)


@pytest.mark.parametrize("budget,expected_evaluations", [(0, 0), (1, 1)])
def test_row_budget_exhaustion_during_coverage_certificate_is_not_found(monkeypatch, budget, expected_evaluations):
    monkeypatch.setattr(lab_solver, "MAX_SERVICE_EVALUATIONS", budget)
    result = run(goal={"type": "cover_selected", "service_ids": [81]})
    assert result.status == "search_limit"
    assert result.evaluations == expected_evaluations
    assert not result.certificate.complete
    assert result.top_up_cents is None and result.result is None
    already = run(account=account(1000), goal={"type": "cover_selected", "service_ids": [81]})
    assert already.status == "already_satisfied"
    assert already.evaluations == 0 and already.certificate.complete


def test_already_satisfied_and_zero_cent_switch_solution():
    result = run(account=account(1000), goal={"type": "score_below", "threshold": 36})
    assert (result.status, result.top_up_cents, result.enable_auto_prolong_ids) == ("already_satisfied", 0, ())
    result = run(account=account(1000), max_top_up_cents=0)
    assert (result.status, result.top_up_cents, result.enable_auto_prolong_ids) == ("found", 0, (81,))
    assert result.certificate.one_cent_less_result is None


@pytest.mark.parametrize("bonus_state,expected", [("locked", 1000), ("unlocked", 700)])
def test_bonus_and_nonspendable_accounts(bonus_state, expected):
    result = run(account=account(bonus=300, bonus_state=bonus_state),
                 goal={"type": "cover_selected", "service_ids": [81]})
    assert result.top_up_cents == expected


@pytest.mark.parametrize("top_up", [True, 1.5, -1, 10000001])
def test_reject_invalid_cent_bounds(top_up):
    with pytest.raises(ValueError, match="max_top_up_cents_out_of_range"):
        run(max_top_up_cents=top_up)


@pytest.mark.parametrize("rows,allowed", [([service(status="blocked")], (81,)),
    ([service(status="suspended")], (81,)), ([service(status="prolong_wait")], (81,)),
    ([service(auto=True)], (81,)), ([service(days=31)], (81,)), ([service()], (81,81)),
    ([service()], (True,)), ([service()], (999,))])
def test_reject_disallowed_switches(rows, allowed):
    with pytest.raises(ValueError, match="service_not_allowed"):
        run(rows, allowed_enable_auto_prolong_ids=allowed)


def test_service_and_switch_bounds_and_overflow():
    with pytest.raises(ValueError, match="solver_service_limit"):
        run([service(i) for i in range(1,102)], allowed_enable_auto_prolong_ids=(), max_switches=0)
    with pytest.raises(ValueError, match="solver_switch_limit"):
        run([service(i) for i in range(1,10)], allowed_enable_auto_prolong_ids=range(1,10))
    with pytest.raises(ValueError, match="max_switches_out_of_range"):
        run(max_switches=2)
    with pytest.raises(ValueError, match="money_out_of_range"):
        run(account=account(MAX_MONEY_CENTS))


def test_unknown_customer_score_is_not_satisfied_and_modified_registry_is_rejected():
    result = run([service(status="blocked")], allowed_enable_auto_prolong_ids=(), max_switches=0)
    assert result.baseline.risk_score is None and result.status == "unreachable"
    definitions = tuple(DEFAULT_FEATURE_REGISTRY)
    # Same IDs and versions do not certify replacement semantics.
    changed = replace(definitions[0], evaluator=lambda service, context: None)
    with pytest.raises(ValueError, match="solver_unsupported_registry"):
        run(registry=FeatureRegistry((changed, *definitions[1:])))


@pytest.mark.parametrize("threshold", [Decimal("20"), Decimal("35"), Decimal("45"), Decimal("65")])
def test_small_search_matches_exhaustive_cent_and_subset_enumeration(threshold):
    rows = [service(81, 7, 2), service(82, 5, 8), service(83, 3, 15)]
    settings = ModelSettings()
    candidates = []
    for cents in range(16):
        for count in range(3):
            for switches in combinations((81,82,83), count):
                score = score_customer(account(cents),
                    [row.model_copy(update={"autoProlong": row.id in switches}) for row in rows],
                    as_of=NOW, settings=settings)
                if score.risk_score < threshold:
                    candidates.append((cents, count, switches))
    result = run(rows, goal={"type": "score_below", "threshold": threshold},
                 max_top_up_cents=15, allowed_enable_auto_prolong_ids=(83,81,82), max_switches=2)
    assert result == run(list(reversed(rows)), goal={"type": "score_below", "threshold": threshold},
                         max_top_up_cents=15, allowed_enable_auto_prolong_ids=(82,83,81), max_switches=2)
    if candidates:
        assert (result.top_up_cents, len(result.enable_auto_prolong_ids), result.enable_auto_prolong_ids) == min(candidates)
    else:
        assert result.status == "unreachable"


def test_maximum_search_bound_is_complete():
    rows = [service(i, price=10000001, days=0) for i in range(1,9)]
    result = run(rows, goal={"type": "score_below", "threshold": 1}, max_top_up_cents=10000000,
                 allowed_enable_auto_prolong_ids=range(1,9), max_switches=8)
    assert result.status == "unreachable"
    assert result.evaluations <= 6144
    assert len(result.certificate.subset_minima) == 256


def test_exactly_one_cent_at_horizon_inclusive_and_same_expiry_id_order():
    rows = [service(82, 1, 30), service(81, 1, 30), service(83, 100, 31)]
    result = run(rows, account=account(1), max_top_up_cents=1,
                 goal={"type": "cover_selected", "service_ids": [82]})
    assert (result.status, result.top_up_cents) == ("found", 1)
    assert result.result.due_services_price_cents == 2
    assert result.result.spendable_cents == 2
    assert result.certificate.one_cent_less_satisfies is False


@pytest.mark.parametrize("goal", [
    {"type": "cover_selected", "service_ids": []},
    {"type": "cover_selected", "service_ids": [81,81]},
    {"type": "cover_selected", "service_ids": [99]},
    {"type": "cover_selected", "service_ids": [81], "threshold": 35},
    {"type": "score_below", "threshold": True},
    {"type": "score_below", "threshold": Decimal("NaN")},
])
def test_invalid_or_mixed_goals_are_rejected(goal):
    with pytest.raises(ValueError):
        run(goal=goal)


def test_fewer_switches_win_equal_cent_candidates():
    settings = ModelSettings(weights={"renewal_proximity": 20, "manual_renewal": 0,
                                      "funding_gap": 35, "payment_wait": 20})
    result = run(settings=settings)
    assert (result.top_up_cents, result.enable_auto_prolong_ids) == (286, ())
    assert result.certificate.subset_minima[0].top_up_cents == result.certificate.subset_minima[1].top_up_cents
