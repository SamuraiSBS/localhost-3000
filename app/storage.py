"""File-backed SQLite persistence for the active source and model settings.

Every operation opens its own SQLite connection.  The repository never shares a
connection between threads and its write methods use optimistic revision checks
in addition to SQLite transactions.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from app.contracts import ModelSettings, SourceSnapshot

_SCHEMA_VERSION = 1


def utc_timestamp(value: datetime | None = None) -> str:
    """Format one aware timestamp in a stable RFC3339 UTC representation."""
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored_timestamp_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def canonical_settings_json(settings: ModelSettings) -> str:
    if not isinstance(settings, ModelSettings):
        raise TypeError("model_settings_required")
    return json.dumps(
        settings.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def snapshot_checksum(snapshot_json: str) -> str:
    return hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PersistedState:
    """A coherent read of persisted revisions and JSON documents."""

    data_revision: int
    settings_revision: int
    analysis_revision: int
    snapshot_json: str | None
    checksum: str | None
    received_at: datetime | None
    settings_json: str
    settings_updated_at: datetime

    @property
    def snapshot(self) -> SourceSnapshot | None:
        # Return a new DTO graph, so callers cannot mutate data held by a cache.
        return SourceSnapshot.model_validate_json(self.snapshot_json) if self.snapshot_json is not None else None

    @property
    def settings(self) -> ModelSettings:
        # ModelSettings is frozen, but its weights mapping is a dict; never hand
        # callers the same nested mapping that another caller could mutate.
        return ModelSettings.model_validate_json(self.settings_json)


class ConcurrentWriteError(RuntimeError):
    """The database changed after a coordinator read its input revisions."""


class SQLiteRepository:
    """Small file-backed repository for one active snapshot and settings row."""

    def __init__(self, database_path: str | Path, *, busy_timeout_ms: int = 5000):
        if isinstance(database_path, str) and database_path == ":memory:":
            raise ValueError("file_backed_database_required")
        if type(busy_timeout_ms) is not int or busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms_must_be_positive")
        self.database_path = Path(database_path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        self._source_observer: ContextVar[tuple[str, Callable] | None] = ContextVar(
            "source_publication_observer", default=None)

    @contextmanager
    def observe_source_publication(self, checksum: str, observer: Callable[[sqlite3.Connection], None]):
        """Bind an internal coordinator callback to one exact source publication.

        The context is local to the calling thread/task and cannot affect another
        writer. A different winning snapshot in a CAS race cannot run this hook.
        commit_snapshot's existing public call signature remains unchanged.
        """
        token = self._source_observer.set((checksum, observer))
        try:
            yield
        finally:
            self._source_observer.reset(token)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self, default_settings: ModelSettings | None = None) -> PersistedState:
        """Create schema and seed defaults only when no settings row exists."""
        settings_json = canonical_settings_json(default_settings or ModelSettings())
        now = utc_timestamp()
        with closing(self._connect()) as connection:
            # WAL permits readers during brief writes and is safe for the single
            # process/single writer architecture described for this repository.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS source_snapshots ("
                    "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                    "data_revision INTEGER NOT NULL CHECK (data_revision > 0), "
                    "document TEXT NOT NULL, checksum TEXT NOT NULL, received_at TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS settings ("
                    "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                    "settings_revision INTEGER NOT NULL CHECK (settings_revision > 0), "
                    "document TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS evaluation_state ("
                    "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                    "analysis_revision INTEGER NOT NULL CHECK (analysis_revision >= 0), "
                    "updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (_SCHEMA_VERSION, now),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO settings(singleton, settings_revision, document, updated_at) "
                    "VALUES (1, 1, ?, ?)",
                    (settings_json, now),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO evaluation_state(singleton, analysis_revision, updated_at) "
                    "VALUES (1, 0, ?)",
                    (now,),
                )
                from app.observation_storage import initialize_observation_schema, ensure_observation_baseline
                from app.workflow_storage import WorkflowStore
                initialize_observation_schema(connection)
                WorkflowStore.initialize_schema(connection)
                ensure_observation_baseline(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.load_state()

    def load_state(self) -> PersistedState:
        """Read snapshot, settings and their revisions in one SQLite snapshot."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            try:
                state = self._read_state(connection)
                connection.commit()
                return state
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _read_state(connection: sqlite3.Connection) -> PersistedState:
        settings_row = connection.execute(
            "SELECT settings_revision, document, updated_at FROM settings WHERE singleton = 1"
        ).fetchone()
        analysis_row = connection.execute(
            "SELECT analysis_revision FROM evaluation_state WHERE singleton = 1"
        ).fetchone()
        snapshot_row = connection.execute(
            "SELECT data_revision, document, checksum, received_at "
            "FROM source_snapshots WHERE singleton = 1"
        ).fetchone()
        if settings_row is None or analysis_row is None:
            raise RuntimeError("storage_not_initialized")
        settings_updated_at = parse_timestamp(str(settings_row["updated_at"]))
        if settings_updated_at is None:
            raise RuntimeError("settings_timestamp_missing")
        return PersistedState(
            data_revision=int(snapshot_row["data_revision"]) if snapshot_row else 0,
            settings_revision=int(settings_row["settings_revision"]),
            analysis_revision=int(analysis_row["analysis_revision"]),
            snapshot_json=str(snapshot_row["document"]) if snapshot_row else None,
            checksum=str(snapshot_row["checksum"]) if snapshot_row else None,
            received_at=parse_timestamp(str(snapshot_row["received_at"])) if snapshot_row else None,
            settings_json=str(settings_row["document"]),
            settings_updated_at=settings_updated_at,
        )

    @staticmethod
    def _assert_expected(current: PersistedState, expected: PersistedState) -> None:
        if (
            current.data_revision != expected.data_revision
            or current.settings_revision != expected.settings_revision
            or current.analysis_revision != expected.analysis_revision
        ):
            raise ConcurrentWriteError("persisted_revisions_changed")

    @staticmethod
    def _validate_snapshot_document(document: str, checksum: str) -> None:
        parsed = SourceSnapshot.model_validate_json(document)
        canonical = parsed.canonical_json()
        if canonical != document or snapshot_checksum(document) != checksum:
            raise ValueError("snapshot_document_not_canonical")

    @staticmethod
    def _validate_settings_document(document: str) -> None:
        parsed = ModelSettings.model_validate_json(document)
        if canonical_settings_json(parsed) != document:
            raise ValueError("settings_document_not_canonical")

    def commit_snapshot(
        self,
        document: str,
        checksum: str,
        *,
        expected: PersistedState,
        received_at: datetime,
    ) -> tuple[PersistedState, bool]:
        """Atomically replace the singleton snapshot and bump both revisions."""
        self._validate_snapshot_document(document, checksum)
        timestamp = utc_timestamp(received_at)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_state(connection)
                self._assert_expected(current, expected)
                if current.checksum == checksum:
                    connection.commit()
                    return current, False
                data_revision = current.data_revision + 1
                analysis_revision = current.analysis_revision + 1
                connection.execute(
                    "INSERT INTO source_snapshots(singleton, data_revision, document, checksum, received_at) "
                    "VALUES (1, ?, ?, ?, ?) "
                    "ON CONFLICT(singleton) DO UPDATE SET data_revision=excluded.data_revision, "
                    "document=excluded.document, checksum=excluded.checksum, received_at=excluded.received_at",
                    (data_revision, document, checksum, timestamp),
                )
                connection.execute(
                    "UPDATE evaluation_state SET analysis_revision = ?, updated_at = ? WHERE singleton = 1",
                    (analysis_revision, timestamp),
                )
                from app.observation_storage import record_source_observations
                record_source_observations(
                    connection, current.snapshot, SourceSnapshot.model_validate_json(document),
                    previous_data_revision=current.data_revision, data_revision=data_revision,
                    received_at=received_at, comparison_settings_revision=current.settings_revision,
                    comparison_model_version=current.settings.model_version, comparison_as_of=received_at,
                )
                observer = self._source_observer.get()
                if observer is not None and observer[0] == checksum:
                    observer[1](connection)
                updated = PersistedState(
                    data_revision=data_revision,
                    settings_revision=current.settings_revision,
                    analysis_revision=analysis_revision,
                    snapshot_json=document,
                    checksum=checksum,
                    received_at=parse_timestamp(timestamp),
                    settings_json=current.settings_json,
                    settings_updated_at=current.settings_updated_at,
                )
                connection.commit()
                return updated, True
            except BaseException:
                connection.rollback()
                raise

    def commit_settings(
        self,
        document: str,
        *,
        expected: PersistedState,
        updated_at: datetime,
    ) -> tuple[PersistedState, bool]:
        """Atomically replace settings and advance settings/analysis revisions."""
        self._validate_settings_document(document)
        timestamp = utc_timestamp(updated_at)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_state(connection)
                self._assert_expected(current, expected)
                if current.settings_json == document:
                    connection.commit()
                    return current, False
                settings_revision = current.settings_revision + 1
                analysis_revision = current.analysis_revision + 1
                connection.execute(
                    "UPDATE settings SET settings_revision = ?, document = ?, updated_at = ? WHERE singleton = 1",
                    (settings_revision, document, timestamp),
                )
                connection.execute(
                    "UPDATE evaluation_state SET analysis_revision = ?, updated_at = ? WHERE singleton = 1",
                    (analysis_revision, timestamp),
                )
                from app.observation_storage import record_model_event
                record_model_event(
                    connection, current.settings, ModelSettings.model_validate_json(document),
                    previous_settings_revision=current.settings_revision, settings_revision=settings_revision,
                    received_at=updated_at, comparison_as_of=updated_at,
                )
                updated = PersistedState(
                    data_revision=current.data_revision,
                    settings_revision=settings_revision,
                    analysis_revision=analysis_revision,
                    snapshot_json=current.snapshot_json,
                    checksum=current.checksum,
                    received_at=current.received_at,
                    settings_json=document,
                    settings_updated_at=parse_timestamp(timestamp),
                )
                connection.commit()
                return updated, True
            except BaseException:
                connection.rollback()
                raise

    def commit_refresh(
        self,
        *,
        expected: PersistedState,
        refreshed_at: datetime,
    ) -> PersistedState:
        """Persist a new analysis revision without changing source or settings."""
        timestamp = utc_timestamp(refreshed_at)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._read_state(connection)
                self._assert_expected(current, expected)
                connection.execute(
                    "UPDATE evaluation_state SET analysis_revision = ?, updated_at = ? WHERE singleton = 1",
                    (current.analysis_revision + 1, timestamp),
                )
                updated = PersistedState(
                    data_revision=current.data_revision,
                    settings_revision=current.settings_revision,
                    analysis_revision=current.analysis_revision + 1,
                    snapshot_json=current.snapshot_json,
                    checksum=current.checksum,
                    received_at=current.received_at,
                    settings_json=current.settings_json,
                    settings_updated_at=current.settings_updated_at,
                )
                connection.commit()
                return updated
            except BaseException:
                connection.rollback()
                raise
