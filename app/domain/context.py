"""Per-account clock, eligibility, warnings, and single-use funding allocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Iterable, Mapping

from app.contracts import AccountSource, ServiceSource, checked_money
from app.domain.money import checked_cents, checked_cents_sum

ELIGIBLE_STATUSES = frozenset({"active", "suspended", "prolong_wait"})
EXCLUDED_STATUS_REASONS = {
    "blocked": "status_blocked",
    "deleted": "status_deleted",
}
PROLONG_WAIT_DAYS = 5


def normalize_as_of(as_of: datetime | None) -> datetime:
    """Use one timezone-aware instant in UTC; if omitted, read the clock once."""
    if as_of is None:
        return datetime.now(timezone.utc)
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of_must_be_timezone_aware")
    return as_of.astimezone(timezone.utc)


def validate_horizon_days(horizon_days: int) -> int:
    if type(horizon_days) is not int or not 1 <= horizon_days <= 90:
        raise ValueError("horizon_days_out_of_range")
    return horizon_days


@dataclass(frozen=True)
class ServiceContext:
    """Derived information for one source service, never mutating its DTO."""

    service_id: int
    eligible: bool
    in_horizon: bool
    overdue: bool
    covered_cents: int | None
    funding_gap_cents: int | None
    excluded_reason: str | None
    data_quality_warnings: tuple[str, ...]


@dataclass(frozen=True)
class AccountContext:
    """Facts shared by all of an account's service features for one evaluation."""

    account_id: int
    as_of: datetime
    horizon_days: int
    horizon_end: datetime
    spendable_cents: int
    due_services_price_cents: int
    funding_gap_cents: int
    services: Mapping[int, ServiceContext]

    @property
    def data_quality_warnings(self) -> tuple[str, ...]:
        return tuple(
            warning
            for service_id in sorted(self.services)
            for warning in self.services[service_id].data_quality_warnings
        )


def _service_warnings(service: ServiceSource, as_of: datetime) -> tuple[str, ...]:
    stale_cutoff = as_of - timedelta(days=PROLONG_WAIT_DAYS)
    warnings: list[str] = []
    if service.status == "prolong_wait" and service.expiresAt < stale_cutoff:
        warnings.append("prolong_wait_older_than_five_days")
    if service.status == "active" and service.expiresAt < stale_cutoff:
        warnings.append("active_expired_over_five_days")
    return tuple(warnings)


def build_account_context(
    account: AccountSource,
    services: Iterable[ServiceSource],
    *,
    as_of: datetime | None = None,
    horizon_days: int = 30,
) -> AccountContext:
    """Allocate spendable funds once over due services ordered by expiry and ID.

    ``withdrawBalance`` and ``totalReplenished`` are intentionally not spendable.
    Only active, suspended, and prolong_wait services inside the inclusive UTC
    horizon enter the basket. Expired eligible services remain due.
    """
    if not isinstance(account, AccountSource):
        raise TypeError("account_source_required")
    fixed_as_of = normalize_as_of(as_of)
    horizon = validate_horizon_days(horizon_days)
    horizon_end = fixed_as_of + timedelta(days=horizon)

    source_services = tuple(services)
    if any(not isinstance(service, ServiceSource) for service in source_services):
        raise TypeError("service_source_required")
    if len({service.id for service in source_services}) != len(source_services):
        raise ValueError("duplicate_service_id")
    if any(service.ownerId != account.id for service in source_services):
        raise ValueError("service_owner_mismatch")

    spendable = checked_money(
        checked_cents(account.balance)
        + (checked_cents(account.bonusBalance) if account.bonusState == "unlocked" else 0)
    )

    service_contexts: dict[int, ServiceContext] = {}
    due: list[ServiceSource] = []
    for service in source_services:
        eligible = service.status in ELIGIBLE_STATUSES
        in_horizon = eligible and service.expiresAt <= horizon_end
        reason = EXCLUDED_STATUS_REASONS.get(service.status)
        service_contexts[service.id] = ServiceContext(
            service_id=service.id,
            eligible=eligible,
            in_horizon=in_horizon,
            overdue=eligible and service.expiresAt < fixed_as_of,
            covered_cents=None,
            funding_gap_cents=None,
            excluded_reason=reason,
            data_quality_warnings=_service_warnings(service, fixed_as_of),
        )
        if in_horizon:
            due.append(service)

    due.sort(key=lambda service: (service.expiresAt, service.id))
    remaining = spendable
    due_prices: list[int] = []
    due_gaps: list[int] = []
    for service in due:
        price = checked_cents(service.price)
        covered = min(price, remaining)
        gap = price - covered
        remaining -= covered
        due_prices.append(price)
        due_gaps.append(gap)
        service_contexts[service.id] = ServiceContext(
            service_id=service.id,
            eligible=True,
            in_horizon=True,
            overdue=service.expiresAt < fixed_as_of,
            covered_cents=covered,
            funding_gap_cents=gap,
            excluded_reason=None,
            data_quality_warnings=_service_warnings(service, fixed_as_of),
        )

    return AccountContext(
        account_id=account.id,
        as_of=fixed_as_of,
        horizon_days=horizon,
        horizon_end=horizon_end,
        spendable_cents=spendable,
        due_services_price_cents=checked_cents_sum(due_prices),
        funding_gap_cents=checked_cents_sum(due_gaps),
        services=MappingProxyType(service_contexts),
    )
