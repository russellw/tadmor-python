"""Applying payments and credit notes to open documents (domain §5, §7.3).

Auto-apply spreads a settling document's unapplied remainder across the
party's posted documents in the same currency, oldest first. An
application posts nothing, except when the two documents' rates value the
applied amount differently in the base currency: then an FX entry moves
the difference between the control account and the FX gain/loss account.
The schema refuses over-application and mismatched parties or currencies.
"""

from django.db import transaction
from django.db.models import Sum

from ..errors import Conflict, NotFound, Unprocessable
from ..models import GLSettings, JournalEntry
from ..values import ZERO, fmt4, round4
from .kinds import CUSTOMER_PAYMENT, PURCHASE_CREDIT_NOTE, SALES_CREDIT_NOTE, SUPPLIER_PAYMENT, DocKind
from .posting import new_entry

# For each kind of settler: (kind, the documents it settles, its amount
# field, its date field).
SETTLERS = {
    CUSTOMER_PAYMENT.collection: (CUSTOMER_PAYMENT, CUSTOMER_PAYMENT.documents, "amount", "payment_date"),
    SUPPLIER_PAYMENT.collection: (SUPPLIER_PAYMENT, SUPPLIER_PAYMENT.documents, "amount", "payment_date"),
    SALES_CREDIT_NOTE.collection: (SALES_CREDIT_NOTE, CUSTOMER_PAYMENT.documents, "total", "credit_note_date"),
    PURCHASE_CREDIT_NOTE.collection: (PURCHASE_CREDIT_NOTE, SUPPLIER_PAYMENT.documents, "total", "credit_note_date"),
}


def _settled(target: DocKind, doc_id):
    """Everything applied to a document, from payments and credit notes alike."""
    pay, note = (CUSTOMER_PAYMENT, SALES_CREDIT_NOTE) if target.sales else (SUPPLIER_PAYMENT, PURCHASE_CREDIT_NOTE)
    total = ZERO
    for model in (pay.applications, note.applications):
        total += model.objects.filter(document_id=doc_id).aggregate(s=Sum("amount_applied"))["s"] or ZERO
    return total


def apply(collection, id):
    kind, target, amount_field, date_field = SETTLERS[collection]
    party_id = kind.party_id
    with transaction.atomic():
        settler = kind.model.objects.select_for_update().filter(pk=id).first()
        if settler is None:
            raise NotFound(f"{kind.noun} not found")
        if settler.status != "posted":
            raise Conflict(f"the {kind.noun} is not posted")
        applied = kind.applications.objects.filter(settler_id=id).aggregate(s=Sum("amount_applied"))["s"] or ZERO
        remaining = getattr(settler, amount_field) - applied

        open_docs = (
            target.model.objects.select_for_update()
            .filter(**{party_id: getattr(settler, party_id)}, currency_code=settler.currency_code, status="posted")
            .order_by(target.date, "id")
        )
        created = []
        for doc in open_docs:
            if remaining <= 0:
                break
            available = doc.total - _settled(target, doc.id)
            if available <= 0:
                continue
            amount = min(available, remaining)
            app = kind.applications.objects.create(settler_id=id, document_id=doc.id, amount_applied=amount)
            created.append((app, doc))
            remaining -= amount

        _post_fx(kind, settler, date_field, created)
        return [{"document_id": doc.id, "amount_applied": fmt4(app.amount_applied)} for app, doc in created]


def _post_fx(kind, settler, date_field, created):
    """Post the realized FX entry for each application that needs one."""
    if not created:
        return
    settler_rate = JournalEntry.objects.get(pk=settler.journal_entry_id).exchange_rate
    doc_rates = dict(
        JournalEntry.objects.filter(pk__in=[doc.journal_entry_id for _, doc in created]).values_list("id", "exchange_rate")
    )
    party = getattr(settler, "customer" if kind.sales else "supplier")
    control = getattr(party, kind.control_account)
    settings = GLSettings.objects.get(pk=1)
    date = getattr(settler, date_field)
    for app, doc in created:
        diff = round4(app.amount_applied * settler_rate) - round4(app.amount_applied * doc_rates[doc.journal_entry_id])
        if diff == 0:
            continue
        if settings.fx_gain_loss_account_id is None:
            raise Unprocessable("a realized exchange difference arises, but no FX gain/loss account is configured")
        # A/R side: a positive difference debits A/R (a gain); A/P mirrors it.
        v = diff if kind.sales else -diff
        lines = [
            (control, max(v, ZERO), max(-v, ZERO), max(v, ZERO), max(-v, ZERO), "Settlement revaluation"),
            (settings.fx_gain_loss_account_id, max(-v, ZERO), max(v, ZERO), max(-v, ZERO), max(v, ZERO), "Exchange gain (loss)"),
        ]
        number = getattr(doc, "invoice_number" if kind.sales else "bill_number")
        noun = "invoice" if kind.sales else "bill"
        entry = new_entry(date, settings.base_currency, lines, rate=1, reference=number,
                          memo=f"Exchange difference on settlement of {noun} {number}")
        kind.applications.objects.filter(pk=app.pk).update(fx_journal_entry_id=entry.id)
