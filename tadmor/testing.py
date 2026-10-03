"""Test runner: Django's own, with the shared schema applied to the test database.

The models are unmanaged, so Django creates no tables; the shared SQL
migrations (db/migrations) build the test database exactly as they build
production. Tests then run in transactions that roll back, so the seed
data stays as a fresh instance has it.
"""

from django.test.runner import DiscoverRunner

from .migrate import apply_migrations


class Runner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        apply_migrations()
        return config
