"""Bank statements and reconciliation (spec/api.md §5.13, domain §8).

A statement belongs to one postable, active cash account. Each of its
lines matches at most one posted journal line on that account with the
same signed transaction-currency amount, and a journal line backs at most
one statement line anywhere. The schema enforces the account and match
rules and freezes reconciled statements; the checks here give the
refusals their statuses.
"""

import csv
import io
import re

from django.db import connection, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from ..errors import BadRequest, Conflict, NotFound, Unprocessable
from ..models import Account, BankStatement, BankStatementLine, JournalLine
from ..values import MONEY, ZERO, fmt4, fmt_date, parse_date, parse_decimal

_AMOUNT = re.compile(r"-?(\d+(\.\d*)?|\.\d+)")


def _statements():
    return BankStatement.objects.select_related("account").annotate(
        n_lines=Count("lines"),
        n_matched=Count("lines", filter=Q(lines__journal_line__isnull=False)),
        total=Sum("lines__amount"),
    )


def statement_json(s):
    total = s.total or ZERO
    return {
        "id": s.id, "account_id": s.account_id, "account_code": s.account.code, "account_name": s.account.name,
        "statement_date": fmt_date(s.statement_date), "opening_balance": fmt4(s.opening_balance),
        "closing_balance": fmt4(s.closing_balance), "reference": s.reference, "status": s.status,
        "line_count": s.n_lines, "matched_count": s.n_matched, "lines_total": fmt4(total),
        "difference": fmt4(s.opening_balance + total - s.closing_balance),
    }


def list_statements():
    return [statement_json(s) for s in _statements().order_by("-statement_date", "-id")]


def get_statement(id):
    s = _statements().filter(pk=id).first()
    if s is None:
        raise NotFound("bank statement not found")
    return s


def _required(b):
    b.required_id("account_id")
    b.required_str("statement_date")
    b.required_str("opening_balance")
    b.required_str("closing_balance")


def _fields(b):
    account = b.int("account_id")
    if not Account.objects.filter(pk=account, is_postable=True, is_active=True, is_cash=True).exists():
        raise Unprocessable("the account must be a postable, active cash account")
    return {
        "account_id": account,
        "statement_date": parse_date(b.str("statement_date"), "statement_date"),
        "opening_balance": b.decimal("opening_balance", MONEY),
        "closing_balance": b.decimal("closing_balance", MONEY),
        "reference": b.text("reference"),
    }


def create_statement(b, user=None):
    _required(b)
    return BankStatement.objects.create(created_by=user.id if user else None, **_fields(b)).id


def _lock_open(id):
    s = BankStatement.objects.select_for_update().filter(pk=id).first()
    if s is None:
        raise NotFound("bank statement not found")
    if s.status != "open":
        raise Conflict("the bank statement is reconciled")
    return s


def update_statement(id, b):
    _required(b)
    with transaction.atomic():
        s = _lock_open(id)
        fields = _fields(b)
        if fields["account_id"] != s.account_id and s.lines.filter(journal_line__isnull=False).exists():
            raise Unprocessable("unmatch the statement's lines before changing its account")
        BankStatement.objects.filter(pk=id).update(**fields)


def delete_statement(id):
    with transaction.atomic():
        _lock_open(id)
        BankStatementLine.objects.filter(statement_id=id).delete()
        BankStatement.objects.filter(pk=id).delete()


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------


def line_json(l):
    jl = l.journal_line
    je = jl.journal_entry if jl else None
    return {
        "id": l.id, "line_no": l.line_no, "txn_date": fmt_date(l.txn_date), "description": l.description,
        "reference": l.reference, "amount": fmt4(l.amount), "journal_line_id": l.journal_line_id,
        "journal_entry_id": je.id if je else None, "entry_date": fmt_date(je.entry_date) if je else None,
        "entry_memo": je.memo if je else None,
    }


def list_lines(id):
    get_statement(id)
    lines = BankStatementLine.objects.filter(statement_id=id).select_related("journal_line__journal_entry")
    return [line_json(l) for l in lines.order_by("line_no")]


def _append(statement_id, txn_date, description, reference, amount):
    last = BankStatementLine.objects.filter(statement_id=statement_id).order_by("-line_no").first()
    return BankStatementLine.objects.create(
        statement_id=statement_id, line_no=(last.line_no if last else 0) + 1, txn_date=txn_date,
        description=description, reference=reference, amount=amount,
    ).id


def add_line(id, b):
    b.required_str("txn_date")
    b.required_str("description")
    b.required_str("amount")
    with transaction.atomic():
        _lock_open(id)
        amount = b.decimal("amount", MONEY)
        if amount == 0:
            raise Unprocessable("amount must not be zero")
        return _append(id, parse_date(b.str("txn_date"), "txn_date"), b.str("description"), b.text("reference"), amount)


