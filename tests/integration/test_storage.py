from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from threading import Event, Thread
from pathlib import Path
from uuid import uuid4

import pytest

from app.contracts import ModelSettings, SettingsPatch, SourceSnapshot
from app.evaluation import EvaluationCoordinator
from app.storage import SQLiteRepository


AS_OF = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def task_database_factory():
    """Create and remove only this test's file-backed SQLite databases."""
    workspace = Path(__file__).resolve().parents[2]
    paths: list[Path] = []

    def create(name: str) -> Path:
        path = workspace / f".b03-test-{name}-{uuid4().hex}.sqlite3"
        paths.append(path)
        return path

    yield create
    for path in paths:
        for file_path in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            file_path.unlink(missing_ok=True)


def source_snapshot(
    email: str = "seed@example.test",
    *,
    balance: int = 0,
    auto_prolong: bool = False,
) -> SourceSnapshot:
    return SourceSnapshot.model_validate(
        {
            "accounts": [
                {
                    "id": 1,
                    "email": email,
                    "balance": balance,
                    "withdrawBalance": 0,
                    "totalReplenished": 0,
                    "bonusBalance": 0,
                    "bonusState": "locked",
                }
            ],
            "services": [
                {
                    "id": 10,
                    "name": "Monthly plan",
                    "ownerId": 1,
                    "paymentTerm": "month",
                    "autoProlong": auto_prolong,
                    "createdAt": "2026-01-01T00:00:00Z",
                    "expiresAt": "2026-10-23T12:00:00Z",
                    "status": "active",
                    "price": 1000,
                }
            ],
            "transactions": [],
        }
    )


def make_coordinator(path, *, calculator=None):
    repo = SQLiteRepository(path)
    coordinator = EvaluationCoordinator(
        repo,
        clock=lambda: AS_OF,
        **({"calculator": calculator} if calculator is not None else {}),
    )
    return repo, coordinator


def test_settings_patch_requires_a_positive_integer_revision(task_database_factory):
    _, coordinator = make_coordinator(task_database_factory("settings-revision"))
    coordinator.start()
    patch = SettingsPatch(horizon_days=45)

    for revision in (0, True, 1.0, "1"):
        with pytest.raises(ValueError, match="settings_revision_required"):
            coordinator.patch_settings(patch, expected_settings_revision=revision)


def test_failed_snapshot_or_settings_calculation_preserves_previous_state(task_database_factory):
    should_fail = {"snapshot": False, "settings": False}

    def calculate(snapshot, settings, **kwargs):
        if should_fail["snapshot"] and snapshot.accounts[0].email == "rejected@example.test":
            raise RuntimeError("deliberate calculation failure")
        if should_fail["settings"] and settings.weights["manual_renewal"] == Decimal(50):
            raise RuntimeError("deliberate settings calculation failure")
        from app.evaluation import calculate_evaluation

        return calculate_evaluation(snapshot, settings, **kwargs)

    repo, coordinator = make_coordinator(task_database_factory("atomic"), calculator=calculate)
    assert coordinator.start() is None
    original = source_snapshot()
    original_json = original.canonical_json()
    initial = coordinator.replace_snapshot(original)
    assert initial.changed is True
    assert original.canonical_json() == original_json  # source DTO was not mutated

    persisted_before = repo.load_state()
    published_before = coordinator.current
    assert published_before is not None

    should_fail["snapshot"] = True
    with pytest.raises(RuntimeError, match="deliberate calculation failure"):
        coordinator.replace_snapshot(source_snapshot("rejected@example.test", balance=200))

    assert repo.load_state() == persisted_before
    assert coordinator.current is published_before

    should_fail["snapshot"] = False
    should_fail["settings"] = True
    changed_settings = SettingsPatch(weights={"manual_renewal": 50}).apply(ModelSettings())
    with pytest.raises(RuntimeError, match="deliberate settings calculation failure"):
        coordinator.replace_settings(changed_settings, expected_settings_revision=1)

    assert repo.load_state() == persisted_before
    assert coordinator.current is published_before


