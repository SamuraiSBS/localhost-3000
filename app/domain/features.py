"""Rules-v1 feature definitions and an immutable, extensible feature registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import re
from types import MappingProxyType
from collections.abc import Callable, Iterable, Mapping

from app.contracts import ServiceSource
from app.domain.context import AccountContext
from app.domain.money import as_decimal

FeatureEvaluator = Callable[[ServiceSource, AccountContext], "FeatureValue"]
_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class FeatureValue:
    """One measured feature result and the facts used to explain it."""

    value: Decimal | None
    available: bool
    explanation: str
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.available) is not bool:
            raise ValueError("feature_availability_must_be_boolean")
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ValueError("feature_explanation_required")
        if not isinstance(self.evidence, Mapping):
            raise ValueError("feature_evidence_must_be_mapping")
        if self.available:
            if self.value is None:
                raise ValueError("available_feature_requires_value")
            exact = as_decimal(self.value, field="feature_value")
            if not _ZERO <= exact <= _ONE:
                raise ValueError("feature_value_out_of_range")
            object.__setattr__(self, "value", exact)
        elif self.value is not None:
            raise ValueError("unavailable_feature_must_not_have_value")
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


@dataclass(frozen=True)
class FeatureDefinition:
    """A stable feature contract, evaluator, and default score weight."""

    id: str
    version: str
    title: str
    description: str
    default_weight: Decimal | int
    required_fields: tuple[str, ...]
    evaluator: FeatureEvaluator

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.id):
            raise ValueError("invalid_feature_id")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.version, self.title, self.description)
        ):
            raise ValueError("feature_metadata_required")
        weight = as_decimal(self.default_weight, field="feature_weight")
        if not _ZERO <= weight <= _HUNDRED:
            raise ValueError("feature_weight_out_of_range")
        if not callable(self.evaluator):
            raise ValueError("feature_evaluator_required")
        if isinstance(self.required_fields, str) or not all(
            isinstance(field, str) and field for field in self.required_fields
        ):
            raise ValueError("feature_required_fields_must_be_strings")
        object.__setattr__(self, "default_weight", weight)
        object.__setattr__(self, "required_fields", tuple(self.required_fields))

    def metadata(self) -> dict[str, object]:
        """JSON-ready settings metadata; functions are intentionally omitted."""
        return {
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "default_weight": self.default_weight,
            "min_weight": 0,
            "max_weight": 100,
            "required_fields": list(self.required_fields),
        }


class FeatureRegistry:
    """Immutable ordered registry; extending it creates a new registry."""

    def __init__(self, features: Iterable[FeatureDefinition]):
        definitions = tuple(features)
        if any(not isinstance(feature, FeatureDefinition) for feature in definitions):
            raise TypeError("feature_definition_required")
        ids = [feature.id for feature in definitions]
        if not definitions:
            raise ValueError("feature_registry_cannot_be_empty")
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_feature_id")
        self._features = definitions
        self._by_id = MappingProxyType({feature.id: feature for feature in definitions})

    def __iter__(self):
        return iter(self._features)

    def __len__(self) -> int:
        return len(self._features)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(feature.id for feature in self._features)

    @property
    def default_weights(self) -> Mapping[str, Decimal]:
        return MappingProxyType({feature.id: feature.default_weight for feature in self._features})

    def get(self, feature_id: str) -> FeatureDefinition:
        try:
            return self._by_id[feature_id]
        except KeyError as exc:
            raise KeyError(f"unknown_feature:{feature_id}") from exc

    def with_feature(self, feature: FeatureDefinition) -> "FeatureRegistry":
        if feature.id in self._by_id:
            raise ValueError("duplicate_feature_id")
        return FeatureRegistry((*self._features, feature))

    def metadata(self) -> list[dict[str, object]]:
        return [feature.metadata() for feature in self._features]


def _zero_outside_horizon(service: ServiceSource, context: AccountContext) -> FeatureValue | None:
    state = context.services[service.id]
    if not state.in_horizon:
        return FeatureValue(
            value=_ZERO,
            available=True,
            explanation=f"Срок услуги вне горизонта {context.horizon_days} дней.",
            evidence={"expires_at": service.expiresAt, "horizon_end": context.horizon_end},
        )
    return None


def _decimal_seconds(delta: timedelta) -> Decimal:
    return (
        Decimal(delta.days * 86_400 + delta.seconds)
        + Decimal(delta.microseconds) / Decimal(1_000_000)
    )


def _renewal_proximity(service: ServiceSource, context: AccountContext) -> FeatureValue:
    outside = _zero_outside_horizon(service, context)
    if outside is not None:
        return outside
    seconds_remaining = max(_decimal_seconds(service.expiresAt - context.as_of), _ZERO)
    horizon_seconds = Decimal(context.horizon_days * 86_400)
    value = min(max(_ONE - seconds_remaining / horizon_seconds, _ZERO), _ONE)
    return FeatureValue(
        value=value,
        available=True,
        explanation=(
            f"Ближайшее продление наступает через {seconds_remaining} секунд "
            f"в горизонте {context.horizon_days} дней."
        ),
        evidence={
            "expires_at": service.expiresAt,
            "seconds_to_expiry": seconds_remaining,
            "horizon_days": context.horizon_days,
            "in_horizon": True,
        },
    )


def _manual_renewal(service: ServiceSource, context: AccountContext) -> FeatureValue:
    outside = _zero_outside_horizon(service, context)
    if outside is not None:
        return outside
    value = _ONE if not service.autoProlong else _ZERO
    explanation = (
        "Автопродление отключено."
        if value
        else "Автопродление включено."
    )
    return FeatureValue(
        value=value,
        available=True,
        explanation=explanation,
        evidence={"auto_prolong": service.autoProlong},
    )


def _funding_gap(service: ServiceSource, context: AccountContext) -> FeatureValue:
    outside = _zero_outside_horizon(service, context)
    if outside is not None:
        return outside
    state = context.services[service.id]
    assert state.funding_gap_cents is not None and state.covered_cents is not None
    price = service.price
    value = _ZERO if price == 0 else Decimal(state.funding_gap_cents) / Decimal(price)
    if price == 0:
        explanation = "Стоимость продления равна нулю; нехватка средств равна нулю."
    elif state.funding_gap_cents:
        explanation = (
            f"Не хватает {state.funding_gap_cents} евроцентов для продления "
            f"стоимостью {price} евроцентов."
        )
    else:
        explanation = f"Доступные средства покрывают продление стоимостью {price} евроцентов."
    return FeatureValue(
        value=value,
        available=True,
        explanation=explanation,
        evidence={
            "price_cents": price,
            "covered_cents": state.covered_cents,
            "funding_gap_cents": state.funding_gap_cents,
            "spendable_cents": context.spendable_cents,
        },
    )


def _payment_wait(service: ServiceSource, context: AccountContext) -> FeatureValue:
    outside = _zero_outside_horizon(service, context)
    if outside is not None:
        return outside
    waiting = service.status == "prolong_wait"
    return FeatureValue(
        value=_ONE if waiting else _ZERO,
        available=True,
        explanation=(
            "Продление ожидает оплаты."
            if waiting
            else "Статус услуги не указывает на ожидание оплаты продления."
        ),
        evidence={"status": service.status},
    )


DEFAULT_FEATURE_REGISTRY = FeatureRegistry(
    (
        FeatureDefinition(
            id="renewal_proximity",
            version="1",
            title="Близость продления",
            description="Близость срока окончания к текущей дате в пределах горизонта",
            default_weight=Decimal(20),
            required_fields=("expiresAt",),
            evaluator=_renewal_proximity,
        ),
        FeatureDefinition(
            id="manual_renewal",
            version="1",
            title="Ручное продление",
            description="Необходимость действия клиента при выключенном автопродлении",
            default_weight=Decimal(25),
            required_fields=("autoProlong",),
            evaluator=_manual_renewal,
        ),
        FeatureDefinition(
            id="funding_gap",
            version="1",
            title="Нехватка средств",
            description="Доля цены ближайшего продления, не покрытая общим балансом",
            default_weight=Decimal(35),
            required_fields=("price", "balance", "bonusBalance", "bonusState"),
            evaluator=_funding_gap,
        ),
        FeatureDefinition(
            id="payment_wait",
            version="1",
            title="Ожидание оплаты",
            description="Снимок сообщает, что услуга ожидает оплаты продления",
            default_weight=Decimal(20),
            required_fields=("status",),
            evaluator=_payment_wait,
        ),
    )
)
