"""Customer list and detail endpoints backed by one immutable evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.auth import AuthenticatedUser, require_session
from app.contracts import MAX_SOURCE_ID
from app.domain.scoring import CustomerScore, FeatureContribution, ServiceScore
from app.evaluation import Evaluation

router = APIRouter()


def _number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return _number(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _metadata(evaluation: Evaluation) -> dict[str, object]:
    return {
        "analysis_revision": evaluation.analysis_revision,
        "data_revision": evaluation.data_revision,
        "settings_revision": evaluation.settings_revision,
        "model_version": evaluation.model_version,
        "evaluated_at": evaluation.evaluated_at.isoformat().replace("+00:00", "Z"),
        "horizon_days": evaluation.horizon_days,
        "currency": "EUR",
    }


def _feature_payload(feature: FeatureContribution) -> dict[str, object]:
    return {
        "id": feature.id,
        "version": feature.version,
        "title": feature.title,
        "value": _number(feature.value),
        "weight": _number(feature.weight),
        "contribution": _number(feature.contribution),
        "available": feature.available,
        "explanation": feature.explanation,
        "evidence": _json_value(feature.evidence),
    }


def _service_score_payload(score: ServiceScore) -> dict[str, object]:
    return {
        "eligible": score.eligible,
        "in_horizon": score.in_horizon,
        "overdue": score.overdue,
        "excluded_reason": score.excluded_reason,
        "covered_cents": score.covered_cents,
        "funding_gap_cents": score.funding_gap_cents,
        "raw_score": _number(score.raw_score),
        "risk_score": _number(score.risk_score),
        "risk_level": score.risk_level,
        "score_clamped": score.score_clamped,
        "monetary_risk_cents": score.monetary_risk_cents,
        "features": [_feature_payload(feature) for feature in score.features],
        "top_reasons": [_feature_payload(feature) for feature in score.top_reasons],
        "unscored_reason": score.unscored_reason,
        "data_quality_warnings": list(score.data_quality_warnings),
    }


def _customer_item(account, score: CustomerScore) -> dict[str, object]:
    return {
        "id": str(account.id),
        "email": account.email,
        "balance_cents": account.balance,
        "total_replenished_cents": account.totalReplenished,
        "risk_score": _number(score.risk_score),
        "risk_level": score.risk_level,
        "monetary_risk_cents": score.monetary_risk_cents,
        "top_reasons": [feature.explanation for feature in score.top_reasons],
        "data_quality_warnings": list(score.data_quality_warnings),
    }


def _current_evaluation(request: Request) -> Evaluation:
    evaluation = request.app.state.coordinator.current
    if evaluation is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "evaluation_unavailable",
                "message": "No active source snapshot is available.",
            },
        )
    return evaluation


@router.get("/customers")
def list_customers(
    request: Request,
    sort_by: Literal["id", "email", "balance", "totalReplenished", "risk_score"] = "risk_score",
    sort_order: Literal["asc", "desc"] = "desc",
    risk_level: Literal["low", "medium", "high", "unknown"] | None = None,
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _user: AuthenticatedUser = Depends(require_session),
) -> dict[str, object]:
    # Keep one evaluation reference for the entire response. Its source and
    # domain scores are immutable and carry the same revision metadata.
    evaluation = _current_evaluation(request)
    snapshot = evaluation.snapshot
    accounts_by_id = {account.id: account for account in snapshot.accounts}
    rows = [
        (accounts_by_id[score.customer_id], score)
        for score in evaluation.customers
        if score.customer_id in accounts_by_id
    ]

    query = q.casefold()
    if risk_level is not None:
        rows = [(account, score) for account, score in rows if score.risk_level == risk_level]
    if query:
        rows = [
            (account, score)
            for account, score in rows
            if query in account.email.casefold() or q == str(account.id)
        ]

    # Evaluation customers are already in numeric ID order. Stable primary
    # sorting therefore leaves ID ASC as the tie-breaker in either direction.
    rows.sort(key=lambda item: item[0].id)
    descending = sort_order == "desc"
    if sort_by == "id":
        rows.sort(key=lambda item: item[0].id, reverse=descending)
    elif sort_by == "email":
        rows.sort(key=lambda item: item[0].email, reverse=descending)
    elif sort_by == "balance":
        rows.sort(key=lambda item: item[0].balance, reverse=descending)
    elif sort_by == "totalReplenished":
        rows.sort(key=lambda item: item[0].totalReplenished, reverse=descending)
    else:
        known = [(account, score) for account, score in rows if score.risk_score is not None]
        unknown = [(account, score) for account, score in rows if score.risk_score is None]
        known.sort(key=lambda item: item[1].risk_score, reverse=descending)
        rows = known + unknown

    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        **_metadata(evaluation),
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_customer_item(account, score) for account, score in page],
    }


def _account_payload(account) -> dict[str, object]:
    payload = _source_payload(account)
    payload["id"] = str(account.id)
    payload["balance_cents"] = account.balance
    payload["withdraw_balance_cents"] = account.withdrawBalance
    payload["total_replenished_cents"] = account.totalReplenished
    payload["bonus_balance_cents"] = account.bonusBalance
    return payload


def _service_payload(service, score: ServiceScore) -> dict[str, object]:
    payload = _source_payload(service)
    payload["id"] = str(service.id)
    payload["ownerId"] = str(service.ownerId)
    payload["price_cents"] = service.price
    payload.update(_service_score_payload(score))
    return payload


def _transaction_payload(transaction) -> dict[str, object]:
    payload = _source_payload(transaction)
    payload["id"] = str(transaction.id)
    payload["serviceId"] = str(transaction.serviceId)
    payload["amount_cents"] = transaction.amount
    return payload


def _source_payload(source) -> dict[str, object]:
    """Serialize canonical source fields without letting extras impersonate API fields."""
    payload = source.model_dump(mode="json")
    extras = source.model_extra or {}
    for key in extras:
        payload.pop(key, None)
    if extras:
        payload["source_extra"] = dict(extras)
    return payload


@router.get("/customers/{customer_id}")
def get_customer(
    request: Request,
    customer_id: int = Path(..., gt=0, le=MAX_SOURCE_ID),
    _user: AuthenticatedUser = Depends(require_session),
) -> dict[str, object]:
    evaluation = _current_evaluation(request)
    score = evaluation.customer(customer_id)
    if score is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "customer_not_found", "message": "Customer not found."},
        )

    snapshot = evaluation.snapshot
    account = next((row for row in snapshot.accounts if row.id == customer_id), None)
    if account is None:
        # Source and score are published together. Keep a defensive invariant
        # check rather than returning a stale card if that ever changes.
        raise HTTPException(
            status_code=404,
            detail={"code": "customer_not_found", "message": "Customer not found."},
        )
    scored_services = {service.service_id: service for service in score.services}
    account_services = [
        service
        for service in snapshot.services
        if service.ownerId == customer_id
    ]
    account_service_ids = {service.id for service in account_services}
    transactions = [
        transaction
        for transaction in snapshot.transactions
        if transaction.serviceId in account_service_ids
    ]
    transactions.sort(key=lambda row: (row.createdAt, row.id))

    payload: dict[str, object] = {
        **_metadata(evaluation),
        **_account_payload(account),
        "risk_score": _number(score.risk_score),
        "risk_level": score.risk_level,
        "monetary_risk_cents": score.monetary_risk_cents,
        "spendable_cents": score.spendable_cents,
        "due_services_price_cents": score.due_services_price_cents,
        "funding_gap_cents": score.funding_gap_cents,
        "unscored_reason": score.unscored_reason,
        "top_service_id": str(score.top_service_id) if score.top_service_id is not None else None,
        "top_reasons": [_feature_payload(feature) for feature in score.top_reasons],
        "data_quality_warnings": list(score.data_quality_warnings),
        "services": [
            _service_payload(service, scored_services[service.id])
            for service in sorted(account_services, key=lambda row: row.id)
        ],
        "transactions": [_transaction_payload(transaction) for transaction in transactions],
    }
    return payload
