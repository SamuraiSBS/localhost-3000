"""Bounded UTF-8 CSV over immutable customer evaluations."""

import csv
from datetime import datetime
from decimal import Decimal
from io import StringIO

from app.storage import snapshot_checksum

MAX_EXPORT_BYTES = 8 * 1024 * 1024
MAX_EXPORT_ROWS = 10000
FIELDS = (
    "scope", "row_count", "customer_id", "email", "risk_score", "risk_level",
    "reasons", "monetary_risk_cents", "currency", "expires_at", "analysis_revision",
    "data_revision", "settings_revision", "as_of", "source_checksum", "model_version",
    "horizon_days", "workflow_status", "workflow_revision", "workflow_next_contact_at",
    "workflow_manual_outcome", "workflow_updated_at",
)


def text_cell(value):
    """Preserve values and prefix formula/control hazards with a text marker.

    Checking after whitespace/control characters also covers importers which trim
    prefixes. Embedded tabs/newlines are preserved by CSV quoting, as plain text.
    """
    if value is None:
        return "unknown"
    if isinstance(value, datetime):
        value = value.isoformat().replace("+00:00", "Z")
    elif isinstance(value, Decimal):
        value = format(value, "f")
    value = str(value)
    start = 0
    while start < len(value) and (value[start].isspace() or ord(value[start]) < 32 or value[start] in "\x7f\ufeff"):
        start += 1
    probe = value[start:]
    if (value and (ord(value[0]) < 32 or value[0] == "\x7f")) or probe.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def customer_csv(evaluation, customer_ids, *, scope, expiries=None, workflow_records=None):
    if len(customer_ids) > MAX_EXPORT_ROWS:
        raise ValueError("export_too_large")
    accounts = {account.id: account for account in evaluation.snapshot.accounts}
    scores = {score.customer_id: score for score in evaluation.customers}
    services = {service.id: service for service in evaluation.snapshot.services}
    checksum = snapshot_checksum(evaluation.snapshot_json)
    output = StringIO(newline="")
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(FIELDS)
    chunks = [output.getvalue().encode("utf-8-sig")]
    byte_count = len(chunks[0])
    for customer_id in customer_ids:
        account, score = accounts[customer_id], scores[customer_id]
        workflow = (workflow_records or {}).get(str(customer_id), {
            "status": "new", "revision": 0, "next_contact_at": None,
            "manual_outcome": "none", "updated_at": None,
        })
        due_ids = {service.service_id for service in score.services if service.eligible and service.in_horizon}
        expiry = min((services[service_id].expiresAt for service_id in due_ids), default=None)
        if expiries is not None:
            expiry = expiries[customer_id]
        values = (
            scope, len(customer_ids), account.id, account.email, score.risk_score, score.risk_level,
            "\n".join(reason.explanation for reason in score.top_reasons) or score.unscored_reason or "",
            score.monetary_risk_cents, "EUR", expiry, evaluation.analysis_revision,
            evaluation.data_revision, evaluation.settings_revision, evaluation.evaluated_at,
            checksum, evaluation.model_version, evaluation.horizon_days,
            workflow["status"], workflow["revision"], workflow["next_contact_at"],
            workflow["manual_outcome"], workflow["updated_at"],
        )
        output.seek(0)
        output.truncate(0)
        writer.writerow([text_cell(value) for value in values])
        chunk = output.getvalue().encode("utf-8")
        byte_count += len(chunk)
        if byte_count > MAX_EXPORT_BYTES:
            raise ValueError("export_too_large")
        chunks.append(chunk)
    return b"".join(chunks)
