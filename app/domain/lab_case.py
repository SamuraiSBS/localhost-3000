"""Portable, synthetic rules-v1 cases. All calculations are caller-owned and pure.

Case v1 retains only fields read by the engine. Account 1 uses
case@example.invalid, service names are ``case-service-N``, nonspendable history
totals are zero, and transactions are empty. Ascending original service IDs map
to ascending synthetic IDs, preserving both expiry/ID allocation and solver ties.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import hmac
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, model_validator

from app.contracts import (AccountSource, ModelSettings, ScoreNumber, ServiceSource,
                           SourceId, SourceSnapshot, Timestamp)
from app.domain.features import DEFAULT_FEATURE_REGISTRY
from app.domain.lab import preview_customer
from app.domain.lab_solver import solve
from app.evaluation import calculate_evaluation
from app.lab_contracts import ActionInput, ModelOverrides

MAX_CASE_BYTES = 64 * 1024
MAX_CASE_SERVICES = 100
MAX_CASE_SWITCHES = 8
FEATURE_VERSIONS = {feature.id: feature.version for feature in DEFAULT_FEATURE_REGISTRY}


class CaseValidationError(ValueError):
    """Stable domain error; HTTP adapters can use code and status_code."""

    def __init__(self, code: str, status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ModelOperation(_Strict):
    type: Literal["model"]
    settings_overrides: ModelOverrides


class _ActionOperation(_Strict):
    type: Literal["action"]
    action: ActionInput


class _ScoreGoal(_Strict):
    type: Literal["score_below"]
    threshold: ScoreNumber = Field(gt=0)


class _CoverGoal(_Strict):
    type: Literal["cover_selected"]
    service_ids: tuple[SourceId, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.service_ids)) != len(self.service_ids):
            raise ValueError("service_not_allowed")
        return self


class _SolveOperation(_Strict):
    type: Literal["solve"]
    goal: Annotated[_ScoreGoal | _CoverGoal, Field(discriminator="type")]
    max_top_up_cents: StrictInt = Field(ge=0, le=10_000_000)
    allowed_enable_auto_prolong_ids: tuple[SourceId, ...] = Field(default=(), max_length=8)
    max_switches: StrictInt = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def unique(self):
        ids = self.allowed_enable_auto_prolong_ids
        if len(set(ids)) != len(ids) or self.max_switches > len(ids):
            raise ValueError("service_not_allowed")
        return self


Operation = Annotated[_ModelOperation | _ActionOperation | _SolveOperation, Field(discriminator="type")]


class _ServiceSummary(_Strict):
    service_id: SourceId
    risk_score: Annotated[StrictStr, Field(pattern=r"^(?:100|[0-9]{1,2})\.[0-9]{2}$")] | None
    risk_level: Literal["low", "medium", "high", "unknown"]
    monetary_risk_cents: StrictInt | None
    covered_cents: StrictInt | None
    funding_gap_cents: StrictInt | None


class _ScoreSummary(_Strict):
    risk_score: Annotated[StrictStr, Field(pattern=r"^(?:100|[0-9]{1,2})\.[0-9]{2}$")] | None
    risk_level: Literal["low", "medium", "high", "unknown"]
    monetary_risk_cents: StrictInt | None
    funding_gap_cents: StrictInt
    due_services_price_cents: StrictInt
    top_service_id: SourceId | None
    services: tuple[_ServiceSummary, ...] = Field(max_length=100)


class _PreviewExpected(_Strict):
    type: Literal["model", "action"]
    baseline: _ScoreSummary
    scenario: _ScoreSummary


class _SolveExpected(_Strict):
    type: Literal["solve"]
    baseline: _ScoreSummary
    scenario: _ScoreSummary | None
    status: Literal["found", "already_satisfied", "unreachable", "search_limit"]
    top_up_cents: StrictInt | None
    enable_auto_prolong_ids: tuple[SourceId, ...] = Field(max_length=8)
    certificate_complete: StrictBool
    one_cent_less_satisfies: StrictBool | None
    one_cent_less_result: _ScoreSummary | None


class _Case(_Strict):
    case_version: StrictInt
    engine_version: StrictStr
    feature_versions: dict[StrictStr, StrictStr]
    settings: ModelSettings
    as_of: Timestamp
    horizon_days: StrictInt
    snapshot: SourceSnapshot
    operation: Operation
    expected: dict[str, Any]
    checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


def canonical_case_json(case: Mapping[str, Any]) -> str:
    """Canonical UTF-8 JSON; checksum is SHA-256 of this form without checksum."""
    return json.dumps(dict(case), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def case_checksum(case: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_case_json({key: value for key, value in case.items()
                                             if key != "checksum"}).encode("utf-8")).hexdigest()


def _score_summary(score) -> dict[str, Any] | None:
    if score is None:
        return None
    return {
        "risk_score": str(score.risk_score) if score.risk_score is not None else None,
        "risk_level": score.risk_level,
        "monetary_risk_cents": score.monetary_risk_cents,
        "funding_gap_cents": score.funding_gap_cents,
        "due_services_price_cents": score.due_services_price_cents,
        "top_service_id": score.top_service_id,
        "services": [{"service_id": row.service_id,
                      "risk_score": str(row.risk_score) if row.risk_score is not None else None,
                      "risk_level": row.risk_level,
                      "monetary_risk_cents": row.monetary_risk_cents,
                      "covered_cents": row.covered_cents,
                      "funding_gap_cents": row.funding_gap_cents}
                     for row in sorted(score.services, key=lambda row: row.service_id)],
    }


def _calculate(case: _Case) -> dict[str, Any]:
    # Fixed local revision labels have no connection to the active coordinator.
    baseline = calculate_evaluation(case.snapshot, case.settings, analysis_revision=1,
                                    data_revision=1, settings_revision=1, as_of=case.as_of).customers[0]
    account = case.snapshot.accounts[0]
    rows = case.snapshot.services
    operation = case.operation
    result: dict[str, Any] = {"type": operation.type, "baseline": _score_summary(baseline)}
    if isinstance(operation, _SolveOperation):
        calculated = solve(account, rows, settings=case.settings, as_of=case.as_of,
                           **operation.model_dump(exclude={"type"}))
        result.update(status=calculated.status, top_up_cents=calculated.top_up_cents,
                      enable_auto_prolong_ids=list(calculated.enable_auto_prolong_ids),
                      scenario=_score_summary(calculated.result),
                      certificate_complete=calculated.certificate.complete,
                      one_cent_less_satisfies=calculated.certificate.one_cent_less_satisfies,
                      one_cent_less_result=_score_summary(calculated.certificate.one_cent_less_result))
    else:
        kwargs = ({"settings_overrides": operation.settings_overrides}
                  if isinstance(operation, _ModelOperation) else {"action": operation.action})
        calculated = preview_customer(account, rows, settings=case.settings, as_of=case.as_of,
                                      mode=operation.type, **kwargs)
        result["scenario"] = _score_summary(calculated.model_scenario or calculated.action_scenario)
    return result


def _operation(value: Mapping[str, Any]) -> Operation:
    if not isinstance(value, Mapping) or not isinstance(value.get("type"), str):
        raise CaseValidationError("invalid_case_schema")
    kind = value.get("type")
    model = {"model": _ModelOperation, "action": _ActionOperation, "solve": _SolveOperation}.get(kind)
    if model is None:
        raise CaseValidationError("invalid_case_schema")
    try:
        return model.model_validate(dict(value))
    except ValidationError as exc:
        raise CaseValidationError("invalid_case_schema") from exc


def _remap_operation(operation, id_map):
    values = operation.model_dump(mode="json", exclude_unset=True)
    def remap(ids):
        try:
            return sorted(id_map[value] for value in ids)
        except KeyError as exc:
            raise CaseValidationError("service_not_allowed") from exc
    if operation.type == "action":
        values["action"]["enable_auto_prolong_ids"] = remap(operation.action.enable_auto_prolong_ids)
    if operation.type == "solve":
        values["allowed_enable_auto_prolong_ids"] = remap(operation.allowed_enable_auto_prolong_ids)
        if operation.goal.type == "cover_selected":
            values["goal"]["service_ids"] = remap(operation.goal.service_ids)
    return values


def export_case(snapshot: SourceSnapshot, settings: ModelSettings, *, customer_id: int,
                as_of: datetime, operation: Mapping[str, Any]) -> dict[str, Any]:
    """Export one customer, stripping all labels, extras and unused history."""
    if type(customer_id) is not int:
        raise CaseValidationError("customer_not_found")
    account = next((row for row in snapshot.accounts if row.id == customer_id), None)
    if account is None:
        raise CaseValidationError("customer_not_found")
    rows = sorted((row for row in snapshot.services if row.ownerId == customer_id), key=lambda row: row.id)
    if len(rows) > MAX_CASE_SERVICES:
        raise CaseValidationError("case_service_limit")
    id_map = {row.id: index for index, row in enumerate(rows, 1)}
    synthetic_account = {name: getattr(account, name) for name in AccountSource.model_fields}
    synthetic_account.update(id=1, email="case@example.invalid", withdrawBalance=0, totalReplenished=0)
    synthetic_rows = []
    for row in rows:
        values = {name: getattr(row, name) for name in ServiceSource.model_fields}
        values.update(id=id_map[row.id], ownerId=1, name=f"case-service-{id_map[row.id]}")
        synthetic_rows.append(values)
    source = SourceSnapshot(accounts=[synthetic_account], services=synthetic_rows, transactions=[])
    payload = {"case_version": 1, "engine_version": "rules-v1", "feature_versions": dict(FEATURE_VERSIONS),
               "settings": settings.model_dump(mode="json"), "as_of": as_of,
               "horizon_days": settings.horizon_days, "snapshot": source.model_dump(mode="json"),
               "operation": _remap_operation(_operation(operation), id_map),
               "expected": {}, "checksum": "0" * 64}
    try:
        case = _Case.model_validate(payload)
        payload["as_of"] = case.model_dump(mode="json")["as_of"]
        payload["expected"] = _calculate(case)
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, CaseValidationError):
            raise
        raise CaseValidationError("invalid_case_schema") from exc
    payload["checksum"] = case_checksum(payload)
    # Apply the exact same bounded validation path used for uploaded cases.
    _parse(payload)
    return payload


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _parse(document: bytes | str | Mapping[str, Any]) -> _Case:
    try:
        if isinstance(document, Mapping):
            raw = canonical_case_json(document).encode("utf-8")
        elif isinstance(document, str):
            raw = document.encode("utf-8")
        elif isinstance(document, bytes):
            raw = document
        else:
            raise ValueError("json_required")
        if len(raw) > MAX_CASE_BYTES:
            raise CaseValidationError("case_too_large", 413)
        payload = json.loads(raw, object_pairs_hook=_pairs,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite_json")))
        if not isinstance(payload, dict):
            raise ValueError("object_required")
        if type(payload.get("case_version")) is not int:
            raise ValueError("version_integer_required")
        if payload["case_version"] != 1:
            raise CaseValidationError("unsupported_case_version")
        if payload.get("engine_version") != "rules-v1":
            raise CaseValidationError("unsupported_case_engine")
        if payload.get("feature_versions") != FEATURE_VERSIONS:
            raise CaseValidationError("unsupported_case_features")
        checksum = payload.get("checksum")
        if not isinstance(checksum, str) or not hmac.compare_digest(case_checksum(payload), checksum):
            raise CaseValidationError("case_checksum_mismatch")
        case = _Case.model_validate(payload)
        expected_model = _SolveExpected if case.operation.type == "solve" else _PreviewExpected
        expected = expected_model.model_validate(case.expected)
        if expected.type != case.operation.type:
            raise ValueError("expected_operation_mismatch")
        source = case.snapshot
        if len(source.accounts) != 1 or source.transactions:
            raise ValueError("minimal_snapshot_required")
        if len(source.services) > MAX_CASE_SERVICES:
            raise CaseValidationError("case_service_limit")
        account = source.accounts[0]
        if (source.model_extra or account.model_extra or account.id != 1
                or account.email != "case@example.invalid" or account.withdrawBalance or account.totalReplenished):
            raise ValueError("synthetic_account_required")
        if sorted(row.id for row in source.services) != list(range(1, len(source.services) + 1)):
            raise ValueError("synthetic_service_ids_required")
        if any(row.model_extra or row.name != f"case-service-{row.id}" for row in source.services):
            raise ValueError("synthetic_services_required")
        if case.settings.horizon_days != case.horizon_days:
            raise ValueError("horizon_mismatch")
        if isinstance(case.operation, _ActionOperation) and len(case.operation.action.enable_auto_prolong_ids) > 8:
            raise CaseValidationError("case_switch_limit")
        return case
    except CaseValidationError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise CaseValidationError("invalid_case_schema") from exc


def replay_case(document: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
    """Recalculate validated inputs; expected is compared and never executed."""
    case = _parse(document)
    try:
        calculated = _calculate(case)
    except ValueError as exc:
        raise CaseValidationError("invalid_case_schema") from exc
    if canonical_case_json(calculated) != canonical_case_json(case.expected):
        raise CaseValidationError("case_result_mismatch")
    return {"matched": True, "checksum": case.checksum, "calculated": calculated}
