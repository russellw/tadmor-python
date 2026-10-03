import sys

from django.core.management.base import BaseCommand, CommandError

from tadmor.errors import ApiError
from tadmor.migrate import apply_migrations
from tadmor.services.users import add_user


class Command(BaseCommand):
    help = (
        "Create (or reset) an administrator, reading the password from stdin. "
        "Applies pending migrations first, so it can bootstrap an empty database."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--not-admin", action="store_true", help="create an ordinary user")

    def handle(self, *args, email, name, not_admin, **options):
        apply_migrations()
        password = sys.stdin.readline().rstrip("\n")
        try:
            add_user(email, name, password, is_admin=not not_admin)
        except ApiError as e:
            raise CommandError(e.message)
        self.stdout.write(f"user {email} is ready")
