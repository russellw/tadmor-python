"""Sign-in and sign-out (domain §13 G1, G2)."""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .. import auth
from .base import safe_next


def login(request):
    if request.current_user is not None and request.method == "GET":
        return HttpResponseRedirect(safe_next(request))
    error, email = None, ""
    if request.method == "POST":
        email = request.POST.get("email", "")
        user = auth.authenticate(email, request.POST.get("password", "")) if email.strip() else None
        if user is not None:
            response = HttpResponseRedirect(safe_next(request))
            auth.set_cookie(request, response, auth.start_session(user))
            return response
        error = "Invalid email or password."
    return render(request, "ui/login.html", {"title": "Sign in", "error": error, "email": email,
                                             "next": request.GET.get("next") or request.POST.get("next", "")})


@require_POST
def logout(request):
    auth.end_session(request.COOKIES.get(auth.COOKIE))
    response = HttpResponseRedirect("/login")
    auth.clear_cookie(response)
    return response
