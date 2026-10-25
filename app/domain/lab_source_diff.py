"""Pure structural and evaluation diffs for proposed source snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from app.contracts import ModelSettings, SourceSnapshot
from app.domain.context import normalize_as_of
from app.evaluation import Evaluation, calculate_evaluation

Entity = Literal["account", "service", "transaction"]
ChangeKind = Literal["added", "removed", "changed"]


@dataclass(frozen=True, slots=True)
class FieldChange:
    name: str
    before: Any
    after: Any
    before_present: bool
    after_present: bool


@dataclass(frozen=True, slots=True)
class RecordChange:
    entity: Entity
    record_id: int
    kind: ChangeKind
    fields: tuple[FieldChange, ...]


@dataclass(frozen=True, slots=True)
class EntityCounts:
    entity: Entity
    before: int
    after: int
    added: int
    removed: int
    changed: int


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    customer_count: int
    scored_customer_count: int
    unknown_customer_count: int
    monetary_risk_cents: int
    monetary_coverage_evaluated: int
    monetary_coverage_unknown: int
    due_services_price_cents: int
    funding_gap_cents: int
    risk_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class CustomerResultDiff:
    customer_id: int
    source_change: Literal["added", "removed", "present"]
    before_score: Decimal | None
    after_score: Decimal | None
    score_delta: Decimal | None
    before_level: str | None
    after_level: str | None
    before_money_cents: int | None
    after_money_cents: int | None
    money_delta_cents: int | None
    before_rank: int | None
    after_rank: int | None
    before_due_price_cents: int | None
    after_due_price_cents: int | None
    before_funding_gap_cents: int | None
    after_funding_gap_cents: int | None


@dataclass(frozen=True, slots=True)
class SnapshotDiff:
    """Result of comparing two caller-owned snapshots under one fixed baseline."""

    as_of: datetime
    entity_counts: tuple[EntityCounts, ...]
    record_changes: tuple[RecordChange, ...]
    before: EvaluationSummary
    after: EvaluationSummary
    customer_results: tuple[CustomerResultDiff, ...]
    warnings: tuple[str, ...]


def _source_records(snapshot: SourceSnapshot, entity: Entity) -> dict[int, dict[str, Any]]:
    rows = {
        "account": snapshot.accounts,
        "service": snapshot.services,
        "transaction": snapshot.transactions,
    }[entity]
    return {
        row.id: row.model_dump(mode="json", exclude={"id"})
        for row in rows
    }


def _record_changes(current: SourceSnapshot, candidate: SourceSnapshot) -> tuple[RecordChange, ...]:
    changes: list[RecordChange] = []
    for entity in ("account", "service", "transaction"):
        before = _source_records(current, entity)
        after = _source_records(candidate, entity)
        for record_id in sorted(before.keys() | after.keys()):
            old_record = before.get(record_id)
            new_record = after.get(record_id)
            if old_record is None:
                fields = tuple(
                    FieldChange(name, None, new_record[name], False, True)
                    for name in sorted(new_record or {})
                )
                changes.append(RecordChange(entity, record_id, "added", fields))
                continue
            if new_record is None:
                fields = tuple(
                    FieldChange(name, old_record[name], None, True, False)
                    for name in sorted(old_record)
                )
                changes.append(RecordChange(entity, record_id, "removed", fields))
                continue
            fields = tuple(
                FieldChange(
                    name, old_record.get(name), new_record.get(name),
                    name in old_record, name in new_record,
                )
                for name in sorted(old_record.keys() | new_record.keys())
                if (name in old_record) != (name in new_record)
                or old_record.get(name) != new_record.get(name)
            )
            if fields:
                changes.append(RecordChange(entity, record_id, "changed", fields))
    return tuple(changes)


def _summary(evaluation: Evaluation) -> EvaluationSummary:
    return EvaluationSummary(
        customer_count=len(evaluation.customers),
        scored_customer_count=evaluation.customers_evaluated,
        unknown_customer_count=evaluation.monetary_coverage_unknown,
        monetary_risk_cents=evaluation.monetary_risk_cents,
        monetary_coverage_evaluated=evaluation.monetary_coverage_evaluated,
        monetary_coverage_unknown=evaluation.monetary_coverage_unknown,
        due_services_price_cents=evaluation.due_services_price_cents,
        funding_gap_cents=evaluation.funding_gap_cents,
        risk_counts=tuple(sorted(evaluation.risk_counts.items())),
    )


def _ranks(evaluation: Evaluation) -> dict[int, int]:
    scored = [customer for customer in evaluation.customers if customer.risk_score is not None]
    ordered = sorted(
        scored,
        key=lambda customer: (
            -customer.risk_score,
            -(customer.monetary_risk_cents if customer.monetary_risk_cents is not None else -1),
            customer.customer_id,
        ),
    )
    return {customer.customer_id: index for index, customer in enumerate(ordered, start=1)}


def _customer_result_diffs(before: Evaluation, after: Evaluation) -> tuple[CustomerResultDiff, ...]:
    before_ranks = _ranks(before)
    after_ranks = _ranks(after)
    customer_ids = sorted(before.customers_by_id.keys() | after.customers_by_id.keys())
    output: list[CustomerResultDiff] = []
    for customer_id in customer_ids:
        old = before.customer(customer_id)
        new = after.customer(customer_id)
        if old is None:
            source_change: Literal["added", "removed", "present"] = "added"
        elif new is None:
            source_change = "removed"
        else:
            source_change = "present"
        old_score = old.risk_score if old is not None else None
        new_score = new.risk_score if new is not None else None
        old_money = old.monetary_risk_cents if old is not None else None
        new_money = new.monetary_risk_cents if new is not None else None
        output.append(CustomerResultDiff(
            customer_id=customer_id,
            source_change=source_change,
            before_score=old_score,
            after_score=new_score,
            score_delta=(new_score - old_score) if old_score is not None and new_score is not None else None,
            before_level=old.risk_level if old is not None else None,
            after_level=new.risk_level if new is not None else None,
            before_money_cents=old_money,
            after_money_cents=new_money,
            money_delta_cents=(new_money - old_money) if old_money is not None and new_money is not None else None,
            before_rank=before_ranks.get(customer_id),
            after_rank=after_ranks.get(customer_id),
            before_due_price_cents=old.due_services_price_cents if old is not None else None,
            after_due_price_cents=new.due_services_price_cents if new is not None else None,
            before_funding_gap_cents=old.funding_gap_cents if old is not None else None,
            after_funding_gap_cents=new.funding_gap_cents if new is not None else None,
        ))
    return tuple(output)


def _entity_counts(
    current: SourceSnapshot,
    candidate: SourceSnapshot,
    record_changes: tuple[RecordChange, ...],
) -> tuple[EntityCounts, ...]:
    output: list[EntityCounts] = []
    for entity in ("account", "service", "transaction"):
        before = _source_records(current, entity)
        after = _source_records(candidate, entity)
        kinds = [change.kind for change in record_changes if change.entity == entity]
        output.append(EntityCounts(
            entity=entity,
            before=len(before),
            after=len(after),
            added=kinds.count("added"),
            removed=kinds.count("removed"),
            changed=kinds.count("changed"),
        ))
    return tuple(output)


def diff_snapshots(
    current: SourceSnapshot,
    candidate: SourceSnapshot,
    *,
    settings: ModelSettings,
    as_of: datetime,
) -> SnapshotDiff:
    """Diff source records and outcomes with unchanged settings and one clock.

    Inputs remain caller-owned and are never mutated. The calculation uses the
    same production evaluation engine for both snapshots, so previews cannot
    drift from published scoring rules or accidentally compare different times.
    """
    if not isinstance(current, SourceSnapshot) or not isinstance(candidate, SourceSnapshot):
        raise TypeError("source_snapshot_required")
    if not isinstance(settings, ModelSettings):
        raise TypeError("model_settings_required")
    if as_of is None:
        raise ValueError("as_of_required")
    fixed_as_of = normalize_as_of(as_of)
    before = calculate_evaluation(
        current, settings, analysis_revision=1, data_revision=1,
        settings_revision=1, as_of=fixed_as_of,
    )
    after = calculate_evaluation(
        candidate, settings, analysis_revision=1, data_revision=1,
        settings_revision=1, as_of=fixed_as_of,
    )
    record_changes = _record_changes(current, candidate)
    return SnapshotDiff(
        as_of=fixed_as_of,
        entity_counts=_entity_counts(current, candidate, record_changes),
        record_changes=record_changes,
        before=_summary(before),
        after=_summary(after),
        customer_results=_customer_result_diffs(before, after),
        warnings=("source_record_absence_is_input_difference_only",),
    )
