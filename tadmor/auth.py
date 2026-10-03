"""Login sessions and passwords (spec/api.md §3, domain §12).

Sessions live in the shared `sessions` table, keyed by the SHA-256 of a
random bearer token; the raw token exists only in the client's cookie.
A session lasts a fixed 30 days from login. The user's active and admin
flags are re-read on every request, so deactivation and demotion take
effect immediately. Passwords use Django's PBKDF2 hasher.
"""

import datetime
import hashlib
import secrets
from dataclasses import dataclass

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from .models import Session, User

COOKIE = "tadmor_session"
TTL = datetime.timedelta(days=30)
MIN_PASSWORD = 8

# Paths under /api/ that do not need a session.
PUBLIC_API = {"/api/auth/login", "/api/auth/logout"}


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    full_name: str
    is_admin: bool

    def as_json(self):
        return {"id": self.id, "email": self.email, "full_name": self.full_name, "is_admin": self.is_admin}


def _hash(token):
    return hashlib.sha256(token.encode()).digest()


def hash_password(password):
    return make_password(password)


def authenticate(email, password):
    """The active user with these credentials, or None.

    Unknown emails still run a full hash, so all three failures (unknown,
    wrong password, deactivated) take the same time.
    """
    user = User.objects.filter(email=email.strip(), is_active=True).first()
    if user is None:
        make_password(password)
        return None
    if not check_password(password, user.password_hash):
        return None
    return user


def start_session(user):
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    Session.objects.filter(expires_at__lt=now).delete()
    Session.objects.create(token_hash=_hash(token), user_id=user.id, expires_at=now + TTL)
    return token


def end_session(token):
    if token:
        Session.objects.filter(token_hash=_hash(token)).delete()


def session_user(token):
    if not token:
        return None
    s = (
        Session.objects.select_related("user")
        .filter(token_hash=_hash(token), expires_at__gt=timezone.now(), user__is_active=True)
        .first()
    )
    if s is None:
        return None
    u = s.user
    return CurrentUser(u.id, u.email, u.full_name, u.is_admin)


def set_cookie(request, response, token):
    response.set_cookie(
        COOKIE, token, max_age=int(TTL.total_seconds()), httponly=True, samesite="Lax",
        secure=request.is_secure(), path="/",
    )


def clear_cookie(response):
    response.delete_cookie(COOKIE, path="/", samesite="Lax")


class SessionMiddleware:
    """Attach request.current_user, and refuse the API without a session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.current_user = session_user(request.COOKIES.get(COOKIE))
        if (
            request.current_user is None
            and request.path.startswith("/api/")
            and request.path not in PUBLIC_API
        ):
            from .api.http import error_response

            return error_response(401, "authentication required")
        return self.get_response(request)
