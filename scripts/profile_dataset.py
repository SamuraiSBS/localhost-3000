"""Read the hackathon fixture and print a deterministic JSON profile.

This planning tool never changes the source data, imports the generator, opens a
database, or calls a network service. Redirect stdout to save a report.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "материалы" / "data" / "data.json"
DEFAULT_REFERENCE = "2026-10-19T15:00:00.000Z"
FIELDS = {
    "accounts": ("id", "email", "balance", "withdrawBalance", "totalReplenished", "bonusBalance", "bonusState"),
    "services": ("id", "name", "ownerId", "paymentTerm", "autoProlong", "createdAt", "expiresAt", "status", "price"),
    "transactions": ("id", "amount", "status", "performedAt", "createdAt", "type", "serviceId"),
}
ENUMS = {
    ("accounts", "bonusState"): {"locked", "unlocked"},
    ("services", "status"): {"active", "blocked", "deleted", "suspended", "prolong_wait"},
    ("services", "paymentTerm"): {"hour", "week", "month", "quarter_year", "half_year", "year"},
    ("transactions", "status"): {"performed", "not_performed"},
    ("transactions", "type"): {"order", "prolong"},
}
INTEGER_FIELDS = {
    "accounts": ("id", "balance", "withdrawBalance", "totalReplenished", "bonusBalance"),
    "services": ("id", "ownerId", "price"),
    "transactions": ("id", "amount", "serviceId"),
}
DATE_FIELDS = {"accounts": (), "services": ("createdAt", "expiresAt"), "transactions": ("createdAt", "performedAt")}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must contain timezone")
    return parsed.astimezone(timezone.utc)


def distribution(values):
    return dict(sorted(Counter(values).items()))


def validate(data):
    """Validate fixture shape, types and relations, without modifying input.

    The source SQL's name and amount constraints are reported separately: the
    brief does not state these as universal product import requirements.
    """
    errors = []
    fixture_constraint_deviations = []
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        return ["root must contain exactly accounts, services, transactions"], []
    for table, expected_fields in FIELDS.items():
        rows = data[table]
        if not isinstance(rows, list):
            errors.append(f"{table}: expected array")
            continue
        seen_ids = set()
        for index, row in enumerate(rows):
            where = f"{table}[{index}]"
            if not isinstance(row, dict) or set(row) != set(expected_fields):
                errors.append(f"{where}: unexpected or missing fields")
                continue
            for field in INTEGER_FIELDS[table]:
                if type(row[field]) is not int or row[field] < 0:
                    errors.append(f"{where}.{field}: expected nonnegative integer")
            if type(row["id"]) is int:
                if row["id"] in seen_ids:
                    errors.append(f"{where}.id: duplicate {row['id']}")
                seen_ids.add(row["id"])
            for (enum_table, field), allowed in ENUMS.items():
                if table == enum_table and row[field] not in allowed:
                    errors.append(f"{where}.{field}: unknown value")
            for field in DATE_FIELDS[table]:
                value = row[field]
                if value is None and table == "transactions" and field == "performedAt":
                    continue
                try:
                    timestamp(value)
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value):
                        errors.append(f"{where}.{field}: expected UTC milliseconds with Z")
                except (AttributeError, TypeError, ValueError):
                    errors.append(f"{where}.{field}: invalid timestamp")
            if table == "accounts" and not isinstance(row["email"], str):
                errors.append(f"{where}.email: expected string")
            if table == "services":
                if type(row["autoProlong"]) is not bool:
                    errors.append(f"{where}.autoProlong: expected boolean")
                if not isinstance(row["name"], str):
                    errors.append(f"{where}.name: expected string")
                elif not re.fullmatch(r"[A-Za-z0-9-]+", row["name"]):
                    fixture_constraint_deviations.append(f"{where}.name: differs from fixture SQL regex")
            if table == "transactions" and type(row["amount"]) is int and row["amount"] > 1000:
                fixture_constraint_deviations.append(f"{where}.amount: exceeds fixture SQL upper bound of 1000")
    if errors:
        return errors, fixture_constraint_deviations
    accounts = {row["id"]: row for row in data["accounts"]}
    services = {row["id"]: row for row in data["services"]}
    emails = [row["email"] for row in data["accounts"]]
    if len(set(emails)) != len(emails):
        errors.append("accounts.email: duplicate exact value")
    for service in services.values():
        if service["ownerId"] not in accounts:
            errors.append(f"services.id={service['id']}: missing owner")
        if timestamp(service["expiresAt"]) < timestamp(service["createdAt"]):
            errors.append(f"services.id={service['id']}: expiresAt < createdAt")
    for transaction in data["transactions"]:
        where = f"transactions.id={transaction['id']}"
        if transaction["serviceId"] not in services:
            errors.append(f"{where}: missing service")
        performed = transaction["status"] == "performed"
        if performed != (transaction["performedAt"] is not None):
            errors.append(f"{where}: performedAt inconsistent with status")
        if performed and transaction["performedAt"] and timestamp(transaction["performedAt"]) < timestamp(transaction["createdAt"]):
            errors.append(f"{where}: performedAt < createdAt")
    return errors, fixture_constraint_deviations


def summarize_table(table, rows):
    result = {"count": len(rows), "fields": {}}
    for field in FIELDS[table]:
        values = [row[field] for row in rows]
        nonnull = [value for value in values if value is not None]
        summary = {
            "types": distribution(type(value).__name__ for value in values),
            "null_count": len(values) - len(nonnull),
            "distinct_nonnull_count": len(set(nonnull)),
        }
        if nonnull and all(type(value) is int for value in nonnull):
            summary.update(min=min(nonnull), max=max(nonnull), sum=sum(nonnull))
        if (table, field) in ENUMS or field == "autoProlong":
            summary["distribution"] = distribution(str(value).lower() for value in values)
        if field in DATE_FIELDS[table] and nonnull:
            summary.update(min=min(nonnull), max=max(nonnull))
        result["fields"][field] = summary
    return result


def semantic_observations(data, reference):
    accounts = {row["id"]: row for row in data["accounts"]}
    services = {row["id"]: row for row in data["services"]}
    transactions = data["transactions"]
    by_owner = {owner: [row for row in services.values() if row["ownerId"] == owner] for owner in accounts}
    by_service = {service: [row for row in transactions if row["serviceId"] == service] for service in services}
    prolongations = [row for row in transactions if row["type"] == "prolong"]
    before_order = []
    for row in prolongations:
        orders = [t for t in by_service[row["serviceId"]] if t["type"] == "order"]
        if orders and timestamp(row["createdAt"]) < min(timestamp(t["createdAt"]) for t in orders):
            before_order.append(row["id"])
    return {
        "analysis_reference_utc": reference.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "services_per_account": {str(owner): len(rows) for owner, rows in by_owner.items()},
        "transactions_per_service": {str(service): len(rows) for service, rows in by_service.items()},
        "amount_not_equal_to_service_price": sum(row["amount"] != services[row["serviceId"]]["price"] for row in transactions),
        "prolong_created_before_service_expiry": sum(timestamp(row["createdAt"]) < timestamp(services[row["serviceId"]]["expiresAt"]) for row in prolongations),
        "prolong_created_before_first_order_count": len(before_order),
        "prolong_created_before_first_order_ids": before_order,
        "all_owners_have_one_service_status": all(len({row["status"] for row in rows}) == 1 for rows in by_owner.values()),
        "all_services_have_one_transaction_status": all(len({row["status"] for row in rows}) == 1 for rows in by_service.values()),
        "transaction_status_matches_id_modulo_3": all((row["status"] == "not_performed") == (row["id"] % 3 == 0) for row in transactions),
        "bonus_state_matches_account_id_parity": all((row["bonusState"] == "locked") == (row["id"] % 2 == 1) for row in accounts.values()),
        "every_account_main_balance_above_max_service_price": bool(accounts and services) and min(row["balance"] for row in accounts.values()) > max(row["price"] for row in services.values()),
        "every_account_bonus_balance_above_max_service_price": bool(accounts and services) and min(row["bonusBalance"] for row in accounts.values()) > max(row["price"] for row in services.values()),
        "expired_service_ids": [row["id"] for row in services.values() if timestamp(row["expiresAt"]) <= reference],
        "active_but_expired_service_ids": [row["id"] for row in services.values() if row["status"] == "active" and timestamp(row["expiresAt"]) <= reference],
        "prolong_wait_over_5_days_service_ids": [row["id"] for row in services.values() if row["status"] == "prolong_wait" and (reference - timestamp(row["expiresAt"])).total_seconds() > 5 * 86400],
        "account_ids_with_only_blocked_or_deleted_services": [owner for owner, rows in by_owner.items() if rows and all(row["status"] in {"blocked", "deleted"} for row in rows)],
        "main_balance_less_than_sum_of_all_owned_service_prices_account_ids": [owner for owner, rows in by_owner.items() if accounts[owner]["balance"] < sum(row["price"] for row in rows)],
        "withdraw_balance_exceeds_main_balance_account_ids": [owner for owner, row in accounts.items() if row["withdrawBalance"] > row["balance"]],
        "total_replenished_less_than_main_balance_account_ids": [owner for owner, row in accounts.items() if row["totalReplenished"] < row["balance"]],
    }


def profile(source, reference):
    data = json.loads(source.read_text(encoding="utf-8"))
    errors, deviations = validate(data)
    result = {
        "profile_version": 1,
        "purpose": "planning audit; source files are read only",
        "source_file": source.name,
        "source_size_bytes": source.stat().st_size,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "validation_errors": errors,
        "fixture_sql_constraint_deviations": deviations,
    }
    if errors:
        return result
    result["tables"] = {table: summarize_table(table, data[table]) for table in FIELDS}
    result["semantic_observations"] = semantic_observations(data, reference)
    seed_path = source.with_name("002_seed.sql")
    if seed_path.is_file():
        sql = seed_path.read_text(encoding="utf-8")
        matches = {}
        for table in FIELDS:
            tag = f"$fixture_{table}$"
            parts = sql.split(tag)
            try:
                matches[table] = len(parts) == 3 and json.loads(parts[1]) == data[table]
            except (IndexError, json.JSONDecodeError):
                matches[table] = False
        result["adjacent_sql_seed"] = {
            "file": seed_path.name,
            "sha256": hashlib.sha256(seed_path.read_bytes()).hexdigest(),
            "json_recordsets_equal_source": matches,
            "postgres_execution_performed": False,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--reference", default=DEFAULT_REFERENCE, help="Explicit UTC analysis clock; default is the fixture generation reference.")
    args = parser.parse_args()
    try:
        reference = timestamp(args.reference)
        report = profile(args.data, reference)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=True, indent=2) + "\n", end="")
    return 1 if report["validation_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
