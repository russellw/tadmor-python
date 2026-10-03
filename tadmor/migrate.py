"""The migration runner for the shared schema (spec/README.md).

Every db/migrations/*.up.sql is applied in lexical order, each in its own
transaction together with the schema_migrations row that records it. The
files are tadmor's and are never edited here; Django's own migration system
is not used for them.
"""

import logging

from django.conf import settings
from django.db import connection, transaction

log = logging.getLogger(__name__)

# Serialises concurrent runners (several server workers starting at once).
LOCK_KEY = 0x7AD0_0001


def apply_migrations():
    files = sorted(settings.MIGRATIONS_DIR.glob("*.up.sql"))
    if not files:
        raise RuntimeError(f"no *.up.sql migration files found in {settings.MIGRATIONS_DIR}")
    applied = []
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", [LOCK_KEY])
        try:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cur.execute("SELECT version FROM schema_migrations")
            done = {row[0] for row in cur.fetchall()}
            for f in files:
                version = f.name.removesuffix(".up.sql")
                if version in done:
                    continue
                with transaction.atomic():
                    # Sent as one batch: without parameters psycopg
                    # runs several statements in one call.
                    cur.execute(f.read_text())
                    cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", [version])
                applied.append(version)
        finally:
            cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
    for v in applied:
        log.info("applied migration %s", v)
    return applied
