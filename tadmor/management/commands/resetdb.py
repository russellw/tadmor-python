import psycopg
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

# Only throwaway databases may be wiped.
SUFFIXES = ("_conformance", "_test")


class Command(BaseCommand):
    # The checks need a connection, and the database may not exist yet.
    requires_system_checks = []
    help = "Create the configured database if missing, then drop and recreate its public schema."

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        name = db["NAME"]
        if not name.endswith(SUFFIXES):
            raise CommandError(f"refusing to wipe database {name!r}: its name must end in {' or '.join(SUFFIXES)}")
        params = {"host": db["HOST"], "port": db["PORT"], "user": db["USER"], "password": db["PASSWORD"]}
        params = {k: v for k, v in params.items() if v}
        with psycopg.connect(dbname="postgres", autocommit=True, **params) as admin:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name]).fetchone() is None:
                admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
                self.stdout.write(f"created database {name}")
        with connection.cursor() as cur:
            cur.execute("SET client_min_messages = warning; DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        self.stdout.write(f"wiped database {name}")
