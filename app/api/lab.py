"""Read-only Decision Lab operations over one captured immutable evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from app.auth import AuthenticatedUser, require_session
from app.contracts import SourceId
from app.domain.lab import preview_customer
from app.domain.lab_case import (CaseValidationError, canonical_case_json, export_case, replay_case)
from app.domain.lab_sensitivity import analyze_sensitivity
from app.domain.lab_solver import solve
from app.domain.lab_worklist import build_worklist
from app.domain.renewal_calendar import build_renewal_calendar
from app.evaluation import Evaluation
from app.lab_contracts import (CaseDocument, CaseExportRequest, CaseReplayRequest, CaseReplayResponse,
                               ActionInput, LabDTO, PreviewRequest, PreviewResult, RevisionTuple,
                               SolveRequest, SolveResponse, WorklistRequest, WorklistResponse,
                               RenewalCalendarRequest, RenewalCalendarResponse)
from app.storage import snapshot_checksum

MAX_LAB_BYTES = 64 * 1024


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


class LabRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded_json(request: Request):
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise _error(415, "unsupported_media_type", "Use application/json.")
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_LAB_BYTES:
                    raise _error(413, "request_too_large", "Lab request exceeds 64 KiB.")
                chunks.append(chunk)
            request._body = b"".join(chunks)
            try:
                document = json.loads(request._body)
            except (ValueError, UnicodeError, RecursionError):
                raise _error(400, "bad_request", "Request body must contain valid JSON.") from None
            revision = document.get("expected_revision") if isinstance(document, dict) else None
            if not isinstance(revision, dict) or not {"analysis_revision", "data_revision", "settings_revision"} <= revision.keys():
                raise _error(400, "revision_required", "A complete expected_revision is required.")
            try:
                return await original(request)
            except RequestValidationError as error:
                if request.url.path.endswith("/lab/cases/replay") and any(
                    tuple(item.get("loc", ()))[:2] == ("body", "case_document")
                    for item in error.errors()
                ):
                    raise _error(422, "invalid_case_schema", "The case document must be valid UTF-8 JSON text.") from None
                if any(str(item.get("ctx", {}).get("error", "")) == "service_not_allowed"
                       for item in error.errors()):
                    raise _error(422, "service_not_allowed", "The requested service action is not allowed.") from None
                raise

        return bounded_json


router = APIRouter(route_class=LabRoute)


class CustomerRequest(LabDTO):
    expected_revision: RevisionTuple
    customer_id: SourceId


class LabEnvelope(RevisionTuple):
    source_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: str
    horizon_days: int
    model_version: str


class PreviewResponse(PreviewResult):
    analysis_revision: str
    data_revision: int
    settings_revision: int
    source_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class SensitivityResponse(LabEnvelope):
    baseline: dict[str, object]
    weights: dict[str, object]
    thresholds: dict[str, object]
    coverage: dict[str, int]
    excluded_candidates: list[dict[str, object]]


def _revision(evaluation: Evaluation) -> dict[str, object]:
    return {name: getattr(evaluation, name) for name in
            ("analysis_revision", "data_revision", "settings_revision")}


def _assert_current(request: Request, expected: RevisionTuple) -> Evaluation:
    current = request.app.state.coordinator.current
    if current is None:
        raise _error(503, "evaluation_unavailable", "No active source snapshot is available.")
    if expected.model_dump() != _revision(current):
        raise HTTPException(409, detail={
            "code": "stale_evaluation", "message": "The working evaluation changed. Refresh and retry.",
            "current_revision": _revision(current),
        })
    return current


def _inputs(evaluation: Evaluation, customer_id: int):
    snapshot = evaluation.snapshot
    account = next((row for row in snapshot.accounts if row.id == customer_id), None)
    if account is None:
        raise _error(404, "customer_not_found", "Customer not found.")
    return account, tuple(row for row in snapshot.services if row.ownerId == customer_id)


def _json(value):
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
        if isinstance(value, PreviewResult) and value.effective_overrides is not None:
            payload["effective_overrides"] = value.effective_overrides.model_dump(
                mode="json", exclude_unset=True,
            )
        return payload
    if is_dataclass(value):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return value


def _response(request: Request, expected: RevisionTuple, evaluation: Evaluation, result):
    payload = {
        **_revision(evaluation), "source_checksum": snapshot_checksum(evaluation.snapshot_json),
        "as_of": _json(evaluation.evaluated_at), "horizon_days": evaluation.horizon_days,
        "model_version": evaluation.model_version, **_json(result),
    }
    _assert_current(request, expected)
    return payload


def _compute(operation):
    try:
        return operation()
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from None
    except ValueError as error:
        # Expose only known machine codes, never arbitrary source values.
        reason = str(error)
        allowed = {
            "service_not_allowed", "money_out_of_range", "solver_unsupported_registry",
            "solver_service_limit", "solver_switch_limit", "max_switches_out_of_range",
            "sensitivity_variant_limit", "customer_not_found",
        }
        code = reason if reason in allowed else "validation_error"
        raise _error(422, code, "The requested scenario is invalid.") from None


@router.post("/lab/preview", response_model=PreviewResponse, response_model_exclude_unset=True)
def preview(request: Request, body: PreviewRequest,
            _user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    account, services = _inputs(evaluation, body.customer_id)
    result = _compute(lambda: preview_customer(
        account, services, settings=evaluation.settings, as_of=evaluation.evaluated_at,
        mode=body.mode, settings_overrides=body.settings_overrides, action=body.action,
    ))
    return _response(request, body.expected_revision, evaluation, result)


@router.post("/lab/worklist", response_model=WorklistResponse,
             summary="Build a contact worklist from the working baseline",
             description="Read-only Medium/High candidates, ordered by affected expiry, monetary risk, "
                         "score and customer ID. Uses the full baseline; no hypothetical draft is applied. "
                         "Suggested actions do not guarantee customer retention.")
def worklist(request: Request, body: WorklistRequest,
             _user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    result = _compute(lambda: build_worklist(evaluation, limit=body.limit))
    return _response(request, body.expected_revision, evaluation, result)


@router.post("/lab/sensitivity", response_model=SensitivityResponse)
def sensitivity(request: Request, body: CustomerRequest,
                _user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    _inputs(evaluation, body.customer_id)
    result = _compute(lambda: analyze_sensitivity(evaluation, body.customer_id))
    return _response(request, body.expected_revision, evaluation, result)


@router.post("/lab/renewal-calendar", response_model=RenewalCalendarResponse,
             summary="Calculate a bounded hypothetical repeat-renewal calendar",
             description="Read-only constant-price UTC scenario. At most 90 days and 1000 events. "
                         "One balance plus unlocked bonus is allocated once in due-time/service-ID order; "
                         "withdrawBalance is excluded. Calendar months anchor to the original expiry. "
                         "The baseline and its horizon stay unchanged; scenario horizon is reported separately. "
                         "This is not a cash-flow prediction, payment promise or service deletion date.")
def renewal_calendar(request: Request, body: RenewalCalendarRequest,
                     _user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    account, services = _inputs(evaluation, body.customer_id)
    assumptions = {item.service_id: item.payment_term for item in body.period_assumptions}
    if not set(assumptions) <= {service.id for service in services}:
        raise _error(422, "service_not_allowed", "The requested service assumption is not allowed.")
    result = _compute(lambda: build_renewal_calendar(
        account, services, as_of=evaluation.evaluated_at,
        horizon_days=body.horizon_days if body.horizon_days is not None else evaluation.horizon_days,
        max_events=body.max_events, period_assumptions=assumptions,
    ))
    baseline = _compute(lambda: preview_customer(
        account, services, settings=evaluation.settings, as_of=evaluation.evaluated_at, mode="model",
    )).baseline
    scenario = _json(result)
    context = scenario.pop("baseline")
    scenario.update({name: context[name] for name in ("as_of", "horizon_days", "horizon_end", "spendable_cents")})
    return _response(request, body.expected_revision, evaluation, {
        "hypothetical": True, "baseline": baseline, "scenario": scenario,
    })


@router.post("/lab/solve", response_model=SolveResponse,
             summary="Find the exact minimum hypothetical action",
             description="Read-only rules-v1 search, ordered by cents, switch count, then service IDs. "
                         "Score goals use a strict threshold. A complete certificate proves bounded "
                         "minimality; search_limit does not prove unreachable. At most 100 due services, "
                         "8 toggle candidates, 10,000,000 cents and 6,144 search evaluations.")
def solver(request: Request, body: SolveRequest,
           _user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    account, services = _inputs(evaluation, body.customer_id)
    result = _compute(lambda: solve(
        account, services, settings=evaluation.settings, as_of=evaluation.evaluated_at,
        goal=body.goal.model_dump(), max_top_up_cents=body.max_top_up_cents,
        allowed_enable_auto_prolong_ids=body.allowed_enable_auto_prolong_ids,
        max_switches=body.max_switches,
    ))
    action = (ActionInput(top_up_cents=result.top_up_cents,
                          enable_auto_prolong_ids=result.enable_auto_prolong_ids)
              if result.status in {"found", "already_satisfied"} else None)
    explanation = _compute(lambda: preview_customer(
        account, services, settings=evaluation.settings, as_of=evaluation.evaluated_at,
        mode="action" if action is not None else "model", action=action,
    ))
    return _response(request, body.expected_revision, evaluation, {
        "hypothetical": True, "effective_goal": body.goal,
        "effective_constraints": body.model_dump(exclude={"expected_revision", "customer_id", "goal"}),
        **_json(result),
        "baseline": explanation.baseline,
        "result": explanation.action_scenario if action is not None else None,
        "effective_action": action,
        "delta": explanation.delta if action is not None else None,
    })


_CASE_MESSAGES = {
    "invalid_case_schema": "The case schema is invalid or contains unsupported fields.",
    "case_checksum_mismatch": "The case checksum does not match its contents.",
    "unsupported_case_version": "The case format version is not supported.",
    "unsupported_case_engine": "The case engine version is not supported.",
    "unsupported_case_features": "The case feature versions are not supported.",
    "case_result_mismatch": "The recalculated result does not match the saved case.",
    "case_too_large": "The case document exceeds 64 KiB.",
    "case_service_limit": "A case may contain at most 100 services.",
    "case_switch_limit": "A case may enable at most 8 service switches.",
    "service_not_allowed": "The requested service action is not allowed.",
}


def _case_compute(operation):
    try:
        return operation()
    except CaseValidationError as error:
        raise _error(error.status_code, error.code,
                     _CASE_MESSAGES.get(error.code, "The case document is invalid.")) from None


@router.post("/lab/cases/export", response_model=CaseDocument,
             summary="Download a synthetic single-customer decision case",
             description="Read-only JSON attachment with rules-v1 inputs, fixed case as_of, settings, "
                         "one strict model/action/solve operation, expected summary and canonical SHA-256. "
                         "Uses the expected working revision before and after calculation. Synthetic labels "
                         "and relative service ordering preserve the engine result; no other customers, "
                         "transactions or source extras are exported. Maximum 64 KiB, 100 services and "
                         "8 switches. No server file is written.",
             responses={200: {"headers": {"Content-Disposition": {
                 "schema": {"type": "string"}, "description": "JSON attachment filename."}}}})

def case_export(request: Request, body: CaseExportRequest,
                _user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    _inputs(evaluation, body.customer_id)
    result = _case_compute(lambda: export_case(
        evaluation.snapshot, evaluation.settings, customer_id=body.customer_id,
        as_of=evaluation.evaluated_at,
        operation=body.operation.model_dump(mode="json", exclude_unset=True),
    ))
    document = canonical_case_json(result)
    _assert_current(request, body.expected_revision)
    return Response(document, media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="decision-case-v1.json"',
        "Cache-Control": "no-store",
    })


@router.post("/lab/cases/replay", response_model=CaseReplayResponse,
             summary="Recalculate a portable case in an isolated sandbox",
             description="Accepts {expected_revision, case_document: untouched UTF-8 JSON string}. "
                         "Both the whole request and the embedded document have a 64 KiB byte cap. "
                         "Validates schema, duplicate keys, checksum and supported format/engine/features "
                         "before real engine calculation. Never writes source, settings, metrics or revisions. "
                         "The active tuple is a concurrency guard checked before and after calculation; "
                         "all calculation inputs come only from the case, including its own case_as_of, "
                         "case_horizon_days and case_engine_version. Active as_of/model/source metadata "
                         "does not replace case inputs. A case does not reproduce a portfolio ranking; "
                         "a match proves reproducibility, not statistical accuracy or authorship.")

def case_replay(request: Request, body: CaseReplayRequest,
                _user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    result = _case_compute(lambda: replay_case(body.case_document))
    # Only inspect metadata after the domain has validated the original text.
    case = json.loads(body.case_document)
    return _response(request, body.expected_revision, evaluation, {
        "sandbox": True, **result, "case_as_of": case["as_of"],
        "case_horizon_days": case["horizon_days"], "case_engine_version": case["engine_version"],
    })
