"""Pure model and hypothetical action previews over a caller-owned fixed clock."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from typing import Iterable, Literal

from app.contracts import AccountSource, ModelSettings, ServiceSource, SettingsPatch, checked_money
from app.domain.context import build_account_context, normalize_as_of
from app.domain.features import DEFAULT_FEATURE_REGISTRY, FeatureRegistry
from app.domain.scoring import score_customer
from app.lab_contracts import (
    ActionInput, CustomerPreview, FeatureDelta, ModelOverrides, PreviewDelta,
    PreviewResult, ServicePreview,
)


def _fixed_clock(as_of: datetime) -> datetime:
    if as_of is None:
        raise ValueError("as_of_required")
    return normalize_as_of(as_of)


def effective_model_settings(settings: ModelSettings, overrides: ModelOverrides) -> ModelSettings:
    """Merge partial known weights/thresholds and revalidate the complete model."""
    return SettingsPatch.model_validate(overrides.model_dump(exclude_unset=True)).apply(settings)


def apply_action(
    account: AccountSource, services: Iterable[ServiceSource], action: ActionInput,
    *, as_of: datetime, horizon_days: int,
) -> tuple[AccountSource, tuple[ServiceSource, ...]]:
    """Copy inputs; neither balances nor auto-prolong flags are ever persisted."""
    fixed_as_of = _fixed_clock(as_of)
    source_services = tuple(services)
    context = build_account_context(account, source_services, as_of=fixed_as_of, horizon_days=horizon_days)
    by_id = {service.id: service for service in source_services}
    for service_id in action.enable_auto_prolong_ids:
        service = by_id.get(service_id)
        if (service is None or service.status != "active" or service.autoProlong
                or not context.services[service_id].in_horizon):
            raise ValueError("service_not_allowed")
    values = account.model_dump()
    values["balance"] = checked_money(account.balance + action.top_up_cents)
    copied_account = AccountSource.model_validate(values)
    enabled = set(action.enable_auto_prolong_ids)
    copied_services = tuple(
        ServiceSource.model_validate({**service.model_dump(), "autoProlong": True})
        if service.id in enabled else service.model_copy(deep=True)
        for service in source_services
    )
    return copied_account, copied_services


def _result(account, services, *, settings, as_of, registry) -> CustomerPreview:
    score = score_customer(account, services, settings=settings, as_of=as_of, registry=registry)
    inputs = {service.id: service for service in services}
    service_results = tuple(
        ServicePreview.model_validate({
            **{field.name: getattr(service, field.name) for field in fields(service)},
            "input": inputs[service.service_id].model_copy(deep=True),
        })
        for service in sorted(score.services, key=lambda item: (inputs[item.service_id].expiresAt, item.service_id))
    )
    return CustomerPreview.model_validate({
        **{field.name: getattr(score, field.name) for field in fields(score) if field.name != "services"},
        "input": account.model_copy(deep=True),
        "services": service_results,
        "covered_cents": score.due_services_price_cents - score.funding_gap_cents,
        "coverage_evaluated": sum(service.risk_score is not None for service in score.services),
        "coverage_unknown": sum(service.risk_score is None for service in score.services),
    })


def preview_customer(
    account: AccountSource, services: Iterable[ServiceSource], *,
    settings: ModelSettings, as_of: datetime, mode: Literal["model", "action"] = "model",
    settings_overrides: ModelOverrides | None = None, action: ActionInput | None = None,
    registry: FeatureRegistry = DEFAULT_FEATURE_REGISTRY,
) -> PreviewResult:
    """Compare independently against the original basket; never chain scenarios.

    API code owns revision checks and adds the captured Evaluation's revision and
    checksum envelope. This domain operation deliberately accepts no coordinator.
    """
    fixed_as_of = _fixed_clock(as_of)
    source_services = tuple(services)
    baseline = _result(account, source_services, settings=settings, as_of=fixed_as_of, registry=registry)
    if mode == "model" and action is None:
        overrides = settings_overrides if settings_overrides is not None else ModelOverrides()
        effective = effective_model_settings(settings, overrides)
        scenario = _result(account, source_services, settings=effective, as_of=fixed_as_of, registry=registry)
    elif mode == "action" and action is not None and settings_overrides is None:
        overrides = None
        effective = settings.model_copy(deep=True)
        copied_account, copied_services = apply_action(
            account, source_services, action, as_of=fixed_as_of, horizon_days=settings.horizon_days,
        )
        scenario = _result(copied_account, copied_services, settings=effective, as_of=fixed_as_of, registry=registry)
    else:
        raise ValueError("invalid_preview_mode_or_branches")
    changed_features = []
    before_services = {service.service_id: service for service in baseline.services}
    for service in scenario.services:
        before = {feature.id: feature for feature in before_services[service.service_id].features}
        for feature in service.features:
            previous = before[feature.id]
            if (previous.value, previous.weight, previous.contribution) != (feature.value, feature.weight, feature.contribution):
                changed_features.append(FeatureDelta(
                    service_id=service.service_id, feature_id=feature.id,
                    baseline_value=previous.value, scenario_value=feature.value,
                    baseline_weight=previous.weight, scenario_weight=feature.weight,
                    baseline_contribution=previous.contribution, scenario_contribution=feature.contribution,
                    explanation=feature.explanation,
                ))
    return PreviewResult(
        mode=mode, hypothetical=mode == "action", as_of=fixed_as_of,
        horizon_days=settings.horizon_days, model_version=settings.model_version,
        baseline=baseline, model_scenario=scenario if mode == "model" else None,
        action_scenario=scenario if mode == "action" else None, effective_settings=effective,
        effective_overrides=overrides.model_copy(deep=True) if overrides is not None else None,
        effective_action=action.model_copy(deep=True) if action is not None else None,
        delta=PreviewDelta(
            risk_score=(scenario.risk_score - baseline.risk_score
                        if scenario.risk_score is not None and baseline.risk_score is not None else None),
            monetary_risk_cents=(scenario.monetary_risk_cents - baseline.monetary_risk_cents
                                 if scenario.monetary_risk_cents is not None and baseline.monetary_risk_cents is not None else None),
            funding_gap_cents=scenario.funding_gap_cents - baseline.funding_gap_cents,
            category_changed=scenario.risk_level != baseline.risk_level,
            features=tuple(changed_features), remaining_reasons=scenario.top_reasons,
        ),
    )
