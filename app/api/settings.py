"""Versioned model settings endpoints with partial updates and ETags."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.auth import AuthenticatedUser, require_session
from app.contracts import SettingsPatch
from app.domain import DEFAULT_FEATURE_REGISTRY
from app.evaluation import Evaluation, SettingsRevisionConflict

router = APIRouter()
# Caddy's `encode zstd gzip` appends the selected content encoding to strong
# entity tags. Accept only those documented proxy variants, then compare the
# underlying settings revision exactly as for the uncompressed representation.
_ETAG_PATTERN = re.compile(r'"settings-([1-9][0-9]*)(?:-(?:zstd|gzip))?"\Z')


def _etag(revision: int) -> str:
    return f'"settings-{revision}"'


def _parse_if_match(value: str | None) -> int:
    if value is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "if_match_required",
                "message": "Read model settings and send the current If-Match ETag.",
                "details": [{"path": "If-Match", "reason": "required"}],
            },
        )
    match = _ETAG_PATTERN.fullmatch(value.strip())
    if match is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "if_match_invalid",
                "message": "If-Match must contain the current settings ETag.",
                "details": [{"path": "If-Match", "reason": "invalid_etag"}],
            },
        )
    return int(match.group(1))


def _payload(evaluation: Evaluation, *, include_analysis_revision: bool = False) -> dict[str, object]:
    settings = evaluation.settings
    payload: dict[str, object] = {
        "settings_revision": evaluation.settings_revision,
        "model_version": settings.model_version,
        "horizon_days": settings.horizon_days,
        "thresholds": settings.thresholds.model_dump(mode="json"),
        "weights": settings.model_dump(mode="json")["weights"],
        "features": DEFAULT_FEATURE_REGISTRY.metadata(),
    }
    if include_analysis_revision:
        payload["analysis_revision"] = evaluation.analysis_revision
    return payload


def _current_evaluation(request: Request) -> Evaluation:
    evaluation = request.app.state.coordinator.current
    if evaluation is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "evaluation_unavailable",
                "message": "No active source snapshot is available.",
            },
        )
    return evaluation


@router.get("/model/settings")
def get_settings(
    request: Request,
    response: Response,
    _user: AuthenticatedUser = Depends(require_session),
) -> dict[str, object]:
    evaluation = _current_evaluation(request)
    response.headers["ETag"] = _etag(evaluation.settings_revision)
    return _payload(evaluation)


@router.patch("/model/settings")
def patch_settings(
    request: Request,
    response: Response,
    patch: SettingsPatch,
    if_match: str | None = Header(default=None, alias="If-Match"),
    _user: AuthenticatedUser = Depends(require_session),
) -> dict[str, object]:
    expected_revision = _parse_if_match(if_match)
    coordinator = request.app.state.coordinator
    try:
        result = coordinator.patch_settings(
            patch,
            expected_settings_revision=expected_revision,
        )
    except SettingsRevisionConflict as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "settings_revision_conflict",
                "message": "Settings changed since the supplied ETag was read.",
                "current_revision": error.current_revision,
                "details": [{"path": "If-Match", "reason": "stale_revision"}],
            },
        ) from None
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from None

    evaluation = result.evaluation
    if evaluation is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "evaluation_unavailable",
                "message": "No active source snapshot is available.",
            },
        )
    response.headers["ETag"] = _etag(result.settings_revision)
    return _payload(evaluation, include_analysis_revision=True)
