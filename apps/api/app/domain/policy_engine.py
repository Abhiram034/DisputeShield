"""Pure deterministic policy decisions. This module never executes an action."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PolicyAction(StrEnum):
    send_receipt = "send_receipt"
    create_support_case = "create_support_case"
    send_delivery_update = "send_delivery_update"
    send_refund_status = "send_refund_status"
    recommend_refund = "recommend_refund"
    flag_human_review = "flag_human_review"


class PolicyConfig(BaseModel):
    version: int
    auto_send_receipt: bool
    auto_create_support_case: bool
    auto_refund_limit: int = Field(ge=0)
    require_human_for_high_value: bool
    high_value_threshold: int = Field(ge=0)
    require_human_for_fraud: bool
    communication_enabled: bool
    evidence_max_age_minutes: int = Field(ge=1, le=10080)


class PolicyFacts(BaseModel):
    case_state: str
    risk_level: str
    intervention_opportunity: str
    risk_signals: list[str]
    amount_minor: int = Field(ge=0)
    communication_opt_out: bool
    dispute_exists: bool
    investigation_status: str | None
    evidence_count: int = Field(ge=0)
    evidence_failed: bool
    evidence_age_minutes: float | None = Field(default=None, ge=0)


class PolicyDecision(BaseModel):
    action: PolicyAction
    decision: str
    allowed: bool
    requires_human: bool
    reason_code: str
    reason: str
    policy_version: int
    checked_at: datetime


ACTION_SIGNALS = {
    PolicyAction.send_receipt: {"receipt_failed"},
    PolicyAction.create_support_case: {
        "support_contact_recent", "receipt_failed", "delivery_failed", "delivery_delayed", "refund_pending",
    },
    PolicyAction.send_delivery_update: {"delivery_failed", "delivery_delayed"},
    PolicyAction.send_refund_status: {"refund_pending"},
    PolicyAction.recommend_refund: {"refund_pending"},
}
ACTION_LABELS = {
    "send_receipt": "payment receipt", "create_support_case": "support case",
    "send_delivery_update": "delivery update", "send_refund_status": "refund status",
    "recommend_refund": "refund recommendation", "flag_human_review": "human review",
}


def decide_action(action: PolicyAction, policy: PolicyConfig | None, facts: PolicyFacts,
                  human_approval_granted: bool = False) -> PolicyDecision:
    """Return a reviewable result; unknown/stale facts never authorize an action."""
    version = policy.version if policy else 0

    def result(decision: str, allowed: bool, requires_human: bool, code: str, reason: str) -> PolicyDecision:
        return PolicyDecision(action=action, decision=decision, allowed=allowed,
                              requires_human=requires_human, reason_code=code, reason=reason,
                              policy_version=version, checked_at=datetime.now().astimezone())

    if action == PolicyAction.flag_human_review:
        return result("RECOMMENDATION_ELIGIBLE", True, False, "HUMAN_REVIEW", "Human review is always available")
    if facts.dispute_exists:
        return result("BLOCKED", False, False, "DISPUTE_EXISTS", "A dispute is already open; prevention actions are suspended")
    if facts.case_state in {"RESOLVED", "EXPIRED", "FAILED", "EXECUTED"}:
        return result("BLOCKED", False, False, "CASE_CLOSED", "The case is not eligible for a new recommendation")
    if facts.case_state == "ESCALATED":
        return result("REQUIRES_HUMAN", False, True, "CASE_ESCALATED", "This case is already routed for human review")
    if policy is None:
        return result("REQUIRES_HUMAN", False, True, "POLICY_UNAVAILABLE", "Merchant policy is unavailable")
    if facts.investigation_status != "COMPLETED" or facts.evidence_count == 0 or facts.evidence_failed:
        return result("REQUIRES_HUMAN", False, True, "EVIDENCE_INCOMPLETE", "Investigation evidence is unavailable or incomplete")
    if facts.evidence_age_minutes is None or facts.evidence_age_minutes > policy.evidence_max_age_minutes:
        return result("BLOCKED", False, True, "EVIDENCE_STALE", "Refresh investigation evidence before evaluating this action")
    if facts.risk_level == "UNKNOWN" or facts.intervention_opportunity == "UNKNOWN":
        return result("REQUIRES_HUMAN", False, True, "RISK_UNKNOWN", "Risk or prevention opportunity is unknown")
    if facts.intervention_opportunity != "HIGH":
        return result("REQUIRES_HUMAN", False, True, "NO_KNOWN_SAFE_PATH", "No known policy-eligible prevention path is available")

    signals = set(facts.risk_signals)
    required_signals = ACTION_SIGNALS[action]
    if not signals.intersection(required_signals):
        return result("BLOCKED", False, False, "ACTION_NOT_SUPPORTED", "Current evidence does not support this action")
    if action in {PolicyAction.send_receipt, PolicyAction.send_delivery_update, PolicyAction.send_refund_status}:
        if not policy.communication_enabled or facts.communication_opt_out:
            return result("BLOCKED", False, False, "COMMUNICATION_BLOCKED", "Merchant communication policy or customer opt-out blocks this action")

    signal_names = signals
    fraud_pattern = (
        {"amount_vs_customer_average", "unusual_device"} <= signal_names
        or ("multiple_failed_attempts" in signal_names
            and bool({"unusual_device", "unusual_country"} & signal_names))
    )
    if policy.require_human_for_fraud and fraud_pattern and not human_approval_granted:
        return result("REQUIRES_HUMAN", False, True, "FRAUD_PATTERN_REVIEW", "A suspicious signal combination requires human review; this is not a fraud finding")
    if policy.require_human_for_high_value and facts.amount_minor >= policy.high_value_threshold and not human_approval_granted:
        return result("REQUIRES_HUMAN", False, True, "HIGH_VALUE_REVIEW", "High-value transactions require human approval")
    if action == PolicyAction.send_receipt and not policy.auto_send_receipt and not human_approval_granted:
        return result("REQUIRES_HUMAN", False, True, "RECEIPT_APPROVAL_REQUIRED", "Receipt automation is disabled")
    if action == PolicyAction.create_support_case and not policy.auto_create_support_case and not human_approval_granted:
        return result("REQUIRES_HUMAN", False, True, "SUPPORT_APPROVAL_REQUIRED", "Support case automation is disabled")
    if action == PolicyAction.recommend_refund and facts.amount_minor > policy.auto_refund_limit and not human_approval_granted:
        return result("REQUIRES_HUMAN", False, True, "REFUND_LIMIT_REVIEW", "Refund amount exceeds the merchant's automatic refund limit")
    return result("RECOMMENDATION_ELIGIBLE", True, False, "POLICY_PASSED",
                  f"Evidence and merchant policy permit recommending a {ACTION_LABELS[action]}")
