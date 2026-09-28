# DisputeShield

**Post-payment risk monitoring and dispute-prevention workspace.** DisputeShield evaluates persisted payment and post-payment signals, opens explainable cases, supports evidence-led investigation, applies merchant policy gates, and tracks outcomes over time.

> **Current scope:** This is a local demo application. Payment actions are simulated and have no external side effects. Merchant IDs in requests are data scopes, not authentication. Do not expose the API to the public internet or use real cardholder data without adding authentication, authorization, operational controls, and a compliant data environment.

## Contents

- [Architecture](#architecture)
- [Runtime services](#runtime-services)
- [Risk and case lifecycle](#risk-and-case-lifecycle)
- [Data model](#data-model)
- [API overview](#api-overview)
- [Run locally](#run-locally)
- [Synthetic data](#synthetic-data)
- [Configuration](#configuration)
- [Deploy a demo](#deploy-a-demo)
- [Tests](#tests)
- [Repository map](#repository-map)
- [Security and deployment boundaries](#security-and-deployment-boundaries)

## Architecture

```mermaid
flowchart LR
    User[Merchant operator] --> Web[Next.js dashboard\napps/web]
    Web -->|/backend/* rewrite| API[FastAPI API\napps/api]
    API --> DB[(PostgreSQL 16)]
    API --> Risk[Deterministic risk engine]
    API --> Policy[Deterministic policy engine]
    API --> Tools[Allowlisted read-only investigation tools]
    Tools --> DB
    Agent[Optional structured LLM investigation] -. bounded tool requests .-> Tools
    API --> Audit[Audited cases, decisions, incidents, actions, outcomes]
    Audit --> DB
    Generator[Deterministic synthetic data tools] --> CSV[CSV fixtures\ndata/generated]
    CSV --> Loader[Idempotent fixture loader]
    Loader --> DB
```

The browser talks to the Next.js application. Next.js rewrites `/backend/*` requests to the API service, keeping the browser and API on one origin in the local setup. The API owns validation, merchant-scoped data access, scoring, policy decisions, state transitions, and audit writes. PostgreSQL is the durable source for business records. SQL migrations are applied before the API starts in Docker Compose.

The optional investigation agent has no database connection and no write tools. It can request only a fixed set of read-only evidence tools; its structured output is validated and must cite evidence references for factual claims. Risk scoring and policy gates remain deterministic and authoritative.

## Runtime services

| Service | Implementation | Local address | Responsibility |
| --- | --- | --- | --- |
| Web | Next.js 15, React 19, TypeScript | `http://localhost:3001` | Merchant selector, risk queue, incident management, outcome observations |
| API | FastAPI, Pydantic, Python 3.11 | `http://localhost:8000` | Risk assessments, case workflows, investigation, policies, incidents, actions, outcomes |
| API reference | FastAPI OpenAPI UI | `http://localhost:8000/docs` | Interactive endpoint documentation |
| Database | PostgreSQL 16 | `localhost:5432` | Merchants, policies, payments, evidence, decisions, and audit records |

Docker Compose runs these services and stores database files in the named `postgres_data` volume. Stopping containers preserves that volume. The synthetic data generator and importer run from the host with Python.

## Risk and case lifecycle

1. **Collect observations.** Transaction facts, customer history, order state, post-payment events, and dispute state are represented as sourced observations. Missing or failed values remain explicitly `UNKNOWN` or `FAILED`; they are not silently treated as false.
2. **Assess risk.** The deterministic engine calculates an explainable score and risk level from weighted signals. It separately estimates whether a safe intervention opportunity is known. This is a risk indicator, not a fraud finding.
3. **Persist a case.** Case detection stores assessments that meet configured deterministic opening criteria, deduplicates by transaction, and records audit information.
4. **Investigate.** Fixed, read-only tools produce merchant-scoped evidence snapshots. The optional LLM can summarize a bounded set of those results; unavailable or unsupported investigation remains explicit and can be escalated for human review.
5. **Check policy.** The policy engine reads current persisted case facts, evidence freshness, and the latest merchant policy. It can allow a recommendation, require human review, or block it with a reason code.
6. **Approve and simulate.** Recommendations are idempotent and policy-gated. Approval is persisted, policy is rechecked before execution, and execution records a simulated result only.
7. **Observe outcomes.** Each simulated intervention can start an observation window. Persisted disputes and events are reconciled into the window. A missing dispute is not called prevented; it remains unknown until the window ends, after which the outcome can record that no dispute was observed.
8. **Detect incidents.** Merchant dispute rates can be compared across a current window and a preceding baseline. Thresholds are merchant-policy settings; incident changes are audited.

## Data model

All primary records are associated with a merchant workspace. The main entities are:

- **Merchant and policy:** workspace identity and versioned risk, action, incident, and observation settings.
- **Customer, transaction, and order:** synthetic customer history, payment facts, and fulfillment status.
- **Post-payment event and dispute:** timestamped signals and recorded dispute lifecycle facts.
- **Risk assessment and case:** deterministic score, risk level, opportunity, supporting signals, and case state.
- **Investigation and evidence:** structured report plus field-level source, value, status, and observation time.
- **Intervention and approval:** idempotent recommendation, policy result, human decision, and simulated execution record.
- **Outcome:** observation-window status and linked persisted evidence for dispute, complaint, or resolution facts.
- **Merchant incident and audit log:** dispute-spike metrics, status transitions, and decision history.

```mermaid
erDiagram
    MERCHANTS ||--o{ MERCHANT_POLICIES : configures
    MERCHANTS ||--o{ CUSTOMERS : owns
    MERCHANTS ||--o{ TRANSACTIONS : scopes
    MERCHANTS ||--o{ CASES : scopes
    MERCHANTS ||--o{ MERCHANT_INCIDENTS : records
    CUSTOMERS ||--o{ TRANSACTIONS : makes
    CUSTOMERS ||--o{ ORDERS : places
    TRANSACTIONS ||--o{ ORDERS : fulfills
    TRANSACTIONS ||--o{ POST_PAYMENT_EVENTS : emits
    TRANSACTIONS ||--o{ DISPUTES : may_have
    TRANSACTIONS ||--o{ RISK_ASSESSMENTS : assessed_by
    TRANSACTIONS ||--o| CASES : may_open
    RISK_ASSESSMENTS ||--o{ CASES : supports
    TRANSACTIONS ||--o{ INVESTIGATIONS : examined_in
    INVESTIGATIONS ||--o{ INVESTIGATION_EVIDENCE : cites
    TRANSACTIONS ||--o{ INTERVENTIONS : considered_for
    INTERVENTIONS ||--o| INTERVENTION_APPROVALS : decided_by
    INTERVENTIONS ||--o| OUTCOMES : observed_in
```

Audit rows use entity type and entity ID references so actions across these workflows share one append-only audit stream.

Schema changes are numbered SQL files in [`migrations/`](migrations/). The migration runner checks applied checksums, takes a PostgreSQL advisory lock, and refuses to apply onto an unexpected partial schema. Applied migrations should not be edited; add a new numbered migration instead.

## API overview

Interactive details and request schemas are available at `/docs` while the API is running. Main routes include:

| Method and route | Purpose |
| --- | --- |
| `GET /health` | API health check |
| `POST /v1/risk/assess` | Score a supplied set of sourced observations |
| `GET, POST /api/merchants` | List workspaces or create a workspace with default policy |
| `DELETE /api/merchants/{merchant_id}` | Delete an empty workspace after exact-name confirmation |
| `POST /api/cases/detect` | Persist assessment and open/deduplicate a case when criteria are met |
| `GET /api/cases?merchant_id=…` | List a merchant’s cases |
| `GET /api/cases/{case_id}` | Read a merchant-scoped case |
| `GET /api/cases/{case_id}/investigation-tools` | Run the allowlisted read-only evidence snapshot |
| `POST /api/cases/{case_id}/investigate` | Attempt optional structured investigation |
| `GET /api/cases/{case_id}/evidence` | Read saved investigation evidence |
| `POST /api/cases/{case_id}/policy-check` | Evaluate a requested action using persisted facts and latest policy |
| `POST /api/cases/{case_id}/actions` | Create a policy-gated, idempotent recommendation |
| `POST /api/cases/{case_id}/interventions/{intervention_id}/approval` | Record a human approval or rejection |
| `POST /api/cases/{case_id}/interventions/{intervention_id}/execute` | Recheck policy and record simulated execution |
| `GET /api/cases/{case_id}/outcomes` | List outcome observation windows |
| `POST /api/cases/{case_id}/outcomes/refresh` | Reconcile persisted disputes and post-payment events |
| `POST /api/incidents/detect` | Check persisted merchant dispute counts against configured thresholds |
| `GET /api/incidents?merchant_id=…` | List merchant incidents |
| `POST /api/incidents/{incident_id}/status` | Acknowledge or resolve an incident |

Case and incident requests require a `merchant_id` scope. This prevents accidental cross-workspace queries in the demo but does **not** verify the caller’s identity or permissions.

## Run locally

### Requirements

- Docker Desktop with the Docker Compose plugin
- Python 3.11 or newer for fixture generation/loading and Python tests
- Node.js 22 is used inside the web container; a host Node install is only needed for direct frontend development

### Start the application

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
# Edit .env for local-only values if needed.
docker compose up --build -d
docker compose ps
```

Open the dashboard at [http://localhost:3001](http://localhost:3001) and the API reference at [http://localhost:8000/docs](http://localhost:8000/docs).

To follow service logs:

```powershell
docker compose logs -f api web postgres
```

To stop the services while preserving the database volume:

```powershell
docker compose down
```

`docker compose down -v` removes the database volume and permanently deletes its local contents. Use it only when you intentionally want a clean database.

### Run the API directly

The API can also run on the host when PostgreSQL is available and `DATABASE_URL` points to it:

```powershell
python -m pip install -r apps/api/requirements.txt
$env:DATABASE_URL = 'postgresql://dispute:local_dev_only@localhost:5432/disputeshield'
python scripts/migrate.py
python -m uvicorn app.main:app --app-dir apps/api --reload --port 8000
```

## Synthetic data

Fixtures are generated deterministically and are clearly synthetic. The standard pack contains 10 merchants, 5,000 customers, 50,000 transactions, 50,000 orders, 100,000 post-payment events, and 601 disputes. The fixture loader maps source identifiers to stable UUIDs and is additive and idempotent; it does not replace or delete existing records.

Generate and load the standard dataset:

```powershell
python scripts/generate_data.py
$env:DATABASE_URL = 'postgresql://dispute:local_dev_only@localhost:5432/disputeshield'
python scripts/load_synthetic_data.py --confirm-load
Remove-Item Env:DATABASE_URL
```

The importer checks that its target database is named `disputeshield` and requires `--confirm-load`. Select **Synthetic Merchant 1** through **Synthetic Merchant 10** in the dashboard to explore the imported workspaces. A smaller dataset can be generated with:

```powershell
python scripts/generate_data.py --customers 100 --transactions 1000 --events 2500 --output data/generated/small
```

Additional loader notes are in [`scripts/README.md`](scripts/README.md).

## Configuration

Configuration is supplied through environment variables. `.env.example` contains local-development defaults; do not reuse its demo credentials in a deployed environment.

| Variable | Used by | Description |
| --- | --- | --- |
| `DATABASE_URL` | API, migration script | PostgreSQL connection string |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose database | Local PostgreSQL initialization |
| `API_BASE_URL` | Next.js | API origin used by the `/backend/*` rewrite; inside Compose this is `http://api:8000` |
| `LLM_PROVIDER` | API | `none` by default; set to `openai_compatible` to enable optional investigation |
| `LLM_API_KEY` | API | Secret for the configured compatible provider; keep out of source control and browser bundles |
| `LLM_MODEL` | API | Provider model identifier |
| `LLM_BASE_URL` | API | Provider API base URL; defaults to `https://api.openai.com/v1` |

The LLM integration is optional; risk assessment, case storage, and policy decisions do not require it.

## Deploy a demo

The repository is a monorepo. Deploy the frontend and API as separate services, with PostgreSQL as a managed database:

1. **API and database on Render:** create a Blueprint from this GitHub repository and use [`render.yaml`](render.yaml). The Blueprint builds the API from the repository-root Docker context, creates PostgreSQL, sets `DATABASE_URL`, and runs migrations as the API starts.
2. **Frontend on Vercel:** import the same repository and set the project Root Directory to `apps/web`. Add `API_BASE_URL` as an environment variable using the deployed API's HTTPS origin, then deploy. The `/backend/*` rewrite forwards dashboard requests to that API.
3. **Optional synthetic fixtures:** generate the CSV pack locally and run the fixture loader with the managed database connection string as `DATABASE_URL`. The database name must be `disputeshield`; the loader is additive and idempotent.

The included Render Blueprint selects free demo resources. Render's free web service can sleep after inactivity, and free PostgreSQL expires after 30 days; upgrade the database before expiry if you need to keep its contents ([Render free-tier limits](https://render.com/docs/free)). This deployment is intended for synthetic-data demonstrations only. The API currently has no authentication, so keep real customer or processor data out of it. Use an authenticated gateway or add application authentication before treating it as a shared service.

## Tests

Install test dependencies:

```powershell
python -m pip install -r tests/requirements.txt
python -m playwright install chromium
```

Run unit and API tests:

```powershell
python -m pytest -q tests
```

PostgreSQL integration tests require a disposable database whose name ends in `_test`. See [`tests/README.md`](tests/README.md) for the test database setup and browser test prerequisites. Integration tests refuse to write to a database without the `_test` suffix.

Build the web application using the same production build used by Docker:

```powershell
docker compose build web
```

## Repository map

```text
apps/
  api/
    app/
      domain/       risk, policy, investigation tools, optional agent
      cases.py      case, evidence, action, and outcome routes
      incidents.py  merchant dispute-rate incident routes
      merchants.py  merchant workspace routes
      main.py       FastAPI application and route registration
    Dockerfile
  web/
    app/            Next.js dashboard and styles
    Dockerfile
data/
  generated/        deterministic synthetic CSV fixtures
docs/               focused design, policy, security, and demo notes
migrations/         numbered PostgreSQL schema migrations
scripts/            data generation, safe import, and migration runner
tests/              unit, API, database integration, and browser tests
docker-compose.yml  local web, API, and database services
```

## Security and deployment boundaries

- **No authentication:** merchant scoping is not authentication or authorization. Add an identity layer and derive tenant scope from trusted credentials before exposing this application to users.
- **Demo credentials:** change database credentials, use managed secret storage, and restrict network access outside local development.
- **Synthetic information only:** do not ingest production payment data. Never store PAN, CVV, authentication secrets, or other unnecessary sensitive data.
- **No live payment integration:** simulated execution records an internal result only. It does not contact customers, processors, or merchant systems and must not be represented as a completed refund or other real action.
- **Human and policy controls:** the backend owns policy decisions, approval state, idempotency, and audit records. Missing, stale, contradictory, or unavailable evidence remains explicit and should fail closed.
- **Hosting:** the Next.js frontend can be hosted separately from the API, but the API needs a reachable PostgreSQL database and configured environment. Vercel hosting of the frontend alone does not deploy the Python API, database, or Docker Compose stack. Configure `API_BASE_URL` to a secured, reachable API endpoint and implement authentication before any public deployment.

For implementation details, see [Risk Engine](docs/RISK_ENGINE.md), [Case Management](docs/CASE_MANAGEMENT.md), [Policy Engine](docs/POLICY_ENGINE.md), [Action Engine](docs/ACTION_ENGINE.md), [Security](docs/SECURITY.md), and [Demo Guide](docs/DEMO.md).
