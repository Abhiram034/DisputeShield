"""Import the generated DisputeShield CSV fixture into the local PostgreSQL database.

Only generated synthetic records are written. Text fixture IDs are mapped to stable
UUIDs, making repeated runs idempotent. Existing merchants and their data are untouched.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_DATA = ROOT / "data" / "generated"
ID_PREFIX = "disputeshield:synthetic-fixture:v1"


def stable_id(kind: str, raw_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"{ID_PREFIX}:{kind}:{raw_id}")


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def batches(values: Iterable[tuple[Any, ...]], size: int = 2_000):
    batch: list[tuple[Any, ...]] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def insert_many(conn: psycopg.Connection, sql: str, values: Iterable[tuple[Any, ...]]) -> int:
    count = 0
    with conn.cursor() as cursor:
        for batch in batches(values):
            cursor.executemany(sql, batch)
            count += len(batch)
    return count


def known(value: Any, source: str, observed_at: datetime | None = None) -> dict[str, Any]:
    return {"status": "KNOWN", "value": value, "source": source,
            "observed_at": observed_at.isoformat() if observed_at else None}


def unknown(source: str, reason: str) -> dict[str, Any]:
    return {"status": "UNKNOWN", "value": None, "source": source,
            "unavailable_reason": reason}


def assessment_for(transaction: dict[str, str], customer: dict[str, str], order: dict[str, str] | None,
                   transaction_events: list[dict[str, Any]], has_dispute: bool,
                   tx_uuid: UUID, now: datetime) -> Any:
    from apps.api.app.domain.risk_engine import RiskAssessmentInput, assess_risk

    latest: dict[str, datetime] = {}
    for event in transaction_events:
        old = latest.get(event["event_type"])
        if old is None or event["created_at"] > old:
            latest[event["event_type"]] = event["created_at"]
    event_types = set(latest)

    receipt_failed = "receipt_failed" in event_types
    delivery_failed = bool(order and order["delivery_status"] == "failed") or "delivery_failed" in event_types
    delivery_delayed = "delivery_delayed" in event_types
    refund_pending = "refund_requested" in event_types and not ({"refund_processed", "issue_resolved"} & event_types)
    subscription_changed = "subscription_changed" in event_types
    duplicate_candidate = "close_similar_transaction" in event_types
    unusual_device = "unusual_device" in event_types
    recent_contact = any(
        event["event_type"] == "support_contact" and event["created_at"] >= now - timedelta(days=30)
        for event in transaction_events
    )
    issue_resolved = "issue_resolved" in event_types
    opted_out = customer["communication_opt_out"].lower() in {"1", "true", "t", "yes"}

    actions = ["flag_for_human_review"]
    issue_signal = receipt_failed or delivery_failed or delivery_delayed or refund_pending or recent_contact
    if issue_signal:
        # These are candidate actions for local workflow testing, not automatic approvals.
        actions.append("create_support_case")
        if not opted_out:
            if receipt_failed:
                actions.append("send_receipt")
            if delivery_failed or delivery_delayed:
                actions.append("send_delivery_update")
            if refund_pending:
                actions.extend(("send_refund_status", "recommend_refund"))

    observed = "synthetic CSV fixture (complete for this generated dataset)"
    event_time = lambda name: latest.get(name)
    signal_observations = {
        "support_contact_recent": known(recent_contact, observed, event_time("support_contact")),
        "receipt_failed": known(receipt_failed, observed, event_time("receipt_failed")),
        "delivery_failed": known(delivery_failed, observed, event_time("delivery_failed")),
        "delivery_delayed": known(delivery_delayed, observed, event_time("delivery_delayed")),
        "refund_pending": known(refund_pending, observed, event_time("refund_requested")),
        "subscription_changed": known(subscription_changed, observed, event_time("subscription_changed")),
        "descriptor_changed": unknown(observed, "No descriptor-change source is included in this fixture"),
        "duplicate_candidate": known(duplicate_candidate, observed, event_time("close_similar_transaction")),
        "unusual_device": known(unusual_device, observed, event_time("unusual_device")),
        "unusual_country": unknown(observed, "No customer country history is included in this fixture"),
        "multiple_failed_attempts": unknown(observed, "Failed payment-attempt history is not included in this fixture"),
    }
    payload = {
        "transaction_id": str(tx_uuid),
        "transaction_amount_minor": known(int(transaction["amount_minor"]), observed, timestamp(transaction["created_at"])),
        "payment_succeeded": known(transaction["status"] == "succeeded", observed, timestamp(transaction["created_at"])),
        "customer_average_amount_minor": known(int(customer["average_transaction_amount"]), observed),
        "previous_disputes": known(int(customer["previous_disputes"]), observed),
        **signal_observations,
        "dispute_exists": known(has_dispute, observed),
        "issue_resolved": known(issue_resolved, observed, event_time("issue_resolved")),
        "customer_opted_out": known(opted_out, observed),
        "safe_actions_available": known(actions, "default local demo merchant policy"),
    }
    return assess_risk(RiskAssessmentInput.model_validate(payload))


def load(data_dir: Path, dsn: str, include_cases: bool) -> dict[str, int]:
    from apps.api.app.cases import _eligible_for_case

    merchant_rows = rows(data_dir / "merchants.csv")
    customer_rows = rows(data_dir / "customers.csv")
    transaction_rows = rows(data_dir / "transactions.csv")
    order_rows = rows(data_dir / "orders.csv")
    event_rows = rows(data_dir / "post_payment_events.csv")
    dispute_rows = rows(data_dir / "disputes.csv")

    merchant_ids = {row["id"]: stable_id("merchant", row["id"]) for row in merchant_rows}
    customer_ids = {row["id"]: stable_id("customer", row["id"]) for row in customer_rows}
    transaction_ids = {row["id"]: stable_id("transaction", row["id"]) for row in transaction_rows}
    order_ids = {row["id"]: stable_id("order", row["id"]) for row in order_rows}
    transaction_by_source = {row["id"]: row for row in transaction_rows}
    order_by_transaction = {row["transaction_id"]: row for row in order_rows}
    customer_by_source = {row["id"]: row for row in customer_rows}

    events_by_transaction: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in event_rows:
        events_by_transaction[event["transaction_id"]].append({
            **event, "created_at": timestamp(event["created_at"]),
        })
    disputes_by_transaction: dict[str, list[dict[str, str]]] = defaultdict(list)
    for dispute in dispute_rows:
        disputes_by_transaction[dispute["transaction_id"]].append(dispute)

    counts = {"merchants": 0, "customers": 0, "transactions": 0, "orders": 0,
              "events": 0, "disputes": 0, "risk_assessments": 0, "cases": 0}
    now = datetime.now(timezone.utc)
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            counts["merchants"] = insert_many(conn, """
                INSERT INTO merchants(id,name,status) VALUES (%s,%s,%s) ON CONFLICT (id) DO NOTHING
            """, ((merchant_ids[row["id"]], row["name"], row["status"]) for row in merchant_rows))
            insert_many(conn, """
                INSERT INTO merchant_policies(merchant_id,version) VALUES (%s,1)
                ON CONFLICT (merchant_id,version) DO NOTHING
            """, ((merchant_ids[row["id"]],) for row in merchant_rows))
            counts["customers"] = insert_many(conn, """
                INSERT INTO customers
                    (id,merchant_id,synthetic_external_id,average_transaction_amount,transaction_count,
                     previous_disputes,previous_refunds,support_contact_count,communication_opt_out)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING
            """, ((customer_ids[row["id"]], merchant_ids[row["merchant_id"]], row["synthetic_external_id"],
                   int(row["average_transaction_amount"]), int(row["transaction_count"]),
                   int(row["previous_disputes"]), int(row["previous_refunds"]),
                   int(row["support_contact_count"]), row["communication_opt_out"].lower() in {"1", "true"})
                  for row in customer_rows))
            counts["transactions"] = insert_many(conn, """
                INSERT INTO transactions
                    (id,merchant_id,customer_id,order_id,amount,currency,payment_method,merchant_descriptor,
                     device_id_hash,country,status,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING
            """, ((transaction_ids[row["id"]], merchant_ids[row["merchant_id"]],
                   customer_ids[row["customer_id"]], stable_id("order", row["order_id"]),
                   int(row["amount_minor"]), row["currency"], row["payment_method"],
                   row["merchant_descriptor"], row["device_id_hash"], row["country"],
                   row["status"], timestamp(row["created_at"])) for row in transaction_rows))
            counts["orders"] = insert_many(conn, """
                INSERT INTO orders
                    (id,merchant_id,customer_id,transaction_id,order_amount,order_status,delivery_status,
                     promised_delivery_at,delivered_at,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING
            """, ((order_ids[row["id"]], merchant_ids[row["merchant_id"]],
                   customer_ids[row["customer_id"]], transaction_ids[row["transaction_id"]],
                   int(row["order_amount_minor"]), row["order_status"], row["delivery_status"],
                   timestamp(row["promised_delivery_at"]), timestamp(row["delivered_at"]),
                   timestamp(transaction_by_source[row["transaction_id"]]["created_at"]))
                  for row in order_rows))
            counts["events"] = insert_many(conn, """
                INSERT INTO post_payment_events
                    (id,merchant_id,transaction_id,customer_id,event_type,metadata,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING
            """, ((stable_id("event", row["id"]),
                   # Some scenario event CSV rows contain incorrect tenant/customer IDs; scope them
                   # using the authoritative synthetic transaction row.
                   merchant_ids[transaction_by_source[row["transaction_id"]]["merchant_id"]],
                   transaction_ids[row["transaction_id"]],
                   customer_ids[transaction_by_source[row["transaction_id"]]["customer_id"]],
                   row["event_type"], Jsonb(json.loads(row["metadata"])), timestamp(row["created_at"]))
                  for row in event_rows))
            counts["disputes"] = insert_many(conn, """
                INSERT INTO disputes(id,merchant_id,transaction_id,customer_id,reason,amount,status,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING
            """, ((stable_id("dispute", row["id"]), merchant_ids[row["merchant_id"]],
                   transaction_ids[row["transaction_id"]], customer_ids[row["customer_id"]],
                   row["reason"], int(row["amount_minor"]), row["status"], timestamp(row["created_at"]))
                  for row in dispute_rows))

            if include_cases:
                existing = {row[0] for row in conn.execute("SELECT transaction_id FROM cases").fetchall()}
                for raw_id, transaction in transaction_by_source.items():
                    tx_uuid = transaction_ids[raw_id]
                    if tx_uuid in existing:
                        continue
                    customer = customer_by_source[transaction["customer_id"]]
                    order = order_by_transaction.get(raw_id)
                    assessment = assessment_for(
                        transaction, customer, order, events_by_transaction.get(raw_id, []),
                        bool(disputes_by_transaction.get(raw_id)), tx_uuid, now,
                    )
                    eligible, _, initial_state = _eligible_for_case(assessment)
                    if not eligible:
                        continue
                    assessment_id = stable_id("risk-assessment", raw_id)
                    case_id = stable_id("case", raw_id)
                    conn.execute("""
                        INSERT INTO risk_assessments
                            (id,transaction_id,risk_score,risk_level,intervention_opportunity,signals,model_version)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING
                    """, (assessment_id, tx_uuid, assessment.risk_score, assessment.risk_level.value,
                          assessment.intervention_opportunity.value,
                          Jsonb(assessment.model_dump(mode="json")["signals"]), assessment.model_version))
                    inserted = conn.execute("""
                        INSERT INTO cases(id,merchant_id,transaction_id,risk_assessment_id,state)
                        VALUES (%s,%s,%s,%s,%s) ON CONFLICT (transaction_id) DO NOTHING RETURNING id
                    """, (case_id, merchant_ids[transaction["merchant_id"]], tx_uuid,
                          assessment_id, initial_state)).fetchone()
                    if inserted:
                        conn.execute("""
                            INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                            VALUES ('risk_assessment',%s,'SYSTEM','synthetic-fixture-loader','risk_assessed',%s),
                                   ('case',%s,'SYSTEM','synthetic-fixture-loader','case_detected',%s)
                        """, (assessment_id, Jsonb({"model_version": assessment.model_version,
                                                     "synthetic_fixture": True}),
                              case_id, Jsonb({"state": initial_state, "synthetic_fixture": True,
                                              "risk_level": assessment.risk_level.value,
                                              "risk_score": assessment.risk_score})))
                        counts["risk_assessments"] += 1
                        counts["cases"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--skip-cases", action="store_true", help="Import source rows without generating case records")
    parser.add_argument("--confirm-load", action="store_true", help="Required acknowledgement before writing synthetic rows")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL", "")
    if not args.confirm_load:
        parser.error("pass --confirm-load to write the synthetic fixture to PostgreSQL")
    if not dsn:
        parser.error("set DATABASE_URL to the DisputeShield database")
    if not args.data_dir.is_dir():
        parser.error(f"data directory does not exist: {args.data_dir}")
    try:
        with psycopg.connect(dsn) as conn:
            db_name = conn.info.dbname
        if db_name != "disputeshield":
            parser.error(f"refusing writes to database {db_name!r}; expected local database 'disputeshield'")
        counts = load(args.data_dir.resolve(), dsn, not args.skip_cases)
    except Exception as exc:
        print(f"Synthetic load failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"data_dir": str(args.data_dir.resolve()), "inserted": counts}, indent=2))
    print("Synthetic fixture IDs map deterministically to UUIDs; existing records were not deleted or overwritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
