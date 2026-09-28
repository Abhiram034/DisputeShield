# Idempotent recommendation and simulated action flow

The case-scoped action endpoints are local demo workflows. They persist recommendations, approvals, decisions, and a simulated outcome; they do not call a processor, send a message, create a support ticket, or issue a refund.

## Workflow

1. `POST /api/cases/{case_id}/actions` accepts `merchant_id`, `action`, and an idempotency key. It locks the key and case, reads current policy/risk/customer/dispute/evidence facts, and evaluates policy in the same transaction as recommendation creation.
2. A policy-eligible action is stored as `READY` / `NOT_REQUIRED`. Recognized policy exceptions such as high value, suspicious signal combinations, or a disabled automation switch are stored as `PENDING_APPROVAL`. Blocked, unsupported, stale, or incomplete requests create no intervention.
3. `POST /api/cases/{case_id}/interventions/{intervention_id}/approval` records a single approval or rejection. Repeating the same decision is idempotent; a conflicting second decision is rejected.
4. `POST /api/cases/{case_id}/interventions/{intervention_id}/execute` requires `READY`, rechecks current policy/evidence/dispute/opt-out state, and stores `EXECUTED` with a result explicitly marking `external_side_effect=false`. Repeating an executed call returns the saved result.

Idempotency keys are scoped to merchant and request fingerprint. Reusing a key with a different case or action returns HTTP 409. Case state and audit records are updated transactionally. Migrations 007–008 add the scoped key, fingerprint, approval table, simulation result, and remove the earlier global key constraint.

Actor IDs and merchant IDs are caller-supplied demo identifiers; they are not authentication. Do not expose this API beyond a trusted local environment until an identity and authorization layer is installed.
