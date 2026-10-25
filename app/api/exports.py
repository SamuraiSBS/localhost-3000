"""Read-only CSV downloads with the same filters as the customer ranking."""

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, StrictStr, model_validator

from app.api.customers import list_customers
from app.api.lab import LabRoute, _assert_current, _error
from app.auth import AuthenticatedUser, require_session
from app.contracts import SourceId
from app.domain.lab_worklist import build_worklist
from app.export_csv import MAX_EXPORT_ROWS, customer_csv
from app.lab_contracts import LabDTO, RevisionTuple

router = APIRouter(route_class=LabRoute)


class ExportFilters(LabDTO):
    q: StrictStr = Field(default="", max_length=1000)
    risk_level: Literal["low", "medium", "high", "unknown"] | None = None
    sort_by: Literal["id", "email", "balance", "totalReplenished", "risk_score"] = "risk_score"
    sort_order: Literal["asc", "desc"] = "desc"


class CsvExportRequest(LabDTO):
    expected_revision: RevisionTuple
    scope: Literal["filtered", "worklist"]
    filters: ExportFilters = Field(default_factory=ExportFilters)
    customer_ids: tuple[SourceId, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def check_scope(self):
        if len(set(self.customer_ids)) != len(self.customer_ids):
            raise ValueError("duplicate_customer_ids")
        if self.scope == "filtered" and self.customer_ids:
            raise ValueError("filtered_export_does_not_accept_ids")
        if self.scope == "worklist" and self.filters != ExportFilters():
            raise ValueError("worklist_export_does_not_accept_filters")
        return self


@router.post("/exports/customers.csv", response_class=Response,
             responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
             summary="Export full filtered customers or selected worklist rows",
             description=("Exports the unpaginated filtered result or selected worklist rows from one "
                          "immutable evaluation. Includes score, reasons, monetary risk, expiry, source "
                          "provenance and a single SQLite read snapshot of workflow status, revision, "
                          "next contact and manual outcome. Notes and author are not exported."))
def export_customers(request: Request, body: CsvExportRequest,
                     user: AuthenticatedUser = Depends(require_session)):
    evaluation = _assert_current(request, body.expected_revision)
    expiries = None
    if body.scope == "filtered":
        # Call the ranking implementation with no pagination truncation. Its
        # endpoint limits are HTTP validation rules; selection/sorting is shared.
        result = list_customers(request, **body.filters.model_dump(),
                                limit=MAX_EXPORT_ROWS + 1, offset=0, _user=user)
        _assert_current(request, body.expected_revision)
        if result["total"] > MAX_EXPORT_ROWS:
            raise _error(413, "export_too_large", "Narrow the filter to at most 10000 customers.")
        ids = [int(row["id"]) for row in result["items"]]
    else:
        candidates = build_worklist(evaluation, limit=50).rows
        selected = set(body.customer_ids)
        if selected - {row.customer_id for row in candidates}:
            raise _error(422, "invalid_worklist_selection", "Refresh the worklist and select its rows.")
        ids = [row.customer_id for row in candidates if row.customer_id in selected]
        expiries = {row.customer_id: row.expires_at for row in candidates}
    try:
        # Workflow fields are captured in one SQLite read transaction. Their
        # record revision is included in the file, separate from source/model
        # revisions; notes and authors remain private to the work queue.
        workflow_records = request.app.state.workflow_store.get_many(ids)
        content = customer_csv(evaluation, ids, scope=body.scope, expiries=expiries,
                               workflow_records=workflow_records)
    except ValueError:
        raise _error(413, "export_too_large", "Narrow the selection; the CSV exceeds its export limit.") from None
    _assert_current(request, body.expected_revision)
    return Response(content, media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="customers-{body.scope}.csv"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "X-Export-Scope": body.scope, "X-Export-Row-Count": str(len(ids)),
        "X-Analysis-Revision": evaluation.analysis_revision,
        "X-Data-Revision": str(evaluation.data_revision),
        "X-Settings-Revision": str(evaluation.settings_revision),
        "X-Export-As-Of": evaluation.evaluated_at.isoformat().replace("+00:00", "Z"),
    })
