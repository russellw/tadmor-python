"""Posting to the general ledger, and reversing it (domain §4, §7).

Each posting runs in one transaction and creates one posted journal entry
in the document's currency, at the entry's exchange rate, with every line
carrying both its transaction and its base amounts. The schema checks that
a posted entry balances in both and touches only postable, active accounts
in an open period; the checks here come first so that each refusal has the
status and message the spec gives it (domain §4.2).
"""

from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..errors import Conflict, NotFound, Unprocessable
from ..models import (
    Account,
    BankStatementLine,
    ExchangeRate,
    GLSettings,
    JournalEntry,
    JournalLine,
    Product,
    StockMovement,
    TaxCode,
)
from ..values import ZERO, round4
from .calendar import period_for_posting
from .kinds import DocKind, PaymentKind


def base_currency():
    return GLSettings.objects.get(pk=1).base_currency


def rate_for(currency, date):
    """The exchange rate a posting in `currency` on `date` uses (domain §7.1)."""
    if currency == base_currency():
        return 1
    r = ExchangeRate.objects.filter(currency_code=currency, rate_date__lte=date).order_by("-rate_date").first()
    if r is None:
        raise Unprocessable(f"no {currency} exchange rate on or before {date}")
    return r.rate


def new_entry(date, currency, lines, *, memo=None, reference=None, rate=None, reverses=None, is_closing=False, period=None):
    """Write a posted journal entry. `lines` holds (account_id, debit, credit,
    base_debit, base_credit, memo) tuples."""
    if period is None:
        period = period_for_posting(date)
    if rate is None:
        rate = rate_for(currency, date)
    entry = JournalEntry.objects.create(
        entry_date=date, period=period, currency_code=currency, exchange_rate=rate, memo=memo,
        reference=reference, status="posted", posted_at=timezone.now(), reverses_entry_id=reverses,
        is_closing=is_closing,
    )
    JournalLine.objects.bulk_create(
        JournalLine(journal_entry=entry, line_no=i, account_id=a, debit=d, credit=c, base_debit=bd, base_credit=bc, memo=m)
        for i, (a, d, c, bd, bc, m) in enumerate(lines, 1)
    )
    return entry


def _side(amount, base, debit, memo, account):
    """A line for a signed amount on its natural side; a negative amount
    goes on the opposite side (domain §4.3)."""
    if (amount > 0) == debit:
        return (account, abs(amount), ZERO, abs(base), ZERO, memo)
    return (account, ZERO, abs(amount), ZERO, abs(base), memo)


# ---------------------------------------------------------------------------
# Invoices, bills, credit notes
# ---------------------------------------------------------------------------


def post_document(kind: DocKind, id):
    with transaction.atomic():
        doc = kind.model.objects.select_for_update().select_related(kind.party).filter(pk=id).first()
        if doc is None:
            raise NotFound(f"{kind.noun} not found")
        if doc.status != "draft":
            raise Conflict(f"{kind.noun} is {doc.status}, not draft")
        if doc.total <= 0:
            raise Unprocessable(f"{kind.noun} total must be greater than zero")
        control = getattr(getattr(doc, kind.party), kind.control_account)
        if control is None:
            raise Unprocessable(f"the {kind.party} has no {'A/R' if kind.sales else 'A/P'} control account")

        lk = kind.lines
        lines = list(lk.model.objects.filter(**{lk.parent + "_id": id}))
        products = {p.id: p for p in Product.objects.filter(pk__in={l.product_id for l in lines if l.product_id})}
        fallback = "revenue_account_id" if kind.sales else "inventory_account_id"
        taxes = {t.code: t.tax_account_id for t in TaxCode.objects.filter(pk__in={l.tax_code for l in lines if l.tax_code})}

        detail = defaultdict(lambda: ZERO)
        tax = defaultdict(lambda: ZERO)
        for line in lines:
            account = getattr(line, lk.account)
            if account is None and line.product_id:
                account = getattr(products[line.product_id], fallback)
            if line.line_subtotal != 0:
                if account is None:
                    raise Unprocessable(f"line {line.line_no} has no {'revenue' if kind.sales else 'expense'} account")
                detail[account] += line.line_subtotal
            if line.tax_amount != 0:
                tax_account = taxes.get(line.tax_code)
                if tax_account is None:
                    raise Unprocessable(f"line {line.line_no} is taxed but its tax code has no tax account")
                tax[tax_account] += line.tax_amount

        date = getattr(doc, kind.date)
        period = period_for_posting(date)
        rate = rate_for(doc.currency_code, date)

        # Detail lines sit opposite the control line.
        detail_debit = not kind.control_debit
        name = "Revenue" if kind.sales else "Expense"
        entries = []
        for memo, sums in ((name, detail), ("Sales tax" if kind.sales else "Input tax", tax)):
            for account in sorted(sums):
                amount = sums[account]
                if amount != 0:
                    entries.append(_side(amount, round4(amount * rate), detail_debit, memo, account))
        # The control line carries the total; its base is the net of the details' (domain §7.2).
        base_net = sum((e[4] - e[3] for e in entries), ZERO) if kind.control_debit else sum((e[3] - e[4] for e in entries), ZERO)
        control_memo = "Accounts receivable" if kind.sales else "Accounts payable"
        if kind.control_debit:
            control_line = (control, doc.total, ZERO, base_net, ZERO, control_memo)
        else:
            control_line = (control, ZERO, doc.total, ZERO, base_net, control_memo)

        number = getattr(doc, kind.number)
        entry = new_entry(date, doc.currency_code, [control_line, *entries], memo=f"{kind.label} {number}",
                          reference=number, rate=rate, period=period)
        kind.model.objects.filter(pk=id).update(status="posted", journal_entry_id=entry.id, period_id=period.id)
        return entry.id


