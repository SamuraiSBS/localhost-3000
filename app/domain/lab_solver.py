"""Pure, bounded exact actions for the audited rules-v1 feature semantics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import combinations
from typing import Literal

from app.contracts import MAX_MONEY_CENTS, AccountSource, ModelSettings, ServiceSource
from app.domain.context import build_account_context
from app.domain.features import DEFAULT_FEATURE_REGISTRY, FeatureRegistry
from app.domain.money import as_decimal
from app.domain.scoring import CustomerScore, score_customer

MAX_EVALUATIONS = 6144
# Count every source row, including future and excluded services, because each
# candidate rebuilds and scores the complete customer input.
MAX_SERVICE_EVALUATIONS = MAX_EVALUATIONS * 100


@dataclass(frozen=True)
class SubsetMinimum:
    enable_auto_prolong_ids: tuple[int, ...]
    top_up_cents: int | None


@dataclass(frozen=True)
class SolverCertificate:
    complete: bool
    subset_minima: tuple[SubsetMinimum, ...]
    one_cent_less_satisfies: bool | None
    one_cent_less_result: CustomerScore | None
    ordering: tuple[str, ...] = ("top_up_cents", "switch_count", "service_ids")


@dataclass(frozen=True)
class SolverResult:
    status: Literal["found", "already_satisfied", "unreachable", "search_limit"]
    top_up_cents: int | None
    enable_auto_prolong_ids: tuple[int, ...]
    baseline: CustomerScore
    result: CustomerScore | None
    evaluations: int
    certificate: SolverCertificate


class _SearchLimit(Exception):
    pass


def _ids(values: Iterable[int]) -> tuple[int, ...]:
    ids = tuple(values)
    if any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("service_not_allowed")
    return tuple(sorted(ids))


def solve(
    account: AccountSource,
    services: Iterable[ServiceSource],
    *,
    as_of: datetime,
    settings: ModelSettings,
    goal: Mapping[str, object],
    max_top_up_cents: int,
    allowed_enable_auto_prolong_ids: Iterable[int] = (),
    max_switches: int = 0,
    registry: FeatureRegistry = DEFAULT_FEATURE_REGISTRY,
    evaluation_limit: int = MAX_EVALUATIONS,
) -> SolverResult:
    """Minimize cents, switches, then ascending IDs without changing source data.

    Only the actual built-in evaluators are supported: matching feature names
    cannot certify a custom evaluator's monotonicity. In these evaluators a
    top-up decreases each funding gap; all other features stay fixed. Nonnegative
    weights, clamping, rounding, and the customer maximum preserve monotonicity.
    ``evaluations`` counts action candidates, excluding the single baseline.
    Candidate scoring visits at most MAX_SERVICE_EVALUATIONS source rows;
    the effective candidate allowance shrinks for customers with over 100 rows.
    """
    if tuple(registry) != tuple(DEFAULT_FEATURE_REGISTRY) or settings.model_version != "rules-v1":
        raise ValueError("solver_unsupported_registry")
    if type(max_top_up_cents) is not int or not 0 <= max_top_up_cents <= 10_000_000:
        raise ValueError("max_top_up_cents_out_of_range")
    if type(evaluation_limit) is not int or not 0 <= evaluation_limit <= MAX_EVALUATIONS:
        raise ValueError("evaluation_limit_out_of_range")
    source = tuple(services)
    candidate_limit = min(evaluation_limit, MAX_SERVICE_EVALUATIONS // max(1, len(source)))
    context = build_account_context(account, source, as_of=as_of, horizon_days=settings.horizon_days)
    if context.spendable_cents + max_top_up_cents > MAX_MONEY_CENTS:
        raise ValueError("money_out_of_range")
    due_ids = {key for key, state in context.services.items() if state.in_horizon}
    if len(due_ids) > 100:
        raise ValueError("solver_service_limit")
    allowed = _ids(allowed_enable_auto_prolong_ids)
    if len(allowed) > 8:
        raise ValueError("solver_switch_limit")
    if type(max_switches) is not int or not 0 <= max_switches <= len(allowed):
        raise ValueError("max_switches_out_of_range")
    permitted = {row.id for row in source if row.id in due_ids and row.status == "active" and not row.autoProlong}
    if not set(allowed) <= permitted:
        raise ValueError("service_not_allowed")
    if hasattr(goal, "model_dump"):
        goal = goal.model_dump()
    if not isinstance(goal, Mapping):
        raise ValueError("invalid_solver_goal")
    kind = goal.get("type")
    selected: tuple[int, ...] = ()
    threshold = Decimal(0)
    if kind == "cover_selected" and set(goal) == {"type", "service_ids"}:
        selected = _ids(goal["service_ids"])
        if not selected or not set(selected) <= due_ids:
            raise ValueError("service_not_allowed")
    elif kind == "score_below" and set(goal) == {"type", "threshold"}:
        threshold = as_decimal(goal["threshold"], field="threshold")
        if not 0 < threshold <= 100:
            raise ValueError("threshold_out_of_range")
    else:
        raise ValueError("invalid_solver_goal")

    def satisfied(score: CustomerScore) -> bool:
        if kind == "score_below":
            return score.risk_score is not None and score.risk_score < threshold
        gaps = {row.service_id: row.funding_gap_cents for row in score.services}
        return all(gaps[key] == 0 for key in selected)

    baseline = score_customer(account, source, as_of=context.as_of, settings=settings, registry=registry)
    if satisfied(baseline):
        return SolverResult("already_satisfied", 0, (), baseline, baseline, 0,
                            SolverCertificate(True, (), None, None))
    evaluations = 0
    minima: list[SubsetMinimum] = []
    cache: dict[tuple[int, tuple[int, ...]], CustomerScore] = {(0, ()): baseline}

    def evaluate(cents: int, switches: tuple[int, ...]) -> CustomerScore:
        nonlocal evaluations
        key = (cents, switches)
        if key in cache:
            return cache[key]
        if evaluations >= candidate_limit:
            raise _SearchLimit
        evaluations += 1
        funded = account.model_copy(update={"balance": account.balance + cents})
        rows = tuple(row.model_copy(update={"autoProlong": True}) if row.id in switches else row for row in source)
        score = score_customer(funded, rows, as_of=context.as_of, settings=settings, registry=registry)
        cache[key] = score
        return score

    best: tuple[int, int, tuple[int, ...]] | None = None
    best_result: CustomerScore | None = None
    best_previous: CustomerScore | None = None
    try:
        if kind == "cover_selected":
            # A switch cannot create funds; zero switches dominate all others.
            cumulative = required = 0
            for row in sorted((row for row in source if row.id in due_ids), key=lambda row: (row.expiresAt, row.id)):
                cumulative += row.price
                if row.id in selected and row.price:
                    required = max(required, cumulative)
            cents = max(0, required - context.spendable_cents)
            minima.append(SubsetMinimum((), cents if cents <= max_top_up_cents else None))
            if cents <= max_top_up_cents:
                best = (cents, 0, ())
                best_result = evaluate(cents, ())
                best_previous = evaluate(cents - 1, ()) if cents else None
        else:
            for count in range(max_switches + 1):
                for switches in combinations(allowed, count):
                    cache.clear()
                    cache[(0, ())] = baseline
                    # max+1 is an unevaluated unreachable sentinel. A lower-bound
                    # search takes <=24 evaluations for the supported cent cap.
                    low, high = 0, max_top_up_cents + 1
                    while low < high:
                        middle = (low + high) // 2
                        if satisfied(evaluate(middle, switches)):
                            high = middle
                        else:
                            low = middle + 1
                    cents = low if low <= max_top_up_cents else None
                    minima.append(SubsetMinimum(switches, cents))
                    if cents is not None:
                        candidate = (cents, count, switches)
                        if best is None or candidate < best:
                            best = candidate
                            best_result = cache[(cents, switches)]
                            best_previous = cache[(cents - 1, switches)] if cents else None
        if best is None:
            return SolverResult("unreachable", None, (), baseline, None, evaluations,
                                SolverCertificate(True, tuple(minima), None, None))
        cents, _, switches = best
        result = best_result
        previous = best_previous
        certificate = SolverCertificate(True, tuple(minima), satisfied(previous) if previous else None, previous)
        return SolverResult("found", cents, switches, baseline, result, evaluations, certificate)
    except _SearchLimit:
        return SolverResult("search_limit", None, (), baseline, None, evaluations,
                            SolverCertificate(False, tuple(minima), None, None))


solve_customer = solve
