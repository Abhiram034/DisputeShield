from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb

from .database import connect_database
from .domain.investigation_agent import investigate_case
from .domain.investigation_tools import InvestigationTools
from .domain.policy_engine import PolicyAction, PolicyConfig, PolicyDecision, PolicyFacts, decide_action
from .domain.risk_engine import RiskAssessment, RiskAssessmentInput, RiskLevel, assess_risk

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cases"])


def _json_value(value: Any) -> Any:
    """Round-trip values such as UUIDs/timestamps through JSON's wire types."""
    import json
    return json.loads(json.dumps(value, default=str))


def _persist_investigation_evidence(conn: psycopg.Connection, investigation_id: UUID,
                                    merchant_id: UUID, tool_results: list[dict[str, Any]]) -> None:
    for result in tool_results:
        tool_name = result["tool_name"]
        status = result["status"]
        data = result.get("data")
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) and data else []
        if status != "KNOWN" or not rows:
            ref = f"{tool_name}:status"
            conn.execute("""
                INSERT INTO investigation_evidence
                    (investigation_id,merchant_id,evidence_ref,source_tool,field_name,value,data_status)
                VALUES (%s,%s,%s,%s,'status',%s,%s)
                ON CONFLICT (investigation_id,evidence_ref) DO NOTHING
            """, (investigation_id, merchant_id, ref, tool_name,
                  Jsonb({"error_code": result.get("error_code")}) if status == "FAILED" else None, status))
            continue
        for index, row in enumerate(rows):
            source_id = str(row.get("id", row.get("transaction_id", index)))
            for field_name, value in row.items():
                reference = f"{tool_name}:{source_id}:{field_name}"
                observed_at = row.get("created_at")
                conn.execute("""
                    INSERT INTO investigation_evidence
                        (investigation_id,merchant_id,evidence_ref,source_tool,source_record_id,
                         field_name,value,data_status,observed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'KNOWN',%s)
                    ON CONFLICT (investigation_id,evidence_ref) DO NOTHING
                """, (investigation_id, merchant_id, reference, tool_name, source_id, field_name,
                      Jsonb(_json_value(value)), observed_at))


class DetectCaseRequest(BaseModel):
    merchant_id: UUID
    assessment: RiskAssessmentInput


class CasePolicyCheckRequest(BaseModel):
    merchant_id: UUID
    action: PolicyAction


class ActionRecommendationRequest(BaseModel):
    merchant_id: UUID
    action: PolicyAction
    idempotency_key: str = Field(min_length=8, max_length=128)
    requested_by: str = Field(default="local-demo-user", min_length=1, max_length=128)


class ApprovalRequest(BaseModel):
    merchant_id: UUID
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=1000)


class ExecutionRequest(BaseModel):
    merchant_id: UUID


HUMAN_APPROVAL_REASONS = {
    "HIGH_VALUE_REVIEW", "FRAUD_PATTERN_REVIEW", "RECEIPT_APPROVAL_REQUIRED",
    "SUPPORT_APPROVAL_REQUIRED", "REFUND_LIMIT_REVIEW",
}


class CaseDetectionResponse(BaseModel):
    opened: bool
    reason: str
    case_id: UUID | None = None
    state: str | None = None
    assessment: RiskAssessment


def _eligible_for_case(assessment: RiskAssessment) -> tuple[bool, str, str]:
    if assessment.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
        if assessment.intervention_opportunity.value == "HIGH":
            return True, "Elevated deterministic risk with an eligible prevention path", "DETECTED"
        return True, "Elevated risk without a known safe prevention path; human review required", "ESCALATED"
    if assessment.risk_level == RiskLevel.MEDIUM and assessment.intervention_opportunity.value == "HIGH":
        return True, "Medium risk with a known safe intervention opportunity", "DETECTED"
    if assessment.risk_level == RiskLevel.UNKNOWN and assessment.signals:
        return True, "Signals present but required data is unavailable", "ESCALATED"
    return False, "Risk/opportunity threshold not met", ""


