"""Independent contact bookkeeping; never modifies source, settings or scores.

GET/polling has no side effects. Source observations are an explicit internal
publication boundary for B29, not an endpoint or a model-refresh hook. B29 must
compute before/after with identical model/settings and a fixed evaluation time,
then call observe_published_source on the SAME transaction that publishes the
complete source snapshot. No observation is inferred from manual outcomes.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Callable, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.contracts import MAX_SOURCE_ID
from app.storage import utc_timestamp

WorkflowStatus = Literal["new", "in_progress", "deferred", "processed"]
ManualOutcome = Literal["none", "contacted", "topup_reported", "renewal_reported", "no_response", "declined"]
MAX_WORKFLOW_BODY_BYTES = 16384


class WorkflowPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0, le=2**63 - 2)
    status: WorkflowStatus | None = None
    note: str | None = Field(default=None, max_length=2000)
    next_contact_at: AwareDatetime | None = None
    manual_outcome: ManualOutcome | None = None

    @field_validator("note")
    @classmethod
    def plain_text(cls, value):
        # HTML is stored as literal plain text, never interpreted or marked safe.
        if value is not None and any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in value):
            raise ValueError("note_contains_control_characters")
        return value


class WorkflowRevisionConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision


def _default(customer_id: int) -> dict:
    return dict(customer_id=str(customer_id), revision=0, status="new", note="",
                next_contact_at=None, manual_outcome="none", author=None, updated_at=None)


class WorkflowStore:
    def __init__(self, database_path: str | Path, *, clock: Callable[[], datetime] | None = None):
        if str(database_path) == ":memory:":
            raise ValueError("file_backed_database_required")
        self.database_path = Path(database_path).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _connect(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.database_path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        # Namespaced additive migration also upgrades existing application DBs.
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self.initialize_schema(connection)
            from app.observation_storage import initialize_observation_schema
            initialize_observation_schema(connection)

    @staticmethod
    def initialize_schema(connection: sqlite3.Connection):
        """Add workflow tables on a repository-owned migration transaction."""
        if not connection.in_transaction:
            raise ValueError("active_workflow_transaction_required")
        connection.execute("CREATE TABLE IF NOT EXISTS workflow_records ("
            "customer_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, status TEXT NOT NULL, "
            "note TEXT NOT NULL, next_contact_at TEXT, manual_outcome TEXT NOT NULL, "
            "author TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS workflow_observations ("
            "customer_id INTEGER PRIMARY KEY, data_revision INTEGER NOT NULL)")

    @staticmethod
    def _get(connection, customer_id: int):
        row = connection.execute("SELECT * FROM workflow_records WHERE customer_id=?", (customer_id,)).fetchone()
        return {**dict(row), "customer_id": str(customer_id)} if row else _default(customer_id)

    def get(self, customer_id: int) -> dict:
        with closing(self._connect()) as connection:
            return self._get(connection, customer_id)

    def get_many(self, customer_ids) -> dict[str, dict]:
        """One SQLite read snapshot for a CSV/list's coherent workflow statuses."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            return {str(customer_id): self._get(connection, customer_id) for customer_id in customer_ids}

    def update(self, customer_id: int, patch: WorkflowPatch, *, author: str) -> dict:
        if type(customer_id) is not int or not 0 < customer_id <= MAX_SOURCE_ID:
            raise ValueError("invalid_customer_id")
        if not author or len(author) > 256:
            raise ValueError("invalid_author")
        now = self.clock()
        timestamp = utc_timestamp(now)
        fields = patch.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if not fields or any(fields.get(key, "valid") is None for key in ("status", "note", "manual_outcome")):
            raise ValueError("workflow_patch_requires_non_null_fields")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._get(connection, customer_id)
                if current["revision"] != patch.expected_revision:
                    raise WorkflowRevisionConflict(current["revision"])
                if "next_contact_at" in fields and fields["next_contact_at"] is not None:
                    fields["next_contact_at"] = utc_timestamp(fields["next_contact_at"])
                updated = {**current, **fields}
                if updated["status"] == "deferred" and (current["status"] != "deferred" or "next_contact_at" in fields):
                    contact = updated["next_contact_at"]
                    if contact is None or datetime.fromisoformat(contact.replace("Z", "+00:00")) <= now:
                        raise ValueError("deferred_requires_future_next_contact")
                # Exact retries/no-ops do not fabricate new contact events.
                if all(updated[key] == current[key] for key in fields):
                    connection.commit()
                    return current
                updated.update(revision=current["revision"] + 1, author=author, updated_at=timestamp)
                connection.execute("INSERT INTO workflow_records VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(customer_id) DO UPDATE SET revision=excluded.revision, status=excluded.status, "
                    "note=excluded.note, next_contact_at=excluded.next_contact_at, manual_outcome=excluded.manual_outcome, "
                    "author=excluded.author, updated_at=excluded.updated_at",
                    (customer_id, updated["revision"], updated["status"], updated["note"], updated["next_contact_at"],
                     updated["manual_outcome"], author, timestamp))
                from app.observation_storage import record_workflow_event
                record_workflow_event(
                    connection, customer_id=customer_id, previous_revision=current["revision"],
                    revision=updated["revision"], before=current, after=updated, author=author, received_at=now,
                )
                connection.commit()
                return updated
            except BaseException:
                connection.rollback()
                raise

    def observe_published_source(self, connection: sqlite3.Connection, *, customer_id: int,
                                 previous_data_revision: int, data_revision: int,
                                 before_risk: str, after_risk: str,
                                 before_gap_cents: int, after_gap_cents: int,
                                 received_at: datetime, comparison_settings_revision: int,
                                 comparison_model_version: str, comparison_as_of: datetime) -> bool:
        """B29 publication-only hook; returns whether a processed record reopened.

        Caller owns commit/rollback, uses this store's database and supplies two
        observations computed at the SAME supplied settings/model/as_of. This
        API deliberately accepts no pair of independently changing evaluations.
        The source row must already be published inside that transaction. A
        first snapshot, repeat, preview, polling or model-only refresh cannot
        reopen. Unknown is not ordered above/below known risk. A new deficit
        means zero -> positive, not growth of an existing deficit. Deferral is an
        explicit manual hold: it never auto-expires or reopens; the operator
        resumes it via CAS. Reopening preserves the manual outcome and note.
        """
        if (not connection.in_transaction or data_revision != previous_data_revision + 1
                or previous_data_revision < 1 or comparison_settings_revision < 1
                or not comparison_model_version):
            raise ValueError("published_source_transition_required")
        databases = connection.execute("PRAGMA database_list").fetchall()
        if not any(row[1] == "main" and Path(row[2]).resolve() == self.database_path for row in databases):
            raise ValueError("workflow_publication_requires_same_database")
        utc_timestamp(comparison_as_of)
        timestamp = utc_timestamp(received_at)
        published = connection.execute("SELECT data_revision FROM source_snapshots WHERE singleton=1").fetchone()
        if published is None or published[0] != data_revision:
            raise ValueError("source_revision_not_published_in_transaction")
        levels = {"low": 0, "medium": 1, "high": 2}
        if before_risk not in {*levels, "unknown"} or after_risk not in {*levels, "unknown"}:
            raise ValueError("invalid_risk_level")
        if any(type(gap) is not int or gap < 0 for gap in (before_gap_cents, after_gap_cents)):
            raise ValueError("invalid_funding_gap")
        seen = connection.execute("SELECT data_revision FROM workflow_observations WHERE customer_id=?", (customer_id,)).fetchone()
        if seen and seen[0] >= data_revision:
            return False
        connection.execute("INSERT INTO workflow_observations VALUES (?, ?) ON CONFLICT(customer_id) "
                           "DO UPDATE SET data_revision=excluded.data_revision", (customer_id, data_revision))
        increased = before_risk in levels and after_risk in levels and levels[after_risk] > levels[before_risk]
        new_deficit = before_gap_cents == 0 and after_gap_cents > 0
        if not (increased or new_deficit):
            return False
        cursor = connection.execute("UPDATE workflow_records SET status='new', revision=revision+1, "
            "author='system:source', updated_at=?, next_contact_at=NULL WHERE customer_id=? AND status='processed'",
            (timestamp, customer_id))
        return cursor.rowcount == 1
