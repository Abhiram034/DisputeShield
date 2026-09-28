from apps.api.app.domain.risk_engine import (
    DataStatus,
    InterventionOpportunity,
    Observation,
    RiskAssessmentInput,
    RiskLevel,
    assess_risk,
)


def known(value, source="synthetic fixture"):
    return Observation(status=DataStatus.KNOWN, value=value, source=source)


def baseline(**overrides):
    fields = {
        "transaction_id": "tx-risk-test",
        "transaction_amount_minor": known(5000),
        "payment_succeeded": known(True),
        "customer_average_amount_minor": known(5000),
        "previous_disputes": known(0),
        "support_contact_recent": known(False),
        "receipt_failed": known(False),
        "delivery_failed": known(False),
        "delivery_delayed": known(False),
        "refund_pending": known(False),
        "subscription_changed": known(False),
        "descriptor_changed": known(False),
        "duplicate_candidate": known(False),
        "unusual_device": known(False),
        "unusual_country": known(False),
        "multiple_failed_attempts": known(False),
        "dispute_exists": known(False),
        "issue_resolved": known(False),
        "customer_opted_out": known(False),
        "safe_actions_available": known(["send_receipt", "create_support_case"]),
    }
    fields.update(overrides)
    return RiskAssessmentInput(**fields)


def test_known_normal_transaction_scores_low_with_no_action_opportunity():
    result = assess_risk(baseline())
    assert result.risk_score == 0
    assert result.risk_level == RiskLevel.LOW
    assert result.intervention_opportunity == InterventionOpportunity.LOW


def test_delivery_failure_and_history_raise_risk_and_separately_allow_action():
    result = assess_risk(baseline(delivery_failed=known(True), previous_disputes=known(1)))
    assert result.risk_score == 27
    assert result.risk_level == RiskLevel.MEDIUM
    assert result.intervention_opportunity == InterventionOpportunity.HIGH
    assert {signal.name for signal in result.signals} == {"delivery_failed", "previous_disputes"}


def test_missing_history_is_unknown_not_zero():
    result = assess_risk(baseline(previous_disputes=Observation(status=DataStatus.FAILED, unavailable_reason="history service timeout")))
    assert "previous_disputes: failed (history service timeout)" in result.unknowns
    assert "previous_disputes" not in {signal.name for signal in result.signals}


def test_required_payment_data_unavailable_produces_unknown_risk_level():
    result = assess_risk(RiskAssessmentInput(transaction_id="tx-missing"))
    assert result.risk_level == RiskLevel.UNKNOWN
    assert result.signal_strength == "UNKNOWN"
    assert result.risk_score == 0


def test_unusual_device_alone_is_not_classified_as_fraud():
    result = assess_risk(baseline(unusual_device=known(True)))
    assert result.risk_score == 7
    assert result.risk_level == RiskLevel.LOW
    assert "unusual behavior alone is not fraud" in result.signals[0].explanation


def test_high_amount_and_unusual_device_route_to_human_review_not_fraud():
    result = assess_risk(baseline(
        transaction_amount_minor=known(20000), customer_average_amount_minor=known(5000),
        unusual_device=known(True), safe_actions_available=known(["flag_for_human_review"]),
    ))
    assert result.intervention_opportunity == InterventionOpportunity.MEDIUM
    assert "not a fraud finding" in result.intervention_reason


def test_invalid_core_amount_facts_keep_risk_band_unknown():
    result = assess_risk(baseline(customer_average_amount_minor=known(0)))
    assert result.risk_level == RiskLevel.UNKNOWN


def test_duplicate_signal_is_candidate_not_proof():
    result = assess_risk(baseline(duplicate_candidate=known(True)))
    assert "not proof" in next(s.explanation for s in result.signals if s.name == "duplicate_candidate")


def test_opt_out_removes_customer_communication_opportunity():
    result = assess_risk(baseline(receipt_failed=known(True), customer_opted_out=known(True), safe_actions_available=known(["send_receipt"])))
    assert result.risk_level == RiskLevel.LOW
    assert result.intervention_opportunity == InterventionOpportunity.LOW


def test_unknown_opt_out_does_not_become_permission_to_communicate():
    result = assess_risk(baseline(receipt_failed=known(True), customer_opted_out=Observation(), safe_actions_available=known(["send_receipt"])))
    assert result.intervention_opportunity == InterventionOpportunity.UNKNOWN


def test_resolved_issue_blocks_intervention_opportunity():
    result = assess_risk(baseline(delivery_failed=known(True), issue_resolved=known(True)))
    assert result.intervention_opportunity == InterventionOpportunity.LOW