def unpost_document(kind: DocKind, id):
    with transaction.atomic():
        doc = kind.model.objects.select_for_update().filter(pk=id).first()
        if doc is None:
            raise NotFound(f"{kind.noun} not found")
        if doc.status != "posted" or doc.journal_entry_id is None:
            raise Conflict(f"{kind.noun} is not posted")
        if kind.credit:
            if kind.applications.objects.filter(settler_id=id).exists():
                raise Conflict(f"the {kind.noun} has been applied and cannot be unposted")
        elif _applied_to(kind, id):
            raise Conflict(f"payments or credit notes are applied to this {kind.noun}; it cannot be unposted")
        reversal = reverse_entry(doc.journal_entry_id)
        kind.model.objects.filter(pk=id).update(status="draft", journal_entry_id=None, period_id=None)
        return reversal.id


def _applied_to(kind, id):
    from .kinds import CUSTOMER_PAYMENT, PURCHASE_CREDIT_NOTE, SALES_CREDIT_NOTE, SUPPLIER_PAYMENT

    pay, note = (CUSTOMER_PAYMENT, SALES_CREDIT_NOTE) if kind.sales else (SUPPLIER_PAYMENT, PURCHASE_CREDIT_NOTE)
    return (
        pay.applications.objects.filter(document_id=id).exists()
        or note.applications.objects.filter(document_id=id).exists()
    )


def reverse_entry(entry_id):
    """Post the mirror of an entry: same date, currency, and rate, every
    line's sides swapped (domain §4.4)."""
    original = JournalEntry.objects.get(pk=entry_id)
    if JournalEntry.objects.filter(reverses_entry_id=entry_id).exists():
        raise Conflict(f"journal entry {entry_id} is already reversed")
    if BankStatementLine.objects.filter(journal_line__journal_entry_id=entry_id).exists():
        raise Conflict(f"journal entry {entry_id} has lines matched on a bank statement")
    lines = [
        (l.account_id, l.credit, l.debit, l.base_credit, l.base_debit, l.memo)
        for l in original.lines.order_by("line_no")
    ]
    return new_entry(
        original.entry_date, original.currency_code, lines, memo=f"Reversal of journal entry {entry_id}",
        reference=original.reference, rate=original.exchange_rate, reverses=entry_id, is_closing=original.is_closing,
    )


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def post_payment(kind: PaymentKind, id):
    with transaction.atomic():
        party = "customer" if kind.sales else "supplier"
        p = kind.model.objects.select_for_update().select_related(party).filter(pk=id).first()
        if p is None:
            raise NotFound(f"{kind.noun} not found")
        if p.status != "draft":
            raise Conflict(f"{kind.noun} is {p.status}, not draft")
        if p.amount <= 0:
            raise Unprocessable("amount must be greater than zero")
        cash = getattr(p, kind.cash_account)
        if cash is None:
            raise Unprocessable(f"the payment has no {'deposit' if kind.sales else 'payment'} account")
        control = getattr(getattr(p, party), kind.control_account)
        if control is None:
            raise Unprocessable(f"the {party} has no {'A/R' if kind.sales else 'A/P'} control account")
        period = period_for_posting(p.payment_date)
        rate = rate_for(p.currency_code, p.payment_date)
        base = round4(p.amount * rate)
        if kind.sales:
            lines = [(cash, p.amount, ZERO, base, ZERO, "Cash received"),
                     (control, ZERO, p.amount, ZERO, base, "Accounts receivable")]
        else:
            lines = [(control, p.amount, ZERO, base, ZERO, "Accounts payable"),
                     (cash, ZERO, p.amount, ZERO, base, "Cash paid")]
        memo = "Customer payment" if kind.sales else "Supplier payment"
        entry = new_entry(p.payment_date, p.currency_code, lines, memo=memo, rate=rate, period=period)
        kind.model.objects.filter(pk=id).update(status="posted", journal_entry_id=entry.id, period_id=period.id)
        return entry.id


