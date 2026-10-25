"""Read-only quality report for one coherent published source/settings revision."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import AuthenticatedUser, require_session
from app.data_quality import data_quality_report
from app.evaluation import Evaluation
from app.storage import PersistedState

router = APIRouter()
FRESHNESS_THRESHOLD = timedelta(hours=24)
_CAPTURE_ATTEMPTS = 3


class QualityCoverage(BaseModel):
    numerator: int
    denominator: int = Field(description="Every customer in this captured evaluation, including unknowns.")
    unknown: int
    state: Literal["empty", "partial", "complete"]


class QualityReason(BaseModel):
    code: str
    count: int
    customer_ids: list[int] = Field(description="Numeric customer IDs for navigation on this revision.")


class QualityWarning(QualityReason):
    customer_count: int = Field(description="Distinct customers; count measures warning occurrences.")


class QualityFreshness(BaseModel):
    state: Literal["fresh", "stale", "no_timestamp", "future_timestamp"]
    threshold_seconds: float
    age_seconds: float | None


class DataQualityResponse(BaseModel):
    analysis_revision: str
    data_revision: int
    settings_revision: int
    model_version: str
    as_of: str = Field(description="UTC evaluation instant; never used as a substitute for source receipt time.")
    received_at: str | None = Field(description="Persisted UTC source receipt time for data_revision, if known.")
    checked_at: str = Field(description="UTC report observation time; reading does not refresh source or evaluation.")
    freshness: QualityFreshness
    score_coverage: QualityCoverage
    monetary_coverage: QualityCoverage
    unknown_reasons: list[QualityReason]
    field_warnings: list[QualityWarning]
    warning_count: int
    source_completeness: Literal["not_established"]
    limitations: list[str]


def quality_now() -> datetime:
    """Read the observation clock only, independently of the calculation clock."""
    return datetime.now(timezone.utc)


def _capture(request: Request) -> tuple[Evaluation, PersistedState]:
    # load_state reads all persisted revisions/documents in one transaction.
    # Retain the immutable evaluation locally even if a later writer publishes.
    for _ in range(_CAPTURE_ATTEMPTS):
        evaluation = request.app.state.coordinator.current
        if evaluation is None:
            raise HTTPException(503, detail={
                "code": "evaluation_unavailable", "message": "No active source evaluation is available.",
            })
        state = request.app.state.repository.load_state()
        if (
            evaluation.analysis_revision == f"a-{state.analysis_revision}"
            and evaluation.data_revision == state.data_revision
            and evaluation.settings_revision == state.settings_revision
            and evaluation.snapshot_json == state.snapshot_json
            and evaluation.settings_json == state.settings_json
        ):
            return evaluation, state
    raise HTTPException(503, detail={
        "code": "evaluation_revision_conflict",
        "message": "Published evaluation and persisted source revisions differ. Retry the report.",
    }, headers={"Retry-After": "1"})


@router.get(
    "/data-quality", response_model=DataQualityResponse,
    summary="Read source freshness and evaluation coverage without refreshing data",
    description=(
        "Requires a session cookie. Captures one immutable evaluation and matching persisted "
        "source/settings revisions, retrying up to three times before 503 on a revision conflict. "
        "Receipt age is measured at checked_at with a fixed 24-hour (86400-second) threshold; "
        "exactly 24 hours is fresh. Missing receipt time is no_timestamp, future receipt time is "
        "future_timestamp. as_of is the evaluation time, received_at is source receipt time. "
        "Polling performs no evaluation refresh or source writes. Coverage includes every "
        "published customer, including unknowns, without filtering or pagination. Unknown and "
        "warning groups expose customer_ids for navigation. Source completeness is not established "
        "without an expected-count manifest; coverage does not measure model accuracy."
    ),
    responses={401: {"description": "Authentication required."},
               503: {"description": "No evaluation or no coherent persisted revision after three attempts."}},
    openapi_extra={"security": [{"SessionCookie": []}]},
)
def get_data_quality(
    request: Request, response: Response,
    _user: AuthenticatedUser = Depends(require_session),
    now: datetime = Depends(quality_now),
) -> dict[str, object]:
    evaluation, state = _capture(request)
    report = data_quality_report(
        evaluation, received_at=state.received_at, now=now,
        freshness_threshold=FRESHNESS_THRESHOLD,
    )
    report["limitations"].append("Source completeness requires an expected-count manifest, which is not available.")
    response.headers["Cache-Control"] = "no-store"
    return report