@router.post("/detect", response_model=CaseDetectionResponse)
def detect_case(request: DetectCaseRequest):
    assessment = request.assessment
    try:
        transaction_id = UUID(assessment.transaction_id)
    except ValueError as exc:
        raise HTTPException(422, "transaction_id must be a UUID for persisted case detection") from exc
    if not request.merchant_id:
        raise HTTPException(422, "merchant_id is required")
    result = assess_risk(assessment)
    qualifies, reason, initial_state = _eligible_for_case(result)
    try:
        with connect_database() as conn:
            transaction = conn.execute(
                "SELECT id FROM transactions WHERE id=%s AND merchant_id=%s",
                (transaction_id, request.merchant_id),
            ).fetchone()
            if not transaction:
                raise HTTPException(404, "Transaction not found for this merchant")
            assessment_row = conn.execute("""
                INSERT INTO risk_assessments
                    (transaction_id,risk_score,risk_level,intervention_opportunity,signals,model_version)
                VALUES (%s,%s,%s,%s,%s,%s) RETURNING id
            """, (transaction_id, result.risk_score, result.risk_level.value,
                  result.intervention_opportunity.value, Jsonb(result.model_dump(mode="json")["signals"]),
                  result.model_version)).fetchone()
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                VALUES ('risk_assessment',%s,'SYSTEM','deterministic-risk-engine','risk_assessed',%s)
            """, (assessment_row["id"], Jsonb({
                "risk_score": result.risk_score,
                "risk_level": result.risk_level.value,
                "intervention_opportunity": result.intervention_opportunity.value,
                "model_version": result.model_version,
            })))
            existing = conn.execute(
                "SELECT id,state FROM cases WHERE transaction_id=%s AND merchant_id=%s",
                (transaction_id, request.merchant_id),
            ).fetchone()
            if existing:
                conn.execute("UPDATE cases SET risk_assessment_id=%s,updated_at=now() WHERE id=%s",
                             (assessment_row["id"], existing["id"]))
                conn.execute("""
                    INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                    VALUES ('case',%s,'SYSTEM','deterministic-risk-engine','case_reassessed',%s)
                """, (existing["id"], Jsonb({"risk_assessment_id": str(assessment_row["id"]),
                                              "risk_level": result.risk_level.value,
                                              "risk_score": result.risk_score})))
                return CaseDetectionResponse(opened=True, reason="Existing case refreshed with latest assessment",
                                             case_id=existing["id"], state=existing["state"], assessment=result)
            if not qualifies:
                return CaseDetectionResponse(opened=False, reason=reason, assessment=result)
            created = conn.execute("""
                INSERT INTO cases(merchant_id,transaction_id,risk_assessment_id,state)
                VALUES (%s,%s,%s,%s) RETURNING id,state
            """, (request.merchant_id, transaction_id, assessment_row["id"], initial_state)).fetchone()
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                VALUES ('case',%s,'SYSTEM','case-detector','case_detected',%s)
            """, (created["id"], Jsonb({"risk_assessment_id": str(assessment_row["id"]),
                                        "risk_level": result.risk_level.value,
                                        "risk_score": result.risk_score,
                                        "intervention_opportunity": result.intervention_opportunity.value,
                                        "initial_state": initial_state})))
            return CaseDetectionResponse(opened=True, reason=reason, case_id=created["id"],
                                         state=created["state"], assessment=result)
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Case detection database operation failed")
        raise HTTPException(503, "Case storage is temporarily unavailable") from None


