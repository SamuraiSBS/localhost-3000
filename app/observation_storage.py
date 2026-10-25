"""Explicit observation writers owned by the caller's SQLite transaction.

Call after publishing the source/workflow row and before committing that SAME
transaction. These functions never open a connection, commit or roll back. They
do not calculate risk, infer churn, or treat missing records as deletion.
"""

from __future__ import annotations

from datetime import datetime
import json
import sqlite3
from collections.abc import Mapping

from app.contracts import MAX_SOURCE_ID, MODEL_VERSION, ModelSettings, SourceSnapshot
from app.storage import canonical_settings_json, parse_timestamp, snapshot_checksum, utc_timestamp


def _transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise ValueError("active_observation_transaction_required")


def _integer(value: int, minimum: int = 1) -> None:
    if type(value) is not int or not minimum <= value <= MAX_SOURCE_ID:
        raise ValueError("invalid_observation_revision_or_id")


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise ValueError("observation_timestamp_required")
    return utc_timestamp(value)


def initialize_observation_schema(connection: sqlite3.Connection) -> None:
    """Additive schema creation; caller explicitly owns migration/commit."""
    _transaction(connection)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_observation_baseline ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
        "data_revision INTEGER NOT NULL CHECK(data_revision > 0), checksum TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_observation_batches ("
        "data_revision INTEGER PRIMARY KEY CHECK(data_revision > 0), "
        "previous_data_revision INTEGER NOT NULL CHECK(previous_data_revision >= 0), "
        "checksum TEXT NOT NULL, previous_checksum TEXT, received_at TEXT NOT NULL, "
        "comparison_settings_revision INTEGER NOT NULL CHECK(comparison_settings_revision > 0), "
        "comparison_model_version TEXT NOT NULL, comparison_as_of TEXT NOT NULL, "
        "CHECK(data_revision = previous_data_revision + 1))")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_observations ("
        "data_revision INTEGER NOT NULL REFERENCES source_observation_batches(data_revision), "
        "customer_id INTEGER NOT NULL CHECK(customer_id > 0), "
        "entity TEXT NOT NULL CHECK(entity IN ('account','service')), "
        "entity_id INTEGER NOT NULL CHECK(entity_id > 0), "
        "field TEXT NOT NULL CHECK(field IN ('balance','autoProlong','status','disappeared')), "
        "before_json TEXT NOT NULL, after_json TEXT NOT NULL, "
        "PRIMARY KEY(data_revision, entity, entity_id, field))")
    connection.execute("CREATE INDEX IF NOT EXISTS source_observations_customer "
                       "ON source_observations(customer_id, data_revision, entity, entity_id, field)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS workflow_events ("
        "customer_id INTEGER NOT NULL CHECK(customer_id > 0), "
        "revision INTEGER NOT NULL CHECK(revision > 0), "
        "previous_revision INTEGER NOT NULL CHECK(previous_revision >= 0), "
        "kind TEXT NOT NULL CHECK(kind = 'workflow'), author TEXT NOT NULL, "
        "received_at TEXT NOT NULL, changes_json TEXT NOT NULL, "
        "PRIMARY KEY(customer_id, revision), CHECK(revision = previous_revision + 1))")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS model_observation_events ("
        "settings_revision INTEGER PRIMARY KEY CHECK(settings_revision > 1), "
        "previous_settings_revision INTEGER NOT NULL CHECK(previous_settings_revision > 0), "
        "kind TEXT NOT NULL CHECK(kind = 'model'), received_at TEXT NOT NULL, "
        "comparison_as_of TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, "
        "CHECK(settings_revision = previous_settings_revision + 1))")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _snapshot(value: SourceSnapshot) -> SourceSnapshot:
    if not isinstance(value, SourceSnapshot):
        raise TypeError("complete_source_snapshot_required")
    # Revalidate: frozen Pydantic graphs may still contain mutable extra values.
    return SourceSnapshot.model_validate_json(value.canonical_json())


def ensure_observation_baseline(connection: sqlite3.Connection) -> bool:
    """Pin the current published source once on feature startup; no events.

    Return True if a baseline was installed, False when no source exists or the
    baseline already exists. Never advance an existing baseline on startup.
    """
    _transaction(connection)
    row = connection.execute("SELECT data_revision, document, checksum FROM source_snapshots "
                             "WHERE singleton=1").fetchone()
    if row is None:
        return False
    revision, document, checksum = tuple(row)
    _integer(revision)
    parsed = SourceSnapshot.model_validate_json(document)
    if parsed.canonical_json() != document or snapshot_checksum(document) != checksum:
        raise ValueError("baseline_source_mismatch")
    cursor = connection.execute("INSERT OR IGNORE INTO source_observation_baseline VALUES (1, ?, ?)",
                                (revision, checksum))
    return cursor.rowcount == 1


