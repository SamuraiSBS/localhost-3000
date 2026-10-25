"""Pure rules-v1 service and customer scores with model-derived explanations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from collections.abc import Iterable, Mapping

from app.contracts import AccountSource, ModelSettings, ServiceSource
from app.domain.context import AccountContext, build_account_context
from app.domain.features import (
    DEFAULT_FEATURE_REGISTRY,
    FeatureDefinition,
    FeatureRegistry,
    FeatureValue,
)
from app.domain.money import as_decimal, checked_cents_sum, risk_money_cents

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_SCORE_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class FeatureContribution:
    id: str
    version: str
    title: str
    value: Decimal | None
    weight: Decimal
    contribution: Decimal | None
    available: bool
    explanation: str
    evidence: Mapping[str, object]


@dataclass(frozen=True)
class ServiceScore:
    service_id: int
    eligible: bool
    in_horizon: bool
    overdue: bool
    excluded_reason: str | None
    covered_cents: int | None
    funding_gap_cents: int | None
    raw_score: Decimal | None
    risk_score: Decimal | None
    risk_level: str
    score_clamped: bool
    monetary_risk_cents: int | None
    features: tuple[FeatureContribution, ...]
    top_reasons: tuple[FeatureContribution, ...]
    unscored_reason: str | None
    data_quality_warnings: tuple[str, ...]


@dataclass(frozen=True)
class CustomerScore:
    customer_id: int
    risk_score: Decimal | None
    risk_level: str
    monetary_risk_cents: int | None
    spendable_cents: int
    due_services_price_cents: int
    funding_gap_cents: int
    services: tuple[ServiceScore, ...]
    top_service_id: int | None
    top_reasons: tuple[FeatureContribution, ...]
    unscored_reason: str | None
    data_quality_warnings: tuple[str, ...]


def risk_level_for(score: Decimal | int | None, settings: ModelSettings | None = None) -> str:
    """Map a measured score to the configured low/medium/high boundary."""
    if score is None:
        return "unknown"
    exact = as_decimal(score, field="score")
    if not _ZERO <= exact <= _HUNDRED:
        raise ValueError("score_out_of_range")
    configured = settings or ModelSettings()
    if exact < configured.thresholds.medium:
        return "low"
    if exact < configured.thresholds.high:
        return "medium"
    return "high"


def _effective_weights(
    registry: FeatureRegistry,
    settings: ModelSettings,
    overrides: Mapping[str, Decimal | int] | None,
) -> dict[str, Decimal]:
    weights = dict(registry.default_weights)
    for feature_id, value in settings.weights.items():
        if feature_id not in weights:
            raise ValueError(f"unknown_feature_weight:{feature_id}")
        weights[feature_id] = as_decimal(value, field=f"weight_{feature_id}")
    if overrides is not None:
        unknown = set(overrides) - set(weights)
        if unknown:
            raise ValueError(f"unknown_feature_weight:{sorted(unknown)[0]}")
        for feature_id, value in overrides.items():
            weights[feature_id] = as_decimal(value, field=f"weight_{feature_id}")
    for feature_id, weight in weights.items():
        if not _ZERO <= weight <= _HUNDRED:
            raise ValueError(f"weight_out_of_range:{feature_id}")
    if not any(weights.values()):
        raise ValueError("weights_cannot_all_be_zero")
    return weights


def _feature_contribution(
    feature_id: str,
    definition: FeatureDefinition,
    value: FeatureValue,
    weight: Decimal,
) -> FeatureContribution:
    contribution = value.value * weight if value.available and value.value is not None else None
    return FeatureContribution(
        id=feature_id,
        version=definition.version,
        title=definition.title,
        value=value.value,
        weight=weight,
        contribution=contribution,
        available=value.available,
        explanation=value.explanation,
        evidence=value.evidence,
    )


def _excluded_score(service: ServiceSource, state) -> ServiceScore:
    return ServiceScore(
        service_id=service.id,
        eligible=False,
        in_horizon=False,
        overdue=False,
        excluded_reason=state.excluded_reason,
        covered_cents=None,
        funding_gap_cents=None,
        raw_score=None,
        risk_score=None,
        risk_level="unknown",
        score_clamped=False,
        monetary_risk_cents=None,
        features=(),
        top_reasons=(),
        unscored_reason=state.excluded_reason,
        data_quality_warnings=state.data_quality_warnings,
    )


def _score_service(
    service: ServiceSource,
    context: AccountContext,
    *,
    settings: ModelSettings,
    registry: FeatureRegistry,
    weights: Mapping[str, Decimal],
) -> ServiceScore:
    state = context.services[service.id]
    if not state.eligible:
        return _excluded_score(service, state)

    contributions: list[FeatureContribution] = []
    for definition in registry:
        result = definition.evaluator(service, context)
        if not isinstance(result, FeatureValue):
            raise TypeError(f"feature_result_required:{definition.id}")
        contributions.append(
            _feature_contribution(definition.id, definition, result, weights[definition.id])
        )

    unavailable = tuple(
        feature.id
        for feature in contributions
        if not feature.available and feature.weight > _ZERO
    )
    if unavailable:
        reasons = tuple(sorted(
            (feature for feature in contributions
             if feature.available and feature.contribution is not None and feature.contribution > _ZERO),
            key=lambda feature: (-feature.contribution, feature.id),
        ))
        return ServiceScore(
            service_id=service.id,
            eligible=True,
            in_horizon=state.in_horizon,
            overdue=state.overdue,
            excluded_reason=None,
            covered_cents=state.covered_cents,
            funding_gap_cents=state.funding_gap_cents,
            raw_score=None,
            risk_score=None,
            risk_level="unknown",
            score_clamped=False,
            monetary_risk_cents=None,
            features=tuple(contributions),
            top_reasons=reasons[:3],
            unscored_reason=f"feature_unavailable:{','.join(unavailable)}",
            data_quality_warnings=state.data_quality_warnings,
        )

    raw_score = sum(
        (feature.contribution or _ZERO for feature in contributions), _ZERO
    )
    clamped_score = min(max(raw_score, _ZERO), _HUNDRED)
    score = clamped_score.quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)
    top_reasons = tuple(sorted(
        (feature for feature in contributions
         if feature.contribution is not None and feature.contribution > _ZERO),
        key=lambda feature: (-feature.contribution, feature.id),
    )[:3])
    money = risk_money_cents(service.price, score) if state.in_horizon else 0
    return ServiceScore(
        service_id=service.id,
        eligible=True,
        in_horizon=state.in_horizon,
        overdue=state.overdue,
        excluded_reason=None,
        covered_cents=state.covered_cents,
        funding_gap_cents=state.funding_gap_cents,
        raw_score=raw_score,
        risk_score=score,
        risk_level=risk_level_for(score, settings),
        score_clamped=raw_score > _HUNDRED,
        monetary_risk_cents=money,
        features=tuple(contributions),
        top_reasons=top_reasons,
        unscored_reason=None,
        data_quality_warnings=state.data_quality_warnings,
    )


def score_customer(
    account: AccountSource,
    services: Iterable[ServiceSource],
    *,
    as_of: datetime | None = None,
    settings: ModelSettings | None = None,
    weight_overrides: Mapping[str, Decimal | int] | None = None,
    registry: FeatureRegistry = DEFAULT_FEATURE_REGISTRY,
) -> CustomerScore:
    """Evaluate all services for one account with one fixed UTC clock.

    Transaction rows are deliberately not accepted: rules-v1 neither rebuilds
    balances nor infers renewals or churn history from the provided fixture.
    ``weight_overrides`` is primarily useful to evaluate custom registry entries;
    normal application calls pass the validated complete ``ModelSettings``.
    """
    configured = settings or ModelSettings()
    weights = _effective_weights(registry, configured, weight_overrides)
    source_services = tuple(services)
    context = build_account_context(
        account,
        source_services,
        as_of=as_of,
        horizon_days=configured.horizon_days,
    )
    service_scores = tuple(
        _score_service(
            service,
            context,
            settings=configured,
            registry=registry,
            weights=weights,
        )
        for service in sorted(source_services, key=lambda row: row.id)
    )

    assessed = tuple(service for service in service_scores if service.risk_score is not None)
    highest = max(assessed, key=lambda service: (service.risk_score, -service.service_id), default=None)
    if highest is None:
        risk_score = None
        risk_level = "unknown"
        top_service_id = None
        top_reasons: tuple[FeatureContribution, ...] = ()
        unscored_reason = (
            "no_eligible_services"
            if not any(service.eligible for service in service_scores)
            else "no_service_has_all_weighted_features"
        )
    else:
        risk_score = highest.risk_score
        risk_level = highest.risk_level
        top_service_id = highest.service_id
        top_reasons = highest.top_reasons
        unscored_reason = None

    due_service_scores = tuple(
        service
        for service in service_scores
        if context.services[service.service_id].in_horizon
    )
    if highest is None or any(service.monetary_risk_cents is None for service in due_service_scores):
        customer_money = None
    else:
        customer_money = checked_cents_sum(
            service.monetary_risk_cents
            for service in due_service_scores
            if service.monetary_risk_cents is not None
        )

    customer_warnings = list(context.data_quality_warnings)
    customer_warnings.extend(
        f"service_{service.service_id}_{service.unscored_reason}"
        for service in service_scores
        if service.unscored_reason is not None
        and service.unscored_reason.startswith("feature_unavailable:")
    )

    return CustomerScore(
        customer_id=account.id,
        risk_score=risk_score,
        risk_level=risk_level,
        monetary_risk_cents=customer_money,
        spendable_cents=context.spendable_cents,
        due_services_price_cents=context.due_services_price_cents,
        funding_gap_cents=context.funding_gap_cents,
        services=service_scores,
        top_service_id=top_service_id,
        top_reasons=top_reasons,
        unscored_reason=unscored_reason,
        data_quality_warnings=tuple(customer_warnings),
    )
