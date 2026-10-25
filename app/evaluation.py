"""Revisioned immutable analysis and its persistence coordinator."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, RLock
from types import MappingProxyType

from app.contracts import (
    ModelSettings,
    SettingsPatch,
    SourceSnapshot,
)
from app.domain import CustomerScore, score_customer
from app.domain.context import normalize_as_of
from app.domain.money import checked_cents_sum
from app.storage import (
    PersistedState,
    SQLiteRepository,
    canonical_settings_json,
    snapshot_checksum,
)


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One self-contained calculation over one source/settings revision pair.

    Source and settings are kept as canonical JSON so nested extra source fields
    and settings mappings cannot be mutated through the published object.
    Domain results are immutable tuples of frozen dataclasses.
    """

    analysis_revision: str
    data_revision: int
    settings_revision: int
    model_version: str
    evaluated_at: datetime
    horizon_days: int
    snapshot_json: str = field(repr=False)
    settings_json: str = field(repr=False)
    customers: tuple[CustomerScore, ...]
    risk_counts: Mapping[str, int]
    customers_evaluated: int
    monetary_risk_cents: int
    monetary_coverage_evaluated: int
    monetary_coverage_unknown: int
    due_services_price_cents: int
    funding_gap_cents: int
    data_quality_warnings: int
    service_count: int
    transaction_count: int
    customers_by_id: Mapping[int, CustomerScore] = field(repr=False)

    @property
    def snapshot(self) -> SourceSnapshot:
        """Return a caller-owned source DTO graph."""
        return SourceSnapshot.model_validate_json(self.snapshot_json)

    @property
    def settings(self) -> ModelSettings:
        """Return a caller-owned settings DTO graph."""
        return ModelSettings.model_validate_json(self.settings_json)

    def customer(self, customer_id: int) -> CustomerScore | None:
        return self.customers_by_id.get(customer_id)


def calculate_evaluation(
    snapshot: SourceSnapshot,
    settings: ModelSettings,
    *,
    analysis_revision: int,
    data_revision: int,
    settings_revision: int,
    as_of: datetime | None = None,
) -> Evaluation:
    """Compute all customer scores and aggregates from immutable inputs."""
    if not isinstance(snapshot, SourceSnapshot):
        raise TypeError("source_snapshot_required")
    if not isinstance(settings, ModelSettings):
        raise TypeError("model_settings_required")
    for name, value, minimum in (
        ("analysis_revision", analysis_revision, 1),
        ("data_revision", data_revision, 1),
        ("settings_revision", settings_revision, 1),
    ):
        if type(value) is not int or value < minimum:
            raise ValueError(f"{name}_out_of_range")

    # Canonical round-trips detach nested mappings/extras from any DTO held by
    # the caller. One normalized clock value is passed to every customer.
    snapshot_json = snapshot.canonical_json()
    fixed_snapshot = SourceSnapshot.model_validate_json(snapshot_json)
    settings_json = canonical_settings_json(settings)
    fixed_settings = ModelSettings.model_validate_json(settings_json)
    fixed_as_of = normalize_as_of(as_of or datetime.now(timezone.utc))

    services_by_owner: dict[int, list] = defaultdict(list)
    for service in fixed_snapshot.services:
        services_by_owner[service.ownerId].append(service)

    customers = tuple(
        score_customer(
            account,
            services_by_owner.get(account.id, ()),
            as_of=fixed_as_of,
            settings=fixed_settings,
        )
        for account in sorted(fixed_snapshot.accounts, key=lambda row: row.id)
    )
    counts = {"low": 0, "medium": 0, "high": 0, "unknown": 0}
    for customer in customers:
        if customer.risk_level not in counts:
            raise ValueError(f"unknown_risk_level:{customer.risk_level}")
        counts[customer.risk_level] += 1
    by_id = {customer.customer_id: customer for customer in customers}
    known_money = tuple(
        customer.monetary_risk_cents
        for customer in customers
        if customer.monetary_risk_cents is not None
    )

    return Evaluation(
        analysis_revision=f"a-{analysis_revision}",
        data_revision=data_revision,
        settings_revision=settings_revision,
        model_version=fixed_settings.model_version,
        evaluated_at=fixed_as_of,
        horizon_days=fixed_settings.horizon_days,
        snapshot_json=snapshot_json,
        settings_json=settings_json,
        customers=customers,
        risk_counts=MappingProxyType(counts),
        customers_evaluated=counts["low"] + counts["medium"] + counts["high"],
        monetary_risk_cents=checked_cents_sum(known_money),
        monetary_coverage_evaluated=len(known_money),
        monetary_coverage_unknown=len(customers) - len(known_money),
        due_services_price_cents=checked_cents_sum(
            customer.due_services_price_cents for customer in customers
        ),
        funding_gap_cents=checked_cents_sum(customer.funding_gap_cents for customer in customers),
        data_quality_warnings=sum(len(customer.data_quality_warnings) for customer in customers),
        service_count=len(fixed_snapshot.services),
        transaction_count=len(fixed_snapshot.transactions),
        customers_by_id=MappingProxyType(by_id),
    )


