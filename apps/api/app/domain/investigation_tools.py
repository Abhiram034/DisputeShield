"""Allowlisted read-only investigation tools. Every query is tenant-scoped."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)
ToolStatus = Literal["KNOWN", "EMPTY", "FAILED"]

@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    status: ToolStatus
    data: Any = None
    error_code: str | None = None

class InvestigationTools:
    """Fixed SELECT-only tool registry; no cursor or write method is exposed."""
    TOOL_NAMES = (
        "get_transaction", "get_customer_profile", "get_customer_transactions",
        "get_dispute_history", "get_refund_history", "get_support_history",
        "get_order_status", "get_delivery_status", "get_receipt_status",
        "get_subscription_history", "search_similar_cases", "get_merchant_policy",
    )

    def __init__(self, connection: psycopg.Connection, merchant_id: UUID):
        self._connection = connection
        self._merchant_id = merchant_id

    def _one(self, name: str, query: str, *params: Any) -> ToolResult:
        try:
            with self._connection.transaction():
                row = self._connection.execute(query, (self._merchant_id, *params)).fetchone()
            return ToolResult(name, "KNOWN", dict(row)) if row else ToolResult(name, "EMPTY", {})
        except psycopg.Error:
            logger.exception("Investigation read tool failed: %s", name)
            return ToolResult(name, "FAILED", None, "DATA_SOURCE_UNAVAILABLE")

    def _many(self, name: str, query: str, *params: Any) -> ToolResult:
        try:
            with self._connection.transaction():
                rows = self._connection.execute(query, (self._merchant_id, *params)).fetchall()
            values = [dict(row) for row in rows]
            return ToolResult(name, "KNOWN" if values else "EMPTY", values)
        except psycopg.Error:
            logger.exception("Investigation read tool failed: %s", name)
            return ToolResult(name, "FAILED", None, "DATA_SOURCE_UNAVAILABLE")

    def get_transaction(self, transaction_id: UUID) -> ToolResult:
        return self._one("get_transaction", """
            SELECT id, merchant_id, customer_id, order_id, amount, currency, payment_method,
                   merchant_descriptor, device_id_hash, country, status, created_at
            FROM transactions WHERE merchant_id=%s AND id=%s
        """, transaction_id)

    def get_customer_profile(self, customer_id: UUID) -> ToolResult:
        return self._one("get_customer_profile", """
            SELECT id, synthetic_external_id, average_transaction_amount, transaction_count,
                   previous_disputes, previous_refunds, support_contact_count,
                   communication_opt_out, created_at
            FROM customers WHERE merchant_id=%s AND id=%s
        """, customer_id)

    def get_customer_transactions(self, customer_id: UUID, limit: int = 20) -> ToolResult:
        return self._many("get_customer_transactions", """
            SELECT id, order_id, amount, currency, status, merchant_descriptor, created_at
            FROM transactions WHERE merchant_id=%s AND customer_id=%s
            ORDER BY created_at DESC LIMIT %s
        """, customer_id, min(max(limit, 1), 50))

    def get_dispute_history(self, customer_id: UUID) -> ToolResult:
        return self._many("get_dispute_history", """
            SELECT id, transaction_id, reason, amount, status, created_at, resolved_at
            FROM disputes WHERE merchant_id=%s AND customer_id=%s ORDER BY created_at DESC LIMIT 50
        """, customer_id)

    def get_refund_history(self, transaction_id: UUID) -> ToolResult:
        return self._many("get_refund_history", """
            SELECT id, event_type, metadata, created_at FROM post_payment_events
            WHERE merchant_id=%s AND transaction_id=%s
              AND event_type IN ('refund_requested','refund_processed','refund_failed')
            ORDER BY created_at DESC LIMIT 50
        """, transaction_id)

    def get_support_history(self, customer_id: UUID) -> ToolResult:
        return self._many("get_support_history", """
            SELECT id, transaction_id, event_type, metadata, created_at FROM post_payment_events
            WHERE merchant_id=%s AND customer_id=%s
              AND event_type IN ('support_contact','complaint')
            ORDER BY created_at DESC LIMIT 50
        """, customer_id)

    def get_order_status(self, transaction_id: UUID) -> ToolResult:
        return self._one("get_order_status", """
            SELECT id, transaction_id, order_status, delivery_status, order_amount,
                   promised_delivery_at, delivered_at, created_at
            FROM orders WHERE merchant_id=%s AND transaction_id=%s
            ORDER BY created_at DESC LIMIT 1
        """, transaction_id)

    def get_delivery_status(self, transaction_id: UUID) -> ToolResult:
        return self._one("get_delivery_status", """
            SELECT id, delivery_status, promised_delivery_at, delivered_at
            FROM orders WHERE merchant_id=%s AND transaction_id=%s
            ORDER BY created_at DESC LIMIT 1
        """, transaction_id)

    def get_receipt_status(self, transaction_id: UUID) -> ToolResult:
        return self._many("get_receipt_status", """
            SELECT id, event_type, metadata, created_at FROM post_payment_events
            WHERE merchant_id=%s AND transaction_id=%s AND event_type IN ('receipt_sent','receipt_failed')
            ORDER BY created_at DESC LIMIT 20
        """, transaction_id)

    def get_subscription_history(self, customer_id: UUID) -> ToolResult:
        return self._many("get_subscription_history", """
            SELECT id, transaction_id, event_type, metadata, created_at FROM post_payment_events
            WHERE merchant_id=%s AND customer_id=%s
              AND event_type IN ('subscription_changed','invoice_sent','invoice_failed','customer_viewed_invoice')
            ORDER BY created_at DESC LIMIT 50
        """, customer_id)

    def search_similar_cases(self, transaction_id: UUID) -> ToolResult:
        return self._many("search_similar_cases", """
            SELECT other.id AS transaction_id, other.customer_id, other.amount, other.currency,
                   other.status, other.created_at,
                   abs(other.amount - tx_current.amount)::BIGINT AS amount_difference
            FROM transactions tx_current JOIN transactions other
              ON other.merchant_id=tx_current.merchant_id AND other.id<>tx_current.id
             AND other.currency=tx_current.currency
             AND abs(other.amount-tx_current.amount) <= greatest(100, (tx_current.amount * 0.10)::BIGINT)
             AND other.created_at BETWEEN tx_current.created_at - interval '10 minutes'
                                      AND tx_current.created_at + interval '10 minutes'
            WHERE tx_current.merchant_id=%s AND tx_current.id=%s
            ORDER BY amount_difference, other.created_at DESC LIMIT 10
        """, transaction_id)

    def get_merchant_policy(self) -> ToolResult:
        return self._one("get_merchant_policy", """
            SELECT id, version, auto_send_receipt, auto_create_support_case, auto_refund_limit,
                   require_human_for_high_value, require_human_for_fraud,
                   communication_enabled, high_value_threshold, created_at
            FROM merchant_policies WHERE merchant_id=%s ORDER BY version DESC LIMIT 1
        """)

    def run_case_snapshot(self, transaction_id: UUID, customer_id: UUID) -> dict[str, ToolResult]:
        calls = (
            ("transaction", lambda: self.get_transaction(transaction_id)),
            ("customer_profile", lambda: self.get_customer_profile(customer_id)),
            ("customer_transactions", lambda: self.get_customer_transactions(customer_id)),
            ("dispute_history", lambda: self.get_dispute_history(customer_id)),
            ("refund_history", lambda: self.get_refund_history(transaction_id)),
            ("support_history", lambda: self.get_support_history(customer_id)),
            ("order_status", lambda: self.get_order_status(transaction_id)),
            ("delivery_status", lambda: self.get_delivery_status(transaction_id)),
            ("receipt_status", lambda: self.get_receipt_status(transaction_id)),
            ("subscription_history", lambda: self.get_subscription_history(customer_id)),
            ("similar_cases", lambda: self.search_similar_cases(transaction_id)),
            ("merchant_policy", self.get_merchant_policy),
        )
        return {name: call() for name, call in calls}
