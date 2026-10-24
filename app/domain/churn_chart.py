"""Snapshot facts and a declared renewal scenario, never inferred churn history."""

from datetime import timedelta

from app.evaluation import Evaluation


def renewal_chart(evaluation: Evaluation) -> dict[str, object]:
    """Count each source service once, using its already published service score.

    The scenario selects high-risk due services with an allocated funding gap.
    It assumes no new funds. Counts are not score-weighted probabilities and do
    not represent observed or expected cancellations.
    """
    as_of = evaluation.evaluated_at
    horizon_end = as_of + timedelta(days=evaluation.horizon_days)
    boundaries = sorted({min(days, evaluation.horizon_days) for days in (7, 14, evaluation.horizon_days)})
    buckets = [{"key": "overdue", "label": "Просрочено к дате расчёта", "start": None,
                "end": as_of.isoformat(), "high_risk_services": 0, "scenario_nonrenewals": 0}]
    start = as_of
    for days in boundaries:
        end = as_of + timedelta(days=days)
        buckets.append({"key": f"day-{days}", "label": f"До {days} дней" if start == as_of else f"{(start-as_of).days}–{days} дней",
                        "start": start.isoformat(), "end": end.isoformat(),
                        "high_risk_services": 0, "scenario_nonrenewals": 0})
        start = end
    counts = {level: 0 for level in ("low", "medium", "high", "unknown")}
    sources = {service.id: service for service in evaluation.snapshot.services}
    excluded = {"deleted": 0, "blocked": 0}
    outside = 0
    for customer in evaluation.customers:
        for score in customer.services:
            source = sources[score.service_id]
            if not score.eligible:
                excluded[source.status] += 1
                continue
            counts[score.risk_level] += 1
            if not score.in_horizon:
                outside += 1
                continue
            if score.risk_level != "high":
                continue
            bucket = buckets[0] if source.expiresAt < as_of else next(
                item for item in buckets[1:] if source.expiresAt <= as_of + timedelta(days=int(item["key"].split("-")[1]))
            )
            bucket["high_risk_services"] += 1
            if score.funding_gap_cents is not None and score.funding_gap_cents > 0:
                bucket["scenario_nonrenewals"] += 1
    return {
        "source": "active_snapshot_and_rules_v1_service_scores",
        "as_of": as_of.isoformat(), "horizon_end": horizon_end.isoformat(),
        "risk_counts": counts, "excluded_current_status_counts": excluded,
        "eligible_outside_horizon": outside,
        "scenario_rule": "high_risk_and_allocated_funding_gap_no_new_funds_v1",
        "scenario_assumption": "Высокий риск и нехватка распределённых средств; до продления новых средств не поступит. Это условный сценарий, не вероятность и не число фактических отказов.",
        "history": {"available": False, "reason": "В источнике нет дат событий удаления. Текущий статус deleted не является историей оттока."},
        "buckets": buckets,
    }
