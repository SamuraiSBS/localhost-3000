"""Deterministic contact candidates from one immutable working evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from app.evaluation import Evaluation


@dataclass(frozen=True)
class WorklistService:
    service_id: int
    expires_at: datetime
    overdue: bool
    status: str
    price_cents: int
    funding_gap_cents: int | None
    reason_codes: tuple[str, ...]
    suggested_actions: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class WorklistRow:
    customer_id: int
    risk_score: Decimal
    risk_level: str
    monetary_risk_cents: int | None
    due_services_price_cents: int
    funding_gap_cents: int
    affected_services: tuple[WorklistService, ...]
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def expires_at(self) -> datetime:
        return self.affected_services[0].expires_at


@dataclass(frozen=True)
class WorklistCoverage:
    total_customers: int
    scored_customers: int
    unknown_customers: int
    monetary_known_customers: int
    monetary_unknown_customers: int


@dataclass(frozen=True)
class WorklistResult:
    rows: tuple[WorklistRow, ...]
    matched_count: int
    returned_count: int
    coverage: WorklistCoverage
    excluded_reason_counts: Mapping[str, int]


def build_worklist(evaluation: Evaluation, *, limit: int = 10) -> WorklistResult:
    """Select Medium/High customers with a due gap or manual renewal.

    Dates, flags, money and scores come only from this baseline. Exclusions are
    mutually exclusive, in this order: no eligible service, no due service,
    unknown score, low score, no actionable reason. Counts cover the full source,
    including candidates beyond ``limit``. Missing money sorts after known zero
    and remains unknown in the returned row.
    """
    if not isinstance(evaluation, Evaluation):
        raise TypeError("evaluation_required")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("worklist_limit_out_of_range")

    sources = {service.id: service for service in evaluation.snapshot.services}
    excluded: Counter[str] = Counter()
    rows: list[WorklistRow] = []
    for customer in evaluation.customers:
        eligible = tuple(service for service in customer.services if service.eligible)
        due = tuple(service for service in eligible if service.in_horizon)
        if not eligible:
            excluded["no_eligible_service"] += 1
            continue
        if not due:
            excluded["no_in_horizon_service"] += 1
            continue
        if customer.risk_score is None or customer.risk_level == "unknown":
            excluded["unknown_score"] += 1
            continue
        if customer.risk_level not in {"medium", "high"}:
            excluded["low_risk"] += 1
            continue

        affected: list[WorklistService] = []
        for service in due:
            source = sources[service.service_id]
            reasons: list[str] = []
            actions: list[str] = []
            if service.funding_gap_cents is not None and service.funding_gap_cents > 0:
                reasons.append("funding_gap")
                actions.append("review_funding_gap")
            if not source.autoProlong:
                reasons.append("manual_renewal")
                actions.append("review_manual_renewal")
            if not reasons:
                continue
            warnings = service.data_quality_warnings
            if source.status == "prolong_wait":
                warnings = tuple(sorted(set(warnings) | {"prolong_wait"}))
            affected.append(WorklistService(
                service_id=service.service_id,
                expires_at=source.expiresAt,
                overdue=service.overdue,
                status=source.status,
                price_cents=source.price,
                funding_gap_cents=service.funding_gap_cents,
                reason_codes=tuple(reasons),
                suggested_actions=tuple(actions),
                warnings=warnings,
            ))
        if not affected:
            excluded["no_actionable_reason"] += 1
            continue
        affected.sort(key=lambda service: (service.expires_at, service.service_id))
        rows.append(WorklistRow(
            customer_id=customer.customer_id,
            risk_score=customer.risk_score,
            risk_level=customer.risk_level,
            monetary_risk_cents=customer.monetary_risk_cents,
            due_services_price_cents=customer.due_services_price_cents,
            funding_gap_cents=customer.funding_gap_cents,
            affected_services=tuple(affected),
            reason_codes=tuple(sorted({reason for service in affected
                                       for reason in service.reason_codes})),
            warnings=tuple(sorted(set(customer.data_quality_warnings)
                                  | {warning for service in affected
                                     for warning in service.warnings}
                                  | ({"prolong_wait"} if any(
                                      sources[service.service_id].status == "prolong_wait"
                                      for service in due) else set()))),
        ))
    rows.sort(key=lambda row: (
        row.expires_at,
        -(row.monetary_risk_cents if row.monetary_risk_cents is not None else -1),
        -row.risk_score,
        row.customer_id,
    ))
    customers = evaluation.customers
    unknown = sum(customer.risk_score is None for customer in customers)
    money_unknown = sum(customer.monetary_risk_cents is None for customer in customers)
    returned = tuple(rows[:limit])
    return WorklistResult(
        rows=returned,
        matched_count=len(rows),
        returned_count=len(returned),
        coverage=WorklistCoverage(
            total_customers=len(customers),
            scored_customers=len(customers) - unknown,
            unknown_customers=unknown,
            monetary_known_customers=len(customers) - money_unknown,
            monetary_unknown_customers=money_unknown,
        ),
        excluded_reason_counts=MappingProxyType(dict(sorted(excluded.items()))),
    )
