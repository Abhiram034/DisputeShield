from apps.api.app.domain.policy_engine import PolicyAction, PolicyConfig, PolicyFacts, decide_action


def policy(**updates):
    values = {
        "version": 3, "auto_send_receipt": True, "auto_create_support_case": True,
        "auto_refund_limit": 10000, "require_human_for_high_value": True,
        "high_value_threshold": 50000, "require_human_for_fraud": True,
        "communication_enabled": True, "evidence_max_age_minutes": 60,
    }
    values.update(updates)
    return PolicyConfig(**values)


def facts(**updates):
    values = {
        "case_state": "DETECTED", "risk_level": "MEDIUM", "intervention_opportunity": "HIGH",
        "risk_signals": ["delivery_failed"], "amount_minor": 2000,
        "communication_opt_out": False, "dispute_exists": False,
        "investigation_status": "COMPLETED", "evidence_count": 2,
        "evidence_failed": False, "evidence_age_minutes": 1,
    }
    values.update(updates)
    return PolicyFacts(**values)


def test_known_delivery_evidence_and_policy_allow_delivery_recommendation():
    result = decide_action(PolicyAction.send_delivery_update, policy(), facts())
    assert result.allowed and result.decision == "RECOMMENDATION_ELIGIBLE"


def test_opt_out_dispute_and_stale_evidence_fail_closed():
    assert decide_action(PolicyAction.send_delivery_update, policy(), facts(communication_opt_out=True)).reason_code == "COMMUNICATION_BLOCKED"
    assert decide_action(PolicyAction.send_delivery_update, policy(), facts(dispute_exists=True)).reason_code == "DISPUTE_EXISTS"
    assert decide_action(PolicyAction.send_delivery_update, policy(), facts(evidence_age_minutes=90)).reason_code == "EVIDENCE_STALE"


def test_fraud_like_combination_and_large_refund_require_human_review():
    fraud = facts(risk_signals=["delivery_failed", "amount_vs_customer_average", "unusual_device"])
    assert decide_action(PolicyAction.create_support_case, policy(), fraud).reason_code == "FRAUD_PATTERN_REVIEW"
    refund = facts(risk_signals=["refund_pending"], amount_minor=12000)
    assert decide_action(PolicyAction.recommend_refund, policy(), refund).reason_code == "REFUND_LIMIT_REVIEW"


def test_unavailable_investigation_cannot_authorize_recommendation():
    result = decide_action(PolicyAction.send_delivery_update, policy(), facts(investigation_status="UNAVAILABLE"))
    assert result.allowed is False and result.requires_human is True
    assert result.reason_code == "EVIDENCE_INCOMPLETE"
