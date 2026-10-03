"""Journal and reports (spec/api.md §5.14, domain §10).

Every figure is a sum of base amounts over posted journal lines. Reports
are aggregate queries, so they are written in SQL over the shared tables
and views rather than through the ORM.
"""

from django.db import connection

from ..errors import NotFound
from ..models import Account, JournalEntry
from ..values import fmt4, fmt_date, fmt_rate


def _rows(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _one(sql, params=()):
    return _rows(sql, params)[0]


def _money(rows, *fields):
    for r in rows:
        for f in fields:
            r[f] = fmt4(r[f])
    return rows


def journal_entry(id):
    e = JournalEntry.objects.filter(pk=id).first()
    if e is None:
        raise NotFound("journal entry not found")
    lines = e.lines.select_related("account").order_by("line_no")
    return {
        "id": e.id, "entry_date": fmt_date(e.entry_date), "currency_code": e.currency_code,
        "exchange_rate": fmt_rate(e.exchange_rate), "reference": e.reference, "memo": e.memo,
        "status": e.status, "is_closing": e.is_closing, "reverses_entry_id": e.reverses_entry_id,
        "lines": [
            {"line_no": l.line_no, "account_id": l.account_id, "account_code": l.account.code,
             "account_name": l.account.name, "memo": l.memo, "debit": fmt4(l.debit), "credit": fmt4(l.credit),
             "base_debit": fmt4(l.base_debit), "base_credit": fmt4(l.base_credit)}
            for l in lines
        ],
    }


def ledger(account_id, date_from, date_to):
    if not Account.objects.filter(pk=account_id).exists():
        raise NotFound("account not found")
    rows = _rows(
        """SELECT je.id AS journal_entry_id, je.entry_date, je.reference, COALESCE(jl.memo, je.memo) AS memo,
                  je.currency_code, jl.debit, jl.credit, jl.base_debit, jl.base_credit
           FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_entry_id
           WHERE jl.account_id = %(a)s AND je.status = 'posted'
             AND (%(f)s::date IS NULL OR je.entry_date >= %(f)s::date)
             AND (%(t)s::date IS NULL OR je.entry_date <= %(t)s::date)
           ORDER BY je.entry_date, je.id, jl.line_no""",
        {"a": account_id, "f": date_from, "t": date_to},
    )
    for r in rows:
        r["entry_date"] = fmt_date(r["entry_date"])
    return _money(rows, "debit", "credit", "base_debit", "base_credit")


def trial_balance():
    rows = _rows("SELECT account_id, code, name, account_type, total_debit, total_credit, balance FROM trial_balance ORDER BY code")
    return _money(rows, "total_debit", "total_credit", "balance")


_POSTED = """FROM journal_lines jl
             JOIN journal_entries je ON je.id = jl.journal_entry_id
             JOIN accounts a ON a.id = jl.account_id
             WHERE je.status = 'posted'"""
_RANGE = """AND (%(f)s::date IS NULL OR je.entry_date >= %(f)s::date)
            AND (%(t)s::date IS NULL OR je.entry_date <= %(t)s::date)"""


def profit_and_loss(date_from, date_to):
    rows = _rows(
        f"""SELECT a.id AS account_id, a.code, a.name, a.account_type,
                   sum(CASE WHEN a.account_type = 'revenue' THEN jl.base_credit - jl.base_debit
                            ELSE jl.base_debit - jl.base_credit END) AS amount
            {_POSTED} AND NOT je.is_closing AND a.account_type IN ('revenue', 'expense') {_RANGE}
            GROUP BY a.id ORDER BY a.code""",
        {"f": date_from, "t": date_to},
    )
    return _money(rows, "amount")


def balance_sheet(as_of):
    rows = _rows(
        f"""SELECT a.id AS account_id, a.code, a.name, a.account_type,
                   sum(CASE WHEN a.account_type = 'asset' THEN jl.base_debit - jl.base_credit
                            ELSE jl.base_credit - jl.base_debit END) AS amount
            {_POSTED} AND a.account_type IN ('asset', 'liability', 'equity')
              AND (%(d)s::date IS NULL OR je.entry_date <= %(d)s::date)
            GROUP BY a.id ORDER BY a.code""",
        {"d": as_of},
    )
    earnings = _one(
        f"""SELECT COALESCE(sum(jl.base_credit - jl.base_debit), 0) AS v
            {_POSTED} AND a.account_type IN ('revenue', 'expense')
              AND (%(d)s::date IS NULL OR je.entry_date <= %(d)s::date)""",
        {"d": as_of},
    )["v"]
    return {"rows": _money(rows, "amount"), "current_earnings": fmt4(earnings)}


def cash_flow(date_from, date_to):
    p = {"f": date_from, "t": date_to}
    net_income = _one(
        f"""SELECT COALESCE(sum(jl.base_credit - jl.base_debit), 0) AS v
            {_POSTED} AND NOT je.is_closing AND a.account_type IN ('revenue', 'expense') {_RANGE}""", p
    )["v"]
    rows = _rows(
        f"""SELECT a.id AS account_id, a.code, a.name, a.cash_flow_activity AS activity,
                   sum(jl.base_credit - jl.base_debit) AS amount
            {_POSTED} AND NOT je.is_closing AND NOT a.is_cash
              AND a.account_type IN ('asset', 'liability', 'equity') {_RANGE}
            GROUP BY a.id ORDER BY a.code""", p
    )
    cash = _one(
        f"""SELECT COALESCE(sum(jl.base_debit - jl.base_credit)
                       FILTER (WHERE %(f)s::date IS NOT NULL AND je.entry_date < %(f)s::date), 0) AS opening,
                   COALESCE(sum(jl.base_debit - jl.base_credit)
                       FILTER (WHERE (%(f)s::date IS NULL OR je.entry_date >= %(f)s::date)
                                 AND (%(t)s::date IS NULL OR je.entry_date <= %(t)s::date)), 0) AS movement,
                   COALESCE(sum(jl.base_debit - jl.base_credit)
                       FILTER (WHERE %(t)s::date IS NULL OR je.entry_date <= %(t)s::date), 0) AS closing
            {_POSTED} AND a.is_cash""", p
    )
    return {
        "net_income": fmt4(net_income), "rows": _money(rows, "amount"), "net_cash_flow": fmt4(cash["movement"]),
        "opening_cash": fmt4(cash["opening"]), "closing_cash": fmt4(cash["closing"]),
    }


def aging(sales):
    view, party, table = ("ar_aging", "customer_id", "customers") if sales else ("ap_aging", "supplier_id", "suppliers")
    rows = _rows(
        f"""SELECT g.{party} AS party_id, o.name AS party_name, g.total_outstanding,
                   COALESCE(g.not_yet_due, 0) AS not_yet_due, COALESCE(g.days_1_30, 0) AS days_1_30,
                   COALESCE(g.days_31_60, 0) AS days_31_60, COALESCE(g.days_61_90, 0) AS days_61_90,
                   COALESCE(g.days_over_90, 0) AS days_over_90
            FROM {view} g JOIN {table} p ON p.id = g.{party} JOIN organizations o ON o.id = p.organization_id
            ORDER BY g.{party}"""
    )
    return _money(rows, "total_outstanding", "not_yet_due", "days_1_30", "days_31_60", "days_61_90", "days_over_90")


def inventory_valuation():
    rows = _rows(
        """SELECT v.product_id, p.sku, p.name, v.qty_on_hand, v.value_on_hand, v.avg_unit_cost
           FROM stock_valuation v JOIN products p ON p.id = v.product_id ORDER BY p.sku, p.id"""
    )
    return _money(rows, "qty_on_hand", "value_on_hand", "avg_unit_cost")
