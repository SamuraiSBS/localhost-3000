"""Authenticated contact workflow, intentionally independent of evaluation writes."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError

from app.auth import AuthenticatedUser, require_session
from app.contracts import MAX_SOURCE_ID
from app.api.customers import _current_evaluation
from app.workflow_storage import MAX_WORKFLOW_BODY_BYTES, WorkflowPatch, WorkflowRevisionConflict

router = APIRouter()


class WorkflowRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: StrictStr
    revision: StrictInt
    status: Literal["new", "in_progress", "deferred", "processed"]
    note: StrictStr
    next_contact_at: StrictStr | None
    manual_outcome: Literal["none", "contacted", "topup_reported", "renewal_reported", "no_response", "declined"]
    author: StrictStr | None
    updated_at: StrictStr | None


def _require_customer(request, customer_id):
    if _current_evaluation(request).customer(customer_id) is None:
        raise HTTPException(404, detail={"code": "customer_not_found", "message": "Customer not found."})


@router.get("/customers/{customer_id}/workflow", response_model=WorkflowRecordResponse,
            summary="Read a customer's manual workflow record")
def get_workflow(request: Request, customer_id: int = Path(gt=0, le=MAX_SOURCE_ID),
                 _user: AuthenticatedUser = Depends(require_session)) -> dict:
    _require_customer(request, customer_id)
    return request.app.state.workflow_store.get(customer_id)


@router.patch("/customers/{customer_id}/workflow", response_model=WorkflowRecordResponse,
              summary="Update a customer's manual workflow record",
              description=("Uses per-record revision CAS. Author and updated_at are derived from the "
                           "authenticated session and server clock. Notes are plain text. Manual outcomes "
                           "are not confirmed source observations."), openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {
        "schema": WorkflowPatch.model_json_schema()}}},
    "responses": {"409": {"description": "Workflow revision changed; reload before saving."},
                  "413": {"description": "Request exceeds 16384 bytes."},
                  "422": {"description": "Invalid update or deferred without future contact time."}},
})
async def patch_workflow(request: Request, customer_id: int = Path(gt=0, le=MAX_SOURCE_ID),
                         user: AuthenticatedUser = Depends(require_session)) -> dict:
    _require_customer(request, customer_id)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, detail={"code": "unsupported_media_type", "message": "JSON required."})
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WORKFLOW_BODY_BYTES:
            raise HTTPException(413, detail={"code": "request_too_large", "message": "Workflow request is too large."})
        body.extend(chunk)
    try:
        patch = WorkflowPatch.model_validate_json(bytes(body))
        return request.app.state.workflow_store.update(customer_id, patch, author=user.login)
    except WorkflowRevisionConflict as error:
        raise HTTPException(409, detail={"code": "workflow_revision_conflict", "message": "Workflow changed. Reload before saving.",
                                         "current_revision": error.current_revision}) from None
    except (ValidationError, ValueError):
        # Never reflect supplied notes or spoofed authors into errors.
        raise HTTPException(422, detail={"code": "invalid_workflow", "message": "Invalid workflow update."}) from None