@router.get("")
def list_cases(
    merchant_id: UUID,
    state: str | None = Query(default=None, max_length=40),
    risk_level: RiskLevel | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict[str, Any]:
    clauses = ["c.merchant_id=%s"]
    params: list[Any] = [merchant_id]
    if state:
        allowed = {"DETECTED", "INVESTIGATING", "INVESTIGATION_COMPLETE", "ACTION_RECOMMENDED",
                   "WAITING_FOR_APPROVAL", "APPROVED", "EXECUTING", "EXECUTED", "MONITORING",
                   "RESOLVED", "ESCALATED", "EXPIRED", "FAILED"}
        if state not in allowed:
            raise HTTPException(422, "Invalid case state")
        clauses.append("c.state=%s"); params.append(state)
    if risk_level:
        clauses.append("ra.risk_level=%s"); params.append(risk_level.value)
    params.extend((limit, offset))
    where = " AND ".join(clauses)
    try:
        with connect_database() as conn:
            rows = conn.execute(f"""
                SELECT c.id,c.merchant_id,c.transaction_id,c.state,c.created_at,c.updated_at,
                       t.amount,t.currency,t.status AS transaction_status,t.merchant_descriptor,
                       cu.synthetic_external_id AS customer_ref,
                       ra.risk_score,ra.risk_level,ra.intervention_opportunity,ra.signals
                FROM cases c
                JOIN transactions t ON t.id=c.transaction_id AND t.merchant_id=c.merchant_id
                JOIN customers cu ON cu.id=t.customer_id AND cu.merchant_id=c.merchant_id
                JOIN risk_assessments ra ON ra.id=c.risk_assessment_id
                WHERE {where}
                ORDER BY c.created_at DESC LIMIT %s OFFSET %s
            """, params).fetchall()
        return {"items": rows, "limit": limit, "offset": offset}
    except psycopg.Error:
        logger.exception("Case list query failed")
        raise HTTPException(503, "Case data is temporarily unavailable") from None


@router.get("/{case_id}")
def get_case(case_id: UUID, merchant_id: UUID) -> dict[str, Any]:
    try:
        with connect_database() as conn:
            row = conn.execute("""
                SELECT c.id,c.merchant_id,c.transaction_id,c.state,c.created_at,c.updated_at,c.resolved_at,
                       t.customer_id,t.order_id,t.amount,t.currency,t.payment_method,t.merchant_descriptor,
                       t.device_id_hash,t.country,t.status AS transaction_status,t.created_at AS payment_created_at,
                       cu.synthetic_external_id AS customer_ref,cu.average_transaction_amount,
                       cu.transaction_count,cu.previous_disputes,cu.previous_refunds,
                       cu.support_contact_count,cu.communication_opt_out,
                       ra.id AS risk_assessment_id,ra.risk_score,ra.risk_level,ra.intervention_opportunity,
                       ra.signals,ra.model_version,ra.created_at AS assessed_at
                FROM cases c
                JOIN transactions t ON t.id=c.transaction_id AND t.merchant_id=c.merchant_id
                JOIN customers cu ON cu.id=t.customer_id AND cu.merchant_id=c.merchant_id
                JOIN risk_assessments ra ON ra.id=c.risk_assessment_id
                WHERE c.id=%s AND c.merchant_id=%s
            """, (case_id, merchant_id)).fetchone()
        if not row:
            raise HTTPException(404, "Case not found")
        return row
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Case detail query failed")
        raise HTTPException(503, "Case data is temporarily unavailable") from None


@router.get("/{case_id}/investigation-tools")
def get_investigation_snapshot(case_id: UUID, merchant_id: UUID) -> dict[str, Any]:
    """Run the allowlisted read-only evidence tools; this endpoint does not mutate state."""
    try:
        with connect_database() as conn:
            context = conn.execute("""
                SELECT c.transaction_id,t.customer_id FROM cases c
                JOIN transactions t ON t.id=c.transaction_id AND t.merchant_id=c.merchant_id
                WHERE c.id=%s AND c.merchant_id=%s
            """, (case_id, merchant_id)).fetchone()
            if not context:
                raise HTTPException(404, "Case not found")
            tools = InvestigationTools(conn, merchant_id)
            results = tools.run_case_snapshot(context["transaction_id"], context["customer_id"])
            return {name: asdict(result) for name, result in results.items()}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Investigation tool snapshot failed")
        raise HTTPException(503, "Investigation data is temporarily unavailable") from None


@router.post("/{case_id}/investigate")
def investigate(case_id: UUID, merchant_id: UUID) -> dict[str, Any]:
    """Run bounded AI analysis; only the service persists its validated result."""
    try:
        with connect_database() as conn:
            context = conn.execute("""
                SELECT c.transaction_id,t.customer_id,ra.risk_level
                FROM cases c JOIN transactions t
                  ON t.id=c.transaction_id AND t.merchant_id=c.merchant_id
                JOIN risk_assessments ra ON ra.id=c.risk_assessment_id
                WHERE c.id=%s AND c.merchant_id=%s
            """, (case_id, merchant_id)).fetchone()
            if not context:
                raise HTTPException(404, "Case not found")
            status, report = investigate_case(
                InvestigationTools(conn, merchant_id), context["transaction_id"], context["customer_id"],
                context["risk_level"],
            )
            if report is None:
                conn.execute("""
                    INSERT INTO investigations(transaction_id,status,primary_hypothesis,confidence,
                                               supporting_evidence,contradicting_evidence,unknowns,completed_at)
                    VALUES (%s,'UNAVAILABLE','AI investigation unavailable',0,'[]'::jsonb,'[]'::jsonb,
                            %s,now())
                """, (context["transaction_id"], Jsonb(["AI investigation unavailable; human review required"])))
                conn.execute("UPDATE cases SET state='ESCALATED',updated_at=now() WHERE id=%s", (case_id,))
                conn.execute("""
                    INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                    VALUES ('case',%s,'SYSTEM','investigation-agent','investigation_unavailable',%s)
                """, (case_id, Jsonb({"route": "human_review"})))
                return {"status": "UNAVAILABLE", "route": "human_review",
                        "message": "AI investigation unavailable; deterministic risk assessment remains available."}
            investigation = conn.execute("""
                INSERT INTO investigations(transaction_id,status,primary_hypothesis,confidence,
                                           supporting_evidence,contradicting_evidence,unknowns,completed_at)
                VALUES (%s,'COMPLETED',%s,%s,%s,%s,%s,now()) RETURNING id
            """, (context["transaction_id"], report["primary_hypothesis"], report["confidence"],
                  Jsonb(report["supporting_evidence"]), Jsonb(report["contradicting_evidence"]),
                  Jsonb(report["unknowns"]))).fetchone()
            _persist_investigation_evidence(conn, investigation["id"], merchant_id, report["tool_results"])
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                VALUES ('case',%s,'SYSTEM','investigation-agent','investigation_completed',%s)
            """, (case_id, Jsonb({"risk_level": report["risk_level"],
                                  "recommended_action": report["recommended_action"],
                                  "confidence": report["confidence"]})))
            return {"status": "COMPLETED", "report": report}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Case investigation persistence failed")
        raise HTTPException(503, "Investigation storage is temporarily unavailable") from None


@router.get("/{case_id}/evidence")
def get_case_evidence(case_id: UUID, merchant_id: UUID) -> dict[str, Any]:
    """Return the latest investigation's source fields, scoped through the case tenant."""
    try:
        with connect_database() as conn:
            case = conn.execute("""
                SELECT c.id,i.id AS investigation_id,i.status
                FROM cases c
                JOIN investigations i ON i.transaction_id=c.transaction_id
                WHERE c.id=%s AND c.merchant_id=%s
                ORDER BY i.created_at DESC LIMIT 1
            """, (case_id, merchant_id)).fetchone()
            if not case:
                exists = conn.execute("SELECT 1 FROM cases WHERE id=%s AND merchant_id=%s",
                                      (case_id, merchant_id)).fetchone()
                if not exists:
                    raise HTTPException(404, "Case not found")
                return {"case_id": str(case_id), "investigation_status": None, "items": []}
            rows = conn.execute("""
                SELECT id,evidence_ref,source_tool,source_record_id,field_name,value,
                       data_status,observed_at,created_at
                FROM investigation_evidence
                WHERE investigation_id=%s AND merchant_id=%s
                ORDER BY source_tool,source_record_id,field_name
            """, (case["investigation_id"], merchant_id)).fetchall()
            return {"case_id": str(case_id), "investigation_status": case["status"], "items": rows}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Case evidence query failed")
        raise HTTPException(503, "Investigation evidence is temporarily unavailable") from None


def _evaluate_case_policy_conn(conn: psycopg.Connection, case_id: UUID, merchant_id: UUID,
                               action: PolicyAction, human_approval_granted: bool = False
                               ) -> tuple[PolicyDecision, list[str], UUID | None, UUID]:
    row = conn.execute("""
        SELECT c.id,c.transaction_id,c.state,t.amount,cu.communication_opt_out,
               ra.risk_level,ra.intervention_opportunity,ra.signals,
               mp.version AS policy_version,mp.auto_send_receipt,
               mp.auto_create_support_case,mp.auto_refund_limit,
               mp.require_human_for_high_value,mp.high_value_threshold,
               mp.require_human_for_fraud,mp.communication_enabled,
               mp.evidence_max_age_minutes
        FROM cases c
        JOIN transactions t ON t.id=c.transaction_id AND t.merchant_id=c.merchant_id
        JOIN customers cu ON cu.id=t.customer_id AND cu.merchant_id=c.merchant_id
        JOIN risk_assessments ra ON ra.id=c.risk_assessment_id
        LEFT JOIN LATERAL (
            SELECT * FROM merchant_policies
            WHERE merchant_id=c.merchant_id ORDER BY version DESC LIMIT 1
        ) mp ON true
        WHERE c.id=%s AND c.merchant_id=%s FOR UPDATE OF c
    """, (case_id, merchant_id)).fetchone()
    if not row:
        raise HTTPException(404, "Case not found")
    policy = None
    if row["policy_version"] is not None:
        policy = PolicyConfig.model_validate({
            "version": row["policy_version"], "auto_send_receipt": row["auto_send_receipt"],
            "auto_create_support_case": row["auto_create_support_case"],
            "auto_refund_limit": row["auto_refund_limit"],
            "require_human_for_high_value": row["require_human_for_high_value"],
            "high_value_threshold": row["high_value_threshold"],
            "require_human_for_fraud": row["require_human_for_fraud"],
            "communication_enabled": row["communication_enabled"],
            "evidence_max_age_minutes": row["evidence_max_age_minutes"],
        })
    investigation = conn.execute("""
        SELECT i.id,i.status FROM investigations i JOIN transactions t ON t.id=i.transaction_id
        WHERE i.transaction_id=%s AND t.merchant_id=%s
        ORDER BY i.created_at DESC LIMIT 1
    """, (row["transaction_id"], merchant_id)).fetchone()
    evidence = {"evidence_count": 0, "evidence_failed": False, "evidence_age_minutes": None}
    refs: list[str] = []
    if investigation:
        evidence = conn.execute("""
            SELECT count(*)::int AS evidence_count,
                   coalesce(bool_or(data_status='FAILED'),false) AS evidence_failed,
                   extract(epoch FROM now()-max(created_at))/60.0 AS evidence_age_minutes
            FROM investigation_evidence WHERE investigation_id=%s AND merchant_id=%s
        """, (investigation["id"], merchant_id)).fetchone()
        refs = [item["evidence_ref"] for item in conn.execute("""
            SELECT evidence_ref FROM investigation_evidence WHERE investigation_id=%s AND merchant_id=%s
            ORDER BY source_tool,source_record_id,field_name LIMIT 100
        """, (investigation["id"], merchant_id)).fetchall()]
    dispute_exists = conn.execute("""
        SELECT EXISTS(SELECT 1 FROM disputes WHERE transaction_id=%s AND merchant_id=%s) AS has_dispute
    """, (row["transaction_id"], merchant_id)).fetchone()["has_dispute"]
    signal_names = [item.get("name", "") for item in (row["signals"] or []) if isinstance(item, dict)]
    facts = PolicyFacts(
        case_state=row["state"], risk_level=row["risk_level"],
        intervention_opportunity=row["intervention_opportunity"], risk_signals=signal_names,
        amount_minor=row["amount"], communication_opt_out=row["communication_opt_out"],
        dispute_exists=dispute_exists,
        investigation_status=investigation["status"] if investigation else None,
        evidence_count=evidence["evidence_count"], evidence_failed=evidence["evidence_failed"],
        evidence_age_minutes=float(evidence["evidence_age_minutes"])
        if evidence["evidence_age_minutes"] is not None else None,
    )
    decision = decide_action(action, policy, facts, human_approval_granted)
    conn.execute("""
        INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details,policy_version)
        VALUES ('case',%s,'SYSTEM','deterministic-policy-engine','policy_evaluated',%s,%s)
    """, (case_id, Jsonb({"action": decision.action.value, "decision": decision.decision,
                          "reason_code": decision.reason_code, "evidence_references": refs,
                          "approval_granted": human_approval_granted}), decision.policy_version or None))
    return decision, refs, investigation["id"] if investigation else None, row["transaction_id"]


@router.post("/{case_id}/policy-check")
def check_case_policy(case_id: UUID, request: CasePolicyCheckRequest) -> dict[str, Any]:
    """Evaluate a requested recommendation from trusted case, risk, evidence, and policy data."""
    try:
        with connect_database() as conn:
            decision, refs, _, _ = _evaluate_case_policy_conn(
                conn, case_id, request.merchant_id, request.action
            )
            return {"case_id": str(case_id), **decision.model_dump(mode="json"), "evidence_references": refs}
    except HTTPException:
        raise
    except (psycopg.Error, ValueError):
        logger.exception("Policy evaluation failed")
        raise HTTPException(503, "Policy evaluation is temporarily unavailable") from None


@router.post("/{case_id}/actions")
def recommend_action(case_id: UUID, request: ActionRecommendationRequest) -> Any:
    """Persist an idempotent action recommendation after a same-transaction policy check."""
    fingerprint = hashlib.sha256(json.dumps({"case_id": str(case_id), "action": request.action.value},
                                            sort_keys=True).encode()).hexdigest()
    try:
        with connect_database() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                         (str(request.merchant_id), request.idempotency_key))
            existing = conn.execute("""
                SELECT id,transaction_id,action_type,approval_status,execution_status,
                       idempotency_key,request_fingerprint,policy_version,created_at
                FROM interventions WHERE merchant_id=%s AND idempotency_key=%s FOR UPDATE
            """, (request.merchant_id, request.idempotency_key)).fetchone()
            if existing:
                if existing["request_fingerprint"].strip() != fingerprint:
                    return JSONResponse(status_code=409, content={"detail": "Idempotency key was already used for a different action"})
                return {"intervention_id": str(existing["id"]), "case_id": str(case_id),
                        "action": existing["action_type"], "approval_status": existing["approval_status"],
                        "execution_status": existing["execution_status"],
                        "idempotency_key": existing["idempotency_key"], "replayed": True}
            decision, refs, investigation_id, transaction_id = _evaluate_case_policy_conn(
                conn, case_id, request.merchant_id, request.action
            )
            open_intervention = conn.execute("""
                SELECT id,action_type,execution_status FROM interventions
                WHERE merchant_id=%s AND transaction_id=%s
                  AND execution_status IN ('PENDING_APPROVAL','READY')
                ORDER BY created_at DESC LIMIT 1
            """, (request.merchant_id, transaction_id)).fetchone()
            if open_intervention:
                return JSONResponse(status_code=409, content={
                    "detail": "This case already has an open intervention",
                    "intervention_id": str(open_intervention["id"]),
                    "action": open_intervention["action_type"],
                    "execution_status": open_intervention["execution_status"],
                })
            needs_approval = decision.reason_code in HUMAN_APPROVAL_REASONS and decision.requires_human
            if not decision.allowed and not needs_approval:
                return JSONResponse(status_code=409, content={"detail": decision.model_dump(mode="json")})
            approval_status = "PENDING" if needs_approval else "NOT_REQUIRED"
            execution_status = "PENDING_APPROVAL" if needs_approval else "READY"
            inserted = conn.execute("""
                INSERT INTO interventions
                    (merchant_id,transaction_id,investigation_id,action_type,recommendation_reason,
                     policy_version,approval_status,execution_status,idempotency_key,
                     request_fingerprint,requested_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at
            """, (request.merchant_id, transaction_id, investigation_id, request.action.value,
                  decision.reason, decision.policy_version, approval_status, execution_status,
                  request.idempotency_key, fingerprint, request.requested_by)).fetchone()
            conn.execute("UPDATE cases SET state=%s,updated_at=now() WHERE id=%s",
                         ("WAITING_FOR_APPROVAL" if needs_approval else "ACTION_RECOMMENDED", case_id))
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details,policy_version)
                VALUES ('intervention',%s,'USER',%s,%s,%s,%s)
            """, (inserted["id"], request.requested_by,
                  "approval_requested" if needs_approval else "action_recommended",
                  Jsonb({"case_id": str(case_id), "action": request.action.value,
                         "reason_code": decision.reason_code, "evidence_references": refs,
                         "idempotency_key": request.idempotency_key}), decision.policy_version))
            return {"intervention_id": str(inserted["id"]), "case_id": str(case_id),
                    "action": request.action.value, "policy_decision": decision.decision,
                    "approval_status": approval_status, "execution_status": execution_status,
                    "policy_version": decision.policy_version, "idempotency_key": request.idempotency_key,
                    "created_at": inserted["created_at"], "replayed": False}
    except HTTPException:
        raise
    except (psycopg.Error, ValueError):
        logger.exception("Action recommendation failed")
        raise HTTPException(503, "Action recommendation is temporarily unavailable") from None


@router.post("/{case_id}/interventions/{intervention_id}/approval")
def decide_approval(case_id: UUID, intervention_id: UUID, request: ApprovalRequest) -> Any:
    try:
        with connect_database() as conn:
            row = conn.execute("""
                SELECT i.approval_status,i.execution_status,i.action_type
                FROM interventions i JOIN cases c ON c.transaction_id=i.transaction_id
                WHERE i.id=%s AND c.id=%s AND i.merchant_id=%s AND c.merchant_id=%s
                FOR UPDATE OF i,c
            """, (intervention_id, case_id, request.merchant_id, request.merchant_id)).fetchone()
            if not row:
                raise HTTPException(404, "Intervention not found")
            existing = conn.execute("SELECT decision FROM intervention_approvals WHERE intervention_id=%s",
                                    (intervention_id,)).fetchone()
            if existing:
                if existing["decision"] == request.decision:
                    return {"intervention_id": str(intervention_id), "decision": existing["decision"], "replayed": True}
                return JSONResponse(status_code=409, content={"detail": "A different approval decision has already been recorded"})
            if row["approval_status"] != "PENDING" or row["execution_status"] != "PENDING_APPROVAL":
                return JSONResponse(status_code=409, content={"detail": "Intervention is not awaiting approval"})
            conn.execute("""
                INSERT INTO intervention_approvals(intervention_id,merchant_id,decision,actor_id,reason)
                VALUES (%s,%s,%s,%s,%s)
            """, (intervention_id, request.merchant_id, request.decision, request.actor_id, request.reason))
            execution_status = "READY" if request.decision == "APPROVED" else "BLOCKED"
            conn.execute("UPDATE interventions SET approval_status=%s,execution_status=%s WHERE id=%s",
                         (request.decision, execution_status, intervention_id))
            conn.execute("UPDATE cases SET state=%s,updated_at=now() WHERE id=%s",
                         ("APPROVED" if request.decision == "APPROVED" else "ACTION_RECOMMENDED", case_id))
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details,policy_version)
                SELECT 'intervention',%s,'USER',%s,'approval_'||lower(%s),%s,policy_version
                FROM interventions WHERE id=%s
            """, (intervention_id, request.actor_id, request.decision,
                  Jsonb({"case_id": str(case_id), "reason": request.reason}), intervention_id))
            return {"intervention_id": str(intervention_id), "decision": request.decision,
                    "execution_status": execution_status, "replayed": False}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Approval decision failed")
        raise HTTPException(503, "Approval storage is temporarily unavailable") from None


