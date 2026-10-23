"""Hand-calculated rules-v1 examples and finance/status edge cases."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import (
    MAX_MONEY_CENTS,
    AccountSource,
    ModelSettings,
    ServiceSource,
)
from app.domain.context import build_account_context
from app.domain.features import (
    DEFAULT_FEATURE_REGISTRY,
    FeatureDefinition,
    FeatureRegistry,
    FeatureValue,
)
from app.domain.money import checked_cents, checked_cents_sum, risk_money_cents
from app.domain.scoring import risk_level_for, score_customer

AS_OF = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def make_account(
    *,
    balance: int = 0,
    withdraw_balance: int = 0,
    total_replenished: int = 0,
    bonus_balance: int = 0,
    bonus_state: str = "locked",
    account_id: int = 1001,
) -> AccountSource:
    return AccountSource.model_validate(
        {
            "id": account_id,
            "email": "demo@example.test",
            "balance": balance,
            "withdrawBalance": withdraw_balance,
            "totalReplenished": total_replenished,
            "bonusBalance": bonus_balance,
            "bonusState": bonus_state,
        }
    )


def make_service(
    *,
    service_id: int = 2001,
    owner_id: int = 1001,
    expires_at: datetime = AS_OF,
    status: str = "active",
    price: int = 1000,
    auto_prolong: bool = True,
    name: str = "example service",
) -> ServiceSource:
    return ServiceSource.model_validate(
        {
            "id": service_id,
            "name": name,
            "ownerId": owner_id,
            "paymentTerm": "month",
            "autoProlong": auto_prolong,
            "createdAt": "2026-01-01T00:00:00Z",
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            "status": status,
            "price": price,
        }
    )


def run_case(account: AccountSource, services, settings: ModelSettings | None = None):
    return score_customer(account, services, as_of=AS_OF, settings=settings)


@pytest.mark.parametrize(
    "case,account_kwargs,service_kwargs,settings,expected_score,expected_level,expected_money",
    [
        ("M01", {"balance": 1000}, {"auto_prolong": True}, None, "20", "low", 200),
        ("M02", {"balance": 1000}, {"auto_prolong": False}, None, "45", "medium", 450),
        ("M03", {"balance": 0}, {"auto_prolong": False}, None, "80", "high", 800),
        (
            "M04",
            {"balance": 0},
            {"auto_prolong": True, "status": "prolong_wait"},
            None,
            "75",
            "high",
            750,
        ),
        (
            "M05",
            {"balance": 1000},
            {"auto_prolong": False},
            ModelSettings(weights={
                "renewal_proximity": 20,
                "manual_renewal": 50,
                "funding_gap": 35,
                "payment_wait": 20,
            }),
            "70",
            "high",
            700,
        ),
        (
            "M06",
            {"balance": 0, "bonus_balance": 1000, "bonus_state": "locked"},
            {"auto_prolong": True},
            None,
            "55",
            "medium",
            550,
        ),
        (
            "M07",
            {"balance": 0, "bonus_balance": 1000, "bonus_state": "unlocked"},
            {"auto_prolong": True},
            None,
            "20",
            "low",
            200,
        ),
        (
            "M08",
            {"balance": 0, "withdraw_balance": 1000},
            {"auto_prolong": False},
            None,
            "80",
            "high",
            800,
        ),
        (
            "M11",
            {"balance": 500},
            {"auto_prolong": False, "expires_at": AS_OF + timedelta(days=15)},
            None,
            "52.50",
            "medium",
            525,
        ),
        (
            "M12",
            {"balance": 0},
            {"auto_prolong": False, "expires_at": AS_OF + timedelta(days=31)},
            None,
            "0",
            "low",
            0,
        ),
    ],
)
def test_manual_model_examples(
    case,
    account_kwargs,
    service_kwargs,
    settings,
    expected_score,
    expected_level,
    expected_money,
):
    result = run_case(make_account(**account_kwargs), [make_service(**service_kwargs)], settings)

    assert result.risk_score == Decimal(expected_score), case
    assert result.risk_level == expected_level, case
    assert result.monetary_risk_cents == expected_money, case


def test_m09_unknown_is_distinct_from_measured_zero():
    account = make_account()
    no_services = run_case(account, [])
    excluded_services = run_case(
        account,
        [make_service(service_id=1, status="blocked"), make_service(service_id=2, status="deleted")],
    )
    measured_zero = run_case(
        account,
        [make_service(expires_at=AS_OF + timedelta(days=31), auto_prolong=True)],
    )

    assert (no_services.risk_score, no_services.risk_level, no_services.monetary_risk_cents) == (
        None,
        "unknown",
        None,
    )
    assert (excluded_services.risk_score, excluded_services.risk_level) == (None, "unknown")
    assert all(not service.eligible and service.monetary_risk_cents is None
               for service in excluded_services.services)
    assert (measured_zero.risk_score, measured_zero.risk_level, measured_zero.monetary_risk_cents) == (
        Decimal("0.00"),
        "low",
        0,
    )


def test_m10_shared_balance_is_allocated_once_by_expiry_then_id():
    account = make_account(balance=1000)
    same_expiry_larger_id_first_in_input = make_service(service_id=8, auto_prolong=False)
    smaller_id_second_in_input = make_service(service_id=3, auto_prolong=False)

    result = run_case(account, [same_expiry_larger_id_first_in_input, smaller_id_second_in_input])
    by_id = {service.service_id: service for service in result.services}

    assert by_id[3].covered_cents == 1000
    assert by_id[3].funding_gap_cents == 0
    assert by_id[3].risk_score == Decimal("45.00")
    assert by_id[3].monetary_risk_cents == 450
    assert by_id[8].covered_cents == 0
    assert by_id[8].funding_gap_cents == 1000
    assert by_id[8].risk_score == Decimal("80.00")
    assert by_id[8].monetary_risk_cents == 800
    assert result.risk_score == Decimal("80.00")
    assert result.monetary_risk_cents == 1250
    assert result.due_services_price_cents == 2000
    assert result.funding_gap_cents == 1000


def test_expiry_sort_precedes_id_sort():
    account = make_account(balance=600)
    later = make_service(service_id=1, expires_at=AS_OF + timedelta(days=5))
    sooner = make_service(service_id=99, expires_at=AS_OF - timedelta(days=1))

    context = build_account_context(account, [later, sooner], as_of=AS_OF)

    assert context.services[99].covered_cents == 600
    assert context.services[99].overdue is True
    assert context.services[1].covered_cents == 0


@pytest.mark.parametrize("status", ["active", "suspended", "prolong_wait"])
def test_eligible_statuses_are_scored(status):
    result = run_case(make_account(balance=1000), [make_service(status=status)])
    assert result.risk_score is not None
    assert result.services[0].eligible is True


def test_stale_wait_warns_without_changing_source_status_or_score_inputs():
    stale = make_service(status="prolong_wait", expires_at=AS_OF - timedelta(days=6))
    result = run_case(make_account(balance=0), [stale])

    assert stale.status == "prolong_wait"
    assert "prolong_wait_older_than_five_days" in result.data_quality_warnings
    assert result.services[0].risk_score == Decimal("75.00")


def test_active_long_expiry_is_warning_and_still_in_due_basket():
    service = make_service(expires_at=AS_OF - timedelta(days=6))
    result = run_case(make_account(balance=0), [service])

    assert service.status == "active"
    assert "active_expired_over_five_days" in result.data_quality_warnings
    assert result.services[0].in_horizon is True
    assert result.services[0].overdue is True
    assert result.due_services_price_cents == 1000


def test_unlocking_bonus_changes_only_spendable_funds_and_total_replenished_is_not_added():
    locked = run_case(make_account(balance=0, bonus_balance=900, bonus_state="locked"), [make_service()])
    unlocked = run_case(make_account(balance=0, bonus_balance=900, bonus_state="unlocked"), [make_service()])
    historical = run_case(
        make_account(balance=0, total_replenished=MAX_MONEY_CENTS), [make_service()]
    )

    assert locked.spendable_cents == 0
    assert unlocked.spendable_cents == 900
    assert unlocked.services[0].funding_gap_cents == 100
    assert historical.services[0].funding_gap_cents == 1000


@pytest.mark.parametrize(
    "score,expected",
    [
        (Decimal("34.99"), "low"),
        (Decimal("35"), "medium"),
        (Decimal("64.99"), "medium"),
        (Decimal("65"), "high"),
    ],
)
def test_risk_level_threshold_edges(score, expected):
    assert risk_level_for(score) == expected


def test_weights_are_validated_and_total_score_is_clamped():
    with pytest.raises(ValidationError, match="weights_cannot_all_be_zero"):
        ModelSettings(weights={key: 0 for key in DEFAULT_FEATURE_REGISTRY.ids})

    settings = ModelSettings(weights={
        "renewal_proximity": 100,
        "manual_renewal": 100,
        "funding_gap": 100,
        "payment_wait": 100,
    })
    result = run_case(
        make_account(balance=0),
        [make_service(auto_prolong=False, status="prolong_wait")],
        settings,
    )
    assert result.services[0].raw_score == Decimal("400")
    assert result.services[0].risk_score == Decimal("100.00")
    assert result.services[0].score_clamped is True
    assert result.services[0].monetary_risk_cents == 1000


def test_decimal_rounding_is_half_up_per_service():
    assert risk_money_cents(1, Decimal("50")) == 1
    assert risk_money_cents(3, Decimal("50")) == 2
    assert risk_money_cents(1, Decimal("49.99")) == 0
    assert risk_money_cents(MAX_MONEY_CENTS, Decimal("100")) == MAX_MONEY_CENTS


@pytest.mark.parametrize("value", [True, -1, MAX_MONEY_CENTS + 1, 1.0])
def test_money_helpers_reject_bool_float_negative_and_unsafe_amounts(value):
    with pytest.raises(ValueError):
        checked_cents(value)


def test_money_sum_overflow_fails_closed():
    with pytest.raises(ValueError, match="money_out_of_range"):
        checked_cents_sum([MAX_MONEY_CENTS, 1])


def test_due_price_sum_overflow_fails_closed_before_returning_context():
    account = make_account()
    with pytest.raises(ValueError, match="money_out_of_range"):
        build_account_context(
            account,
            [make_service(service_id=1, price=MAX_MONEY_CENTS),
             make_service(service_id=2, price=MAX_MONEY_CENTS)],
            as_of=AS_OF,
        )


def test_spendable_sum_overflow_is_checked_even_if_dto_validation_was_bypassed():
    account = AccountSource.model_construct(
        id=1001,
        email="demo@example.test",
        balance=MAX_MONEY_CENTS,
        withdrawBalance=0,
        totalReplenished=0,
        bonusBalance=1,
        bonusState="unlocked",
    )
    with pytest.raises(ValueError, match="money_out_of_range"):
        build_account_context(account, [], as_of=AS_OF)


def test_price_zero_has_zero_gap_and_no_divide_by_zero():
    result = run_case(make_account(), [make_service(price=0, auto_prolong=False)])
    assert result.services[0].funding_gap_cents == 0
    assert result.services[0].risk_score == Decimal("45.00")
    assert result.services[0].monetary_risk_cents == 0


def test_horizon_is_inclusive_and_uses_one_utc_instant():
    service = make_service(expires_at=AS_OF + timedelta(days=30), auto_prolong=True)
    utc_result = run_case(make_account(balance=1000), [service])
    offset_as_of = AS_OF.astimezone(timezone(timedelta(hours=3)))
    offset_result = score_customer(make_account(balance=1000), [service], as_of=offset_as_of)

    assert utc_result.services[0].in_horizon is True
    assert utc_result.services[0].risk_score == Decimal("0.00")
    assert offset_result == utc_result


def test_invalid_context_clock_horizon_duplicate_and_owner_are_rejected():
    account = make_account()
    service = make_service()
    with pytest.raises(ValueError, match="as_of_must_be_timezone_aware"):
        build_account_context(account, [service], as_of=datetime(2026, 10, 23))
    with pytest.raises(ValueError, match="horizon_days_out_of_range"):
        build_account_context(account, [service], as_of=AS_OF, horizon_days=True)
    with pytest.raises(ValueError, match="duplicate_service_id"):
        build_account_context(account, [service, service], as_of=AS_OF)
    with pytest.raises(ValueError, match="service_owner_mismatch"):
        build_account_context(account, [make_service(owner_id=999)], as_of=AS_OF)


def test_registry_extension_contributes_weight_and_explanation_from_same_result():
    def evaluator(service, context):
        return FeatureValue(
            Decimal("1"), True, "Дополнительный признак подтверждён.", {"service_id": service.id}
        )

    custom = FeatureDefinition(
        id="custom_signal",
        version="1",
        title="Дополнительный признак",
        description="Тестовый расширяемый сигнал",
        default_weight=Decimal("10"),
        required_fields=("status",),
        evaluator=evaluator,
    )
    registry = DEFAULT_FEATURE_REGISTRY.with_feature(custom)
    result = score_customer(make_account(balance=1000), [make_service()], as_of=AS_OF, registry=registry)
    feature = next(item for item in result.services[0].features if item.id == "custom_signal")

    assert feature.weight == Decimal("10")
    assert feature.contribution == Decimal("10")
    assert feature.explanation == "Дополнительный признак подтверждён."
    assert result.risk_score == Decimal("30.00")
    assert result.top_reasons[0].id == "renewal_proximity"
    assert any(reason.id == "custom_signal" for reason in result.top_reasons)


def test_weighted_unavailable_feature_makes_service_unknown_not_zero():
    custom = FeatureDefinition(
        id="not_measured",
        version="1",
        title="Нет измерения",
        description="Пример недоступных данных",
        default_weight=Decimal("10"),
        required_fields=("future_field",),
        evaluator=lambda service, context: FeatureValue(
            None, False, "В исходном снимке нет значения.", {"field": "future_field"}
        ),
    )
    registry = DEFAULT_FEATURE_REGISTRY.with_feature(custom)
    result = score_customer(make_account(balance=1000), [make_service()], as_of=AS_OF, registry=registry)

    assert result.risk_score is None
    assert result.risk_level == "unknown"
    assert result.monetary_risk_cents is None
    assert result.services[0].unscored_reason == "feature_unavailable:not_measured"
    assert "service_2001_feature_unavailable:not_measured" in result.data_quality_warnings


def test_unavailable_zero_weight_feature_is_visible_but_does_not_change_score():
    custom = FeatureDefinition(
        id="optional_signal",
        version="1",
        title="Необязательный сигнал",
        description="Нет дополнительного измерения",
        default_weight=Decimal("10"),
        required_fields=("future_field",),
        evaluator=lambda service, context: FeatureValue(
            None, False, "Дополнительный источник пока не доступен.", {}
        ),
    )
    registry = DEFAULT_FEATURE_REGISTRY.with_feature(custom)
    result = score_customer(
        make_account(balance=1000),
        [make_service()],
        as_of=AS_OF,
        registry=registry,
        weight_overrides={"optional_signal": 0},
    )
    feature = next(item for item in result.services[0].features if item.id == "optional_signal")

    assert result.risk_score == Decimal("20.00")
    assert feature.available is False
    assert feature.value is None
    assert feature.contribution is None


def test_registry_rejects_duplicate_ids_and_unsafe_weights():
    original = DEFAULT_FEATURE_REGISTRY.get("manual_renewal")
    with pytest.raises(ValueError, match="duplicate_feature_id"):
        DEFAULT_FEATURE_REGISTRY.with_feature(original)
    with pytest.raises(ValueError, match="feature_weight_out_of_range"):
        FeatureDefinition(
            id="bad_weight",
            version="1",
            title="Bad",
            description="Bad weight",
            default_weight=Decimal("101"),
            required_fields=(),
            evaluator=lambda service, context: FeatureValue(Decimal(0), True, "ok", {}),
        )


def test_unicode_service_names_and_lack_of_transaction_history_do_not_create_risk():
    account = make_account(balance=1000)
    service = make_service(name="测试 / услуга <script>", auto_prolong=True)
    result = run_case(account, [service])

    assert service.name == "测试 / услуга <script>"
    assert result.risk_score == Decimal("20.00")
    assert result.services[0].features[1].explanation == "Автопродление включено."
    assert account.balance == 1000


def test_settings_weight_overrides_reject_unknown_float_negative_and_all_zero():
    with pytest.raises(ValueError, match="unknown_feature_weight"):
        score_customer(
            make_account(), [make_service()], as_of=AS_OF,
            weight_overrides={"invented": 5},
        )
    with pytest.raises(ValueError, match="weight_manual_renewal_must_be_decimal_or_integer"):
        score_customer(
            make_account(), [make_service()], as_of=AS_OF,
            weight_overrides={"manual_renewal": 5.0},
        )
    with pytest.raises(ValueError, match="weight_out_of_range:manual_renewal"):
        score_customer(
            make_account(), [make_service()], as_of=AS_OF,
            weight_overrides={"manual_renewal": -1},
        )
    with pytest.raises(ValueError, match="weights_cannot_all_be_zero"):
        score_customer(
            make_account(), [make_service()], as_of=AS_OF,
            weight_overrides={feature_id: 0 for feature_id in DEFAULT_FEATURE_REGISTRY.ids},
        )