def record_source_observations(
    connection: sqlite3.Connection, previous_snapshot: SourceSnapshot | None,
    snapshot: SourceSnapshot, *, previous_data_revision: int, data_revision: int,
    received_at: datetime, comparison_settings_revision: int,
    comparison_model_version: str, comparison_as_of: datetime,
) -> int:
    """Record actual source transitions, returning the number of new events.

    Both complete snapshots are caller supplied. The caller must obtain the old
    snapshot in its publication transaction before replacing the singleton; the
    current singleton is checked here. A first publication has previous=None,
    revision 0 -> 1 and records a baseline without fabricating events. If feature
    startup did not establish a baseline, the new snapshot becomes the first
    observation point, even when a previous snapshot was supplied.
    Repeating an identical publication returns zero; changed provenance for the
    same revision is rejected. Model/settings/as_of describe one FIXED comparison
    context, never a pair of independently refreshed evaluations.
    """
    _transaction(connection)
    for value, minimum in ((previous_data_revision, 0), (data_revision, 1),
                           (comparison_settings_revision, 1)):
        _integer(value, minimum)
    if data_revision != previous_data_revision + 1:
        raise ValueError("consecutive_source_revisions_required")
    if (previous_snapshot is None) != (previous_data_revision == 0):
        raise ValueError("previous_complete_snapshot_required")
    if comparison_model_version != MODEL_VERSION:
        raise ValueError("unsupported_observation_model")
    timestamp, as_of = _timestamp(received_at), _timestamp(comparison_as_of)
    current = _snapshot(snapshot)
    previous = _snapshot(previous_snapshot) if previous_snapshot is not None else None
    document = current.canonical_json()
    checksum = snapshot_checksum(document)
    previous_checksum = snapshot_checksum(previous.canonical_json()) if previous else None
    row = connection.execute("SELECT data_revision, document, checksum, received_at "
                             "FROM source_snapshots WHERE singleton=1").fetchone()
    if row is None or tuple(row) != (data_revision, document, checksum, timestamp):
        raise ValueError("source_publication_mismatch")
    if previous_checksum == checksum:
        raise ValueError("unchanged_source_is_not_publication")
    metadata = (data_revision, previous_data_revision, checksum, previous_checksum,
                timestamp, comparison_settings_revision, comparison_model_version, as_of)
    seen = connection.execute("SELECT * FROM source_observation_batches WHERE data_revision=?",
                              (data_revision,)).fetchone()
    if seen:
        if tuple(seen) != metadata:
            raise ValueError("observation_publication_conflict")
        return 0
    baseline = connection.execute("SELECT data_revision, checksum FROM source_observation_baseline "
                                  "WHERE singleton=1").fetchone()
    if baseline and tuple(baseline) != (previous_data_revision, previous_checksum):
        raise ValueError("observation_history_discontinuity")
    events = []
    if previous is not None and baseline is not None:
        for entity, old_rows, new_rows, fields in (
            ("account", previous.accounts, current.accounts, ("balance",)),
            ("service", previous.services, current.services, ("autoProlong", "status")),
        ):
            new_by_id = {item.id: item for item in new_rows}
            for old in old_rows:
                customer_id = old.id if entity == "account" else old.ownerId
                new = new_by_id.get(old.id)
                if new is None:
                    events.append((data_revision, customer_id, entity, old.id,
                                   "disappeared", "true", "null"))
                else:
                    for field in fields:
                        before, after = getattr(old, field), getattr(new, field)
                        if before != after:
                            events.append((data_revision, customer_id, entity, old.id,
                                           field, _json(before), _json(after)))
    events.sort(key=lambda event: (event[2], event[3], event[4]))
    connection.execute("INSERT INTO source_observation_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?)", metadata)
    connection.executemany("INSERT INTO source_observations VALUES (?, ?, ?, ?, ?, ?, ?)", events)
    connection.execute("INSERT INTO source_observation_baseline VALUES (1, ?, ?) "
                       "ON CONFLICT(singleton) DO UPDATE SET data_revision=excluded.data_revision, "
                       "checksum=excluded.checksum", (data_revision, checksum))
    return len(events)


