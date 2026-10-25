"""Whole-snapshot analytics derived from one immutable evaluation."""

from __future__ import annotations

from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import AuthenticatedUser, require_session
from app.evaluation import Evaluation
from app.domain.churn_chart import renewal_chart

router = APIRouter()


@router.get("/analytics/churn-chart")
def analytics_churn_chart(
    request: Request,
    _user: AuthenticatedUser = Depends(require_session),
) -> dict[str, object]:
    """Current service risk and an explicitly conditional renewal scenario."""
    evaluation = request.app.state.coordinator.current
    if evaluation is None:
        raise HTTPException(status_code=503, detail={"code": "evaluation_unavailable", "message": "No active source snapshot is available."})
    return {**_metadata(evaluation), **renewal_chart(evaluation)}


def _metadata(evaluation: Evaluation) -> dict[str, object]:
    return {
        "analysis_revision": evaluation.analysis_revision,
        "data_revision": evaluation.data_revision,
        "settings_revision": evaluation.settings_revision,
        "model_version": evaluation.model_version,
        "evaluated_at": evaluation.evaluated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "horizon_days": evaluation.horizon_days,
        "currency": "EUR",
    }


@router.get("/analytics/summary")
def analytics_summary(
    request: Request,
    _user: AuthenticatedUser = Depends(require_session),
) -> dict[str, object]:
    # Read the published pointer once so all counts and amounts belong to the
    # same source/settings revision pair.
    evaluation = request.app.state.coordinator.current
    if evaluation is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "evaluation_unavailable",
                "message": "No active source snapshot is available.",
            },
        )

    evaluated_scores = [
        customer.risk_score
        for customer in evaluation.customers
        if customer.risk_score is not None
    ]
    mean_score = None
    if evaluated_scores:
        mean_score = int(
            (sum(evaluated_scores, Decimal(0)) / Decimal(len(evaluated_scores)))
            .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            * 100
        ) / 100

    return {
        **_metadata(evaluation),
        "customers_total": len(evaluation.customers),
        "customers_evaluated": evaluation.customers_evaluated,
        "risk_counts": dict(evaluation.risk_counts),
        "monetary_risk_cents": evaluation.monetary_risk_cents,
        "monetary_coverage": {
            "evaluated": evaluation.monetary_coverage_evaluated,
            "unknown": evaluation.monetary_coverage_unknown,
        },
        "due_services_price_cents": evaluation.due_services_price_cents,
        "funding_gap_cents": evaluation.funding_gap_cents,
        "data_quality_warnings": evaluation.data_quality_warnings,
        "mean_risk_score": mean_score,
    }