@router.post("/{case_id}/interventions/{intervention_id}/execute")
def simulate_action(case_id: UUID, intervention_id: UUID, request: ExecutionRequest) -> Any:
    try:
        with connect_database() as conn:
            row = conn.execute("""
                SELECT i.*,c.id AS case_id FROM interventions i JOIN cases c ON c.transaction_id=i.transaction_id
                WHERE i.id=%s AND c.id=%s AND i.merchant_id=%s AND c.merchant_id=%s
                FOR UPDATE OF i,c
            """, (intervention_id, case_id, request.merchant_id, request.merchant_id)).fetchone()
            if not row:
                raise HTTPException(404, "Intervention not found")
            if row["execution_status"] == "EXECUTED":
                return {"intervention_id": str(intervention_id), "execution_status": "EXECUTED",
                        "simulation_result": row["simulation_result"], "replayed": True}
            if row["execution_status"] != "READY":
                return JSONResponse(status_code=409, content={"detail": "Intervention is not approved and ready to execute"})
            approved = row["approval_status"] == "APPROVED"
            decision, refs, _, _ = _evaluate_case_policy_conn(
                conn, case_id, request.merchant_id, PolicyAction(row["action_type"]), approved
            )
            if not decision.allowed:
                conn.execute("UPDATE interventions SET execution_status='BLOCKED' WHERE id=%s", (intervention_id,))
                conn.execute("UPDATE cases SET state='ESCALATED',updated_at=now() WHERE id=%s", (case_id,))
                conn.execute("""
                    INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details,policy_version)
                    VALUES ('intervention',%s,'SYSTEM','simulated-action-engine','execution_blocked',%s,%s)
                """, (intervention_id, Jsonb({"reason_code": decision.reason_code,
                                               "evidence_references": refs}), decision.policy_version))
                return JSONResponse(status_code=409, content={"detail": decision.model_dump(mode="json")})
            result = {"mode": "SIMULATED", "external_side_effect": False,
                      "message": "Policy rechecked; no payment processor or customer system was called."}
            outcome = conn.execute("""
                INSERT INTO outcomes(transaction_id,intervention_id,observation_window,window_started_at,
                                     window_ends_at,outcome_status,last_observed_at)
                SELECT i.transaction_id,i.id,make_interval(days=>mp.outcome_observation_days),now(),
                       now()+make_interval(days=>mp.outcome_observation_days),'OBSERVING',now()
                FROM interventions i JOIN transactions t ON t.id=i.transaction_id
                JOIN merchant_policies mp ON mp.merchant_id=i.merchant_id
                WHERE i.id=%s AND i.merchant_id=%s
                ON CONFLICT (intervention_id) WHERE intervention_id IS NOT NULL DO NOTHING
                RETURNING id,window_started_at,window_ends_at,outcome_status
            """, (intervention_id, request.merchant_id)).fetchone()
            if not outcome:
                outcome = conn.execute("""
                    SELECT id,window_started_at,window_ends_at,outcome_status FROM outcomes
                    WHERE intervention_id=%s
                """, (intervention_id,)).fetchone()
            result["outcome_id"] = str(outcome["id"])
            result["observation_window_ends_at"] = outcome["window_ends_at"].isoformat()
            result["outcome_status"] = outcome["outcome_status"]
            conn.execute("""
                UPDATE interventions SET execution_status='EXECUTED',executed_at=now(),simulation_result=%s
                WHERE id=%s
            """, (Jsonb(result), intervention_id))
            final_case_state = "ESCALATED" if row["action_type"] == PolicyAction.flag_human_review.value else "EXECUTED"
            conn.execute("UPDATE cases SET state=%s,updated_at=now() WHERE id=%s",
                         (final_case_state, case_id))
            conn.execute("""
                INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details,policy_version)
                VALUES ('intervention',%s,'SYSTEM','simulated-action-engine','action_simulated',%s,%s)
            """, (intervention_id, Jsonb({"action": row["action_type"], "result": result,
                                           "evidence_references": refs}), decision.policy_version))
            return {"intervention_id": str(intervention_id), "execution_status": "EXECUTED",
                    "simulation_result": result, "replayed": False}
    except HTTPException:
        raise
    except (psycopg.Error, ValueError):
        logger.exception("Simulated action execution failed")
        raise HTTPException(503, "Action execution is temporarily unavailable") from None


