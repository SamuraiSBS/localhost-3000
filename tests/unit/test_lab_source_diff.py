"""Source preview compares structure and results under one fixed baseline."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.contracts import AccountSource, ModelSettings, ServiceSource, SourceSnapshot
from app.domain.lab_source_diff import diff_snapshots

AS_OF = datetime(2026, 10, 25, tzinfo=timezone.utc)


def account(*, balance: int = 1200, account_id: int = 9001) -> AccountSource:
    return AccountSource.model_validate({
        "id": account_id,
        "email": f"customer-{account_id}@example.invalid",
        "balance": balance,
        "withdrawBalance": 9000,
        "totalReplenished": 25000,
        "bonusBalance": 500,
        "bonusState": "locked",
    })


def service(*, service_id: int, auto: bool, expires: str) -> ServiceSource:
    return ServiceSource.model_validate({
        "id": service_id,
        "name": f"service-{service_id}",
        "ownerId": 9001,
        "paymentTerm": "month",
        "autoProlong": auto,
        "createdAt": "2026-01-01T00:00:00Z",
        "expiresAt": expires,
        "status": "active",
        "price": 1000,
    })


def snapshot(balance: int = 1200) -> SourceSnapshot:
    return SourceSnapshot.model_validate({
        "accounts": [account(balance=balance).model_dump(mode="json")],
        "services": [
            service(service_id=101, auto=True, expires="2026-10-30T00:00:00Z").model_dump(mode="json"),
            service(service_id=102, auto=False, expires="2026-11-04T00:00:00Z").model_dump(mode="json"),
        ],
        "transactions": [],
    })


def test_l17_balance_preview_uses_same_settings_and_clock_and_never_mutates_inputs():
    current = snapshot()
    candidate = snapshot(balance=2000)
    current_bytes = current.canonical_json()
    candidate_bytes = candidate.canonical_json()

    result = diff_snapshots(current, candidate, settings=ModelSettings(), as_of=AS_OF)

    assert result.as_of == AS_OF
    assert result.before.funding_gap_cents == 800
    assert result.after.funding_gap_cents == 0
    assert result.before.monetary_risk_cents == 830
    assert result.after.monetary_risk_cents == 550
    assert result.before.risk_counts == (("high", 1), ("low", 0), ("medium", 0), ("unknown", 0))
    assert result.after.risk_counts == (("high", 0), ("low", 0), ("medium", 1), ("unknown", 0))

    account_changes = [change for change in result.record_changes if change.entity == "account"]
    assert len(account_changes) == 1
    assert (account_changes[0].record_id, account_changes[0].kind) == (9001, "changed")
    assert [(field.name, field.before, field.after) for field in account_changes[0].fields] == [
        ("balance", 1200, 2000),
    ]

    customer = result.customer_results[0]
    assert customer.customer_id == 9001
    assert customer.source_change == "present"
    assert customer.before_score == Decimal("66.33")
    assert customer.after_score == Decimal("38.33")
    assert customer.score_delta == Decimal("-28.00")
    assert (customer.before_money_cents, customer.after_money_cents) == (830, 550)
    assert (customer.before_rank, customer.after_rank) == (1, 1)
    assert current.canonical_json() == current_bytes
    assert candidate.canonical_json() == candidate_bytes


def test_record_add_remove_and_customer_coverage_are_explicit():
    before = snapshot()
    candidate = SourceSnapshot.model_validate({
        "accounts": [account(account_id=9002).model_dump(mode="json")],
        "services": [],
        "transactions": [],
    })

    result = diff_snapshots(before, candidate, settings=ModelSettings(), as_of=AS_OF)

    counts = {item.entity: item for item in result.entity_counts}
    assert (counts["account"].added, counts["account"].removed) == (1, 1)
    assert (counts["service"].removed, counts["service"].changed) == (2, 0)
    assert {item.kind for item in result.record_changes} == {"added", "removed"}
    assert result.after.unknown_customer_count == 1
    assert result.customer_results[0].source_change == "removed"
    assert result.customer_results[1].source_change == "added"
    assert result.warnings == ("source_record_absence_is_input_difference_only",)


def test_diff_requires_an_explicit_aware_clock():
    current = snapshot()
    with pytest.raises(ValueError, match="as_of_required"):
        diff_snapshots(current, current, settings=ModelSettings(), as_of=None)
    with pytest.raises(ValueError, match="as_of_must_be_timezone_aware"):
        diff_snapshots(current, current, settings=ModelSettings(), as_of=datetime(2026, 10, 25))


@pytest.mark.parametrize("entity,collection", [("account", "accounts"), ("service", "services")])
@pytest.mark.parametrize("before_present", [False, True])
def test_allowed_extra_absence_and_explicit_null_are_distinct(entity, collection, before_present):
    absent = snapshot()
    present_data = absent.model_dump(mode="json")
    present_data[collection][0]["nullable_extra"] = None
    present = SourceSnapshot.model_validate(present_data)
    current, candidate = (present, absent) if before_present else (absent, present)
    original_bytes = current.canonical_json(), candidate.canonical_json()

    result = diff_snapshots(current, candidate, settings=ModelSettings(), as_of=AS_OF)

    change, = result.record_changes
    assert (change.entity, change.kind) == (entity, "changed")
    field, = change.fields
    assert (field.name, field.before, field.after) == ("nullable_extra", None, None)
    assert (field.before_present, field.after_present) == (before_present, not before_present)
    counts = next(item for item in result.entity_counts if item.entity == entity)
    assert (counts.changed, counts.added, counts.removed) == (1, 0, 0)
    assert result.before == result.after
    assert (current.canonical_json(), candidate.canonical_json()) == original_bytes


def test_added_and_removed_record_fields_include_presence():
    current = snapshot()
    empty = SourceSnapshot.model_validate({"accounts": [], "services": [], "transactions": []})
    for before, after, presence in [(empty, current, (False, True)), (current, empty, (True, False))]:
        result = diff_snapshots(before, after, settings=ModelSettings(), as_of=AS_OF)
        assert all((field.before_present, field.after_present) == presence
                   for record in result.record_changes for field in record.fields)
