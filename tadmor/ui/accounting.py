"""Periods, year-end, exchange rates, and bank statements (domain §13.9, A1–A5)."""

import datetime
from decimal import Decimal

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from ..models import Account, AccountingPeriod, FiscalYear
from ..services import banking, calendar, master, yearend
from ..values import Body, fmt_date
from . import choices as ch
from .base import Column, Field, admin_required, attempt, crud_form, list_page, login_required

# ---------------------------------------------------------------------------
# A1 Fiscal years and periods, A2 year-end
# ---------------------------------------------------------------------------


def periods_page(request, action_error=None):
    action_error = action_error or {}
    years = []
    periods = list(AccountingPeriod.objects.order_by("start_date"))
    for p in periods:
        p.error = action_error.get(f"period-{p.id}")
    year_list = list(FiscalYear.objects.order_by("start_date"))
    for y in year_list:
        years.append({"year": y, "periods": [p for p in periods if p.fiscal_year_id == y.id]})
    open_years = [y for y in year_list if y.status == "open"]
    closed_years = [y for y in year_list if y.status == "closed"]
    return render(request, "ui/periods.html", {
        "title": "Periods and year-end", "years": years,
        "year_error": next((m for k, m in action_error.items() if k.startswith("year-")), None),
        "closable": open_years[0] if open_years else None,
        "reopenable": closed_years[-1] if closed_years else None,
    })


@login_required
def periods(request):
    return periods_page(request)


@login_required
def toggle_period(request, id):
    """A1: close or reopen a period in one step."""
    if request.method == "POST":
        p = calendar.get_period(id)
        body = Body({**calendar.period_json(p), "status": "closed" if p.status == "open" else "open"})
        _, error = attempt(calendar.update_period, id, body)
        if error:
            return periods_page(request, {f"period-{id}": error})
    return HttpResponseRedirect(reverse("periods"))


YEAR_FIELDS = [Field("name", "Name", required=True), Field("start_date", "Start date", "date", required=True),
               Field("end_date", "End date", "date", required=True)]


@login_required
def new_year(request):
    last = FiscalYear.objects.order_by("-end_date").first()
    initial = {}
    if last:
        start = last.end_date + datetime.timedelta(days=1)
        end = yearend._plus_year(start) - datetime.timedelta(days=1)
        initial = {"name": f"FY{end.year}", "start_date": start.isoformat(), "end_date": end.isoformat()}
    return crud_form(request, title="New fiscal year", fields=YEAR_FIELDS, initial=initial,
                     save=calendar.create_fiscal_year, done=lambda _: reverse("periods"), back=reverse("periods"))


@login_required
def edit_year(request, id):
    y = calendar.fiscal_year_json(calendar.get_fiscal_year(id))
    return crud_form(request, title=f"Edit fiscal year {y['name']}", fields=YEAR_FIELDS, initial=y,
                     save=lambda b: calendar.update_fiscal_year(id, b), done=lambda _: reverse("periods"),
                     back=reverse("periods"))


def _years():
    return [(y.id, y.name) for y in FiscalYear.objects.order_by("start_date")]


PERIOD_FIELDS = [Field("fiscal_year_id", "Fiscal year", "ref", _years, required=True),
                 Field("name", "Name", required=True), Field("start_date", "Start date", "date", required=True),
                 Field("end_date", "End date", "date", required=True)]


@login_required
def new_period(request):
    proposal = calendar.next_period_proposal() or {}
    initial = {k: (fmt_date(v) if hasattr(v, "isoformat") else v) for k, v in proposal.items()}
    year = request.GET.get("year")
    if year and year.isdigit() and not initial:
        initial["fiscal_year_id"] = int(year)
    return crud_form(request, title="New accounting period", fields=PERIOD_FIELDS, initial=initial,
                     save=calendar.create_period, done=lambda _: reverse("periods"), back=reverse("periods"))


@login_required
def edit_period(request, id):
    p = calendar.period_json(calendar.get_period(id))
    fields = PERIOD_FIELDS + [Field("status", "Status", "select", ch.static(("open", "Open"), ("closed", "Closed")))]
    return crud_form(request, title=f"Edit period {p['name']}", fields=fields, initial=p,
                     save=lambda b: calendar.update_period(id, b), done=lambda _: reverse("periods"),
                     back=reverse("periods"))


@admin_required
def close_year(request, id):
    """A2: close a year, after saying what will happen."""
    y = calendar.get_fiscal_year(id)
    retained = Account.objects.filter(code="3000").values_list("id", flat=True).first()
    error = None
    chosen = request.POST.get("retained_earnings_account_id") or retained
    if request.method == "POST":
        raw = request.POST.get("retained_earnings_account_id", "")
        body = Body({"retained_earnings_account_id": int(raw) if raw.isdigit() else None})
        result, error = attempt(lambda: yearend.close(id, body.required_id("retained_earnings_account_id")))
        if error is None:
            return HttpResponseRedirect(reverse("periods"))
    return render(request, "ui/year_close.html", {
        "title": f"Close fiscal year {y.name}", "year": y, "error": error, "chosen": chosen,
        "equity_accounts": ch.accounts(is_postable=True, account_type="equity")(),
    })


