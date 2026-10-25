"""Independent deterministic perturbations of one captured evaluation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from app.contracts import ModelSettings, Thresholds
from app.domain.features import DEFAULT_FEATURE_REGISTRY, FeatureRegistry
from app.domain.scoring import CustomerScore, risk_level_for, score_customer
from app.evaluation import Evaluation


@dataclass(frozen=True)
class SensitivityCoverage:
    total_customers: int
    scored_customers: int
    unknown_customers: int


@dataclass(frozen=True)
class SensitivitySettings:
    model_version: str
    horizon_days: int
    weights: Mapping[str, Decimal]
    thresholds: Thresholds

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))


@dataclass(frozen=True)
class SensitivityBaseline:
    customer_id: int
    risk_score: Decimal | None
    risk_level: str
    rank: int | None
    top_k: int


@dataclass(frozen=True)
class SensitivityVariant:
    id: str
    parameter: str
    value: Decimal
    effective_settings: SensitivitySettings
    risk_score: Decimal | None
    risk_level: str
    rank: int | None
    top_k: int
    in_top_k: bool
    coverage: SensitivityCoverage


@dataclass(frozen=True)
class ExcludedCandidate:
    kind: str
    parameter: str
    value: Decimal
    reason: str


@dataclass(frozen=True)
class WeightSensitivity:
    variants: tuple[SensitivityVariant, ...]
    denominator: int
    score_min: Decimal | None
    score_max: Decimal | None
    rank_min: int | None
    rank_max: int | None
    top_k_inclusions: int
    empty: bool


@dataclass(frozen=True)
class ThresholdSensitivity:
    variants: tuple[SensitivityVariant, ...]
    denominator: int
    category_stable: int
    empty: bool


@dataclass(frozen=True)
class SensitivityResult:
    baseline: SensitivityBaseline
    weights: WeightSensitivity
    thresholds: ThresholdSensitivity
    coverage: SensitivityCoverage
    excluded_candidates: tuple[ExcludedCandidate, ...]


def _ranking(customers: tuple[CustomerScore, ...]) -> tuple[dict[int, int], SensitivityCoverage]:
    scored = sorted((row for row in customers if row.risk_score is not None),
                    key=lambda row: (-row.risk_score, row.customer_id))
    return ({row.customer_id: rank for rank, row in enumerate(scored, 1)},
            SensitivityCoverage(len(customers), len(scored), len(customers) - len(scored)))


def _canonical(settings: SensitivitySettings) -> tuple:
    # Decimal equality canonicalizes 0, 0.0, and differing exponent spellings.
    return (settings.model_version, settings.horizon_days,
            tuple(sorted(settings.weights.items())),
            settings.thresholds.medium, settings.thresholds.high)


def analyze_sensitivity(
    evaluation: Evaluation, customer_id: int, *,
    registry: FeatureRegistry = DEFAULT_FEATURE_REGISTRY,
) -> SensitivityResult:
    """Compare each weight +/-10% and threshold +/-5 independently.

    Candidates are deduplicated by complete effective settings before any
    scoring; the baseline is never a variant. More than 32 unique settings
    raises ``sensitivity_variant_limit`` without returning a truncated sample.
    Rankings exclude unknown scores and break ties by customer ID ascending.
    Empty variant sets have denominator zero and no score/rank range. Unknown
    target customers likewise have no numeric range, while remaining visible
    in every coverage count. All calculations use the captured source/clock.
    """
    if not isinstance(evaluation, Evaluation):
        raise TypeError("evaluation_required")
    if type(customer_id) is not int or customer_id <= 0:
        raise ValueError("customer_not_found")
    baseline_customer = evaluation.customer(customer_id)
    if baseline_customer is None:
        raise ValueError("customer_not_found")
    settings = evaluation.settings
    if not set(settings.weights) <= set(registry.ids):
        raise ValueError("settings_registry_mismatch")
    weights = {**registry.default_weights, **settings.weights}
    baseline_settings = SensitivitySettings(settings.model_version, settings.horizon_days,
                                            weights, settings.thresholds)
    baseline_key = _canonical(baseline_settings)
    seen = {baseline_key}
    excluded: list[ExcludedCandidate] = []
    candidates: list[tuple[str, str, str, Decimal, SensitivitySettings]] = []

    def add(kind: str, parameter: str, direction: str, value: Decimal,
            effective: SensitivitySettings) -> None:
        key = _canonical(effective)
        if key in seen:
            excluded.append(ExcludedCandidate(kind, parameter, value,
                            "unchanged_settings" if key == baseline_key else "duplicate_settings"))
            return
        seen.add(key)
        candidates.append((kind, parameter, direction, value, effective))

    for feature_id in sorted(weights):
        for direction, factor in (("minus", Decimal("0.9")), ("plus", Decimal("1.1"))):
            value = min(Decimal(100), max(Decimal(0), weights[feature_id] * factor))
            effective = SensitivitySettings(settings.model_version, settings.horizon_days,
                                           {**weights, feature_id: value}, settings.thresholds)
            add("weights", feature_id, direction, value, effective)
    for parameter in ("medium", "high"):
        for direction, delta in (("minus", Decimal(-5)), ("plus", Decimal(5))):
            value = getattr(settings.thresholds, parameter) + delta
            values = {**settings.thresholds.model_dump(), parameter: value}
            if not 0 < values["medium"] < values["high"] <= 100:
                excluded.append(ExcludedCandidate("thresholds", parameter, value,
                                                    "thresholds_must_be_ordered"))
                continue
            effective = SensitivitySettings(settings.model_version, settings.horizon_days,
                                           weights, Thresholds(**values))
            add("thresholds", parameter, direction, value, effective)
    if len(candidates) > 32:
        raise ValueError("sensitivity_variant_limit")

    baseline_ranks, coverage = _ranking(evaluation.customers)
    baseline_top_k = min(5, coverage.scored_customers)
    baseline = SensitivityBaseline(customer_id, baseline_customer.risk_score,
                                   baseline_customer.risk_level, baseline_ranks.get(customer_id),
                                   baseline_top_k)
    snapshot = evaluation.snapshot
    services: dict[int, list] = defaultdict(list)
    for service in snapshot.services:
        services[service.ownerId].append(service)
    weight_variants: list[SensitivityVariant] = []
    threshold_variants: list[SensitivityVariant] = []
    for kind, parameter, direction, value, effective in candidates:
        if kind == "weights":
            rows = tuple(score_customer(account, services.get(account.id, ()),
                                        as_of=evaluation.evaluated_at, settings=settings,
                                        registry=registry, weight_overrides=effective.weights)
                         for account in sorted(snapshot.accounts, key=lambda row: row.id))
            ranks, variant_coverage = _ranking(rows)
            selected = next(row for row in rows if row.customer_id == customer_id)
            score, level = selected.risk_score, selected.risk_level
            rank = ranks.get(customer_id)
        else:
            # A threshold perturbation cannot change score, ordering or coverage.
            configured = ModelSettings.model_validate({**settings.model_dump(),
                                                       "thresholds": effective.thresholds})
            score = baseline.risk_score
            level = risk_level_for(score, configured)
            rank, variant_coverage = baseline.rank, coverage
        top_k = min(5, variant_coverage.scored_customers)
        variant = SensitivityVariant(f"{kind}:{parameter}:{direction}", parameter, value,
                                     effective, score, level, rank, top_k,
                                     rank is not None and rank <= top_k, variant_coverage)
        (weight_variants if kind == "weights" else threshold_variants).append(variant)
    scores = [variant.risk_score for variant in weight_variants if variant.risk_score is not None]
    ranks = [variant.rank for variant in weight_variants if variant.rank is not None]
    return SensitivityResult(
        baseline=baseline,
        weights=WeightSensitivity(tuple(weight_variants), len(weight_variants),
                                  min(scores, default=None), max(scores, default=None),
                                  min(ranks, default=None), max(ranks, default=None),
                                  sum(variant.in_top_k for variant in weight_variants),
                                  not weight_variants),
        thresholds=ThresholdSensitivity(tuple(threshold_variants), len(threshold_variants),
                                        sum(variant.risk_level == baseline.risk_level
                                            for variant in threshold_variants),
                                        not threshold_variants),
        coverage=coverage, excluded_candidates=tuple(excluded),
    )
