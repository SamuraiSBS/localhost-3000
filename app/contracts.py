"""Validated source and model inputs; no billing side effects or inference here."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt,
    StrictStr, PlainSerializer, field_serializer, field_validator, model_validator,
)

MAX_MONEY_CENTS = 2**53 - 1
MAX_SOURCE_ID = 2**63 - 1
MODEL_VERSION = "rules-v1"
MoneyCents = Annotated[StrictInt, Field(ge=0, le=MAX_MONEY_CENTS)]
SourceId = Annotated[StrictInt, Field(gt=0, le=MAX_SOURCE_ID)]
BonusState = Literal["locked", "unlocked"]
PaymentTerm = Literal["hour", "week", "month", "quarter_year", "half_year", "year"]
ServiceStatus = Literal["active", "suspended", "prolong_wait", "deleted", "blocked"]
TransactionStatus = Literal["performed", "not_performed"]
TransactionType = Literal["prolong", "order"]
RiskLevel = Literal["low", "medium", "high", "unknown"]
FEATURE_IDS = ("renewal_proximity", "manual_renewal", "funding_gap", "payment_wait")
DEFAULT_WEIGHTS = dict(zip(FEATURE_IDS, (Decimal(20), Decimal(25), Decimal(35), Decimal(20))))


def checked_money(value: int) -> int:
    """Use for every computed monetary sum before persistence/publication."""
    if type(value) is not int or not 0 <= value <= MAX_MONEY_CENTS:
        raise ValueError("money_out_of_range")
    return value


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        # RFC3339 date-time only: do not coerce integer Unix dates or naive dates.
        # fromisoformat normalizes invalid offset minutes such as +00:60;
        # constrain offset components before it can change the supplied instant.
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value):
            raise ValueError("timezone_required_rfc3339")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required_rfc3339")
    return value.astimezone(timezone.utc)


Timestamp = Annotated[datetime, BeforeValidator(_timestamp)]


class SourceModel(BaseModel):
    # Extra upstream fields survive round-trip and remain unused by rules-v1.
    model_config = ConfigDict(extra="allow", frozen=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_timestamp(self, value: object) -> object:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value


class AccountSource(SourceModel):
    id: SourceId
    email: StrictStr
    balance: MoneyCents
    withdrawBalance: MoneyCents
    totalReplenished: MoneyCents
    bonusBalance: MoneyCents
    bonusState: BonusState

    @model_validator(mode="after")
    def available_balance_fits(self) -> Self:
        checked_money(self.balance + (self.bonusBalance if self.bonusState == "unlocked" else 0))
        return self


class ServiceSource(SourceModel):
    id: SourceId
    name: StrictStr
    ownerId: SourceId
    paymentTerm: PaymentTerm
    autoProlong: StrictBool
    createdAt: Timestamp
    expiresAt: Timestamp
    status: ServiceStatus
    price: MoneyCents

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.expiresAt < self.createdAt:
            raise ValueError("expires_before_created")
        return self


class TransactionSource(SourceModel):
    id: SourceId
    amount: MoneyCents
    status: TransactionStatus
    performedAt: Timestamp | None
    createdAt: Timestamp
    type: TransactionType
    serviceId: SourceId

    @model_validator(mode="after")
    def valid_execution(self) -> Self:
        if self.status == "performed" and self.performedAt is None:
            raise ValueError("performed_at_required")
        if self.status == "performed" and self.performedAt < self.createdAt:
            raise ValueError("performed_before_created")
        return self


class SourceSnapshot(SourceModel):
    accounts: tuple[AccountSource, ...]
    services: tuple[ServiceSource, ...]
    transactions: tuple[TransactionSource, ...]

    @field_validator("accounts", "services", "transactions", mode="before")
    @classmethod
    def array_required(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            raise ValueError("array_required")
        return value

    @model_validator(mode="after")
    def valid_relations(self) -> Self:
        for table in ("accounts", "services", "transactions"):
            rows = getattr(self, table)
            if len({row.id for row in rows}) != len(rows):
                raise ValueError(f"{table}: duplicate_id")
        accounts = {row.id for row in self.accounts}
        services = {row.id for row in self.services}
        for row in self.services:
            if row.ownerId not in accounts:
                raise ValueError(f"services[{row.id}].ownerId: account_not_found")
        for row in self.transactions:
            if row.serviceId not in services:
                raise ValueError(f"transactions[{row.id}].serviceId: service_not_found")
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)


def load_source(path: str | Path) -> SourceSnapshot:
    """Read and validate upstream bytes; never rewrite the supplied material."""
    return SourceSnapshot.model_validate_json(Path(path).read_bytes())


def _decimal_number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("finite_number_required")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("finite_number_required")
    return result


ScoreNumber = Annotated[
    Decimal, BeforeValidator(_decimal_number), Field(ge=0, le=100),
    PlainSerializer(lambda value: int(value) if value == value.to_integral_value() else float(value),
                    return_type=int | float, when_used="json"),
]
HorizonDays = Annotated[StrictInt, Field(ge=1, le=90)]


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    medium: ScoreNumber = Decimal(35)
    high: ScoreNumber = Decimal(65)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not 0 < self.medium < self.high <= 100:
            raise ValueError("thresholds_must_be_ordered")
        return self


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_version: Literal["rules-v1"] = MODEL_VERSION
    horizon_days: HorizonDays = 30
    thresholds: Thresholds = Field(default_factory=Thresholds)
    weights: dict[str, ScoreNumber] = Field(default_factory=lambda: DEFAULT_WEIGHTS.copy())

    @field_validator("weights")
    @classmethod
    def complete_weights(cls, weights: dict[str, Decimal]) -> dict[str, Decimal]:
        if set(weights) != set(FEATURE_IDS):
            raise ValueError("weights_must_match_registry")
        if not any(weights.values()):
            raise ValueError("weights_cannot_all_be_zero")
        return weights


class ThresholdsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    medium: ScoreNumber | None = None
    high: ScoreNumber | None = None

    @model_validator(mode="after")
    def valid_supplied_thresholds(self) -> Self:
        if any(getattr(self, name) is None or getattr(self, name) <= 0
               for name in self.model_fields_set):
            raise ValueError("positive_threshold_required")
        if self.medium is not None and self.high is not None and self.medium >= self.high:
            raise ValueError("thresholds_must_be_ordered")
        return self


class SettingsPatch(BaseModel):
    """A partial patch is merged then validated as a complete ModelSettings."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    horizon_days: HorizonDays | None = None
    thresholds: ThresholdsPatch | None = None
    weights: dict[str, ScoreNumber] | None = None

    @model_validator(mode="after")
    def known_fields(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError("null_setting_not_allowed")
        if self.weights is not None and not set(self.weights) <= set(FEATURE_IDS):
            raise ValueError("unknown_feature")
        return self

    def apply(self, current: ModelSettings) -> ModelSettings:
        values = current.model_dump()
        patch = self.model_dump(exclude_unset=True)
        if "weights" in patch:
            patch["weights"] = {**values["weights"], **patch["weights"]}
        if "thresholds" in patch:
            patch["thresholds"] = {**values["thresholds"], **patch["thresholds"]}
        values.update(patch)
        return ModelSettings.model_validate(values)