def test_snapshot_and_settings_survive_repository_restart(task_database_factory):
    database = task_database_factory("restart")
    repo, coordinator = make_coordinator(database)
    assert coordinator.start() is None
    snapshot = source_snapshot(balance=250)
    first = coordinator.replace_snapshot(snapshot)
    settings = SettingsPatch(
        horizon_days=45,
        weights={"manual_renewal": 50},
    ).apply(ModelSettings())
    second = coordinator.replace_settings(settings, expected_settings_revision=1)
    assert first.changed and second.changed
    before_restart = repo.load_state()

    restarted_repo, restarted = make_coordinator(database)
    restored = restarted.start()

    assert restored is not None
    assert restored.snapshot.canonical_json() == snapshot.canonical_json()
    assert restored.settings.horizon_days == 45
    assert restored.settings.weights["manual_renewal"] == Decimal(50)
    assert restored.data_revision == before_restart.data_revision == 1
    assert restored.settings_revision == before_restart.settings_revision == 2
    assert restored.analysis_revision == f"a-{before_restart.analysis_revision + 1}"
    assert restarted_repo.load_state().checksum == before_restart.checksum


def test_canonical_duplicate_snapshot_is_a_true_noop(task_database_factory):
    repo, coordinator = make_coordinator(task_database_factory("duplicate"))
    coordinator.start()
    original = source_snapshot()
    first = coordinator.replace_snapshot(original)
    state_after_first = repo.load_state()
    published_after_first = coordinator.current

    reordered = SourceSnapshot.model_validate(
        {
            "transactions": [],
            "services": [
                {
                    "price": 1000,
                    "status": "active",
                    "expiresAt": "2026-10-23T12:00:00Z",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "autoProlong": False,
                    "paymentTerm": "month",
                    "ownerId": 1,
                    "name": "Monthly plan",
                    "id": 10,
                }
            ],
            "accounts": [
                {
                    "bonusState": "locked",
                    "bonusBalance": 0,
                    "totalReplenished": 0,
                    "withdrawBalance": 0,
                    "balance": 0,
                    "email": "seed@example.test",
                    "id": 1,
                }
            ],
        }
    )
    duplicate = coordinator.replace_snapshot(reordered)

    assert original.canonical_json() == reordered.canonical_json()
    assert first.changed is True
    assert duplicate.changed is False
    assert repo.load_state() == state_after_first
    assert duplicate.data_revision == first.data_revision == 1
    assert duplicate.analysis_revision == first.analysis_revision
    assert coordinator.current is published_after_first


def test_delayed_older_calculation_cannot_publish_after_newer_write(task_database_factory):
    calculation_started = Event()
    release_calculation = Event()

    def delayed_calculate(snapshot, settings, **kwargs):
        if snapshot.accounts[0].email == "older@example.test":
            calculation_started.set()
            if not release_calculation.wait(timeout=5):
                raise TimeoutError("test did not release delayed calculation")
        from app.evaluation import calculate_evaluation

        return calculate_evaluation(snapshot, settings, **kwargs)

    class RecordingCoordinator(EvaluationCoordinator):
        def __init__(self, *args, **kwargs):
            self.publication_order = []
            super().__init__(*args, **kwargs)

        def _publish(self, evaluation):
            super()._publish(evaluation)
            if evaluation is not None:
                email = evaluation.snapshot.accounts[0].email
                self.publication_order.append((evaluation.data_revision, email))

    repo = SQLiteRepository(task_database_factory("race"))
    coordinator = RecordingCoordinator(repo, clock=lambda: AS_OF, calculator=delayed_calculate)
    coordinator.start()
    coordinator.replace_snapshot(source_snapshot())

    older_result = []
    newer_result = []
    newer_started = Event()
    newer_finished = Event()

    def write_older():
        older_result.append(coordinator.replace_snapshot(source_snapshot("older@example.test")))

    def write_newer():
        newer_started.set()
        newer_result.append(coordinator.replace_snapshot(source_snapshot("newer@example.test")))
        newer_finished.set()

    older_thread = Thread(target=write_older)
    older_thread.start()
    assert calculation_started.wait(timeout=3)

    newer_thread = Thread(target=write_newer)
    newer_thread.start()
    assert newer_started.wait(timeout=3)
    assert not newer_finished.wait(timeout=0.05)

    release_calculation.set()
    older_thread.join(timeout=3)
    newer_thread.join(timeout=3)
    assert not older_thread.is_alive()
    assert not newer_thread.is_alive()

    assert older_result[0].data_revision == 2
    assert newer_result[0].data_revision == 3
    assert coordinator.publication_order == [
        (1, "seed@example.test"),
        (2, "older@example.test"),
        (3, "newer@example.test"),
    ]
    assert coordinator.current is not None
    assert coordinator.current.data_revision == repo.load_state().data_revision == 3
    assert coordinator.current.snapshot.accounts[0].email == "newer@example.test"
