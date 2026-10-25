"""Hand-calculated calendar scenarios; nearest-renewal remains independent."""

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import MAX_MONEY_CENTS, AccountSource, ServiceSource
from app.domain.context import build_account_context
from app.domain.renewal_calendar import MAX_EVENTS, build_renewal_calendar

AS_OF = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)


def account(**updates):
    data = dict(
        id=1, email="calendar@example.test", balance=1000, withdrawBalance=90000,
        totalReplenished=80000, bonusBalance=500, bonusState="locked",
    )
    data.update(updates)
    return AccountSource.model_validate(data)


def service(service_id=1, **updates):
    data = dict(id=service_id, ownerId=1, name="renewal", paymentTerm="month",
                autoProlong=False, createdAt="2020-01-01T00:00:00Z",
                expiresAt=AS_OF, status="active", price=300)
    data.update(updates)
    return ServiceSource.model_validate(data)


def run(services, source_account=None, **kwargs):
    return build_renewal_calendar(source_account or account(), services, as_of=AS_OF, **kwargs)


def test_anchored_month_end_two_repeats_and_baseline_unchanged():
    rows = [service()]
    before = rows[0].model_dump()
    result = run(rows, horizon_days=60)
    assert [event.due_at for event in result.events] == [
        AS_OF, AS_OF.replace(month=2, day=28), AS_OF.replace(month=3),
    ]
    assert [event.occurrence_index for event in result.events] == [0, 1, 2]
    assert result.total_price_cents == result.total_covered_cents == 900
    assert result.remaining_cents == 100
    assert result.first_shortfall_at is None
    assert result.baseline == build_account_context(account(), rows, as_of=AS_OF, horizon_days=60)
    assert result.baseline.due_services_price_cents == 300
    assert rows[0].model_dump() == before


def test_shared_budget_ties_and_cents_without_double_spend():
    rows = [service(2, paymentTerm="week", price=401), service(1, paymentTerm="week", price=400)]
    result = run(rows, horizon_days=7)
    assert [(e.service_id, e.occurrence_index) for e in result.events] == [(1, 0), (2, 0), (1, 1), (2, 1)]
    assert [e.covered_cents for e in result.events] == [400, 401, 199, 0]
    assert [e.funding_gap_cents for e in result.events] == [0, 0, 201, 401]
    assert result.total_price_cents == 1602
    assert result.total_covered_cents == 1000
    assert result.funding_gap_cents == 602
    assert result.remaining_cents == 0
    assert result.first_shortfall_at == AS_OF + timedelta(days=7)
    assert result == run(reversed(rows), horizon_days=7)


@pytest.mark.parametrize("state,covered,gap", [("locked", 1000, 200), ("unlocked", 1200, 0)])
def test_only_balance_and_unlocked_bonus_spendable(state, covered, gap):
    data = account().model_copy(update={"bonusState": state})
    result = run([service(price=600, paymentTerm="week")], data, horizon_days=7)
    assert result.total_covered_cents == covered
    assert result.funding_gap_cents == gap
    assert result.baseline.spendable_cents == (1500 if state == "unlocked" else 1000)


@pytest.mark.parametrize("status,reason", [("deleted", "status_deleted"), ("blocked", "status_blocked")])
def test_excluded_statuses_have_reasons(status, reason):
    result = run([service(status=status)])
    assert not result.events
    assert result.exclusions[0].reason == reason
    assert result.status == "ok"


@pytest.mark.parametrize("status", ["active", "suspended", "prolong_wait"])
def test_eligible_statuses_include_overdue_and_manual_services(status):
    expiry = AS_OF - timedelta(days=7)
    result = run([service(status=status, expiresAt=expiry, paymentTerm="week")], horizon_days=1)
    assert [e.due_at for e in result.events] == [expiry, AS_OF]
    assert "repeat_including_manual_renewal" in result.assumptions


@pytest.mark.parametrize("term", [None, "day", "", 3])
def test_unknown_period_is_insufficient_data_and_never_guessed(term):
    unknown = service().model_copy(update={"paymentTerm": term})
    result = run([unknown, service(2)], horizon_days=1)
    assert result.status == "insufficient_data"
    assert [e.service_id for e in result.events] == [2]
    assert result.exclusions[0].reason == "insufficient_data:unsupported_payment_term"
    assert result.baseline.due_services_price_cents == 600


