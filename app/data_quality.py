"""Pure quality facts about one captured evaluation, not model accuracy.

The caller must obtain ``received_at`` for the evaluation's data revision from
persistence. This module cannot verify that pairing or establish whether an
upstream snapshot omitted customers. Coverage describes only published customers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app.evaluation import Evaluation


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _coverage(numerator: int, denominator: int) -> dict[str, object]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "unknown": denominator - numerator,
        "state": "empty" if denominator == 0 else (
            "complete" if numerator == denominator else "partial"
        ),
    }


def data_quality_report(
    evaluation: Evaluation,
    *,
    received_at: datetime | None,
    now: datetime,
    freshness_threshold: timedelta,
) -> dict[str, object]:
    """Return independent JSON-ready facts without reading a clock or storage.

    ``freshness_threshold`` is an explicit nonnegative maximum receipt age;
    age exactly at the threshold is fresh. No product default is chosen here.
    All timestamps must be aware and are returned in UTC. Future receipt times
    yield ``future_timestamp`` with age unknown, never a fresh result. Receipt
    age measures time since import, not time since upstream collection. ``as_of``
    remains the evaluation instant; recalculation/polling cannot reset receipt.

    Coverage's denominator is every customer in this evaluation, including
    unknown customers, with no list filter or pagination. A known score of zero
    counts as evaluated. Unknown reasons are the existing customer-level domain
    reason (or explicit ``reason_unavailable``); they are not diagnoses of missing
    source rows. Warning counts count occurrences in the published customer
    warnings, including repeated codes across services; customer IDs are unique
    numeric domain IDs. Groups and IDs are sorted for deterministic output.

    The caller owns the returned mutable graph. Input evaluation and timestamps
    are untouched. Source completeness and statistical accuracy are not inferred.
    """
    if not isinstance(evaluation, Evaluation):
        raise TypeError("evaluation_required")
    if not isinstance(freshness_threshold, timedelta):
        raise TypeError("freshness_threshold_must_be_timedelta")
    if freshness_threshold < timedelta(0):
        raise ValueError("freshness_threshold_must_be_nonnegative")
    checked_at = _utc(now, "now")
    as_of = _utc(evaluation.evaluated_at, "as_of")
    received = _utc(received_at, "received_at") if received_at is not None else None
    age = checked_at - received if received is not None else None
    if age is None:
        freshness = "no_timestamp"
    elif age < timedelta(0):
        freshness = "future_timestamp"
    else:
        freshness = "fresh" if age <= freshness_threshold else "stale"

    unknown_ids: dict[str, list[int]] = defaultdict(list)
    warning_counts: Counter[str] = Counter()
    warning_ids: dict[str, set[int]] = defaultdict(set)
    scored = 0
    monetary_scored = 0
    for customer in sorted(evaluation.customers, key=lambda row: row.customer_id):
        if customer.risk_score is None:
            unknown_ids[customer.unscored_reason or "reason_unavailable"].append(customer.customer_id)
        else:
            scored += 1
        if customer.monetary_risk_cents is not None:
            monetary_scored += 1
        for warning in customer.data_quality_warnings:
            warning_counts[warning] += 1
            warning_ids[warning].add(customer.customer_id)

    denominator = len(evaluation.customers)
    return {
        "analysis_revision": evaluation.analysis_revision,
        "data_revision": evaluation.data_revision,
        "settings_revision": evaluation.settings_revision,
        "model_version": evaluation.model_version,
        "as_of": _timestamp(as_of),
        "received_at": _timestamp(received) if received is not None else None,
        "checked_at": _timestamp(checked_at),
        "freshness": {
            "state": freshness,
            "threshold_seconds": freshness_threshold.total_seconds(),
            "age_seconds": age.total_seconds() if age is not None and age >= timedelta(0) else None,
        },
        "score_coverage": _coverage(scored, denominator),
        "monetary_coverage": _coverage(monetary_scored, denominator),
        "unknown_reasons": [
            {"code": code, "count": len(ids), "customer_ids": list(ids)}
            for code, ids in sorted(unknown_ids.items())
        ],
        "field_warnings": [
            {"code": code, "count": warning_counts[code],
             "customer_count": len(warning_ids[code]), "customer_ids": sorted(warning_ids[code])}
            for code in sorted(warning_counts)
        ],
        "warning_count": sum(warning_counts.values()),
        "source_completeness": "not_established",
        "limitations": [
            "Coverage describes customers in this evaluation, not upstream source completeness.",
            "Receipt age does not establish the age of upstream observations.",
            "Quality facts do not measure statistical model accuracy.",
        ],
    }