@dataclass(frozen=True, slots=True)
class UpdateResult:
    changed: bool
    data_revision: int
    settings_revision: int
    analysis_revision: str
    checksum: str | None
    received_at: datetime | None
    evaluation: Evaluation | None


class SettingsRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int):
        super().__init__("settings_revision_conflict")
        self.current_revision = current_revision


Calculator = Callable[..., Evaluation]
Clock = Callable[[], datetime]

_LOCKS_GUARD = Lock()
_LOCKS_BY_DATABASE: dict[str, RLock] = {}


def _lock_for(database_path: object) -> RLock:
    key = str(database_path)
    with _LOCKS_GUARD:
        lock = _LOCKS_BY_DATABASE.get(key)
        if lock is None:
            lock = RLock()
            _LOCKS_BY_DATABASE[key] = lock
        return lock


class EvaluationCoordinator:
    """Serialize all writes, calculations and whole-evaluation publication.

    The process mutex is shared by coordinators that target the same database.
    The repository also checks all three revisions inside each transaction, so
    stale work cannot commit if a separate process changes the file.
    """

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        clock: Clock | None = None,
        calculator: Calculator = calculate_evaluation,
    ):
        self.repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._calculator = calculator
        self._lock = _lock_for(repository.database_path)
        self._started = False
        self._published: Evaluation | None = None

    @property
    def current(self) -> Evaluation | None:
        with self._lock:
            return self._published

    def start(self, default_settings: ModelSettings | None = None) -> Evaluation | None:
        """Initialize storage and restore a complete evaluation after restart.

        Defaults are only inserted when no settings document exists. A stored
        source is recomputed at the current clock and gets a new analysis
        revision; its data and settings revisions remain unchanged.
        """
        with self._lock:
            if self._started:
                return self._published
            state = self.repository.initialize(default_settings)
            if state.snapshot_json is None:
                self._published = None
                self._started = True
                return None
            refreshed_at = normalize_as_of(self._clock())
            candidate = self._calculate(
                state.snapshot_json,
                state.settings_json,
                analysis_revision=state.analysis_revision + 1,
                data_revision=state.data_revision,
                settings_revision=state.settings_revision,
                as_of=refreshed_at,
            )
            self.repository.commit_refresh(expected=state, refreshed_at=refreshed_at)
            self._publish(candidate)
            self._started = True
            return candidate

    def replace_snapshot(self, snapshot: SourceSnapshot) -> UpdateResult:
        """Validate, compute, commit and publish a full active-snapshot replace."""
        self._require_started()
        if not isinstance(snapshot, SourceSnapshot):
            raise TypeError("source_snapshot_required")
        # Freeze the candidate input before acquiring the lock; only this string
        # is used for calculation and the SQLite commit.
        document = snapshot.canonical_json()
        fixed_snapshot = SourceSnapshot.model_validate_json(document)
        checksum = snapshot_checksum(document)
        with self._lock:
            state = self.repository.load_state()
            if state.checksum == checksum:
                return self._result(state, changed=False)
            received_at = normalize_as_of(self._clock())
            candidate = self._calculate(
                fixed_snapshot.canonical_json(),
                state.settings_json,
                analysis_revision=state.analysis_revision + 1,
                data_revision=state.data_revision + 1,
                settings_revision=state.settings_revision,
                as_of=received_at,
            )
            updated, changed = self.repository.commit_snapshot(
                document,
                checksum,
                expected=state,
                received_at=received_at,
            )
            if changed:
                self._publish(candidate)
            return self._result(updated, changed=changed)

    def replace_settings(
        self,
        settings: ModelSettings,
        *,
        expected_settings_revision: int,
    ) -> UpdateResult:
        """Replace complete settings with optimistic revision protection."""
        self._require_started()
        if type(expected_settings_revision) is not int or expected_settings_revision < 1:
            raise ValueError("settings_revision_required")
        document = canonical_settings_json(settings)
        fixed_settings = ModelSettings.model_validate_json(document)
        with self._lock:
            state = self.repository.load_state()
            if state.settings_revision != expected_settings_revision:
                raise SettingsRevisionConflict(state.settings_revision)
            if state.settings_json == document:
                return self._result(state, changed=False)
            updated_at = normalize_as_of(self._clock())
            candidate = None
            if state.snapshot_json is not None:
                candidate = self._calculate(
                    state.snapshot_json,
                    canonical_settings_json(fixed_settings),
                    analysis_revision=state.analysis_revision + 1,
                    data_revision=state.data_revision,
                    settings_revision=state.settings_revision + 1,
                    as_of=updated_at,
                )
            updated, changed = self.repository.commit_settings(
                document,
                expected=state,
                updated_at=updated_at,
            )
            if changed:
                self._publish(candidate)
            return self._result(updated, changed=changed)

    def patch_settings(
        self,
        patch: SettingsPatch,
        *,
        expected_settings_revision: int,
    ) -> UpdateResult:
        """Apply a partial settings patch against the revision the caller read."""
        self._require_started()
        if type(expected_settings_revision) is not int or expected_settings_revision < 1:
            raise ValueError("settings_revision_required")
        with self._lock:
            state = self.repository.load_state()
            if state.settings_revision != expected_settings_revision:
                raise SettingsRevisionConflict(state.settings_revision)
            return self.replace_settings(
                patch.apply(state.settings),
                expected_settings_revision=expected_settings_revision,
            )

    def refresh(self) -> Evaluation | None:
        """Recompute the active source at one new clock instant and publish it."""
        self._require_started()
        with self._lock:
            state = self.repository.load_state()
            if state.snapshot_json is None:
                return None
            refreshed_at = normalize_as_of(self._clock())
            candidate = self._calculate(
                state.snapshot_json,
                state.settings_json,
                analysis_revision=state.analysis_revision + 1,
                data_revision=state.data_revision,
                settings_revision=state.settings_revision,
                as_of=refreshed_at,
            )
            self.repository.commit_refresh(expected=state, refreshed_at=refreshed_at)
            self._publish(candidate)
            return candidate

    def _calculate(
        self,
        snapshot_json: str,
        settings_json: str,
        *,
        analysis_revision: int,
        data_revision: int,
        settings_revision: int,
        as_of: datetime,
    ) -> Evaluation:
        candidate = self._calculator(
            SourceSnapshot.model_validate_json(snapshot_json),
            ModelSettings.model_validate_json(settings_json),
            analysis_revision=analysis_revision,
            data_revision=data_revision,
            settings_revision=settings_revision,
            as_of=as_of,
        )
        expected = (f"a-{analysis_revision}", data_revision, settings_revision)
        actual = (candidate.analysis_revision, candidate.data_revision, candidate.settings_revision)
        if actual != expected:
            raise ValueError("calculator_returned_mismatched_revisions")
        return candidate

    def _publish(self, evaluation: Evaluation | None) -> None:
        # Callers hold the coordinator mutex. A single reference assignment
        # publishes the complete immutable view after the database commit.
        self._published = evaluation

    def _result(self, state: PersistedState, *, changed: bool) -> UpdateResult:
        return UpdateResult(
            changed=changed,
            data_revision=state.data_revision,
            settings_revision=state.settings_revision,
            analysis_revision=f"a-{state.analysis_revision}",
            checksum=state.checksum,
            received_at=state.received_at,
            evaluation=self._published,
        )

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("evaluation_coordinator_not_started")
