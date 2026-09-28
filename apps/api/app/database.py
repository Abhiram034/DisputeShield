from __future__ import annotations

import os
import psycopg
from psycopg.rows import dict_row


def connect_database():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(dsn, row_factory=dict_row)