def parse_csv(text):
    """Statement lines from `date,description,amount[,reference]` CSV (domain §8.2)."""
    try:
        records = list(csv.reader(io.StringIO(text), strict=True, skipinitialspace=True))
    except csv.Error as e:
        raise Unprocessable(f"invalid CSV: {e}")
    out = []
    for i, rec in enumerate(records, 1):
        if not rec or (len(rec) == 1 and not rec[0].strip()):
            continue
        if len(rec) not in (3, 4):
            raise Unprocessable(f"record {i} has {len(rec)} fields; want date,description,amount[,reference]")
        rec = [f.strip() for f in rec]
        try:
            date = parse_date(rec[0])
        except Unprocessable:
            if i == 1:
                continue  # a header row
            raise Unprocessable(f"record {i}: {rec[0]!r} is not a YYYY-MM-DD date")
        if not rec[1]:
            raise Unprocessable(f"record {i}: the description is empty")
        if not _AMOUNT.fullmatch(rec[2]):
            raise Unprocessable(f"record {i}: {rec[2]!r} is not a decimal amount")
        amount = parse_decimal(rec[2], MONEY, f"record {i} amount")
        if amount == 0:
            raise Unprocessable(f"record {i}: the amount must not be zero")
        out.append((date, rec[1], (rec[3] or None) if len(rec) == 4 else None, amount))
    if not out:
        raise Unprocessable("the CSV has no data rows")
    return out


def import_csv(id, b):
    text = b.str("csv")
    if not text:
        raise BadRequest("csv is required")
    with transaction.atomic():
        _lock_open(id)
        rows = parse_csv(text)
        for date, description, reference, amount in rows:
            _append(id, date, description, reference, amount)
        return len(rows)


def _lock_line(line_id):
    line = BankStatementLine.objects.select_for_update().select_related("statement").filter(pk=line_id).first()
    if line is None:
        raise NotFound("bank statement line not found")
    if line.statement.status != "open":
        raise Conflict("the bank statement is reconciled")
    return line


def delete_line(line_id):
    with transaction.atomic():
        _lock_line(line_id)
        BankStatementLine.objects.filter(pk=line_id).delete()


def match(line_id, b):
    journal_line_id = b.int("journal_line_id")
    if journal_line_id is None or journal_line_id <= 0:
        raise BadRequest("journal_line_id is required")
    with transaction.atomic():
        line = _lock_line(line_id)
        if line.journal_line_id is not None:
            raise Conflict("the statement line is already matched")
        jl = JournalLine.objects.select_related("journal_entry").filter(pk=journal_line_id).first()
        if jl is None or jl.journal_entry.status != "posted":
            raise Unprocessable("journal_line_id must name a line of a posted journal entry")
        if jl.account_id != line.statement.account_id:
            raise Unprocessable("the journal line is on a different account")
        if jl.debit - jl.credit != line.amount:
            raise Unprocessable("the journal line's amount differs from the statement line's")
        if BankStatementLine.objects.filter(journal_line_id=journal_line_id).exists():
            raise Conflict("the journal line already backs another statement line")
        BankStatementLine.objects.filter(pk=line_id).update(journal_line_id=journal_line_id)


def unmatch(line_id):
    with transaction.atomic():
        _lock_line(line_id)
        BankStatementLine.objects.filter(pk=line_id).update(journal_line=None)


_CANDIDATES = """
    SELECT jl.id AS journal_line_id, je.id AS journal_entry_id, je.entry_date, je.reference,
           COALESCE(jl.memo, je.memo) AS memo, jl.debit - jl.credit AS amount
    FROM journal_lines jl
    JOIN journal_entries je ON je.id = jl.journal_entry_id
    WHERE je.status = 'posted' AND jl.account_id = %s
      AND NOT EXISTS (SELECT 1 FROM bank_statement_lines b WHERE b.journal_line_id = jl.id)"""


def candidates(id):
    s = get_statement(id)
    with connection.cursor() as cur:
        cur.execute(_CANDIDATES + " ORDER BY je.entry_date, je.id, jl.line_no", [s.account_id])
        names = [c.name for c in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
    for r in rows:
        r["entry_date"] = fmt_date(r["entry_date"])
        r["amount"] = fmt4(r["amount"])
    return rows


def auto_match(id):
    with transaction.atomic():
        s = _lock_open(id)
        matched = 0
        for line in BankStatementLine.objects.filter(statement_id=id, journal_line__isnull=True).order_by("line_no"):
            with connection.cursor() as cur:
                cur.execute(
                    _CANDIDATES + " AND jl.debit - jl.credit = %s ORDER BY abs(je.entry_date - %s::date), jl.id LIMIT 1",
                    [s.account_id, line.amount, line.txn_date],
                )
                row = cur.fetchone()
            if row:
                BankStatementLine.objects.filter(pk=line.id).update(journal_line_id=row[0])
                matched += 1
        return matched


def reconcile(id):
    with transaction.atomic():
        s = _lock_open(id)
        lines = BankStatementLine.objects.filter(statement_id=id)
        if lines.filter(journal_line__isnull=True).exists():
            raise Unprocessable("every line must be matched before reconciling")
        total = lines.aggregate(t=Sum("amount"))["t"] or ZERO
        if s.opening_balance + total != s.closing_balance:
            raise Unprocessable("opening balance plus the lines does not equal the closing balance")
        BankStatement.objects.filter(pk=id).update(status="reconciled", reconciled_at=timezone.now())


def reopen(id):
    with transaction.atomic():
        s = BankStatement.objects.select_for_update().filter(pk=id).first()
        if s is None:
            raise NotFound("bank statement not found")
        if s.status != "reconciled":
            raise Conflict("the bank statement is not reconciled")
        BankStatement.objects.filter(pk=id).update(status="open", reconciled_at=None)
