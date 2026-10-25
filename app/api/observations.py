"""Authenticated read-only history of source, contact and model publications."""

from contextlib import closing
import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from app.auth import AuthenticatedUser, require_session
from app.contracts import MAX_SOURCE_ID

router = APIRouter()


class ObservationEvent(BaseModel):
    event_id: str
    kind: Literal["source", "workflow", "model"]
    received_at: str
    customer_id: str | None = None
    entity: Literal["account", "service"] | None = None
    entity_id: str | None = None
    field: Literal["balance", "autoProlong", "status", "disappeared"] | None = None
    previous_data_revision: int | None = None
    data_revision: int | None = None
    comparison_settings_revision: int | None = None
    comparison_model_version: str | None = None
    comparison_as_of: str | None = None
    previous_workflow_revision: int | None = None
    workflow_revision: int | None = None
    previous_settings_revision: int | None = None
    settings_revision: int | None = None
    author: str | None = None
    before: object = None
    after: object = None
    changes: dict | None = None


class ObservationHistoryResponse(BaseModel):
    events: list[ObservationEvent]
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    source_baseline_revision: int | None = Field(
        description="Current source continuity cursor; this is not a fabricated historical event.")
    limitations: list[str]


# A single read transaction and UNION query provide coherent bounded pagination.
# Global settings changes apply to every customer, including filtered histories.
_HISTORY_SQL = """
SELECT 'source' AS kind, b.received_at AS received_at, s.data_revision AS revision,
       s.customer_id AS customer_id, s.entity AS entity, s.entity_id AS entity_id,
       s.field AS field, s.before_json AS before_json, s.after_json AS after_json,
       b.previous_data_revision AS previous_revision,
       b.comparison_settings_revision AS comparison_settings_revision,
       b.comparison_model_version AS comparison_model_version,
       b.comparison_as_of AS comparison_as_of, NULL AS author, NULL AS changes_json
FROM source_observations s JOIN source_observation_batches b USING(data_revision)
WHERE (? IS NULL OR s.customer_id = ?)
UNION ALL
SELECT 'workflow', received_at, revision, customer_id, NULL, NULL, NULL, NULL, NULL,
       previous_revision, NULL, NULL, NULL, author, changes_json
FROM workflow_events WHERE (? IS NULL OR customer_id = ?)
UNION ALL
SELECT 'model', received_at, settings_revision, NULL, NULL, NULL, NULL,
       before_json, after_json, previous_settings_revision, NULL, NULL,
       comparison_as_of, NULL, NULL
FROM model_observation_events
ORDER BY received_at, kind, revision, customer_id, entity, entity_id, field
LIMIT ? OFFSET ?
"""


def _event(row) -> dict:
    kind, revision = row["kind"], row["revision"]
    event = dict(kind=kind, received_at=row["received_at"])
    if kind == "source":
        event.update(
            event_id=f"source:{revision}:{row['entity']}:{row['entity_id']}:{row['field']}",
            customer_id=str(row["customer_id"]), entity=row["entity"], entity_id=str(row["entity_id"]),
            field=row["field"], previous_data_revision=row["previous_revision"], data_revision=revision,
            comparison_settings_revision=row["comparison_settings_revision"],
            comparison_model_version=row["comparison_model_version"], comparison_as_of=row["comparison_as_of"],
            before=json.loads(row["before_json"]), after=json.loads(row["after_json"]),
        )
    elif kind == "workflow":
        event.update(event_id=f"workflow:{row['customer_id']}:{revision}",
            customer_id=str(row["customer_id"]), previous_workflow_revision=row["previous_revision"],
            workflow_revision=revision, author=row["author"], changes=json.loads(row["changes_json"]))
    else:
        event.update(event_id=f"model:{revision}", previous_settings_revision=row["previous_revision"],
            settings_revision=revision, comparison_as_of=row["comparison_as_of"],
            before=json.loads(row["before_json"]), after=json.loads(row["after_json"]))
    return event


@router.get(
    "/observations", response_model=ObservationHistoryResponse, response_model_exclude_unset=True,
    summary="Read observed source changes and separate workflow/model events",
    description=("Session required. Read-only history, oldest first in deterministic order. "
                 "customer_id filters source and workflow events; global model events remain visible. "
                 "Source events retain received_at, consecutive revisions and fixed comparison provenance. "
                 "Missing source records are labeled disappeared. No history is inferred before baseline. "
                 "Offset pagination reads one SQLite snapshot per request; concurrent inserts can change later pages."),
    responses={401: {"description": "Authentication required."}},
    openapi_extra={"security": [{"SessionCookie": []}]},
)
def get_observations(
    request: Request, response: Response,
    customer_id: int | None = Query(default=None, gt=0, le=MAX_SOURCE_ID),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    _user: AuthenticatedUser = Depends(require_session),
) -> dict:
    with closing(request.app.state.repository._connect()) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        baseline = connection.execute("SELECT data_revision FROM source_observation_baseline WHERE singleton=1").fetchone()
        rows = connection.execute(_HISTORY_SQL, (customer_id, customer_id, customer_id, customer_id,
                                                limit + 1, offset)).fetchall()
    has_more = len(rows) > limit
    response.headers["Cache-Control"] = "no-store"
    return dict(events=[_event(row) for row in rows[:limit]], limit=limit, offset=offset,
                has_more=has_more, next_offset=offset + limit if has_more else None,
                source_baseline_revision=baseline[0] if baseline else None,
                limitations=["History starts when observations are enabled; earlier events are unknown.",
                             "Disappearance means absence from a complete source snapshot.",
                             "Manual outcomes are operator reports, separate from observed source changes.",
                             "Model events describe settings changes, not customer behavior or contact effectiveness."])
