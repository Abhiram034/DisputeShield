"""Apply ordered, checksummed PostgreSQL migrations; adopt the original init schema safely."""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
VERSIONED_SQL = re.compile(r"^(?P<version>\d{3,})_(?P<name>[a-z0-9_]+)\.sql$")
LOCK_NAME = "disputeshield:schema-migrations"
BASELINE_TABLES = {
    "merchants", "merchant_policies", "customers", "transactions", "orders",
    "post_payment_events", "disputes", "risk_assessments", "investigations",
    "interventions", "outcomes", "audit_logs",
}


def migration_files() -> list[Path]:
    files = sorted(MIGRATIONS.glob("*.sql"))
    invalid = [file.name for file in files if not VERSIONED_SQL.fullmatch(file.name)]
    if invalid:
        raise ValueError(f"Migration names must be NNN_description.sql: {', '.join(invalid)}")
    versions = [VERSIONED_SQL.fullmatch(file.name).group("version") for file in files]
    if len(versions) != len(set(versions)):
        raise ValueError("Migration version numbers must be unique")
    return files


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_migrations(dsn: str) -> list[str]:
    applied_now: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    migration_name TEXT NOT NULL,
                    checksum CHAR(64) NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    adopted_existing_schema BOOLEAN NOT NULL DEFAULT false
                )
            """)
            for path in migration_files():
                match = VERSIONED_SQL.fullmatch(path.name)
                assert match is not None
                version = match.group("version")
                digest = checksum(path)
                existing = conn.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = %s", (version,)
                ).fetchone()
                if existing:
                    if existing[0].strip() != digest:
                        raise RuntimeError(f"Applied migration {version} checksum changed: {path.name}")
                    continue

                tables = {
                    row[0] for row in conn.execute("""
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                    """).fetchall()
                } - {"schema_migrations"}
                adopted = False
                if version == "001":
                    if BASELINE_TABLES <= tables:
                        # The initial project bootstrapped these tables from data/schema.sql.
                        # Record that baseline without rerunning DDL or touching any data.
                        adopted = True
                    elif tables:
                        missing = sorted(BASELINE_TABLES - tables)
                        raise RuntimeError(
                            "Refusing to apply initial schema over a partial existing schema; "
                            f"existing tables={sorted(tables)}, missing baseline tables={missing}"
                        )

                with conn.transaction():
                    if not adopted:
                        # Migrations are plain DDL statements; no external data is interpolated.
                        conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                    conn.execute("""
                        INSERT INTO schema_migrations
                            (version, migration_name, checksum, adopted_existing_schema)
                        VALUES (%s, %s, %s, %s)
                    """, (version, path.name, digest, adopted))
                applied_now.append(f"{path.name} ({'adopted' if adopted else 'applied'})")
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
    return applied_now


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL must be set", file=sys.stderr)
        return 2
    try:
        applied = apply_migrations(dsn)
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    if applied:
        for migration in applied:
            print(migration)
    else:
        print("Database schema is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
