"""Django settings. All configuration comes from environment variables."""

import os
import secrets
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qsl

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DEBUG", "") == "1"
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(50)
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = ["tadmor"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "tadmor.auth.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tadmor.ui.http.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "tadmor.urls"
WSGI_APPLICATION = "tadmor.wsgi.application"
APPEND_SLASH = False

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.csrf"],
            "builtins": ["tadmor.ui.templatetags"],
        },
    }
]


def _database(url):
    u = urlsplit(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise ValueError("DATABASE_URL must be a postgres:// URL")
    options = dict(parse_qsl(u.query))
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(u.path.lstrip("/")),
        "USER": unquote(u.username or ""),
        "PASSWORD": unquote(u.password or ""),
        "HOST": u.hostname or "",
        "PORT": str(u.port or ""),
        "OPTIONS": options,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


DATABASES = {"default": _database(os.environ.get("DATABASE_URL", "postgres://localhost/tadmor"))}

USE_TZ = True
TIME_ZONE = "UTC"
USE_I18N = False

# Email is sent only when SMTP_ADDR (host:port) is set; otherwise the email
# endpoints answer 501 (spec/api.md §5.11).
SMTP_ADDR = os.environ.get("SMTP_ADDR", "")
EMAIL_HOST, _, _port = SMTP_ADDR.partition(":")
EMAIL_PORT = int(_port or 587)
EMAIL_HOST_USER = os.environ.get("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("SMTP_PASS", "")
EMAIL_USE_TLS = EMAIL_PORT != 465
EMAIL_USE_SSL = EMAIL_PORT == 465
DEFAULT_FROM_EMAIL = os.environ.get("MAIL_FROM", "")

MIGRATIONS_DIR = BASE_DIR / "db" / "migrations"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.server": {"level": "WARNING"}},
}
