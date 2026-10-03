"""Errors that carry their HTTP status (spec/api.md §1.4).

The service layer raises these; the JSON API turns them into
{"error": ...} responses and the UI shows their message next to the action
that failed. Database errors that escape the service layer's own checks are
translated by `from_database` according to their SQLSTATE.
"""

from django.db import DatabaseError


class ApiError(Exception):
    status = 500

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class BadRequest(ApiError):
    status = 400


class Unauthorized(ApiError):
    status = 401


class Forbidden(ApiError):
    status = 403


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


class Unprocessable(ApiError):
    status = 422


class NotConfigured(ApiError):
    status = 501


# SQLSTATE classes and codes that mean the request, not the server, is at fault.
_STATUS_BY_SQLSTATE = {
    "23505": Conflict,  # unique_violation
    "23503": Unprocessable,  # foreign_key_violation
    "23514": Unprocessable,  # check_violation
    "23P01": Unprocessable,  # exclusion_violation (overlapping periods)
    "23502": Unprocessable,  # not_null_violation
    "22003": Unprocessable,  # numeric_value_out_of_range
    "22007": Unprocessable,  # invalid_datetime_format
    "22008": Unprocessable,  # datetime_field_overflow
    "22P02": Unprocessable,  # invalid_text_representation
    "P0001": Unprocessable,  # raise_exception from a trigger
}


def sqlstate(exc):
    cause = exc.__cause__ if isinstance(exc, DatabaseError) else exc
    diag = getattr(cause, "diag", None)
    return getattr(diag, "sqlstate", None) or getattr(cause, "sqlstate", None)


def from_database(exc):
    """The ApiError for a database error, or None if it is a server fault."""
    state = sqlstate(exc)
    cls = _STATUS_BY_SQLSTATE.get(state)
    if cls is None:
        return None
    cause = exc.__cause__ or exc
    diag = getattr(cause, "diag", None)
    message = _friendly(state, diag) or (getattr(diag, "message_primary", None) or str(cause)).strip()
    return cls(message)


def _friendly(state, diag):
    """A message naming the offending column, for the common constraint kinds."""
    constraint = getattr(diag, "constraint_name", None) or ""
    table = getattr(diag, "table_name", None) or ""
    column = constraint.removeprefix(table + "_")
    if state == "23503" and column.endswith("_fkey"):
        return f"unknown {column.removesuffix('_fkey')}"
    if state == "23505":
        if column.endswith("_key"):
            return f"{column.removesuffix('_key')} already exists"
        return "a record with the same key already exists"
    if state == "23P01":
        return "overlaps an existing record"
    return None


def constraint_name(exc):
    cause = exc.__cause__ if isinstance(exc, DatabaseError) else exc
    diag = getattr(cause, "diag", None)
    return getattr(diag, "constraint_name", None)
