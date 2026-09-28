# Database migrations

Versioned SQL migrations live in `migrations/` and run automatically before the API starts in Compose. For a database-only setup, run the migration runner from the API image:

```powershell
docker compose run --rm api python scripts/migrate.py
```

To run from the host, install `apps/api/requirements.txt` and set `DATABASE_URL` from `.env.example` first. The runner takes an advisory lock, stores migration checksums in `schema_migrations`, adopts the previous `data/schema.sql` bootstrap if the full baseline table set is present, and refuses to apply over a partial schema. Never edit an applied migration; add a new numbered SQL migration.

Migration 005 adds tenant-scoped, field-level investigation evidence records. Evidence belongs to one investigation and is returned through a merchant-scoped case endpoint.

Migration 006 adds the merchant-configurable maximum evidence age used by deterministic policy checks. Checks use the latest policy version and store their result in the case audit log.

Migration 007 adds action idempotency fingerprints, requested-by metadata, explicit approval records, and simulated execution results. Migration 008 replaces the baseline's global idempotency key constraint with merchant-scoped uniqueness.
