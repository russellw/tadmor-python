"""Year-end close and reopen (domain §9.3)."""

import datetime

from django.db import connection, transaction

from ..errors import Conflict, NotFound, Unprocessable
from ..models import Account, AccountingPeriod, FiscalYear
from ..values import ZERO
from .calendar import period_for_posting
from .posting import base_currency, new_entry, reverse_entry


def _year(id):
    y = FiscalYear.objects.select_for_update().filter(pk=id).first()
    if y is None:
        raise NotFound("fiscal year not found")
    return y


def _plus_year(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year + 1, day=28)


def _income_balances(end_date):
    """Each revenue and expense account's non-zero base balance (debit-positive) up to a date."""
    with connection.cursor() as cur:
        cur.execute(
            """SELECT jl.account_id, sum(jl.base_debit - jl.base_credit)
               FROM journal_lines jl
               JOIN journal_entries je ON je.id = jl.journal_entry_id
               JOIN accounts a ON a.id = jl.account_id
               WHERE je.status = 'posted' AND a.account_type IN ('revenue', 'expense') AND je.entry_date <= %s
               GROUP BY jl.account_id HAVING sum(jl.base_debit - jl.base_credit) <> 0
               ORDER BY jl.account_id""",
            [end_date],
        )
        return cur.fetchall()


def close(id, retained_earnings_id):
    with transaction.atomic():
        year = _year(id)
        if year.status != "open":
            raise Conflict(f"fiscal year {year.name} is not open")
        if FiscalYear.objects.filter(status="open", start_date__lt=year.start_date).exclude(pk=id).exists():
            raise Unprocessable("an earlier fiscal year is still open")
        if not Account.objects.filter(
            pk=retained_earnings_id, is_postable=True, is_active=True, account_type="equity"
        ).exists():
            raise Unprocessable("the retained earnings account must be a postable, active equity account")

        closing_id = None
        balances = _income_balances(year.end_date)
        if balances:
            period = AccountingPeriod.objects.filter(start_date__lte=year.end_date, end_date__gte=year.end_date).first()
            if period is None:
                period = period_for_posting(year.end_date)
            elif period.status == "closed":
                AccountingPeriod.objects.filter(pk=period.pk).update(status="open")
            lines = [
                (account, max(-bal, ZERO), max(bal, ZERO), max(-bal, ZERO), max(bal, ZERO), "Year-end close")
                for account, bal in balances
            ]
            net = sum((bal for _, bal in balances), ZERO)  # debit-positive: a net loss is positive
            if net != 0:
                lines.append((retained_earnings_id, max(net, ZERO), max(-net, ZERO), max(net, ZERO), max(-net, ZERO),
                              f"Net income (loss) for {year.name}"))
            entry = new_entry(year.end_date, base_currency(), lines, memo=f"Year-end close {year.name}",
                              reference=year.name, rate=1, is_closing=True, period=period)
            closing_id = entry.id

        AccountingPeriod.objects.filter(fiscal_year_id=id, status="open").update(status="closed")
        FiscalYear.objects.filter(pk=id).update(status="closed", closing_entry_id=closing_id)

        next_id = None
        start = year.end_date + datetime.timedelta(days=1)
        end = _plus_year(start) - datetime.timedelta(days=1)
        name = f"FY{end.year}"
        if not FiscalYear.objects.filter(start_date__lte=start, end_date__gte=start).exists() and \
                not FiscalYear.objects.filter(name=name).exists():
            next_id = FiscalYear.objects.create(name=name, start_date=start, end_date=end).id
        return {"closing_entry_id": closing_id, "next_fiscal_year_id": next_id}


def reopen(id):
    with transaction.atomic():
        year = _year(id)
        if year.status != "closed":
            raise Conflict(f"fiscal year {year.name} is not closed")
        if FiscalYear.objects.filter(status="closed", start_date__gt=year.start_date).exists():
            raise Unprocessable("a later fiscal year is closed")
        FiscalYear.objects.filter(pk=id).update(status="open", closing_entry_id=None)
        if year.closing_entry_id is None:
            return None
        from ..models import JournalEntry

        period_id = JournalEntry.objects.get(pk=year.closing_entry_id).period_id
        AccountingPeriod.objects.filter(pk=period_id, status="closed").update(status="open")
        return reverse_entry(year.closing_entry_id).id
