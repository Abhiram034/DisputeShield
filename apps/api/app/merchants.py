from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb

from .database import connect_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/merchants", tags=["merchants"])


class CreateMerchantRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120, strip_whitespace=True)


class DeleteMerchantRequest(BaseModel):
    confirmation: str = Field(min_length=2, max_length=120)


@router.get("")
def list_merchants() -> dict[str, Any]:
    try:
        with connect_database() as conn:
            rows = conn.execute("""
                SELECT m.id,m.name,m.status,m.created_at,mp.version AS policy_version
                FROM merchants m LEFT JOIN LATERAL (
                    SELECT version FROM merchant_policies WHERE merchant_id=m.id
                    ORDER BY version DESC LIMIT 1
                ) mp ON true
                WHERE m.status='active' ORDER BY m.name,m.created_at
            """).fetchall()
            return {"items": rows}
    except psycopg.Error:
        logger.exception("Merchant list failed")
        raise HTTPException(503, "Merchant data is temporarily unavailable") from None


@router.post("")
def create_merchant(request: CreateMerchantRequest) -> dict[str, Any]:
    try:
        with connect_database() as conn:
            merchant = conn.execute("""
                INSERT INTO merchants(name) VALUES (%s) RETURNING id,name,status,created_at
            """, (request.name,)).fetchone()
            conn.execute("INSERT INTO merchant_policies(merchant_id,version) VALUES (%s,1)",
                         (merchant["id"],))
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                VALUES ('merchant',%s,'USER','local-demo-user','merchant_created',%s)
            """, (merchant["id"], Jsonb({"policy_version": 1})))
            return {**merchant, "policy_version": 1}
    except psycopg.Error:
        logger.exception("Merchant creation failed")
        raise HTTPException(503, "Merchant setup is temporarily unavailable") from None


@router.delete("/{merchant_id}")
def delete_empty_merchant(merchant_id: UUID, request: DeleteMerchantRequest) -> dict[str, Any]:
    """Permanently remove an empty workspace; populated merchant data is never cascaded."""
    try:
        with connect_database() as conn:
            merchant = conn.execute("""
                SELECT id,name FROM merchants WHERE id=%s FOR UPDATE
            """, (merchant_id,)).fetchone()
            if not merchant:
                raise HTTPException(404, "Merchant workspace not found")
            if request.confirmation != merchant["name"]:
                raise HTTPException(422, "Confirmation must exactly match the merchant name")
            counts = conn.execute("""
                SELECT
                    (SELECT count(*) FROM customers WHERE merchant_id=%s) AS customers,
                    (SELECT count(*) FROM transactions WHERE merchant_id=%s) AS transactions,
                    (SELECT count(*) FROM orders WHERE merchant_id=%s) AS orders,
                    (SELECT count(*) FROM post_payment_events WHERE merchant_id=%s) AS events,
                    (SELECT count(*) FROM disputes WHERE merchant_id=%s) AS disputes,
                    (SELECT count(*) FROM merchant_incidents WHERE merchant_id=%s) AS incidents,
                    (SELECT count(*) FROM cases WHERE merchant_id=%s) AS cases
            """, (merchant_id, merchant_id, merchant_id, merchant_id, merchant_id,
                  merchant_id, merchant_id)).fetchone()
            populated = {key: count for key, count in counts.items() if count}
            if populated:
                raise HTTPException(409, {
                    "message": "This workspace contains merchant data and cannot be deleted from this screen.",
                    "record_counts": populated,
                })
            conn.execute("DELETE FROM audit_logs WHERE entity_type='merchant' AND entity_id=%s",
                         (merchant_id,))
            conn.execute("DELETE FROM merchant_policies WHERE merchant_id=%s", (merchant_id,))
            conn.execute("DELETE FROM merchants WHERE id=%s", (merchant_id,))
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                VALUES ('merchant_deleted',%s,'USER','local-demo-user','empty_workspace_deleted',%s)
            """, (merchant_id, Jsonb({"merchant_name": merchant["name"], "record_counts": populated})))
            return {"deleted": True, "merchant_id": str(merchant_id), "name": merchant["name"]}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Merchant workspace deletion failed")
        raise HTTPException(503, "Merchant workspace deletion is temporarily unavailable") from None