@router.get("/{case_id}/outcomes")
def list_case_outcomes(case_id: UUID, merchant_id: UUID) -> dict[str, Any]:
    try:
        with connect_database() as conn:
            case = conn.execute("SELECT transaction_id FROM cases WHERE id=%s AND merchant_id=%s",
                                (case_id, merchant_id)).fetchone()
            if not case:
                raise HTTPException(404, "Case not found")
            rows = conn.execute("""
                SELECT o.id,o.transaction_id,o.intervention_id,o.dispute_occurred,o.dispute_date,
                       o.customer_response,o.complaint_after_intervention,o.resolution_status,
                       o.observation_window,o.window_started_at,o.window_ends_at,o.outcome_status,
                       o.last_observed_at,o.observation_evidence,o.created_at
                FROM outcomes o WHERE o.transaction_id=%s ORDER BY o.created_at DESC
            """, (case["transaction_id"],)).fetchall()
            return {"case_id": str(case_id), "items": rows}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Outcome list failed")
        raise HTTPException(503, "Outcome data is temporarily unavailable") from None


@router.post("/{case_id}/outcomes/refresh")
def refresh_case_outcomes(case_id: UUID, merchant_id: UUID) -> dict[str, Any]:
    try:
        with connect_database() as conn:
            case = conn.execute("SELECT transaction_id FROM cases WHERE id=%s AND merchant_id=%s",
                                (case_id, merchant_id)).fetchone()
            if not case:
                raise HTTPException(404, "Case not found")
            outcomes = conn.execute("""
                SELECT * FROM outcomes WHERE transaction_id=%s AND outcome_status='OBSERVING'
                ORDER BY created_at FOR UPDATE
            """, (case["transaction_id"],)).fetchall()
            refreshed = []
            for outcome in outcomes:
                disputes = conn.execute("""
                    SELECT id,created_at FROM disputes WHERE transaction_id=%s AND merchant_id=%s
                      AND created_at>= %s AND created_at<=least(%s,now()) ORDER BY created_at
                """, (case["transaction_id"], merchant_id, outcome["window_started_at"],
                      outcome["window_ends_at"])).fetchall()
                events = conn.execute("""
                    SELECT id,event_type,metadata,created_at FROM post_payment_events
                    WHERE transaction_id=%s AND merchant_id=%s AND created_at>=%s
                      AND created_at<=least(%s,now()) ORDER BY created_at
                """, (case["transaction_id"], merchant_id, outcome["window_started_at"],
                      outcome["window_ends_at"])).fetchall()
                complaints = [e for e in events if e["event_type"].lower() in
                              {"complaint", "customer_complaint", "support_complaint"}]
                replies = [e for e in events if e["event_type"].lower() in
                           {"customer_reply", "customer_responded"}]
                resolutions = [e for e in events if e["event_type"].lower() in
                               {"issue_resolved", "support_case_resolved", "refund_processed", "delivery_completed"}]
                complete = outcome["window_ends_at"] <= conn.execute("SELECT now() AS n").fetchone()["n"]
                response = None
                if replies:
                    meta = replies[-1]["metadata"] or {}
                    response = str(meta.get("response", meta.get("message", meta.get("body", "")))) or None
                resolution = resolutions[-1]["event_type"].upper() if resolutions else (
                    "NO_RESOLUTION_RECORDED" if complete else outcome["resolution_status"])
                status = "COMPLETE" if complete else "OBSERVING"
                evidence = {"dispute_ids": [str(d["id"]) for d in disputes],
                            "complaint_event_ids": [str(e["id"]) for e in complaints],
                            "customer_reply_event_ids": [str(e["id"]) for e in replies],
                            "resolution_event_ids": [str(e["id"]) for e in resolutions]}
                conn.execute("""
                    UPDATE outcomes SET dispute_occurred=CASE WHEN %s THEN %s ELSE NULL END,
                        dispute_date=%s,complaint_after_intervention=CASE WHEN %s THEN %s ELSE NULL END,
                        customer_response=coalesce(%s,customer_response),resolution_status=%s,
                        outcome_status=%s,last_observed_at=now(),observation_evidence=%s WHERE id=%s
                """, (bool(disputes) or complete, bool(disputes) if disputes else False,
                      disputes[0]["created_at"] if disputes else None,
                      bool(complaints) or complete, bool(complaints) if complaints else False,
                      response, resolution, status, Jsonb(evidence), outcome["id"]))
                conn.execute("""
                    INSERT INTO audit_logs(entity_type,entity_id,actor_type,actor_id,action,details)
                    VALUES ('outcome',%s,'SYSTEM','outcome-observer','outcome_refreshed',%s)
                """, (outcome["id"], Jsonb({"status": status, "evidence": evidence})))
                refreshed.append(str(outcome["id"]))
            return {"case_id": str(case_id), "refreshed": refreshed}
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("Outcome refresh failed")
        raise HTTPException(503, "Outcome refresh is temporarily unavailable") from None
