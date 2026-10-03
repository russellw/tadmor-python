"""The server-rendered UI's shared machinery.

UI views call the same service layer as the JSON API, so the rules and
the error messages are the API's. Forms post plain HTML fields, which are
turned into the API's request-body shape (a Body) before the service sees
them. A failed action re-renders its page with the server's message next
to the action (domain §13 G5); a successful one redirects.
"""

import functools
from dataclasses import dataclass, field

from django.db import DatabaseError, transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode

from ..errors import ApiError, Forbidden, from_database
from ..values import Body


def login_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.current_user is None:
            return HttpResponseRedirect(reverse("login") + "?" + urlencode({"next": request.get_full_path()}))
        try:
            return view(request, *args, **kwargs)
        except Forbidden:
            return render(request, "ui/message.html", {"title": "Administrators only",
                          "message": "This page is for administrators."}, status=403)
        except ApiError as e:
            if e.status == 404:
                raise Http404(e.message)
            raise

    return wrapper


def admin_required(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.current_user.is_admin:
            raise Forbidden("administrators only")
        return view(request, *args, **kwargs)

    return login_required(wrapper)


def safe_next(request, default="/"):
    target = request.POST.get("next") or request.GET.get("next") or default
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return target
    return default


def attempt(fn, *args, **kwargs):
    """Run a service call in one transaction: (result, None) on success,
    (None, message) on refusal, with nothing written."""
    try:
        with transaction.atomic():
            return fn(*args, **kwargs), None
    except ApiError as e:
        return None, e.message
    except DatabaseError as e:
        mapped = from_database(e)
        if mapped is None:
            raise
        return None, mapped.message


# ---------------------------------------------------------------------------
# Declarative forms
# ---------------------------------------------------------------------------


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"  # text, email, password, textarea, decimal, int, date, bool, select, ref
    choices: object = None  # for select/ref: a callable returning [(value, label)]
    required: bool = False
    help: str = ""
    readonly_on_edit: bool = False

    def to_json(self, raw):
        """An HTML form value in the API body's JSON shape."""
        if self.type == "bool":
            return raw == "on"
        if raw is None:
            return None
        raw = raw.strip() if self.type != "password" else raw
        if self.type in ("int", "ref"):
            if raw == "":
                return None
            try:
                return int(raw)
            except ValueError:
                return raw  # Body refuses it with a clear message
        return raw if raw != "" else None


@dataclass
class Form:
    fields: list
    values: dict = field(default_factory=dict)
    editing: bool = False

    def body(self, post):
        data = {f.name: f.to_json(post.get(f.name)) for f in self.fields if not (self.editing and f.readonly_on_edit)}
        for f in self.fields:
            if self.editing and f.readonly_on_edit:
                data[f.name] = self.values.get(f.name)
        return Body(data)

    def rows(self):
        """Fields paired with their current values and choices, for the template."""
        out = []
        for f in self.fields:
            value = self.values.get(f.name)
            choices = None
            if f.choices is not None:
                choices = list(f.choices())
                # Keep an inactive current value selectable rather than losing it.
                if value not in (None, "") and str(value) not in {str(v) for v, _ in choices}:
                    choices.append((value, f"{value} (inactive)"))
            out.append({"field": f, "value": "" if value is None else value, "choices": choices,
                        "readonly": self.editing and f.readonly_on_edit})
        return out


def form_values(post, fields):
    """Re-display what the user typed after a refused submission."""
    out = {}
    for f in fields:
        out[f.name] = (post.get(f.name) == "on") if f.type == "bool" else post.get(f.name, "")
    return out


def crud_form(request, *, title, fields, initial, save, done, back, editing=False, extra=None):
    """GET shows the form; POST saves through the service or shows its refusal."""
    form = Form(fields, dict(initial), editing)
    error = None
    if request.method == "POST":
        result, error = attempt(save, form.body(request.POST))
        if error is None:
            return HttpResponseRedirect(done(result))
        form.values.update(form_values(request.POST, [f for f in fields if not (editing and f.readonly_on_edit)]))
    ctx = {"title": title, "form": form.rows(), "error": error, "back": back}
    ctx.update(extra or {})
    return render(request, "ui/form.html", ctx)


@dataclass
class Column:
    label: str
    key: object  # a dict key, or a callable on the row
    numeric: bool = False
    kind: str = "text"  # text, amount, qty, bool, date

    def value(self, row):
        return self.key(row) if callable(self.key) else row.get(self.key)


def list_page(request, *, title, rows, columns, link, new=None, empty="Nothing here yet.", extra=None):
    ctx = {
        "title": title, "columns": columns, "new": new, "empty": empty,
        "rows": [{"cells": [(c, c.value(r)) for c in columns], "link": link(r) if link else None} for r in rows],
    }
    ctx.update(extra or {})
    return render(request, "ui/list.html", ctx)