@admin_required
def reopen_year(request, id):
    if request.method == "POST":
        _, error = attempt(yearend.reopen, id)
        if error:
            return periods_page(request, {f"year-{id}": error})
    return HttpResponseRedirect(reverse("periods"))


# ---------------------------------------------------------------------------
# A3 Exchange rates
# ---------------------------------------------------------------------------

RATE_FIELDS = [Field("currency_code", "Currency", "select", ch.currencies, required=True, readonly_on_edit=True),
               Field("rate_date", "Date", "date", required=True, readonly_on_edit=True),
               Field("rate", "Rate", "decimal", required=True,
                     help="Base-currency units bought by one unit of this currency.")]


@login_required
def rates(request):
    from ..services.posting import base_currency

    return list_page(
        request, title="Exchange rates", rows=master.list_exchange_rates(),
        columns=[Column("Currency", "currency_code"), Column("Date", "rate_date"), Column("Rate", "rate", True)],
        link=lambda r: reverse("exchange-rates-edit", args=[r["currency_code"], r["rate_date"]]),
        new={"url": reverse("exchange-rates-new"), "text": "New rate"},
        empty=f"No exchange rates. Documents in {base_currency()} need none.",
    )


@login_required
def new_rate(request):
    return crud_form(request, title="New exchange rate", fields=RATE_FIELDS, initial={},
                     save=master.create_exchange_rate, done=lambda _: reverse("exchange-rates"),
                     back=reverse("exchange-rates"))


@login_required
def edit_rate(request, currency, date):
    rows = [r for r in master.list_exchange_rates() if r["currency_code"] == currency and r["rate_date"] == date]
    if not rows:
        from django.http import Http404

        raise Http404("no such rate")
    return crud_form(request, title=f"{currency} rate on {date}", fields=RATE_FIELDS, initial=rows[0], editing=True,
                     save=lambda b: master.update_exchange_rate(currency, date, b),
                     done=lambda _: reverse("exchange-rates"), back=reverse("exchange-rates"),
                     extra={"sections": [{"template": "ui/rate_delete_link.html"}],
                            "delete_url": reverse("exchange-rates-delete", args=[currency, date])})


@login_required
def delete_rate(request, currency, date):
    error = None
    if request.method == "POST":
        _, error = attempt(master.delete_exchange_rate, currency, date)
        if error is None:
            return HttpResponseRedirect(reverse("exchange-rates"))
    return render(request, "ui/confirm.html", {
        "title": f"Delete the {currency} rate for {date}?", "error": error,
        "question": "Entries already posted keep the rate they used.",
        "back": reverse("exchange-rates-edit", args=[currency, date])})


# ---------------------------------------------------------------------------
# A4 and A5 Bank statements
# ---------------------------------------------------------------------------

STATEMENT_FIELDS = [
    Field("account_id", "Cash account", "ref", ch.accounts(is_postable=True, is_cash=True), required=True),
    Field("statement_date", "Statement date", "date", required=True),
    Field("opening_balance", "Opening balance", "decimal", required=True),
    Field("closing_balance", "Closing balance", "decimal", required=True),
    Field("reference", "Reference"),
]


@login_required
def statements(request):
    return list_page(
        request, title="Bank statements", rows=banking.list_statements(),
        columns=[Column("Date", "statement_date"), Column("Account", lambda r: f"{r['account_code']} {r['account_name']}"),
                 Column("Reference", "reference"), Column("Closing balance", "closing_balance", True, "amount"),
                 Column("Matched", lambda r: f"{r['matched_count']} of {r['line_count']}", True),
                 Column("Difference", "difference", True, "amount"), Column("Status", "status", kind="status")],
        link=lambda r: reverse("bank-statements-detail", args=[r["id"]]),
        new={"url": reverse("bank-statements-new"), "text": "New statement"},
    )


@login_required
def new_statement(request):
    return crud_form(request, title="New bank statement", fields=STATEMENT_FIELDS, initial={},
                     save=lambda b: banking.create_statement(b, request.current_user),
                     done=lambda id: reverse("bank-statements-detail", args=[id]), back=reverse("bank-statements"))


@login_required
def edit_statement(request, id):
    s = banking.statement_json(banking.get_statement(id))
    return crud_form(request, title="Edit bank statement", fields=STATEMENT_FIELDS, initial=s,
                     save=lambda b: banking.update_statement(id, b),
                     done=lambda _: reverse("bank-statements-detail", args=[id]),
                     back=reverse("bank-statements-detail", args=[id]))


