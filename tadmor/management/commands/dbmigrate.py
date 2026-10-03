from django.core.management.base import BaseCommand

from tadmor.migrate import apply_migrations


class Command(BaseCommand):
    help = "Apply pending shared-schema migrations from db/migrations."

    def handle(self, *args, **options):
        applied = apply_migrations()
        self.stdout.write(f"applied {len(applied)} migration(s)")
