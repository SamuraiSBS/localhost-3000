"""Publication-only history, baseline continuity, provenance and rollback."""

from datetime import datetime, timezone
import json
import sqlite3

import pytest

from app.contracts import ModelSettings, SourceSnapshot
from app.observation_storage import (
    ensure_observation_baseline, initialize_observation_schema, record_model_event,
    record_source_observations, record_workflow_event,
)
from app.storage import canonical_settings_json, snapshot_checksum, utc_timestamp

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def snapshot(*, balance=100, auto=True, status="active", empty=False, expires="2026-11-01T00:00:00Z"):
    return SourceSnapshot.model_validate(dict(
        accounts=[] if empty else [dict(id=1, email="synthetic@example.invalid", balance=balance,
            withdrawBalance=0, totalReplenished=0, bonusBalance=0, bonusState="locked")],
        services=[] if empty else [dict(id=10, ownerId=1, name="service", paymentTerm="month",
            autoProlong=auto, status=status, price=100, createdAt="2026-01-01T00:00:00Z", expiresAt=expires)],
        transactions=[]))


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute("BEGIN")
    connection.execute("CREATE TABLE source_snapshots (singleton INTEGER PRIMARY KEY, data_revision INTEGER, "
                       "document TEXT, checksum TEXT, received_at TEXT)")
    connection.execute("CREATE TABLE settings (singleton INTEGER PRIMARY KEY, settings_revision INTEGER, "
                       "document TEXT, updated_at TEXT)")
    connection.execute("CREATE TABLE workflow_records (customer_id INTEGER PRIMARY KEY, revision INTEGER, "
                       "status TEXT, note TEXT, next_contact_at TEXT, manual_outcome TEXT, author TEXT, updated_at TEXT)")
    initialize_observation_schema(connection)
    connection.commit()
    yield connection
    connection.close()


def publish(db, source, revision):
    document = source.canonical_json()
    db.execute("INSERT OR REPLACE INTO source_snapshots VALUES (1, ?, ?, ?, ?)",
               (revision, document, snapshot_checksum(document), utc_timestamp(NOW)))


def record(db, before, after, revision=2, **overrides):
    kwargs = dict(previous_data_revision=revision-1, data_revision=revision, received_at=NOW,
                  comparison_settings_revision=1, comparison_model_version="rules-v1", comparison_as_of=NOW)
    kwargs.update(overrides)
    return record_source_observations(db, before, after, **kwargs)


def seed(db, source=None, revision=1):
    source = source or snapshot()
    db.execute("BEGIN")
    publish(db, source, revision)
    assert ensure_observation_baseline(db)
    assert not ensure_observation_baseline(db)
    db.commit()
    return source


def test_first_snapshot_and_missing_baseline_have_no_fabricated_events(db):
    db.execute("BEGIN")
    assert not ensure_observation_baseline(db)
    publish(db, snapshot(), 1)
    assert record(db, None, snapshot(), revision=1) == 0
    assert db.execute("SELECT * FROM source_observations").fetchall() == []
    db.rollback()
    db.execute("BEGIN")
    publish(db, snapshot(balance=200), 8)
    assert record(db, snapshot(), snapshot(balance=200), revision=8) == 0
    assert db.execute("SELECT data_revision FROM source_observation_baseline").fetchone() == (8,)


def test_records_only_real_fields_with_fixed_provenance_and_deduplicates(db):
    before = seed(db)
    after = snapshot(balance=200, auto=False, status="suspended", expires="2026-12-01T00:00:00Z")
    db.execute("BEGIN")
    publish(db, after, 2)
    assert record(db, before, after) == 3
    assert record(db, before, after) == 0
    rows = db.execute("SELECT entity, entity_id, field, before_json, after_json "
                      "FROM source_observations ORDER BY rowid").fetchall()
    assert rows == [("account", 1, "balance", "100", "200"),
                    ("service", 10, "autoProlong", "true", "false"),
                    ("service", 10, "status", '"active"', '"suspended"')]
    assert db.execute("SELECT previous_data_revision, data_revision, received_at, "
                      "comparison_settings_revision, comparison_model_version, comparison_as_of "
                      "FROM source_observation_batches").fetchone() == (1, 2, utc_timestamp(NOW), 1, "rules-v1", utc_timestamp(NOW))
    with pytest.raises(ValueError, match="conflict"):
        record(db, before, after, comparison_settings_revision=2)
    assert db.in_transaction


def test_disappearance_is_not_deletion_and_expiry_alone_is_not_churn(db):
    before = seed(db)
    after = snapshot(expires="2026-12-01T00:00:00Z")
    db.execute("BEGIN")
    publish(db, after, 2)
    assert record(db, before, after) == 0
    empty = snapshot(empty=True)
    publish(db, empty, 3)
    assert record(db, after, empty, revision=3) == 2
    assert db.execute("SELECT entity, field FROM source_observations ORDER BY entity").fetchall() == [
        ("account", "disappeared"), ("service", "disappeared")]


def test_rollback_erases_source_and_observations(db):
    before = seed(db)
    db.execute("BEGIN")
    after = snapshot(balance=200)
    publish(db, after, 2)
    assert record(db, before, after) == 1
    db.rollback()
    assert db.execute("SELECT data_revision FROM source_snapshots").fetchone() == (1,)
    assert db.execute("SELECT * FROM source_observations").fetchall() == []
    assert db.execute("SELECT data_revision FROM source_observation_baseline").fetchone() == (1,)


