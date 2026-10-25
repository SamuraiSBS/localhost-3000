"""Atomic full replacement of the active source snapshot."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from secrets import token_urlsafe
from threading import RLock
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.auth import AuthenticatedUser, require_session
from app.contracts import SourceSnapshot
from app.evaluation import SourceRevisionConflict
from app.lab_contracts import (
    RevisionTuple, SourceConfirmRequest, SourceConfirmResponse,
    SourcePreviewRequest, SourcePreviewResponse,
)
from app.storage import ConcurrentWriteError, snapshot_checksum

router = APIRouter()
_CONTENT_LENGTH_PATTERN = re.compile(r"[0-9]+\Z")


def _problem(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, str]] | None = None,
) -> HTTPException:
    detail: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        detail["details"] = details
    return HTTPException(status_code=status_code, detail=detail)


async def _read_body(request: Request, *, maximum: int | None = None) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise _problem(415, "unsupported_media_type", "Snapshot upload requires JSON.")

    maximum = maximum if maximum is not None else request.app.state.config.max_snapshot_bytes
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        raw_length = raw_length.strip()
        if _CONTENT_LENGTH_PATTERN.fullmatch(raw_length) is None:
            raise _problem(
                400,
                "invalid_content_length",
                "Content-Length must be a non-negative integer.",
                details=[{"path": "Content-Length", "reason": "invalid_integer"}],
            )
        if int(raw_length) > maximum:
            raise _problem(413, "request_too_large", "Snapshot exceeds the configured size limit.")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise _problem(413, "request_too_large", "Snapshot exceeds the configured size limit.")
        body.extend(chunk)
    return bytes(body)


def _warning_summary(evaluation) -> list[dict[str, object]]:
    counts = Counter(
        warning
        for customer in evaluation.customers
        for warning in customer.data_quality_warnings
    )
    return [{"code": code, "count": counts[code]} for code in sorted(counts)]


@router.put("/source/snapshot")
async def replace_source_snapshot(
    request: Request,
    _user: AuthenticatedUser = Depends(require_session),
) -> dict[str, object]:
    body = await _read_body(request)
    try:
        snapshot = SourceSnapshot.model_validate_json(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors(), body=body) from None

    try:
        result = request.app.state.coordinator.replace_snapshot(snapshot)
    except ValidationError as error:
        raise RequestValidationError(error.errors(), body=body) from None
    except ValueError as error:
        raise _problem(
            422,
            "invalid_snapshot",
            "Snapshot could not be evaluated and was not activated.",
            details=[{"path": "snapshot", "reason": str(error) or "invalid_value"}],
        ) from None

    return _snapshot_response(result)


def _snapshot_response(result) -> dict[str, object]:
    evaluation = result.evaluation
    if evaluation is None:
        raise _problem(503, "evaluation_unavailable", "No active source snapshot is available.")
    source = evaluation.snapshot
    received_at = result.received_at
    return {
        "changed": result.changed,
        "data_revision": result.data_revision,
        "analysis_revision": result.analysis_revision,
        "received_at": (
            received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if received_at is not None
            else None
        ),
        "checksum": result.checksum,
        "counts": {
            "accounts": len(source.accounts),
            "services": evaluation.service_count,
            "transactions": evaluation.transaction_count,
        },
        "warnings": _warning_summary(evaluation),
    }


class SourcePreviewRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request: Request):
            maximum = 64 * 1024 if request.url.path.endswith("/confirm") else None
            request._body = await _read_body(request, maximum=maximum)
            try:
                document = json.loads(request._body)
            except (ValueError, UnicodeError, RecursionError):
                raise _problem(400, "bad_request", "Request body must contain valid JSON.") from None
            revision = document.get("expected_revision") if isinstance(document, dict) else None
            if not isinstance(revision, dict) or not {
                "analysis_revision", "data_revision", "settings_revision",
            } <= revision.keys():
                raise _problem(400, "revision_required", "A complete expected_revision is required.")
            return await original(request)

        return bounded


@dataclass(frozen=True, slots=True)
class _SourcePreview:
    document: bytes
    checksum: str
    revision: RevisionTuple
    as_of: datetime
    deadline: float
    session_hash: str


_preview_lock = RLock()
_PREVIEW_TTL_SECONDS = 15 * 60
preview_router = APIRouter(route_class=SourcePreviewRoute)


def _store(request: Request) -> dict[str, _SourcePreview]:
    # Access is serialized by _preview_lock, including lazy per-app creation.
    if not hasattr(request.app.state, "source_previews"):
        request.app.state.source_previews = {}
    entries = request.app.state.source_previews
    for key, entry in list(entries.items()):
        if entry.deadline <= monotonic():
            del entries[key]
    return entries


def _conflict(request: Request, code: str = "stale_evaluation") -> HTTPException:
    state = request.app.state.repository.load_state()
    return HTTPException(409, detail={
        "code": code, "message": "The source preview is no longer applicable. Create a new preview.",
        "current_revision": {
            "analysis_revision": f"a-{state.analysis_revision}",
            "data_revision": state.data_revision,
            "settings_revision": state.settings_revision,
        },
    })


@preview_router.post("/source/snapshot/preview", response_model=SourcePreviewResponse,
                     responses={415: {"description": "Snapshot upload requires application/json."}},
                     summary="Preview a full source replacement without writing working data")
def preview_source_snapshot(request: Request, body: SourcePreviewRequest,
                            user: AuthenticatedUser = Depends(require_session)):
    from app.api.lab import _assert_current, _json, _revision
    from app.domain.lab_source_diff import diff_snapshots

    baseline = _assert_current(request, body.expected_revision)
    try:
        difference = diff_snapshots(
            baseline.snapshot, body.candidate_snapshot,
            settings=baseline.settings, as_of=baseline.evaluated_at,
        )
    except ValueError:
        raise _problem(422, "invalid_snapshot", "Snapshot could not be evaluated.") from None
    document = body.candidate_snapshot.canonical_json().encode("utf-8")
    checksum = snapshot_checksum(document.decode("utf-8"))
    preview_id = token_urlsafe(32)
    with _preview_lock:
        entries = _store(request)
        _assert_current(request, body.expected_revision)
        entries[preview_id] = _SourcePreview(
            document, checksum, body.expected_revision, baseline.evaluated_at,
            monotonic() + _PREVIEW_TTL_SECONDS, user.session_hash,
        )
    return {
        **_revision(baseline), "preview_id": preview_id, "candidate_checksum": checksum,
        "source_checksum": snapshot_checksum(baseline.snapshot_json),
        "expires_at": _json(datetime.now(timezone.utc) + timedelta(seconds=_PREVIEW_TTL_SECONDS)),
        "as_of": _json(baseline.evaluated_at), "horizon_days": baseline.horizon_days,
        "model_version": baseline.model_version, "diff": _json(difference),
    }


@preview_router.post("/source/snapshot/preview/{preview_id}/confirm", response_model=SourceConfirmResponse,
                     responses={415: {"description": "Confirmation requires application/json."}},
                     openapi_extra={"parameters": [{
                         "name": "If-Match", "in": "header", "required": True,
                         "schema": {"type": "string"},
                         "description": 'Exact preview baseline ETag, such as "lab-a-12-d-7-s-3".',
                     }]},
                     summary="Atomically confirm the exact source bytes and baseline revision")
def confirm_source_snapshot(request: Request, preview_id: str, body: SourceConfirmRequest,
                            user: AuthenticatedUser = Depends(require_session)):
    if_match = request.headers.get("If-Match")
    if if_match is None:
        raise _problem(400, "if_match_required", "If-Match is required.")
    with _preview_lock:
        entries = _store(request)
        entry = entries.get(preview_id)
        if entry is None or entry.session_hash != user.session_hash:
            raise _conflict(request, "preview_unavailable")
        expected = entry.revision
        etag = f'"lab-{expected.analysis_revision}-d-{expected.data_revision}-s-{expected.settings_revision}"'
        if (if_match != etag or body.expected_revision != expected
                or body.candidate_checksum != entry.checksum):
            del entries[preview_id]
            raise _conflict(request, "preview_mismatch")
        try:
            snapshot = SourceSnapshot.model_validate_json(entry.document)
            if snapshot_checksum(snapshot.canonical_json()) != entry.checksum:
                del entries[preview_id]
                raise _conflict(request, "preview_mismatch")
            result = request.app.state.coordinator.replace_snapshot(
                snapshot, expected_revision=(expected.analysis_revision, expected.data_revision,
                                             expected.settings_revision),
            )
        except (SourceRevisionConflict, ConcurrentWriteError):
            del entries[preview_id]
            raise _conflict(request) from None
        except (ValidationError, ValueError):
            del entries[preview_id]
            raise _conflict(request, "preview_mismatch") from None
        del entries[preview_id]
        evaluation = result.evaluation
        return {
            **_snapshot_response(result), "settings_revision": result.settings_revision,
            "preview_id": preview_id,
            "preview_as_of": entry.as_of.isoformat().replace("+00:00", "Z"),
            "as_of": evaluation.evaluated_at.isoformat().replace("+00:00", "Z"),
        }


router.include_router(preview_router)
