from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb

from .database import connect_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/incidents", tags=["merchant incidents"])


class DetectIncidentRequest(BaseModel):
    merchant_id: UUID
    window_days: int = Field(default=7, ge=1, le=30)
    baseline_days: int = Field(default=30, ge=7, le=180)


class IncidentStatusRequest(BaseModel):
    merchant_id: UUID
    status: str = Field(pattern="^(ACKNOWLEDGED|RESOLVED)$")
    actor_id: str = Field(min_length=1, max_length=128)
    resolution_note: str | None = Field(default=None, max_length=1000)


@router.post("/detect")
def detect_incident(request: DetectIncidentRequest) -> dict[str, Any]:
    """Detect merchant dispute-rate spikes from persisted payment and dispute records."""
    if request.baseline_days < request.window_days:
        raise HTTPException(422, "baseline_days must be at least window_days")
    try:
        with connect_database() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                         (str(request.merchant_id), "merchant-incident-detection"))
            policy = conn.execute("""
                SELECT incident_min_disputes,incident_rate_multiplier,incident_min_transactions
                FROM merchant_policies WHERE merchant_id=%s ORDER BY version DESC LIMIT 1
            """, (request.merchant_id,)).fetchone()
            if not policy:
                exists = conn.execute("SELECT 1 FROM merchants WHERE id=%s", (request.merchant_id,)).fetchone()
                if not exists:
                    raise HTTPException(404, "Merchant not found")
                raise HTTPException(409, "Merchant has no policy configured")
            counts = conn.execute("""
                WITH bounds AS (
                    SELECT now()-make_interval(days=>%s) AS current_start,
                           now()-make_interval(days=>%s) AS baseline_start,
                           now() AS observed_at
                )
                SELECT b.current_start,b.baseline_start,b.observed_at,
                    (SELECT count(*)::int FROM transactions t WHERE t.merchant_id=%s
                       AND t.status='succeeded' AND t.created_at>=b.current_start AND t.created_at<b.observed_at) AS current_transactions,
                    (SELECT count(*)::int FROM disputes d WHERE d.merchant_id=%s
                       AND d.created_at>=b.current_start AND d.created_at<b.observed_at) AS current_disputes,
                    (SELECT count(*)::int FROM transactions t WHERE t.merchant_id=%s
                       AND t.status='succeeded' AND t.created_at>=b.baseline_start AND t.created_at<b.current_start) AS baseline_transactions,
                    (SELECT count(*)::int FROM disputes d WHERE d.merchant_id=%s
                       AND d.created_at>=b.baseline_start AND d.created_at<b.current_start) AS baseline_disputes
                FROM bounds b
            """, (request.window_days, request.window_days + request.baseline_days,
                  request.merchant_id, request.merchant_id, request.merchant_id, request.merchant_id)).fetchone()
            current_rate = (counts["current_disputes"] / counts["current_transactions"]
                            if counts["current_transactions"] else None)
            baseline_rate = (counts["baseline_disputes"] / counts["baseline_transactions"]
                             if counts["baseline_transactions"] else None)
            ratio = (current_rate / baseline_rate if current_rate is not None and baseline_rate
                     else None)
            triggered = (
                counts["current_transactions"] >= policy["incident_min_transactions"]
                and counts["current_disputes"] >= policy["incident_min_disputes"]
                and current_rate is not None
                and ((baseline_rate is None or baseline_rate == 0)
                     or (ratio is not None and ratio >= float(policy["incident_rate_multiplier"])))
            )
            metrics = {"window_days": request.window_days, "baseline_days": request.baseline_days,
                       "current_disputes": counts["current_disputes"],
                       "current_transactions": counts["current_transactions"],
                       "baseline_disputes": counts["baseline_disputes"],
                       "baseline_transactions": counts["baseline_transactions"],
                       "current_dispute_rate": current_rate, "baseline_dispute_rate": baseline_rate,
                       "rate_multiplier": ratio, "thresholds": {
                           "minimum_disputes": policy["incident_min_disputes"],
                           "minimum_transactions": policy["incident_min_transactions"],
                           "rate_multiplier": float(policy["incident_rate_multiplier"])}}
            if not triggered:
                conn.execute("""
                    INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                    VALUES ('merchant',%s,'SYSTEM','incident-detector','incident_check_clear',%s)
                """, (request.merchant_id, Jsonb(metrics)))
                return {"triggered": False, "reason": "DISPUTE_SPIKE_THRESHOLDS_NOT_MET", **metrics}
            overlap = conn.execute("""
                SELECT id,status,window_started_at,window_ends_at FROM merchant_incidents
                WHERE merchant_id=%s AND status IN ('OPEN','ACKNOWLEDGED')
                  AND window_started_at<%s AND window_ends_at>%s
                ORDER BY created_at LIMIT 1 FOR UPDATE
            """, (request.merchant_id, counts["observed_at"], counts["current_start"])).fetchone()
            if overlap:
                conn.execute("""
                    INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                    VALUES ('merchant_incident',%s,'SYSTEM','incident-detector','incident_overlap_conflict',%s)
                """, (overlap["id"], Jsonb({"merchant_id": str(request.merchant_id), **metrics})))
                return {"triggered": True, "conflict": True, "incident_id": str(overlap["id"]),
                        "status": overlap["status"], "reason": "OVERLAPPING_OPEN_INCIDENT", **metrics}
            created = conn.execute("""
                INSERT INTO merchant_incidents
                    (merchant_id,incident_type,window_started_at,window_ends_at,current_disputes,
                     current_transactions,baseline_disputes,baseline_transactions,current_dispute_rate,
                     baseline_dispute_rate,rate_multiplier,detection_evidence)
                VALUES (%s,'DISPUTE_RATE_SPIKE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id,status,created_at
            """, (request.merchant_id, counts["current_start"], counts["observed_at"],
                  counts["current_disputes"], counts["current_transactions"],
                  counts["baseline_disputes"], counts["baseline_transactions"], current_rate,
                  baseline_rate, ratio, Jsonb(metrics))).fetchone()
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                VALUES ('merchant_incident',%s,'SYSTEM','incident-detector','incident_opened',%s)
            """, (created["id"], Jsonb(metrics)))
            return {"triggered": True, "conflict": False, "incident_id": str(created["id"]),
                    "status": created["status"], "created_at": created["created_at"], **metrics}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Merchant incident detection failed")
        raise HTTPException(503, "Incident detection is temporarily unavailable") from None


@router.get("")
def list_incidents(merchant_id: UUID, status: str | None = Query(default=None, pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$"),
                   limit: int = Query(default=50, ge=1, le=100),
                   offset: int = Query(default=0, ge=0, le=10000)) -> dict[str, Any]:
    try:
        with connect_database() as conn:
            params: list[Any] = [merchant_id]
            clause = "merchant_id=%s"
            if status:
                clause += " AND status=%s"; params.append(status)
            params.extend((limit, offset))
            rows = conn.execute(f"""
                SELECT id,merchant_id,incident_type,status,window_started_at,window_ends_at,
                       current_disputes,current_transactions,baseline_disputes,baseline_transactions,
                       current_dispute_rate,baseline_dispute_rate,rate_multiplier,detection_evidence,
                       acknowledged_by,resolution_note,resolved_at,created_at,updated_at
                FROM merchant_incidents WHERE {clause} ORDER BY created_at DESC LIMIT %s OFFSET %s
            """, params).fetchall()
            return {"items": rows, "limit": limit, "offset": offset}
    except psycopg.Error:
        logger.exception("Merchant incident list failed")
        raise HTTPException(503, "Incident data is temporarily unavailable") from None


@router.post("/{incident_id}/status")
def update_incident_status(incident_id: UUID, request: IncidentStatusRequest) -> dict[str, Any]:
    try:
        with connect_database() as conn:
            row = conn.execute("""
                SELECT status FROM merchant_incidents WHERE id=%s AND merchant_id=%s FOR UPDATE
            """, (incident_id, request.merchant_id)).fetchone()
            if not row:
                raise HTTPException(404, "Incident not found")
            if row["status"] == request.status:
                return {"incident_id": str(incident_id), "status": row["status"], "replayed": True}
            if row["status"] == "RESOLVED" or (row["status"] == "ACKNOWLEDGED" and request.status == "ACKNOWLEDGED"):
                raise HTTPException(409, "Incident status transition is not allowed")
            updated = conn.execute("""
                UPDATE merchant_incidents SET status=%s,
                    acknowledged_by=CASE WHEN %s='ACKNOWLEDGED' THEN %s ELSE acknowledged_by END,
                    resolution_note=CASE WHEN %s='RESOLVED' THEN %s ELSE resolution_note END,
                    resolved_at=CASE WHEN %s='RESOLVED' THEN now() ELSE resolved_at END,
                    updated_at=now() WHERE id=%s RETURNING status
            """, (request.status, request.status, request.actor_id, request.status,
                  request.resolution_note, request.status, incident_id)).fetchone()
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                VALUES ('merchant_incident',%s,'USER',%s,%s,%s)
            """, (incident_id, request.actor_id, "incident_" + request.status.lower(),
                  Jsonb({"from": row["status"], "to": request.status,
                         "resolution_note": request.resolution_note})))
            return {"incident_id": str(incident_id), "status": updated["status"], "replayed": False}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Merchant incident status update failed")
        raise HTTPException(503, "Incident status is temporarily unavailable") from None
