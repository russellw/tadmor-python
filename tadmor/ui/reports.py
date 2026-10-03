"""Reports (domain §13.8, R1–R8). Every date bound is optional."""

from decimal import Decimal

from django.shortcuts import render
from django.urls import path

from ..errors import BadRequest
from ..models import Account
from ..services import reports
from ..services.posting import base_currency
from ..values import ZERO, parse_date
from .base import login_required


def _dates(request, *names):
    """Optional YYYY-MM-DD query parameters, and a message if one is malformed."""
    out, error = {}, None
    for n in names:
        v = request.GET.get(n, "").strip()
        try:
            out[n] = parse_date(v, n, status=BadRequest) if v else None
        except BadRequest as e:
            out[n], error = None, e.message
    return out, error


FROM_TO = [("from", "From"), ("to", "To")]
AS_OF = [("as_of", "As of")]


def _sum(rows, key="amount"):
    return sum((Decimal(r[key]) for r in rows), ZERO)


@login_required
def profit_and_loss(request):
    d, error = _dates(request, "from", "to")
    rows = reports.profit_and_loss(d["from"], d["to"])
    revenue = [r for r in rows if r["account_type"] == "revenue"]
    expense = [r for r in rows if r["account_type"] == "expense"]
    return render(request, "ui/report_pnl.html", {
        "title": "Profit and loss", "error": error, "params": request.GET, "filters_ft": FROM_TO,
        "revenue": revenue, "expense": expense, "total_revenue": _sum(revenue), "total_expense": _sum(expense),
        "net_income": _sum(revenue) - _sum(expense),
    })


@login_required
def balance_sheet(request):
    d, error = _dates(request, "as_of")
    bs = reports.balance_sheet(d["as_of"])
    sections = {t: [r for r in bs["rows"] if r["account_type"] == t] for t in ("asset", "liability", "equity")}
    earnings = Decimal(bs["current_earnings"])
    totals = {t: _sum(rows) for t, rows in sections.items()}
    return render(request, "ui/report_bs.html", {
        "title": "Balance sheet", "error": error, "params": request.GET, "filters_asof": AS_OF, "sections": sections, "totals": totals,
        "earnings": earnings, "liabilities_and_equity": totals["liability"] + totals["equity"] + earnings,
    })


@login_required
def cash_flow(request):
    d, error = _dates(request, "from", "to")
    cf = reports.cash_flow(d["from"], d["to"])
    sections = []
    for activity in ("operating", "investing", "financing"):
        rows = [r for r in cf["rows"] if r["activity"] == activity]
        subtotal = _sum(rows) + (Decimal(cf["net_income"]) if activity == "operating" else ZERO)
        sections.append({"name": activity, "rows": rows, "subtotal": subtotal})
    return render(request, "ui/report_cf.html", {
        "title": "Cash flow", "error": error, "params": request.GET, "filters_ft": FROM_TO, "cf": cf, "sections": sections,
    })


@login_required
def trial_balance(request):
    rows = reports.trial_balance()
    return render(request, "ui/report_tb.html", {
        "title": "Trial balance", "rows": rows,
        "total_debit": _sum(rows, "total_debit"), "total_credit": _sum(rows, "total_credit"),
        "total_balance": _sum(rows, "balance"),
    })


@login_required
def ledger(request, id):
    account = Account.objects.filter(pk=id).first()
    d, error = _dates(request, "from", "to")
    rows = reports.ledger(id, d["from"], d["to"])
    base = base_currency()
    running = ZERO
    if d["from"]:  # the balance carried in from before the range
        for r in reports.ledger(id, None, None):
            if r["entry_date"] < d["from"].isoformat():
                running += Decimal(r["base_debit"]) - Decimal(r["base_credit"])
    opening = running
    for r in rows:
        running += Decimal(r["base_debit"]) - Decimal(r["base_credit"])
        r["running"] = running
    return render(request, "ui/report_ledger.html", {
        "title": f"Ledger: {account.code} {account.name}", "account": account, "error": error, "params": request.GET,
        "filters_ft": FROM_TO,
        "rows": rows, "opening": opening, "closing": running, "base": base,
        "foreign": any(r["currency_code"] != base for r in rows),
    })


@login_required
def journal_entry(request, id):
    e = reports.journal_entry(id)
    lines = e["lines"]
    return render(request, "ui/journal_entry.html", {
        "title": f"Journal entry {id}", "e": e,
        "totals": {k: _sum(lines, k) for k in ("debit", "credit", "base_debit", "base_credit")},
    })


def _aging(request, sales):
    rows = reports.aging(sales)
    keys = ("not_yet_due", "days_1_30", "days_31_60", "days_61_90", "days_over_90", "total_outstanding")
    return render(request, "ui/report_aging.html", {
        "title": "AR aging" if sales else "AP aging", "rows": rows, "sales": sales,
        "totals": {k: _sum(rows, k) for k in keys},
        "party_url": "customers-edit" if sales else "suppliers-edit",
    })


@login_required
def ar_aging(request):
    return _aging(request, True)


@login_required
def ap_aging(request):
    return _aging(request, False)


@login_required
def inventory_valuation(request):
    rows = reports.inventory_valuation()
    return render(request, "ui/report_valuation.html", {
        "title": "Inventory valuation", "rows": rows, "total": _sum(rows, "value_on_hand"),
    })


urlpatterns = [
    path("reports/profit-and-loss", profit_and_loss, name="profit-and-loss"),
    path("reports/balance-sheet", balance_sheet, name="balance-sheet"),
    path("reports/cash-flow", cash_flow, name="cash-flow"),
    path("reports/trial-balance", trial_balance, name="trial-balance"),
    path("reports/ar-aging", ar_aging, name="ar-aging"),
    path("reports/ap-aging", ap_aging, name="ap-aging"),
    path("inventory-valuation", inventory_valuation, name="inventory-valuation"),
    path("accounts/<int:id>/ledger", ledger, name="ledger"),
    path("journal-entries/<int:id>", journal_entry, name="journal-entry"),
]
