# Security

- Synthetic data only; do not ingest private processor datasets into the demo.
- Never collect or store PAN, CVV, or authentication secrets. Use opaque transaction IDs and hashed device identifiers.
- Treat free text and JSON metadata as untrusted and potentially prompt-injected. The model receives bounded evidence summaries, not authority to call write tools.
- Enforce merchant scoping at every query. Use least-privilege service credentials and TLS outside local development.
- Re-check current policy, dispute/refund state, opt-out, approval expiry and evidence freshness immediately before action.
- Require idempotency for action requests and append-only audit events for decision, approval, execution and outcome.
- Fail closed: provider/database/tool errors, stale or contradictory evidence, suspicious clusters, fraud signals and policy ambiguity require human review.
- Local `.env` files are ignored. Rotate secrets and configure provider credentials outside source control.
The MVP `merchant_id` query/header scope is not an authentication mechanism. Keep the API local or behind a trusted gateway; bind scope from authenticated identity before multi-tenant exposure.
