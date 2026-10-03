"""User administration (spec/api.md §5.1, domain §12)."""

from django.db import transaction

from .. import auth
from ..errors import NotFound, Unprocessable
from ..models import Session, User


def user_json(u):
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "is_active": u.is_active, "is_admin": u.is_admin}


def list_users():
    return [user_json(u) for u in User.objects.order_by("email", "id")]


def get_user(id):
    u = User.objects.filter(pk=id).first()
    if u is None:
        raise NotFound("user not found")
    return u


def _check_email(email):
    email = email.strip()
    if "@" not in email:
        raise Unprocessable("email must contain @")
    return email


def _check_password(password):
    if len(password) < auth.MIN_PASSWORD:
        raise Unprocessable(f"password must be at least {auth.MIN_PASSWORD} characters")


def create_user(b):
    email = b.required_str("email")
    full_name = b.required_str("full_name")
    password = b.required_str("password")
    email = _check_email(email)
    _check_password(password)
    u = User.objects.create(
        email=email, full_name=full_name, password_hash=auth.hash_password(password), is_admin=b.bool("is_admin")
    )
    return u.id


def update_user(caller, id, b):
    email = b.required_str("email")
    full_name = b.required_str("full_name")
    is_active, is_admin = b.bool("is_active"), b.bool("is_admin")
    u = get_user(id)
    email = _check_email(email)
    if u.id == caller.id and not is_active:
        raise Unprocessable("you cannot deactivate yourself")
    if u.id == caller.id and not is_admin:
        raise Unprocessable("you cannot remove your own administrator role")
    User.objects.filter(pk=id).update(email=email, full_name=full_name, is_active=is_active, is_admin=is_admin)


def set_password(id, b):
    password = b.required_str("password")
    get_user(id)
    _check_password(password)
    with transaction.atomic():
        User.objects.filter(pk=id).update(password_hash=auth.hash_password(password))
        Session.objects.filter(user_id=id).delete()


def add_user(email, full_name, password, is_admin=True):
    """The out-of-band bootstrap: create or reset a user (spec/api.md §3)."""
    email = _check_email(email)
    _check_password(password)
    User.objects.update_or_create(
        email=email,
        defaults={"full_name": full_name, "password_hash": auth.hash_password(password), "is_active": True, "is_admin": is_admin},
    )