def statement_page(request, id, errors=None, values=None):
    s = banking.statement_json(banking.get_statement(id))
    lines = banking.list_lines(id)
    candidates = banking.candidates(id) if s["status"] == "open" else []
    errors = errors or {}
    for line in lines:
        line["error"] = next((m for k, m in errors.items() if k.endswith(f"-{line['id']}")), None)
        if line["journal_line_id"] is None:
            amount = Decimal(line["amount"])
            line["candidates"] = [c for c in candidates if Decimal(c["amount"]) == amount]
    return render(request, "ui/statement_detail.html", {
        "title": f"Bank statement {s['reference'] or s['id']}", "s": s, "lines": lines, "all_candidates": candidates,
        "errors": errors, "values": values or {},
    })


@login_required
def statement_detail(request, id):
    return statement_page(request, id)


def statement_action(fn, name, admin=False):
    @login_required
    def view(request, id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("bank-statements-detail", args=[id]))
        if admin and not request.current_user.is_admin:
            return statement_page(request, id, {name: "Only administrators can do this."})
        _, error = attempt(fn, request, id)
        if error:
            return statement_page(request, id, {name: error}, request.POST)
        return HttpResponseRedirect(reverse("bank-statements-detail", args=[id]))
    return view


def _add_line(request, id):
    data = {k: request.POST.get(k, "").strip() or None for k in ("txn_date", "description", "reference", "amount")}
    return banking.add_line(id, Body(data))


def _match(request, line_id):
    raw = request.POST.get("journal_line_id", "")
    return banking.match(line_id, Body({"journal_line_id": int(raw) if raw.isdigit() else None}))


@login_required
def delete_statement(request, id):
    s = banking.statement_json(banking.get_statement(id))
    error = None
    if request.method == "POST":
        _, error = attempt(banking.delete_statement, id)
        if error is None:
            return HttpResponseRedirect(reverse("bank-statements"))
    return render(request, "ui/confirm.html", {
        "title": f"Delete bank statement {s['reference'] or s['id']}?", "error": error,
        "question": "This deletes the statement and all its lines.",
        "back": reverse("bank-statements-detail", args=[id])})


def line_action(fn, name):
    """Actions addressed to one statement line, which return to its statement."""
    @login_required
    def view(request, id, line):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("bank-statements-detail", args=[id]))
        _, error = attempt(fn, request, line)
        if error:
            return statement_page(request, id, {f"{name}-{line}": error})
        return HttpResponseRedirect(reverse("bank-statements-detail", args=[id]))
    return view


urlpatterns = [
    path("periods/", periods, name="periods"),
    path("fiscal-years/new", new_year, name="fiscal-years-new"),
    path("fiscal-years/<int:id>/", edit_year, name="fiscal-years-edit"),
    path("fiscal-years/<int:id>/close", close_year, name="fiscal-years-close"),
    path("fiscal-years/<int:id>/reopen", reopen_year, name="fiscal-years-reopen"),
    path("accounting-periods/new", new_period, name="accounting-periods-new"),
    path("accounting-periods/<int:id>/", edit_period, name="accounting-periods-edit"),
    path("accounting-periods/<int:id>/toggle", toggle_period, name="accounting-periods-toggle"),
    path("exchange-rates/", rates, name="exchange-rates"),
    path("exchange-rates/new", new_rate, name="exchange-rates-new"),
    path("exchange-rates/<str:currency>/<str:date>/", edit_rate, name="exchange-rates-edit"),
    path("exchange-rates/<str:currency>/<str:date>/delete", delete_rate, name="exchange-rates-delete"),
    path("bank-statements/", statements, name="bank-statements"),
    path("bank-statements/new", new_statement, name="bank-statements-new"),
    path("bank-statements/<int:id>/", statement_detail, name="bank-statements-detail"),
    path("bank-statements/<int:id>/edit", edit_statement, name="bank-statements-edit"),
    path("bank-statements/<int:id>/delete", delete_statement, name="bank-statements-delete"),
    path("bank-statements/<int:id>/lines", statement_action(_add_line, "add"), name="bank-statements-add-line"),
    path("bank-statements/<int:id>/import", statement_action(
        lambda r, id: banking.import_csv(id, Body({"csv": r.POST.get("csv") or None})), "import"),
        name="bank-statements-import"),
    path("bank-statements/<int:id>/auto-match", statement_action(lambda r, id: banking.auto_match(id), "auto"),
         name="bank-statements-auto-match"),
    path("bank-statements/<int:id>/reconcile", statement_action(lambda r, id: banking.reconcile(id), "reconcile"),
         name="bank-statements-reconcile"),
    path("bank-statements/<int:id>/reopen", statement_action(lambda r, id: banking.reopen(id), "reopen", admin=True),
         name="bank-statements-reopen"),
    path("bank-statements/<int:id>/lines/<int:line>/match", line_action(_match, "match"), name="bank-lines-match"),
    path("bank-statements/<int:id>/lines/<int:line>/unmatch", line_action(lambda r, l: banking.unmatch(l), "unmatch"),
         name="bank-lines-unmatch"),
    path("bank-statements/<int:id>/lines/<int:line>/delete", line_action(lambda r, l: banking.delete_line(l), "delete"),
         name="bank-lines-delete"),
]
