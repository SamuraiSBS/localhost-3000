"""Atomic full replacement of the active source snapshot."""

from __future__ import annotations

from collections import Counter
from datetime import timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.auth import AuthenticatedUser, require_session
from app.contracts import SourceSnapshot

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


async def _read_body(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise _problem(415, "unsupported_media_type", "Snapshot upload requires JSON.")

    maximum = request.app.state.config.max_snapshot_bytes
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
