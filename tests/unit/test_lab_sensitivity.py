"""L07 arithmetic, complete-snapshot ranking and perturbation boundaries."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.contracts import AccountSource, ModelSettings, ServiceSource, SourceSnapshot
from app.domain.features import DEFAULT_FEATURE_REGISTRY, FeatureDefinition, FeatureValue
from app.domain.lab_sensitivity import analyze_sensitivity
from app.evaluation import calculate_evaluation


AS_OF = datetime(2026, 10, 25, tzinfo=timezone.utc)


def account(customer_id, balance=1000):
    return AccountSource(id=customer_id, email=f"{customer_id}@example.test",
                         balance=balance, withdrawBalance=0, totalReplenished=0,
                         bonusBalance=0, bonusState="locked")


def service(customer_id, days=0, auto=True, status="active"):
    return ServiceSource(id=customer_id, ownerId=customer_id, name="Synthetic",
                         paymentTerm="month", autoProlong=auto, price=1000,
                         status=status, createdAt=AS_OF - timedelta(days=100),
                         expiresAt=AS_OF + timedelta(days=days))


def evaluate(accounts, services=(), settings=None):
    return calculate_evaluation(
        SourceSnapshot(accounts=tuple(accounts), services=tuple(services), transactions=()),
        settings or ModelSettings(), analysis_revision=12, data_revision=7,
        settings_revision=3, as_of=AS_OF,
    )


def l07():
    # A=(1,1,0,0), B=(.5,1,0,0), C=(0,1,.5,0),
    # D=(1,0,.5,0), E=(0,0,.625,0), F=(1,0,0,0).
    return evaluate(
        [account(1), account(2), account(3, 500), account(4, 500),
         account(5, 375), account(6)],
        [service(1, auto=False), service(2, 15, auto=False), service(3, 30, auto=False),
         service(4), service(5, 30), service(6)],
    )


def test_l07_eight_weight_and_four_threshold_variants_match_manual_fixture():
    evaluation = l07()
    expected_scores = [Decimal(value) for value in ("45", "35", "42.50", "37.50", "21.88", "20")]
    assert [row.risk_score for row in evaluation.customers] == expected_scores
    assert [row.customer_id for row in sorted(evaluation.customers,
            key=lambda row: (-row.risk_score, row.customer_id))] == [1, 3, 4, 2, 5, 6]
    original = (evaluation.snapshot_json, evaluation.settings_json, evaluation.customers,
                evaluation.analysis_revision, evaluation.data_revision, evaluation.settings_revision)
    for customer_id in range(1, 7):
        result = analyze_sensitivity(evaluation, customer_id)
        assert result == analyze_sensitivity(evaluation, customer_id)
        assert result.baseline.risk_score == expected_scores[customer_id - 1]
        assert result.baseline.top_k == 5
        assert result.weights.denominator == len(result.weights.variants) == 8
        assert result.weights.top_k_inclusions == {5: 6, 6: 2}.get(customer_id, 8)
        assert result.thresholds.denominator == len(result.thresholds.variants) == 4
        assert result.thresholds.category_stable == (3 if customer_id in (2, 4) else 4)
        assert not result.weights.empty and not result.thresholds.empty
        assert result.coverage.scored_customers == 6
        assert {v.value for v in result.thresholds.variants} == {30, 40, 60, 70}
    e_result = analyze_sensitivity(evaluation, 5)
    assert {v.id for v in e_result.weights.variants if not v.in_top_k} == {
        "weights:funding_gap:minus", "weights:renewal_proximity:plus",
    }
    assert (e_result.weights.rank_min, e_result.weights.rank_max) == (5, 6)
    assert (evaluation.snapshot_json, evaluation.settings_json, evaluation.customers,
            evaluation.analysis_revision, evaluation.data_revision, evaluation.settings_revision) == original


def test_each_variant_changes_only_one_parameter_from_baseline():
    evaluation = l07()
    result = analyze_sensitivity(evaluation, 1)
    weights = evaluation.settings.weights
    for variant in result.weights.variants:
        assert {key for key in weights if variant.effective_settings.weights[key] != weights[key]} == {
            variant.parameter,
        }
        assert variant.effective_settings.thresholds == evaluation.settings.thresholds
    for variant in result.thresholds.variants:
        assert variant.effective_settings.weights == weights
        assert variant.risk_score == result.baseline.risk_score
        assert variant.rank == result.baseline.rank
        assert sum(getattr(variant.effective_settings.thresholds, key)
                   != getattr(evaluation.settings.thresholds, key) for key in ("medium", "high")) == 1


def test_equal_scores_are_sorted_by_customer_id_and_small_top_k_uses_scored_only():
    evaluation = evaluate([account(8), account(2), account(10)], [service(8), service(2)])
    first, second = (analyze_sensitivity(evaluation, customer_id) for customer_id in (2, 8))
    assert first.baseline.rank == 1 and second.baseline.rank == 2
    assert first.baseline.top_k == 2
    assert first.weights.top_k_inclusions == second.weights.top_k_inclusions == 8
    assert all(v.rank == 1 and v.top_k == 2 for v in first.weights.variants)
    assert (first.coverage.total_customers, first.coverage.scored_customers,
            first.coverage.unknown_customers) == (3, 2, 1)


def test_unknown_customer_has_no_score_rank_or_inclusion_and_zero_top_k_when_all_unknown():
    result = analyze_sensitivity(evaluate([account(1)]), 1)
    assert result.coverage.scored_customers == 0
    assert result.coverage.unknown_customers == 1
    assert result.baseline.risk_score is None and result.baseline.rank is None
    assert result.baseline.risk_level == "unknown" and result.baseline.top_k == 0
    assert result.weights.score_min is result.weights.score_max is None
    assert result.weights.rank_min is result.weights.rank_max is None
    assert result.weights.top_k_inclusions == 0
    assert result.weights.denominator == 8  # actually enumerated model variants, no baseline
    assert result.thresholds.category_stable == result.thresholds.denominator == 4
    assert all(v.rank is None and v.top_k == 0 and not v.in_top_k for v in result.weights.variants)


def test_zero_weight_and_clamped_baseline_candidates_are_removed_canonically():
    weights = {**ModelSettings().weights, "payment_wait": Decimal(0), "manual_renewal": Decimal(100)}
    evaluation = evaluate([account(1)], [service(1)], ModelSettings(weights=weights))
    result = analyze_sensitivity(evaluation, 1)
    assert result.weights.denominator == 5
    assert [(e.parameter, e.value, e.reason) for e in result.excluded_candidates] == [
        ("manual_renewal", Decimal(100), "unchanged_settings"),
        ("payment_wait", Decimal(0), "unchanged_settings"),
        ("payment_wait", Decimal(0), "unchanged_settings"),
    ]
    settings_keys = [(tuple(v.effective_settings.weights.items()), v.effective_settings.thresholds)
                     for v in result.weights.variants + result.thresholds.variants]
    assert len(settings_keys) == len(set(settings_keys))


def test_invalid_threshold_candidates_are_reported_without_entering_denominator():
    settings = ModelSettings(thresholds={"medium": 1, "high": 2})
    result = analyze_sensitivity(evaluate([account(1)], [service(1)], settings), 1)
    assert result.thresholds.denominator == 1
    assert result.thresholds.variants[0].parameter == "high"
    assert result.thresholds.variants[0].value == 7
    invalid = [e for e in result.excluded_candidates if e.kind == "thresholds"]
    assert len(invalid) == 3
    assert {e.reason for e in invalid} == {"thresholds_must_be_ordered"}
    assert result.weights.denominator == 8


def extra_registry(count):
    registry = DEFAULT_FEATURE_REGISTRY
    for index in range(count):
        registry = registry.with_feature(FeatureDefinition(
            id=f"extra_{index}", version="1", title="Extra", description="Synthetic guard feature",
            default_weight=1, required_fields=(),
            evaluator=lambda service, context: FeatureValue(Decimal(0), True, "Known zero", {}),
        ))
    return registry


def test_unique_variant_guard_accepts_exactly_32_and_rejects_34_before_scoring(monkeypatch):
    evaluation = evaluate([account(1)], [service(1)])
    result = analyze_sensitivity(evaluation, 1, registry=extra_registry(10))
    assert result.weights.denominator + result.thresholds.denominator == 32

    def forbidden_score(*args, **kwargs):
        raise AssertionError("overflow must be rejected before candidate evaluation")

    monkeypatch.setattr("app.domain.lab_sensitivity.score_customer", forbidden_score)
    with pytest.raises(ValueError, match="^sensitivity_variant_limit$"):
        analyze_sensitivity(evaluation, 1, registry=extra_registry(11))


def test_frozen_result_and_nested_settings_cannot_mutate_captured_inputs():
    evaluation = l07()
    result = analyze_sensitivity(evaluation, 1)
    with pytest.raises(FrozenInstanceError):
        result.baseline.rank = 2
    with pytest.raises(TypeError):
        result.weights.variants[0].effective_settings.weights["funding_gap"] = Decimal(0)
    with pytest.raises(FrozenInstanceError):
        result.weights.denominator = 100
    assert evaluation.settings.weights == ModelSettings().weights


@pytest.mark.parametrize("customer_id", [0, -1, 999, True, "1"])
def test_missing_or_invalid_customer_rejected(customer_id):
    with pytest.raises(ValueError, match="^customer_not_found$"):
        analyze_sensitivity(l07(), customer_id)