@pytest.mark.parametrize("override", [dict(data_revision=4), dict(previous_data_revision=True),
    dict(comparison_settings_revision=0), dict(comparison_model_version="future"),
    dict(received_at=None), dict(comparison_as_of=None),
    dict(received_at=NOW.replace(tzinfo=None)), dict(comparison_as_of=NOW.replace(tzinfo=None))])
def test_rejects_invalid_provenance(db, override):
    before = seed(db)
    db.execute("BEGIN")
    after = snapshot(balance=200)
    publish(db, after, 2)
    with pytest.raises(ValueError):
        record(db, before, after, **override)
    assert db.execute("SELECT * FROM source_observations").fetchall() == []


def test_rejects_nontransaction_preview_wrong_snapshot_and_discontinuous_baseline(db):
    before = seed(db)
    after = snapshot(balance=200)
    with pytest.raises(ValueError, match="transaction"):
        record(db, before, after)
    db.execute("BEGIN")
    with pytest.raises(ValueError, match="publication_mismatch"):
        record(db, before, after)
    publish(db, after, 2)
    with pytest.raises(ValueError, match="discontinuity"):
        record(db, snapshot(balance=50), after)
    with pytest.raises(ValueError, match="previous_complete"):
        record(db, None, after)
    with pytest.raises(TypeError, match="complete_source"):
        record(db, {}, after)


def test_rejects_changed_publication_checksum_timestamp_and_noop(db):
    before = seed(db)
    db.execute("BEGIN")
    after = snapshot(balance=200)
    publish(db, after, 2)
    db.execute("UPDATE source_snapshots SET checksum='wrong'")
    with pytest.raises(ValueError, match="publication_mismatch"):
        record(db, before, after)
    publish(db, after, 2)
    db.execute("UPDATE source_snapshots SET received_at='2026-10-24T00:00:00Z'")
    with pytest.raises(ValueError, match="publication_mismatch"):
        record(db, before, after)
    publish(db, before, 2)
    with pytest.raises(ValueError, match="unchanged_source"):
        record(db, before, before)


def test_additions_have_no_prior_values_and_order_is_deterministic(db):
    before = seed(db, snapshot(empty=True))
    after = snapshot()
    db.execute("BEGIN")
    publish(db, after, 2)
    assert record(db, before, after) == 0
    multiple = after.model_dump(mode="json")
    multiple["accounts"].append(dict(multiple["accounts"][0], id=3))
    multiple["services"].append(dict(multiple["services"][0], id=30, ownerId=3))
    source = SourceSnapshot.model_validate(multiple)
    publish(db, source, 3)
    assert record(db, after, source, revision=3) == 0
    removed = snapshot(empty=True)
    publish(db, removed, 4)
    assert record(db, source, removed, revision=4) == 4
    assert db.execute("SELECT entity,entity_id FROM source_observations ORDER BY rowid").fetchall() == [
        ("account", 1), ("account", 3), ("service", 10), ("service", 30)]


def test_baseline_and_history_survive_reopen(db):
    # SQLite backup/reopen represents a restart without relying on temp ACLs.
    before = seed(db)
    db.execute("BEGIN")
    after = snapshot(balance=200)
    publish(db, after, 2)
    record(db, before, after)
    db.commit()
    reopened = sqlite3.connect(":memory:", isolation_level=None)
    db.backup(reopened)
    reopened.execute("BEGIN")
    initialize_observation_schema(reopened)
    assert not ensure_observation_baseline(reopened)
    assert record(reopened, before, after) == 0
    assert reopened.execute("SELECT COUNT(*) FROM source_observations").fetchone() == (1,)
    reopened.close()


def workflow(db):
    before = dict(customer_id="1", revision=0, status="new", note="", next_contact_at=None, manual_outcome="none")
    after = dict(before, revision=1, status="processed", manual_outcome="topup_reported")
    db.execute("INSERT INTO workflow_records VALUES (1,1,'processed','',NULL,'topup_reported','operator',?)",
               (utc_timestamp(NOW),))
    return dict(customer_id=1, previous_revision=0, revision=1, before=before, after=after,
                author="operator", received_at=NOW)


def test_workflow_contacts_are_separate_deduplicated_and_rollbackable(db):
    db.execute("BEGIN")
    kwargs = workflow(db)
    assert record_workflow_event(db, **kwargs)
    assert not record_workflow_event(db, **kwargs)
    row = db.execute("SELECT kind, changes_json FROM workflow_events").fetchone()
    assert row[0] == "workflow"
    assert json.loads(row[1])["manual_outcome"]["after"] == "topup_reported"
    assert db.execute("SELECT * FROM source_observations").fetchall() == []
    with pytest.raises(ValueError, match="publication_mismatch"):
        record_workflow_event(db, **dict(kwargs, author="wrong"))
    db.rollback()
    assert db.execute("SELECT * FROM workflow_events").fetchall() == []


def test_model_events_are_distinct_validate_publication_and_deduplicate(db):
    db.execute("BEGIN")
    before, after = ModelSettings(), ModelSettings(horizon_days=20)
    kwargs = dict(previous_settings_revision=1, settings_revision=2, received_at=NOW, comparison_as_of=NOW)
    with pytest.raises(ValueError, match="publication_mismatch"):
        record_model_event(db, before, after, **kwargs)
    db.execute("INSERT INTO settings VALUES (1,2,?,?)", (canonical_settings_json(after), utc_timestamp(NOW)))
    assert record_model_event(db, before, after, **kwargs)
    assert not record_model_event(db, before, after, **kwargs)
    assert db.execute("SELECT kind FROM model_observation_events").fetchone() == ("model",)
    assert db.execute("SELECT * FROM source_observations").fetchall() == []
    db.rollback()
    assert db.execute("SELECT * FROM model_observation_events").fetchall() == []