def record_workflow_event(
    connection: sqlite3.Connection, *, customer_id: int, previous_revision: int,
    revision: int, before: Mapping, after: Mapping, author: str, received_at: datetime,
) -> bool:
    """Record a published contact/manual update separately from source evidence.

    Supply the complete records returned by WorkflowStore._get/update, after its
    CAS and UPDATE but before commit. Notes are literal text, never executable.
    No-op calls return False and do not manufacture an event.
    """
    _transaction(connection)
    _integer(customer_id)
    _integer(previous_revision, 0)
    _integer(revision)
    if revision != previous_revision + 1:
        raise ValueError("consecutive_workflow_revisions_required")
    if not isinstance(author, str) or not author or len(author) > 256:
        raise ValueError("invalid_observation_author")
    timestamp = _timestamp(received_at)
    fields = ("status", "note", "next_contact_at", "manual_outcome")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise TypeError("complete_workflow_records_required")
    if any(key not in record for record in (before, after) for key in (*fields, "revision", "customer_id")):
        raise ValueError("complete_workflow_records_required")
    for record in (before, after):
        _integer(record["revision"], 0)
        if (record["status"] not in ("new", "in_progress", "deferred", "processed")
                or record["manual_outcome"] not in ("none", "contacted", "topup_reported",
                                                   "renewal_reported", "no_response", "declined")
                or not isinstance(record["note"], str) or len(record["note"]) > 2000):
            raise ValueError("invalid_workflow_observation_record")
        contact = record["next_contact_at"]
        if contact is not None:
            if not isinstance(contact, str) or parse_timestamp(contact) is None:
                raise ValueError("invalid_workflow_contact_timestamp")
    if (before["revision"] != previous_revision or after["revision"] != revision
            or str(before["customer_id"]) != str(customer_id) or str(after["customer_id"]) != str(customer_id)):
        raise ValueError("workflow_record_mismatch")
    changes = {field: {"before": before[field], "after": after[field]}
               for field in fields if before[field] != after[field]}
    row = connection.execute("SELECT revision, status, note, next_contact_at, manual_outcome, author, updated_at "
                             "FROM workflow_records WHERE customer_id=?", (customer_id,)).fetchone()
    if row is None or tuple(row) != (revision, *(after[field] for field in fields), author, timestamp):
        raise ValueError("workflow_publication_mismatch")
    if not changes:
        return False
    metadata = (customer_id, revision, previous_revision, "workflow", author, timestamp, _json(changes))
    seen = connection.execute("SELECT * FROM workflow_events WHERE customer_id=? AND revision=?",
                              (customer_id, revision)).fetchone()
    if seen:
        if tuple(seen) != metadata:
            raise ValueError("workflow_event_conflict")
        return False
    connection.execute("INSERT INTO workflow_events VALUES (?, ?, ?, ?, ?, ?, ?)", metadata)
    return True


def record_model_event(
    connection: sqlite3.Connection, previous_settings: ModelSettings, settings: ModelSettings, *,
    previous_settings_revision: int, settings_revision: int, received_at: datetime,
    comparison_as_of: datetime,
) -> bool:
    """Record a settings publication separately; never infer source changes."""
    _transaction(connection)
    _integer(previous_settings_revision)
    _integer(settings_revision)
    if settings_revision != previous_settings_revision + 1:
        raise ValueError("consecutive_settings_revisions_required")
    before, after = canonical_settings_json(previous_settings), canonical_settings_json(settings)
    ModelSettings.model_validate_json(before)
    ModelSettings.model_validate_json(after)
    if before == after:
        raise ValueError("unchanged_settings_is_not_publication")
    timestamp, as_of = _timestamp(received_at), _timestamp(comparison_as_of)
    row = connection.execute("SELECT settings_revision, document, updated_at FROM settings "
                             "WHERE singleton=1").fetchone()
    if row is None or tuple(row) != (settings_revision, after, timestamp):
        raise ValueError("settings_publication_mismatch")
    metadata = (settings_revision, previous_settings_revision, "model", timestamp, as_of, before, after)
    seen = connection.execute("SELECT * FROM model_observation_events WHERE settings_revision=?",
                              (settings_revision,)).fetchone()
    if seen:
        if tuple(seen) != metadata:
            raise ValueError("model_event_conflict")
        return False
    connection.execute("INSERT INTO model_observation_events VALUES (?, ?, ?, ?, ?, ?, ?)", metadata)
    return True
