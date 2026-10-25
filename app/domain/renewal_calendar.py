"""Bounded repeat-renewal scenario, separate from nearest-renewal metrics.

Calendar v1 uses UTC instants and an inclusive horizon of 1..90 days. The first
event is expiresAt, including an overdue expiry, with occurrence index zero.
Hours/weeks are fixed elapsed durations; months/quarters/half-years/years are
calendar increments from the ORIGINAL expiry, clamping its day to month end.
Thus January 31 -> February 28 -> March 31, retaining UTC time of day.

Every eligible service repeats at its unchanged integer-cent source price,
including manual renewal services. This is an explicit scenario assumption,
not a prediction of payments, cash flow, churn, or deletion. Missing/unsupported
periods never derive from price. Optional per-service periods are explicit user
assumptions. No income, price changes, or bonus unlocking is simulated.

The account budget comes from the existing nearest-renewal engine: balance plus
unlocked bonus only. Each event consumes min(price, remaining), including partial
coverage, in (due_at, service_id, occurrence_index) order. First funding gap is
reported as a scenario shortfall, never an actual service removal date. At most
1000 events are allocated; truncated totals describe only the emitted prefix.
Money is already integer cents, so no new conversion or rounding is needed.
"""

from __future__ import annotations

import calendar
import heapq
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Literal, Mapping

from app.contracts import AccountSource, PaymentTerm, ServiceSource
from app.domain.context import AccountContext, build_account_context
from app.domain.money import checked_cents, checked_cents_sum

MAX_EVENTS = 1000
CALENDAR_VERSION = "renewal-calendar-v1"
_MONTHS = {"month": 1, "quarter_year": 3, "half_year": 6, "year": 12}
_PERIODS = frozenset({"hour", "week", *_MONTHS})


@dataclass(frozen=True)
class RenewalEvent:
    service_id: int
    occurrence_index: int
    due_at: datetime
    price_cents: int
    covered_cents: int
    funding_gap_cents: int
    remaining_cents: int


@dataclass(frozen=True)
class RenewalExclusion:
    service_id: int
    reason: str


@dataclass(frozen=True)
class PeriodAssumption:
    service_id: int
    payment_term: PaymentTerm


@dataclass(frozen=True)
class RenewalCalendar:
    calendar_version: str
    status: Literal["ok", "insufficient_data"]
    baseline: AccountContext
    events: tuple[RenewalEvent, ...]
    exclusions: tuple[RenewalExclusion, ...]
    period_assumptions: tuple[PeriodAssumption, ...]
    assumptions: tuple[str, ...]
    max_events: int
    truncated: bool
    total_price_cents: int
    total_covered_cents: int
    funding_gap_cents: int
    remaining_cents: int
    first_shortfall_at: datetime | None


def _occurrence(anchor: datetime, period: str, index: int) -> datetime:
    if period == "hour":
        return anchor + timedelta(hours=index)
    if period == "week":
        return anchor + timedelta(weeks=index)
    absolute_month = anchor.year * 12 + anchor.month - 1 + _MONTHS[period] * index
    year, month_zero = divmod(absolute_month, 12)
    month = month_zero + 1
    return anchor.replace(
        year=year, month=month,
        day=min(anchor.day, calendar.monthrange(year, month)[1]),
    )


def build_renewal_calendar(
    account: AccountSource,
    services: Iterable[ServiceSource],
    *,
    as_of: datetime,
    horizon_days: int = 30,
    max_events: int = MAX_EVENTS,
    period_assumptions: Mapping[int, PaymentTerm] | None = None,
) -> RenewalCalendar:
    """Calculate a deterministic scenario without changing source/baseline data.

    Invalid horizon, event limits, owners or duplicate IDs raise ValueError as
    caller contract errors. Unsupported periods produce insufficient_data and
    an explicit exclusion; results for the remaining services are partial.
    Unknown assumption IDs are rejected rather than silently ignored.
    """
    if type(max_events) is not int or not 1 <= max_events <= MAX_EVENTS:
        raise ValueError("max_events_out_of_range")
    if as_of is None:
        raise ValueError("as_of_must_be_timezone_aware")
    source = tuple(services)
    baseline = build_account_context(account, source, as_of=as_of, horizon_days=horizon_days)
    overrides = dict(period_assumptions or {})
    if any(type(key) is not int for key in overrides) or not set(overrides) <= set(baseline.services):
        raise ValueError("unknown_period_assumption_service")
    exclusions: list[RenewalExclusion] = []
    explicit: list[PeriodAssumption] = []
    # Heap has at most one pending event per service; work is O(services + cap log services).
    pending: list[tuple[datetime, int, int]] = []
    scheduled: dict[int, tuple[ServiceSource, str]] = {}
    insufficient = False
    for service in sorted(source, key=lambda row: row.id):
        context = baseline.services[service.id]
        if not context.eligible:
            exclusions.append(RenewalExclusion(service.id, context.excluded_reason or "unsupported_status"))
            continue
        period = overrides.get(service.id, getattr(service, "paymentTerm", None))
        if not isinstance(period, str) or period not in _PERIODS:
            insufficient = True
            exclusions.append(RenewalExclusion(service.id, "insufficient_data:unsupported_payment_term"))
            continue
        if service.id in overrides:
            explicit.append(PeriodAssumption(service.id, period))
        if not context.in_horizon:
            exclusions.append(RenewalExclusion(service.id, "outside_horizon"))
            continue
        scheduled[service.id] = (service, period)
        heapq.heappush(pending, (service.expiresAt, service.id, 0))

    remaining = baseline.spendable_cents
    events: list[RenewalEvent] = []
    while pending and len(events) < max_events:
        due_at, service_id, index = heapq.heappop(pending)
        service, period = scheduled[service_id]
        price = checked_cents(service.price)
        covered = min(price, remaining)
        remaining -= covered
        events.append(RenewalEvent(service_id, index, due_at, price, covered, price - covered, remaining))
        try:
            next_due = _occurrence(service.expiresAt, period, index + 1)
        except (OverflowError, ValueError):
            # Beyond representable datetime means beyond this validated horizon.
            continue
        if next_due <= baseline.horizon_end:
            heapq.heappush(pending, (next_due, service_id, index + 1))

    return RenewalCalendar(
        calendar_version=CALENDAR_VERSION,
        status="insufficient_data" if insufficient else "ok",
        baseline=baseline,
        events=tuple(events),
        exclusions=tuple(exclusions),
        period_assumptions=tuple(explicit),
        assumptions=("constant_source_price", "repeat_including_manual_renewal",
                     "no_future_income_or_bonus_unlock", "overdue_expiry_is_first_event"),
        max_events=max_events,
        truncated=bool(pending),
        total_price_cents=checked_cents_sum(event.price_cents for event in events),
        total_covered_cents=checked_cents_sum(event.covered_cents for event in events),
        funding_gap_cents=checked_cents_sum(event.funding_gap_cents for event in events),
        remaining_cents=remaining,
        first_shortfall_at=next((event.due_at for event in events if event.funding_gap_cents), None),
    )
