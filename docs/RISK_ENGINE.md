# Deterministic risk engine baseline

Endpoint: `POST /v1/risk/assess`. Code: `apps/api/app/domain/risk_engine.py`.

## Scoring

Every known positive signal contributes a fixed, reviewable amount. Score is capped at 100. Thresholds: 0–24 LOW, 25–44 MEDIUM, 45–69 HIGH, 70–100 CRITICAL. The risk band is UNKNOWN until amount, successful-payment result, customer average, and prior-dispute count are known and valid. A partial numeric score can still be shown, with `unknowns` and `signal_strength` making incomplete coverage explicit. Missing/failed history is never interpreted as zero.

| Signal | Contribution |
| --- | ---: |
| Amount ≥1.5×, ≥2×, ≥3× customer average | 5, 10, 15 |
| One prior dispute / two or more | 7 / 13 |
| Recent support contact | 8 |
| Receipt failure | 12 |
| Delivery failure / delay | 20 / 12 |
| Pending refund | 16 |
| Subscription change | 8 |
| Descriptor change | 7 |
| Duplicate candidate | 8 |
| Unusual device / country | 7 / 5 |
| Multiple failed attempts | 15 |

Unusual device, country, amount, or duplicate-candidate signals are not fraud findings. A limited combination (high amount plus unusual device, or repeated failures plus device/country anomaly) routes toward human review; the response explicitly labels it as a non-fraud conclusion.

## Intervention opportunity

This is independent of risk level. A known actionable post-payment issue plus a known policy-eligible safe action can yield HIGH opportunity. Only human review available yields MEDIUM. Existing dispute/resolution, no eligible action, or opted-out-only communication yields LOW. Missing current case state, action availability, or relevant communication preference yields UNKNOWN. The engine does not execute actions or replace the policy gate.

Signal explanations include weight and data source/timestamp when provided. API callers must populate observations from trusted backend reads and keep FAILED distinct from an empty-but-known result.