def test_missing_period_and_explicit_assumption():
    unknown = service().model_copy()
    del unknown.__dict__["paymentTerm"]
    assert run([unknown]).status == "insufficient_data"
    result = run([unknown], horizon_days=7, period_assumptions={1: "week"})
    assert result.status == "ok"
    assert len(result.events) == 2
    assert result.period_assumptions[0].payment_term == "week"
    with pytest.raises(ValueError, match="unknown_period_assumption_service"):
        run([service()], period_assumptions={2: "week"})


def test_event_cap_and_horizon_are_explicit_inclusive_and_deterministic():
    rows = [service(2, paymentTerm="hour"), service(1, paymentTerm="hour")]
    result = run(rows, horizon_days=1, max_events=3)
    assert result.truncated
    assert result.max_events == 3
    assert [(e.due_at, e.service_id) for e in result.events] == [
        (AS_OF, 1), (AS_OF, 2), (AS_OF + timedelta(hours=1), 1),
    ]
    full = run([service(paymentTerm="hour", price=0)], horizon_days=1)
    assert len(full.events) == 25
    assert full.events[-1].due_at == full.baseline.horizon_end
    assert not full.truncated
    beyond = run([service(expiresAt=AS_OF + timedelta(days=2))], horizon_days=1)
    assert beyond.exclusions[0].reason == "outside_horizon"


@pytest.mark.parametrize("limit", [0, -1, MAX_EVENTS + 1, True, 1.5])
def test_invalid_event_limits_rejected(limit):
    with pytest.raises(ValueError, match="max_events_out_of_range"):
        run([], max_events=limit)


@pytest.mark.parametrize("horizon", [0, 91, True, 1.5])
def test_invalid_horizon_rejected(horizon):
    with pytest.raises(ValueError, match="horizon_days_out_of_range"):
        run([], horizon_days=horizon)


@pytest.mark.parametrize("term,first,next_due", [
    ("quarter_year", datetime(2026, 1, 31, 12, tzinfo=timezone.utc), datetime(2026, 4, 30, 12, tzinfo=timezone.utc)),
    ("half_year", datetime(2025, 8, 31, 12, tzinfo=timezone.utc), datetime(2026, 2, 28, 12, tzinfo=timezone.utc)),
    ("year", datetime(2024, 2, 29, 12, tzinfo=timezone.utc), datetime(2025, 2, 28, 12, tzinfo=timezone.utc)),
])
def test_long_period_calendar_and_leap_year(term, first, next_due):
    result = build_renewal_calendar(account(), [service(paymentTerm=term, expiresAt=first)],
                                    as_of=next_due, horizon_days=1)
    assert [e.due_at for e in result.events][:2] == [first, next_due]


def test_maximum_cap_and_no_false_shortfall_for_zero_price():
    result = run([service(paymentTerm="hour", price=0)], horizon_days=90)
    assert len(result.events) == MAX_EVENTS
    assert result.truncated
    assert result.first_shortfall_at is None


def test_total_money_overflow_uses_shared_money_contract():
    with pytest.raises(ValueError, match="money_out_of_range"):
        run([service(paymentTerm="week", price=MAX_MONEY_CENTS)], horizon_days=7)


@pytest.mark.parametrize("as_of", [None, datetime(2026, 1, 31)])
def test_explicit_aware_clock_required(as_of):
    with pytest.raises(ValueError, match="as_of_must_be_timezone_aware"):
        build_renewal_calendar(account(), [], as_of=as_of)


def test_source_timezone_normalized_before_calendar_arithmetic():
    plus_three = timezone(timedelta(hours=3))
    source = service(expiresAt=datetime(2026, 2, 1, 2, tzinfo=plus_three))
    result = run([source], horizon_days=60)
    assert result.events[0].due_at == datetime(2026, 1, 31, 23, tzinfo=timezone.utc)
    assert result.events[1].due_at == datetime(2026, 2, 28, 23, tzinfo=timezone.utc)
