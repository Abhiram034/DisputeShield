# Architecture and implementation plan

## Product flow

PAYMENT -> POST-PAYMENT SIGNALS -> DETECT -> INVESTIGATE -> EXPLAIN -> RECOMMEND -> POLICY CHECK -> APPROVE/EXECUTE -> MONITOR -> MEASURE OUTCOME.

The product works before a dispute is filed. Synthetic demo data is clearly labeled; non-disputed transactions are not automatically called prevented disputes.

## Components and dependencies

- **Web:** Next.js and TypeScript dashboard in `apps/web`; merchant-scoped API reads are proxied by Next.js to avoid browser CORS configuration. Merchant setup, case search, incident operations, and outcome observation are backend driven.
- **API:** FastAPI/Pydantic service in `apps/api`; deterministic risk and action-policy checks; action execution remains simulated.
- **Database:** PostgreSQL 16 in Docker Compose; versioned SQL migrations run before API startup. Named volume preserves database data.
- **Data tools:** Python standard-library generator; deterministic seed defaults to `20260925`.
- **Local dependencies:** Docker Engine + Compose plugin; Python 3.11+; pinned API dependencies in `apps/api/requirements.txt`; tests in `tests/requirements.txt` and Playwright Chromium for browser tests.

## Phase plan

1. **Repository + Docker + PostgreSQL - complete.** Compose services, environment example, health check and persistent volume verified.
2. **Database schema + migrations - complete.** Checksummed baseline, advisory lock, safe adoption of existing schema, fresh install and idempotent rerun verified.
3. **Synthetic data - complete.** Fixed-seed generator produced 10 merchants, 5,000 customers, 50,000 transactions, 50,000 orders, 100,000 events, 601 disputes, and 16 labeled scenarios.
4. **Deterministic risk engine - complete (baseline).** Weighted, interpretable signals; UNKNOWN handling; separate risk/opportunity outputs; API endpoint and tests.
5. **Case management - complete.** Persist deterministic assessments, deduplicate cases by transaction, list/detail with merchant-scoped filters, and audit detections.
6. **Read-only investigation tools - complete.** Fixed SELECT-only tool registry with KNOWN/EMPTY/FAILED results and savepoint-isolated failures.
7. **Structured investigation agent - complete (local implementation).** Optional OpenAI-compatible Chat Completions provider; bounded to 15 tool calls, 2 retries, and 30 seconds. It can call only the fixed merchant-scoped read-only tools. Validated claims must cite collected evidence references. Provider/model failure records AI unavailable and escalates the case for human review; deterministic risk remains available.
8. **Evidence structure and evidence UI - complete.** Migration 005 stores source, field, value, status, and timestamps per investigation with tenant scope. The API serves persisted evidence; earlier fixture evidence was illustrative and is not presented as connected data.
9. **Deterministic policy engine - complete.** The case policy check reads trusted risk, customer, dispute, investigation freshness, and latest merchant policy data from PostgreSQL. It writes an audit record and only returns a recommendation decision; it never executes the action.
10. **Idempotent simulated action engine and approval workflow - complete.** Recommendation creation is policy-gated and idempotent, approvals are persisted once, and the final gate is rechecked before a no-side-effect simulated execution.
11. Outcome tracking and observation windows.
12. **Merchant incident detection and conflict handling - complete.** Detect dispute-rate spikes against a trailing baseline using merchant policy thresholds; deduplicate overlapping open incidents and record status changes in audit logs.
13. **Next.js/TypeScript application and backend-driven dashboard - complete.** Merchant, case, incident, and observation views call the persisted API; there are no illustrative case rows or outcome claims in the live dashboard.
14. Simulation engine.
15. Evaluation and deterministic experiment assignment.
16. Automated tests and security hardening.
17. Demo polish.

Phases 14 onward are sequenced next and are not represented as implemented functionality. Configure `LLM_PROVIDER=openai_compatible`, `LLM_API_KEY`, and `LLM_MODEL` to enable optional investigation; the default is `none`.

## Trust boundaries

Transaction/event metadata and customer-entered text are untrusted. The model may only inspect bounded read-only evidence and return validated structured hypotheses. Deterministic backend code owns risk thresholds, permissions, merchant policy, money calculations, action idempotency, state transitions, approvals, and audit records. Fraud-like and high-value cases route to review; customer opt-out blocks communication. Tool/model failures and missing data remain explicit unknowns and fail closed.

## Current boundaries

The current web app is a static prototype; metrics and case rows are illustrative and not backend-derived. Action and approval records persist, but action execution is simulated and does not contact payment processors or customer systems. These paths must not be presented as live integrations or production execution.