def unpost_payment(kind: PaymentKind, id):
    with transaction.atomic():
        p = kind.model.objects.select_for_update().filter(pk=id).first()
        if p is None:
            raise NotFound(f"{kind.noun} not found")
        if p.status != "posted" or p.journal_entry_id is None:
            raise Conflict(f"{kind.noun} is not posted")
        reversal = reverse_entry(p.journal_entry_id)
        apps = kind.applications.objects.filter(settler_id=id)
        for fx in apps.exclude(fx_journal_entry_id=None).values_list("fx_journal_entry_id", flat=True):
            reverse_entry(fx)
        apps.delete()
        kind.model.objects.filter(pk=id).update(status="draft", journal_entry_id=None, period_id=None)
        return reversal.id


# ---------------------------------------------------------------------------
# Stock movements
# ---------------------------------------------------------------------------


def post_movement(id, credit_account_id):
    with transaction.atomic():
        sm = StockMovement.objects.select_for_update().select_related("product").filter(pk=id).first()
        if sm is None:
            raise NotFound("stock movement not found")
        if sm.journal_entry_id is not None:
            raise Conflict("stock movement is already posted")
        if sm.movement_type not in ("receipt", "issue"):
            raise Unprocessable(f"{sm.movement_type} movements do not post to the ledger")
        if sm.total_cost == 0:
            raise Unprocessable("the movement has no cost to post")
        product = sm.product
        cost = abs(sm.total_cost)
        if sm.movement_type == "issue":
            if product.cogs_account_id is None or product.inventory_account_id is None:
                raise Unprocessable("the product needs both a COGS and an inventory account")
            lines = [(product.cogs_account_id, cost, ZERO, cost, ZERO, "Cost of goods sold"),
                     (product.inventory_account_id, ZERO, cost, ZERO, cost, "Inventory")]
            memo = "Inventory issue"
        else:
            if product.inventory_account_id is None:
                raise Unprocessable("the product has no inventory account")
            if not credit_account_id or not Account.objects.filter(
                pk=credit_account_id, is_postable=True, is_active=True
            ).exists():
                raise Unprocessable("credit_account_id must name a postable, active account")
            lines = [(product.inventory_account_id, cost, ZERO, cost, ZERO, "Inventory"),
                     (credit_account_id, ZERO, cost, ZERO, cost, "Goods received not invoiced")]
            memo = "Inventory receipt"
        period = period_for_posting(sm.movement_date)
        entry = new_entry(sm.movement_date, base_currency(), lines, memo=memo, rate=1, period=period)
        StockMovement.objects.filter(pk=id).update(journal_entry_id=entry.id, period_id=period.id)
        return entry.id


def unpost_movement(id):
    with transaction.atomic():
        sm = StockMovement.objects.select_for_update().filter(pk=id).first()
        if sm is None:
            raise NotFound("stock movement not found")
        if sm.journal_entry_id is None:
            raise Conflict("stock movement is not posted")
        reversal = reverse_entry(sm.journal_entry_id)
        StockMovement.objects.filter(pk=id).update(journal_entry_id=None, period_id=None)
        return reversal.id


def posted_lines():
    """Journal lines of posted entries."""
    return JournalLine.objects.filter(Q(journal_entry__status="posted"))
