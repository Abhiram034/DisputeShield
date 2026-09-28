# Case management and read-only investigation

## Case API

- `POST /api/cases/detect`: validate a transaction and merchant, persist a deterministic assessment, open or refresh an idempotent case when thresholds require attention, and append audit records. Low-risk cases without a qualifying opportunity retain their risk assessment but do not open a case.
- `GET /api/cases?merchant_id=...`: tenant-scoped case listing with bounded pagination and optional state/risk filters.
- `GET /api/cases/{case_id}?merchant_id=...`: case, transaction, customer profile, and latest assessment.
- `GET /api/cases/{case_id}/investigation-tools?merchant_id=...`: executes the fixed, SELECT-only evidence tool set.
- `POST /api/cases/{case_id}/policy-check`: evaluates a requested recommendation against trusted case/risk/evidence data and the latest merchant policy. Returns a reason code and evidence references and writes an audit record; it does not execute the action.
- `POST /v1/policy/check`: compatible body form requiring `case_id`, `merchant_id`, and `action`; policy values and safety facts are read from PostgreSQL.
- `POST /api/cases/{case_id}/actions`: creates an idempotent recommendation after rechecking policy in the same database transaction.
- `POST /api/cases/{case_id}/interventions/{intervention_id}/approval`: records one approval or rejection for a pending intervention.
- `POST /api/cases/{case_id}/interventions/{intervention_id}/execute`: rechecks policy and records a simulated outcome only; no external system is called. Repeating a completed call returns the original result.

Versioned aliases also exist under `/v1/cases`. Risk scoring stays at `/v1/risk/assess`.

Case creation is unique by transaction. Repeated detections refresh the assessment pointer instead of creating a second case. Initial case state is DETECTED; insufficient core data with positive signals opens as ESCALATED.

## Read-only tools

`InvestigationTools` exposes only named queries: transaction; customer profile and transactions; dispute, refund, support, order, delivery, receipt and subscription history; similar-transaction candidates; and current merchant policy. All reads include merchant scope and parameterized SQL. Similar amounts are candidates only, never duplicate proof.

Each result is `KNOWN` (nonempty data), `EMPTY` (successful query with no rows), or `FAILED` (source error), with a stable safe error code. A failed query rolls back to its savepoint so remaining read tools may still succeed. No tool can write rows or execute customer actions.

## Access limitation

`merchant_id` filters provide data scoping, not user authentication. The current service is a local synthetic-data MVP and must sit behind a trusted identity/auth gateway before shared or production use.
