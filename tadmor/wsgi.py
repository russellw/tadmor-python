"""WSGI entry point. Applies pending migrations before serving."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tadmor.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()

from tadmor.migrate import apply_migrations  # noqa: E402

apply_migrations()
