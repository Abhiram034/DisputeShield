# Deterministic policy engine

`POST /api/cases/{case_id}/policy-check` accepts a merchant ID and requested action. `POST /v1/policy/check` accepts the same values plus the case ID in its JSON body. Both read the latest merchant policy, risk assessment, customer communication preference, dispute state, case state, investigation status, and evidence freshness from PostgreSQL. Client-supplied policy values, risk labels, opt-out flags, or dispute flags do not affect the decision.

## Decisions

- `RECOMMENDATION_ELIGIBLE`: the requested recommendation matches a recorded risk signal, investigation evidence is complete and fresh, and merchant policy allows it.
- `REQUIRES_HUMAN`: facts are incomplete, the case is escalated, risk has no known safe path, the amount is high value, a suspicious signal combination exists, or policy limits require approval.
- `BLOCKED`: a dispute exists, the case is closed, evidence is stale, the action is unsupported by recorded signals, or communication is disabled/opted out.

The engine treats suspicious combinations as review signals, not fraud findings. It never executes actions. Every evaluation writes the action, decision reason code, policy version, and evidence references to the case audit log. Migration 006 adds `evidence_max_age_minutes` to merchant policy; the default is 60 minutes and the allowed range is 1 minute to 7 days.

Action-specific signal requirements are:

- Receipt: `receipt_failed`.
- Delivery update: `delivery_failed` or `delivery_delayed`.
- Refund status or recommendation: `refund_pending`.
- Support case: a recent support contact, receipt issue, delivery issue, or pending refund.
- Human review: always available.

High-value thresholds, communication permission, customer opt-out, automation switches, refund limits, evidence age, and suspicious-pattern review are all evaluated deterministically. The action execution endpoint remains a separate simulated path for the next phase.
