from apps.api.app.cases import _eligible_for_case
from apps.api.app.domain.risk_engine import (
    InterventionOpportunity,
    RiskAssessment,
    RiskLevel,
    SignalStrength,
)


def test_high_risk_without_safe_intervention_escalates_for_human_review():
    assessment = RiskAssessment(
        transaction_id="tx-policy",
        risk_score=75,
        risk_level=RiskLevel.CRITICAL,
        signal_strength=SignalStrength.HIGH,
        signals=[],
        unknowns=[],
        intervention_opportunity=InterventionOpportunity.LOW,
        intervention_reason="No safe action is available",
    )
    opened, reason, state = _eligible_for_case(assessment)
    assert opened is True
    assert state == "ESCALATED"
    assert "human review" in reason
