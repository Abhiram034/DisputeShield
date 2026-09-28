"""Interpretable deterministic post-payment risk scoring.

This module scores only supplied observations. Missing/failed retrievals are never
coerced to false or zero. It estimates dispute risk separately from intervention
opportunity and does not label unusual behavior as fraud.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")


class DataStatus(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class Observation(BaseModel, Generic[T]):
    status: DataStatus = DataStatus.UNKNOWN
    value: T | None = None
    source: str | None = None
    observed_at: str | None = None
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_value(self) -> "Observation[T]":
        if self.status == DataStatus.KNOWN and self.value is None:
            raise ValueError("KNOWN observations require a value (false, zero and empty lists are valid)")
        if self.status != DataStatus.KNOWN and self.value is not None:
            raise ValueError("UNKNOWN/FAILED observations cannot carry a value")
        if self.status == DataStatus.FAILED and not self.unavailable_reason:
            raise ValueError("FAILED observations require unavailable_reason")
        return self


class RiskLevel(StrEnum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class InterventionOpportunity(StrEnum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SignalStrength(StrEnum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskAssessmentInput(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=128)
    transaction_amount_minor: Observation[int] = Field(default_factory=Observation)
    payment_succeeded: Observation[bool] = Field(default_factory=Observation)
    customer_average_amount_minor: Observation[int] = Field(default_factory=Observation)
    previous_disputes: Observation[int] = Field(default_factory=Observation)
    support_contact_recent: Observation[bool] = Field(default_factory=Observation)
    receipt_failed: Observation[bool] = Field(default_factory=Observation)
    delivery_failed: Observation[bool] = Field(default_factory=Observation)
    delivery_delayed: Observation[bool] = Field(default_factory=Observation)
    refund_pending: Observation[bool] = Field(default_factory=Observation)
    subscription_changed: Observation[bool] = Field(default_factory=Observation)
    descriptor_changed: Observation[bool] = Field(default_factory=Observation)
    duplicate_candidate: Observation[bool] = Field(default_factory=Observation)
    unusual_device: Observation[bool] = Field(default_factory=Observation)
    unusual_country: Observation[bool] = Field(default_factory=Observation)
    multiple_failed_attempts: Observation[bool] = Field(default_factory=Observation)
    dispute_exists: Observation[bool] = Field(default_factory=Observation)
    issue_resolved: Observation[bool] = Field(default_factory=Observation)
    customer_opted_out: Observation[bool] = Field(default_factory=Observation)
    safe_actions_available: Observation[list[str]] = Field(default_factory=Observation)


class RiskSignal(BaseModel):
    name: str
    contribution: int
    strength: SignalStrength
    explanation: str
    source: str | None = None
    observed_at: str | None = None


class RiskAssessment(BaseModel):
    transaction_id: str
    risk_score: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    signal_strength: SignalStrength
    signals: list[RiskSignal]
    unknowns: list[str]
    intervention_opportunity: InterventionOpportunity
    intervention_reason: str
    model_version: str = "deterministic-v1"


SIGNAL_WEIGHTS = {
    "previous_disputes": 13,
    "support_contact_recent": 8,
    "receipt_failed": 12,
    "delivery_failed": 20,
    "delivery_delayed": 12,
    "refund_pending": 16,
    "subscription_changed": 8,
    "descriptor_changed": 7,
    "duplicate_candidate": 8,
    "unusual_device": 7,
    "unusual_country": 5,
    "multiple_failed_attempts": 15,
}
ACTIONABLE_SIGNALS = {
    "support_contact_recent", "receipt_failed", "delivery_failed", "delivery_delayed",
    "refund_pending", "subscription_changed", "descriptor_changed", "duplicate_candidate",
}
COMMUNICATION_ACTIONS = {"send_receipt", "send_payment_confirmation", "send_refund_status", "send_delivery_update"}


def _level(score: int) -> RiskLevel:
    if score >= 70:
        return RiskLevel.CRITICAL
    if score >= 45:
        return RiskLevel.HIGH
    if score >= 25:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _coverage_strength(known: int, total: int) -> SignalStrength:
    if known == 0:
        return SignalStrength.UNKNOWN
    ratio = known / total
    if ratio >= 0.75:
        return SignalStrength.HIGH
    if ratio >= 0.45:
        return SignalStrength.MEDIUM
    return SignalStrength.LOW


def assess_risk(data: RiskAssessmentInput) -> RiskAssessment:
    signals: list[RiskSignal] = []
    unknowns: list[str] = []

    def missing(name: str, observation: Observation) -> None:
        if observation.status != DataStatus.KNOWN:
            detail = observation.unavailable_reason or "value unavailable"
            unknowns.append(f"{name}: {observation.status.value.lower()} ({detail})")

    # Amount anomaly uses ratios only when both values are present and valid.
    amount_obs = data.transaction_amount_minor
    average_obs = data.customer_average_amount_minor
    for key, obs in (("transaction_amount_minor", amount_obs), ("customer_average_amount_minor", average_obs)):
        missing(key, obs)
    ratio: float | None = None
    if amount_obs.status == average_obs.status == DataStatus.KNOWN:
        amount, average = amount_obs.value, average_obs.value
        if amount is not None and average is not None and amount >= 0 and average > 0:
            ratio = amount / average
            contribution = 15 if ratio >= 3 else 10 if ratio >= 2 else 5 if ratio >= 1.5 else 0
            if contribution:
                signals.append(RiskSignal(
                    name="amount_vs_customer_average", contribution=contribution,
                    strength=SignalStrength.HIGH if ratio >= 3 else SignalStrength.MEDIUM,
                    explanation=f"Amount is {ratio:.2f}× customer average ({amount} vs {average} minor currency units)",
                    source=amount_obs.source,
                    observed_at=amount_obs.observed_at,
                ))
        elif average == 0:
            unknowns.append("amount_vs_customer_average: customer average is zero; ratio is undefined")
        else:
            unknowns.append("amount_vs_customer_average: invalid negative amount or average")

    previous = data.previous_disputes
    missing("previous_disputes", previous)
    if previous.status == DataStatus.KNOWN and previous.value is not None:
        if previous.value < 0:
            unknowns.append("previous_disputes: invalid negative count")
        elif previous.value:
            contribution = 13 if previous.value >= 2 else 7
            signals.append(RiskSignal(name="previous_disputes", contribution=contribution,
                strength=SignalStrength.HIGH if previous.value >= 2 else SignalStrength.MEDIUM,
                explanation=f"{previous.value} prior dispute(s) recorded", source=previous.source, observed_at=previous.observed_at))

    for name, weight in SIGNAL_WEIGHTS.items():
        observation = getattr(data, name)
        missing(name, observation)
        if observation.status == DataStatus.KNOWN and observation.value is True:
            strength = SignalStrength.HIGH if weight >= 12 else SignalStrength.MEDIUM if weight >= 8 else SignalStrength.LOW
            explanation = {
                "support_contact_recent": "Customer contacted support recently",
                "receipt_failed": "Payment receipt delivery failed",
                "delivery_failed": "Order delivery failed",
                "delivery_delayed": "Order delivery is delayed",
                "refund_pending": "Requested refund remains pending",
                "subscription_changed": "Subscription changed recently",
                "descriptor_changed": "Merchant descriptor changed recently",
                "duplicate_candidate": "Similar nearby transaction detected; this is not proof of a duplicate",
                "unusual_device": "Device differs from known customer activity; unusual behavior alone is not fraud",
                "unusual_country": "Country differs from known customer activity; unusual behavior alone is not fraud",
                "multiple_failed_attempts": "Multiple payment attempts failed",
            }[name]
            signals.append(RiskSignal(name=name, contribution=weight, strength=strength,
                explanation=explanation, source=observation.source, observed_at=observation.observed_at))

    missing("payment_succeeded", data.payment_succeeded)
    score = min(100, sum(signal.contribution for signal in signals))
    # Keep the partial numeric score visible, but do not assign a risk band when
    # any of the core profile facts are missing or failed to load.
    core_fields_known = all(obs.status == DataStatus.KNOWN for obs in (
        amount_obs, average_obs, previous, data.payment_succeeded
    ))
    core_values_valid = (
        amount_obs.value is not None and amount_obs.value >= 0
        and average_obs.value is not None and average_obs.value > 0
        and previous.value is not None and previous.value >= 0
    )
    required_known = core_fields_known and core_values_valid
    risk_level = _level(score) if required_known else RiskLevel.UNKNOWN
    coverage_total = len(SIGNAL_WEIGHTS) + 3
    coverage_known = sum(
        getattr(data, name).status == DataStatus.KNOWN for name in SIGNAL_WEIGHTS
    ) + sum((amount_obs.status == DataStatus.KNOWN, average_obs.status == DataStatus.KNOWN, previous.status == DataStatus.KNOWN))
    signal_strength = _coverage_strength(coverage_known, coverage_total)

    for name, obs in (
        ("dispute_exists", data.dispute_exists), ("issue_resolved", data.issue_resolved),
        ("customer_opted_out", data.customer_opted_out), ("safe_actions_available", data.safe_actions_available),
    ):
        missing(name, obs)

    actionable = any(
        getattr(data, name).status == DataStatus.KNOWN and getattr(data, name).value is True
        for name in ACTIONABLE_SIGNALS
    )
    high_amount_and_unusual_device = ratio is not None and ratio >= 3 and data.unusual_device.status == DataStatus.KNOWN and data.unusual_device.value is True
    failed_attempts_and_unusual = (
        data.multiple_failed_attempts.status == DataStatus.KNOWN and data.multiple_failed_attempts.value is True
        and ((data.unusual_device.status == DataStatus.KNOWN and data.unusual_device.value is True)
             or (data.unusual_country.status == DataStatus.KNOWN and data.unusual_country.value is True))
    )
    review_only_pattern = high_amount_and_unusual_device or failed_attempts_and_unusual
    actions = data.safe_actions_available.value if data.safe_actions_available.status == DataStatus.KNOWN else None
    state_blocked = (
        (data.dispute_exists.status == DataStatus.KNOWN and data.dispute_exists.value is True)
        or (data.issue_resolved.status == DataStatus.KNOWN and data.issue_resolved.value is True)
    )
    opportunity_unknown = any(obs.status != DataStatus.KNOWN for obs in (
        data.dispute_exists, data.issue_resolved, data.customer_opted_out, data.safe_actions_available
    ))
    state_unknown = any(obs.status != DataStatus.KNOWN for obs in (data.dispute_exists, data.issue_resolved))
    if state_blocked:
        opportunity, opportunity_reason = InterventionOpportunity.LOW, "Issue already resolved or dispute already filed"
    elif state_unknown:
        opportunity, opportunity_reason = InterventionOpportunity.UNKNOWN, "Current dispute and resolution state is unavailable"
    elif review_only_pattern:
        if actions is None:
            opportunity = InterventionOpportunity.UNKNOWN
            opportunity_reason = "Safe actions/policy availability is unknown for human review routing"
        else:
            review_available = "flag_for_human_review" in actions
            opportunity = InterventionOpportunity.MEDIUM if review_available else InterventionOpportunity.LOW
            opportunity_reason = "Unusual signal combination routes to human review; it is not a fraud finding"
    elif actionable:
        if actions is None:
            opportunity, opportunity_reason = InterventionOpportunity.UNKNOWN, "Safe actions/policy availability is unknown"
        elif not actions:
            opportunity, opportunity_reason = InterventionOpportunity.LOW, "No policy-eligible action is available"
        else:
            noncommunication_actions = set(actions) - COMMUNICATION_ACTIONS - {"flag_for_human_review"}
            communication_actions = set(actions) & COMMUNICATION_ACTIONS
            if noncommunication_actions:
                opportunity, opportunity_reason = InterventionOpportunity.HIGH, "A specific post-payment issue and policy-eligible action are both known"
            elif communication_actions and data.customer_opted_out.status != DataStatus.KNOWN:
                opportunity, opportunity_reason = InterventionOpportunity.UNKNOWN, "Customer communication preference is unavailable"
            elif communication_actions and data.customer_opted_out.value is False:
                opportunity, opportunity_reason = InterventionOpportunity.HIGH, "A specific post-payment issue and policy-eligible communication are both known"
            elif "flag_for_human_review" in actions:
                opportunity, opportunity_reason = InterventionOpportunity.MEDIUM, "Only human review is available; no automatic prevention action is eligible"
            else:
                opportunity, opportunity_reason = InterventionOpportunity.LOW, "Available actions are blocked by customer communication opt-out"
    elif opportunity_unknown:
        opportunity, opportunity_reason = InterventionOpportunity.UNKNOWN, "Resolution, opt-out, or policy-eligible action data is unavailable"
    else:
        opportunity, opportunity_reason = InterventionOpportunity.LOW, "No actionable post-payment issue is evidenced"

    return RiskAssessment(
        transaction_id=data.transaction_id,
        risk_score=score,
        risk_level=risk_level,
        signal_strength=signal_strength,
        signals=signals,
        unknowns=unknowns,
        intervention_opportunity=opportunity,
        intervention_reason=opportunity_reason,
    )
