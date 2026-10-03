"""The JSON API's plumbing: routing, bodies, responses, and error mapping.

Handlers are registered with `route(method, pattern)` and return a
response built by `ok`, `created`, or `no_content`, or raise an ApiError.
An unknown path, or a known path with an unknown method, is a JSON 404
(spec/api.md §1.1). Authentication is enforced before any of this by
tadmor.auth.SessionMiddleware.
"""

import json
import logging

from django.db import DatabaseError, transaction
from django.http import HttpResponse, JsonResponse
from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt

from ..errors import ApiError, BadRequest, from_database
from ..values import Body

log = logging.getLogger(__name__)

_routes = {}  # pattern -> {method: (handler, admin)}


def route(method, pattern, admin=False):
    def register(handler):
        _routes.setdefault(pattern, {})[method] = (handler, admin)
        return handler

    return register


def error_response(status, message):
    return JsonResponse({"error": message}, status=status)


def ok(data):
    return JsonResponse(data, status=200, safe=False)


def created(data):
    return JsonResponse(data, status=201)


def no_content():
    return HttpResponse(status=204)


def body(request):
    """The request's JSON object; an empty body counts as {}."""
    raw = request.body
    if not raw.strip():
        return Body({})
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as e:
        raise BadRequest(f"invalid JSON body: {e}")
    return Body(data)


def call(handler, request, **kwargs):
    """Run a handler in one transaction, translating ApiErrors and
    client-caused database errors (including those raised at commit by
    the schema's deferred constraint triggers)."""
    try:
        with transaction.atomic():
            return handler(request, **kwargs)
    except ApiError as e:
        return error_response(e.status, e.message)
    except DatabaseError as e:
        mapped = from_database(e)
        if mapped is None:
            raise
        return error_response(mapped.status, mapped.message)


def _dispatcher(methods):
    @csrf_exempt
    def view(request, **kwargs):
        entry = methods.get(request.method)
        if entry is None:
            return error_response(404, "no such endpoint")
        handler, admin = entry
        if admin and not request.current_user.is_admin:
            return error_response(403, "administrator only")
        return call(handler, request, **kwargs)

    return view


@csrf_exempt
def _not_found(request, rest=""):
    return error_response(404, "no such endpoint")


def urlpatterns():
    from . import endpoints  # noqa: F401  (registers the routes)

    pats = [path("api/" + p, _dispatcher(m)) for p, m in _routes.items()]
    pats.append(re_path(r"^api/(?P<rest>.*)$", _not_found))
    return pats
