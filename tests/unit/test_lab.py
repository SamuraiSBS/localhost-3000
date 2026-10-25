"""Independent arithmetic fixtures L01-L06 and strict sandbox boundaries."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import AccountSource, MAX_MONEY_CENTS, ModelSettings, ServiceSource
from app.domain.features import DEFAULT_FEATURE_REGISTRY, FeatureDefinition, FeatureRegistry, FeatureValue
from app.domain.lab import apply_action, preview_customer
from app.domain.scoring import risk_level_for, score_customer
from app.lab_contracts import ActionInput, ModelOverrides, PreviewRequest

AS_OF = datetime(2026, 10, 25, tzinfo=timezone.utc)


def account(**changes):
    return AccountSource.model_validate({
        "id": 9001, "email": "synthetic@example.invalid", "balance": 1200,
        "bonusBalance": 500, "bonusState": "locked", "withdrawBalance": 9000,
        "totalReplenished": 25000, **changes,
    })


def service(service_id=101, days=5, **changes):
    return ServiceSource.model_validate({
        "id": service_id, "ownerId": 9001, "name": "Synthetic service",
        "price": 1000, "paymentTerm": "month", "status": "active", "autoProlong": True,
        "createdAt": AS_OF - timedelta(days=100), "expiresAt": AS_OF + timedelta(days=days),
        **changes,
    })


def basket():
    return account(), (service(), service(102, 10, autoProlong=False))


def preview(acct, services, **kwargs):
    return preview_customer(acct, services, settings=ModelSettings(), as_of=AS_OF, **kwargs)


def test_l01_baseline_matches_existing_engine_and_manual_arithmetic():
    acct, services = basket()
    result = preview(acct, reversed(services))
    baseline = result.baseline
    engine = score_customer(acct, services, settings=ModelSettings(), as_of=AS_OF)
    assert baseline.risk_score == engine.risk_score == Decimal("66.33")
    assert baseline.risk_level == "high"
    assert baseline.monetary_risk_cents == engine.monetary_risk_cents == 830
    assert baseline.top_service_id == 102
    assert (baseline.due_services_price_cents, baseline.covered_cents, baseline.funding_gap_cents) == (2000, 1200, 800)
    assert [s.covered_cents for s in baseline.services] == [1000, 200]
    assert [s.risk_score for s in baseline.services] == [Decimal("16.67"), Decimal("66.33")]
    assert baseline.services[1].features[2].contribution == 28
    assert result.model_scenario == baseline
    assert result.delta.risk_score == 0
    assert result.delta.features == ()
    assert result.as_of == AS_OF
    assert result.model_version == "rules-v1"
    assert (baseline.coverage_evaluated, baseline.coverage_unknown) == (2, 0)


def test_l02_top_up_allocated_once_without_mutating_input():
    acct, services = basket()
    original_account = acct.model_dump()
    original_services = [s.model_dump() for s in services]
    result = preview(acct, services, mode="action", action=ActionInput(top_up_cents=800))
    after = result.action_scenario
    assert after.risk_score == Decimal("38.33")
    assert after.monetary_risk_cents == 550
    assert (after.spendable_cents, after.due_services_price_cents, after.funding_gap_cents) == (2000, 2000, 0)
    assert [s.covered_cents for s in after.services] == [1000, 1000]
    assert result.delta.monetary_risk_cents == -280
    assert result.delta.risk_score == -28
    assert result.hypothetical is True
    assert [f.feature_id for f in result.delta.features] == ["funding_gap"]
    assert acct.model_dump() == original_account
    assert [s.model_dump() for s in services] == original_services
    assert preview(acct, services).baseline == result.baseline


def test_l03_auto_prolong_changes_only_manual_feature():
    acct, services = account(balance=1000), (service(102, 10, autoProlong=False),)
    result = preview(acct, services, mode="action", action=ActionInput(enable_auto_prolong_ids=[102]))
    assert result.baseline.risk_score == Decimal("38.33")
    assert result.action_scenario.risk_score == Decimal("13.33")
    assert result.action_scenario.monetary_risk_cents == 133
    assert result.action_scenario.funding_gap_cents == 0
    assert [f.feature_id for f in result.delta.features] == ["manual_renewal"]
    before = services[0].model_dump()
    after = result.action_scenario.services[0].input.model_dump()
    assert after == {**before, "autoProlong": True}


def test_l04_payment_wait_survives_top_up():
    result = preview(account(balance=0), (service(days=2, status="prolong_wait"),),
                     mode="action", action=ActionInput(top_up_cents=1000))
    assert result.baseline.risk_score == Decimal("73.67")
    assert result.action_scenario.risk_score == Decimal("38.67")
    assert result.action_scenario.services[0].input.status == "prolong_wait"
    assert result.action_scenario.services[0].features[3].contribution == 20
    assert result.action_scenario.funding_gap_cents == 0


def test_l05_locked_bonus_excluded_statuses_and_inclusive_horizon():
    acct = account(balance=0, bonusBalance=1000, withdrawBalance=1000, totalReplenished=5000)
    result = preview(acct, (service(days=30), service(102, status="blocked"), service(103, status="deleted")))
    baseline = result.baseline
    assert baseline.spendable_cents == 0
    assert baseline.due_services_price_cents == baseline.funding_gap_cents == 1000
    assert baseline.risk_score == Decimal("35.00")
    assert baseline.risk_level == "medium"
    for excluded in [s for s in baseline.services if s.service_id in (102, 103)]:
        assert excluded.risk_score is None
        assert excluded.risk_level == "unknown"
        assert excluded.monetary_risk_cents is None
        assert excluded.excluded_reason in ("status_blocked", "status_deleted")
    assert (baseline.coverage_evaluated, baseline.coverage_unknown) == (1, 2)


def test_l06_clamping_and_level_boundaries():
    result = preview(account(balance=0), (service(days=0, status="prolong_wait", autoProlong=False),),
                     settings_overrides=ModelOverrides(weights={name: 100 for name in ModelSettings().weights}))
    score = result.model_scenario.services[0]
    assert score.raw_score == 400
    assert score.risk_score == 100
    assert score.score_clamped is True
    assert score.monetary_risk_cents == 1000
    assert [risk_level_for(Decimal(x)) for x in ("34.99", "35", "64.99", "65")] == ["low", "medium", "medium", "high"]


@pytest.mark.parametrize("overrides", [
    {"weights": {name: 0 for name in ModelSettings().weights}},
    {"weights": {"funding_gap": -1}}, {"weights": {"funding_gap": 101}},
    {"weights": {"unknown": 20}}, {"thresholds": {"medium": 65}},
    {"thresholds": {"medium": 70, "high": 60}}, {"horizon_days": 5},
    {"weights": None}, {"weights": {"funding_gap": True}},
    {"weights": {"funding_gap": float("nan")}},
])
def test_l06_invalid_model_drafts(overrides):
    acct, services = basket()
    with pytest.raises(ValidationError):
        preview(acct, services, settings_overrides=ModelOverrides.model_validate(overrides))


@pytest.mark.parametrize("amount", [-1, 1.5, True, float("nan"), MAX_MONEY_CENTS + 1])
def test_action_cents_are_strict_and_bounded(amount):
    with pytest.raises(ValidationError):
        ActionInput(top_up_cents=amount)


@pytest.mark.parametrize("candidate", [
    service(102, 31, autoProlong=False), service(102, autoProlong=True),
    service(102, status="suspended", autoProlong=False),
    service(102, status="prolong_wait", autoProlong=False),
    service(102, status="blocked", autoProlong=False),
    service(102, status="deleted", autoProlong=False),
])
def test_action_rejects_ineligible_toggle(candidate):
    with pytest.raises(ValueError, match="service_not_allowed"):
        preview(account(), [candidate], mode="action", action=ActionInput(enable_auto_prolong_ids=[102]))


def test_action_unknown_duplicate_ids_and_overflow():
    with pytest.raises(ValidationError, match="service_not_allowed"):
        ActionInput(enable_auto_prolong_ids=[101, 101])
    with pytest.raises(ValueError, match="service_not_allowed"):
        preview(account(), [service()], mode="action", action=ActionInput(enable_auto_prolong_ids=[999]))
    with pytest.raises(ValueError, match="money_out_of_range"):
        preview(account(balance=MAX_MONEY_CENTS), [], mode="action", action=ActionInput(top_up_cents=1))
    with pytest.raises(ValueError, match="money_out_of_range"):
        apply_action(account(balance=MAX_MONEY_CENTS - 10, bonusBalance=10, bonusState="unlocked"), [],
                     ActionInput(top_up_cents=1), as_of=AS_OF, horizon_days=30)


def test_model_and_action_branches_cannot_be_combined_and_clock_is_required():
    acct, services = basket()
    with pytest.raises(ValueError, match="invalid_preview"):
        preview(acct, services, settings_overrides=ModelOverrides(), action=ActionInput())
    with pytest.raises(ValueError, match="as_of_required"):
        preview_customer(acct, services, settings=ModelSettings(), as_of=None)
    with pytest.raises(ValueError, match="as_of_must_be_timezone_aware"):
        preview_customer(acct, services, settings=ModelSettings(), as_of=AS_OF.replace(tzinfo=None))
    request = {"expected_revision": {"analysis_revision": "a-12", "data_revision": 7, "settings_revision": 3},
               "customer_id": 9001, "mode": "model", "settings_overrides": {}}
    assert PreviewRequest.model_validate(request).expected_revision.data_revision == 7
    with pytest.raises(ValidationError):
        PreviewRequest.model_validate({**request, "action": None})


def test_model_draft_l12_and_numeric_json_explanations():
    acct, services = basket()
    result = preview(acct, services, settings_overrides=ModelOverrides(weights={"manual_renewal": 50}))
    assert result.model_scenario.risk_score == Decimal("91.33")
    assert result.model_scenario.monetary_risk_cents == 1080
    assert result.baseline.risk_score == Decimal("66.33")
    payload = result.model_dump(mode="json")
    assert payload["baseline"]["risk_score"] == 66.33
    assert payload["delta"]["features"][0]["feature_id"] == "manual_renewal"
    assert payload["baseline"]["services"][0]["features"][0]["explanation"]


def test_weighted_unavailable_feature_stays_unknown():
    definitions = []
    for definition in DEFAULT_FEATURE_REGISTRY:
        if definition.id == "funding_gap":
            definition = FeatureDefinition(
                id=definition.id, version=definition.version, title=definition.title,
                description=definition.description, default_weight=definition.default_weight,
                required_fields=definition.required_fields,
                evaluator=lambda svc, ctx: FeatureValue(None, False, "Unavailable synthetic input", {}),
            )
        definitions.append(definition)
    registry = FeatureRegistry(definitions)
    result = preview_customer(account(), [service()], settings=ModelSettings(), as_of=AS_OF, registry=registry)
    assert result.baseline.risk_score is None
    assert result.baseline.monetary_risk_cents is None
    assert result.baseline.risk_level == "unknown"
    assert result.delta.risk_score is None
    assert result.baseline.coverage_unknown == 1
