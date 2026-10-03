"""Draft documents: create, replace, delete, and read (spec/api.md §5.9–5.10).

Invoices, bills, credit notes, and orders share the header-and-lines
shape; payments are header only. Line money and header totals are
computed by the database (generated columns and triggers), so this module
writes only the inputs. A PUT replaces the header and the whole line set.
"""

from django.db import transaction
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce

from ..errors import BadRequest, Conflict, NotFound, Unprocessable
from ..values import MONEY, RATE, check_magnitude, fmt4, fmt_date, round4
from .kinds import (
    CUSTOMER_PAYMENT,
    DOCUMENTS,
    PAYMENT_METHODS,
    PURCHASE_CREDIT_NOTE,
    SALES_CREDIT_NOTE,
    SUPPLIER_PAYMENT,
    DocKind,
    OrderKind,
    PaymentKind,
)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


def _check_required(kind, b):
    """The 400 checks of a header-and-lines body (spec/api.md §5.9)."""
    b.required_str(_number(kind))
    b.required_id(kind.party_id)
    b.required_str(_date(kind))
    b.required_str("currency_code")
    for i, line in enumerate(b.list("lines"), 1):
        if not line.str("description"):
            raise BadRequest(f"line {i}: description is required")


def _number(kind):
    return "order_number" if isinstance(kind, OrderKind) else kind.number


def _date(kind):
    return "order_date" if isinstance(kind, OrderKind) else kind.date


def _second_date(kind):
    if isinstance(kind, OrderKind):
        return kind.expected_date
    return "due_date" if kind.has_due_date else None


def _header(kind, b):
    fields = {
        _number(kind): b.str(_number(kind)),
        kind.party_id: b.int(kind.party_id),
        _date(kind): b.date(_date(kind)),
        "currency_code": b.str("currency_code").strip().upper(),
        "reference": b.text("reference"),
        "memo": b.text("memo"),
    }
    second = _second_date(kind)
    if second:
        fields[second] = b.date(second)
        if fields[second] is not None and fields[second] < fields[_date(kind)]:
            raise Unprocessable(f"{second} must not be before {_date(kind)}")
    return fields


def _lines(kind, b):
    """Parse the line set, checking what the database would refuse."""
    lk = kind.lines
    order = isinstance(kind, OrderKind)
    out = []
    for i, line in enumerate(b.list("lines"), 1):
        qty = line.decimal("quantity", MONEY, default=1)
        price = line.decimal(lk.price, MONEY, default=0)
        rate = line.decimal("tax_rate", RATE, default=0)
        if qty == 0 or (order and qty < 0):
            raise Unprocessable(f"line {i}: quantity must be {'greater than' if order else 'other than'} zero")
        if rate < 0:
            raise Unprocessable(f"line {i}: tax_rate must not be negative")
        subtotal = check_magnitude(round4(qty * price), f"line {i} subtotal")
        check_magnitude(subtotal + round4(qty * price * rate / 100), f"line {i} total")
        out.append({
            "line_no": i,
            "product_id": line.int("product_id"),
            "description": line.str("description"),
            "quantity": qty,
            lk.price: price,
            lk.account: line.int(lk.account),
            "tax_code": line.text("tax_code"),
            "tax_rate": rate,
        })
    return out


def _insert_lines(kind, doc_id, lines):
    lk = kind.lines
    lk.model.objects.bulk_create(lk.model(**{lk.parent + "_id": doc_id}, **line) for line in lines)


def _check_number_free(kind, fields, id=None):
    number = _number(kind)
    rows = kind.model.objects.filter(**{number: fields[number]})
    if isinstance(kind, DocKind) and kind.number_per_party:
        rows = rows.filter(**{kind.party_id: fields[kind.party_id]})
    if rows.exclude(pk=id).exists():
        raise Conflict(f"{number} {fields[number]!r} is already used")


def create(kind, b, user=None):
    _check_required(kind, b)
    header = _header(kind, b)
    lines = _lines(kind, b)
    with transaction.atomic():
        _check_number_free(kind, header)
        doc = kind.model.objects.create(created_by=user.id if user else None, **header)
        _insert_lines(kind, doc.id, lines)
    return doc.id


def _lock_draft(kind, id):
    doc = kind.model.objects.select_for_update().filter(pk=id).first()
    if doc is None:
        raise NotFound(f"{kind.noun} not found")
    if doc.status != "draft":
        raise Conflict(f"{kind.noun} is {doc.status}, not draft")
    return doc


def _order_linked(kind, id):
    lk = kind.lines
    return lk.has_order_line and lk.model.objects.filter(**{lk.parent + "_id": id}, order_line_id__isnull=False).exists()


def update(kind, id, b):
    _check_required(kind, b)
    with transaction.atomic():
        _lock_draft(kind, id)
        if _order_linked(kind, id):
            raise Conflict(f"this {kind.noun} was produced from an order and cannot be edited")
        header = _header(kind, b)
        lines = _lines(kind, b)
        _check_number_free(kind, header, id)
        kind.model.objects.filter(pk=id).update(**header)
        kind.lines.model.objects.filter(**{kind.lines.parent + "_id": id}).delete()
        _insert_lines(kind, id, lines)


def delete(kind, id):
    with transaction.atomic():
        _lock_draft(kind, id)
        kind.lines.model.objects.filter(**{kind.lines.parent + "_id": id}).delete()
        kind.model.objects.filter(pk=id).delete()


# ---------------------------------------------------------------------------
# Reads: invoices, bills, credit notes
# ---------------------------------------------------------------------------


def document_json(kind, doc):
    bal = doc.bal
    out = {"id": doc.id, kind.number: getattr(doc, kind.number), kind.party_id: getattr(doc, kind.party_id),
           kind.date: fmt_date(getattr(doc, kind.date))}
    if kind.has_due_date:
        out["due_date"] = fmt_date(doc.due_date)
    out[kind.status_field] = getattr(bal, kind.status_field)
    out.update({
        "currency_code": doc.currency_code, "status": doc.status, "total": fmt4(doc.total),
        "amount_applied": fmt4(bal.amount_applied), "balance": fmt4(bal.balance),
        "journal_entry_id": doc.journal_entry_id, "reference": doc.reference, "memo": doc.memo,
    })
    return out


def _documents(kind):
    return kind.model.objects.select_related("bal", kind.party + "__organization")


def list_documents(kind):
    return [document_json(kind, d) for d in _documents(kind).order_by("-" + kind.date, "-id")]


def get_document(kind, id):
    doc = _documents(kind).filter(pk=id).first()
    if doc is None:
        raise NotFound(f"{kind.noun} not found")
    return doc


def line_json(kind, line):
    lk = kind.lines
    out = {
        "line_no": line.line_no, "product_id": line.product_id, "description": line.description,
        "quantity": fmt4(line.quantity), lk.price: fmt4(getattr(line, lk.price)),
        "tax_code": line.tax_code, "tax_rate": fmt4(line.tax_rate),
        "line_subtotal": fmt4(line.line_subtotal), "tax_amount": fmt4(line.tax_amount),
        "line_total": fmt4(line.line_total), lk.account: getattr(line, lk.account),
    }
    if isinstance(kind, DocKind):
        out["order_line_id"] = line.order_line_id if lk.has_order_line else None
    return out


def lines_of(kind, id):
    lk = kind.lines
    return lk.model.objects.filter(**{lk.parent + "_id": id}).order_by("line_no")


def list_lines(kind, id):
    if isinstance(kind, DocKind):
        get_document(kind, id)
    return [line_json(kind, line) for line in lines_of(kind, id)]


def applications_json(rows, kind):
    """Applications in creation order: the documents a settler is applied to."""
    return [
        {"document_id": a.document_id, "document_number": getattr(a.document, kind.number),
         "amount_applied": fmt4(a.amount_applied)}
        for a in rows.select_related("document").order_by("id")
    ]


def credit_note_applications(kind, id):
    get_document(kind, id)
    target = DOCUMENTS["sales-invoices" if kind.sales else "purchase-bills"]
    return applications_json(kind.applications.objects.filter(settler_id=id), target)


def applications_to(kind, id):
    """What has been applied to an invoice or bill, by payments and credit notes."""
    pay, note = (CUSTOMER_PAYMENT, SALES_CREDIT_NOTE) if kind.sales else (SUPPLIER_PAYMENT, PURCHASE_CREDIT_NOTE)
    out = []
    for a in pay.applications.objects.filter(document_id=id).select_related("settler").order_by("id"):
        out.append({"kind": "payment", "collection": pay.collection, "id": a.settler_id,
                    "label": f"Payment {a.settler_id}", "date": a.settler.payment_date,
                    "status": a.settler.status, "amount_applied": fmt4(a.amount_applied)})
    for a in note.applications.objects.filter(document_id=id).select_related("settler").order_by("id"):
        out.append({"kind": "credit", "collection": note.collection, "id": a.settler_id,
                    "label": f"Credit note {a.settler.credit_note_number}", "date": a.settler.credit_note_date,
                    "status": a.settler.status, "amount_applied": fmt4(a.amount_applied)})
    return out


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def _payment_fields(kind, b):
    b.required_id(kind.party_id)
    b.required_str("payment_date")
    b.required_str("currency_code")
    if not b.str("amount"):
        raise BadRequest("amount is required")
    amount = b.decimal("amount", MONEY)
    if amount <= 0:
        raise Unprocessable("amount must be greater than 0")
    method = b.text("method")
    if method is not None and method not in PAYMENT_METHODS:
        raise Unprocessable("method must be one of " + ", ".join(PAYMENT_METHODS))
    return {
        kind.party_id: b.int(kind.party_id),
        "payment_date": b.date("payment_date"),
        "currency_code": b.str("currency_code").strip().upper(),
        "amount": amount,
        "method": method,
        "reference": b.text("reference"),
        kind.cash_account: b.int(kind.cash_account),
    }


def _payment_required(kind, b):
    b.required_id(kind.party_id)
    b.required_str("payment_date")
    b.required_str("currency_code")
    if not b.str("amount"):
        raise BadRequest("amount is required")


def create_payment(kind: PaymentKind, b, user=None):
    _payment_required(kind, b)
    fields = _payment_fields(kind, b)
    return kind.model.objects.create(created_by=user.id if user else None, **fields).id


def update_payment(kind, id, b):
    _payment_required(kind, b)
    with transaction.atomic():
        _lock_draft(kind, id)
        kind.model.objects.filter(pk=id).update(**_payment_fields(kind, b))


def delete_payment(kind, id):
    with transaction.atomic():
        _lock_draft(kind, id)
        kind.model.objects.filter(pk=id).delete()


def _payments(kind):
    return kind.model.objects.select_related(("customer" if kind.sales else "supplier") + "__organization").annotate(
        applied=Coalesce(Sum("applications__amount_applied"), Value(0), output_field=kind.model._meta.get_field("amount"))
    )


def payment_json(kind, p):
    return {
        "id": p.id, kind.party_id: getattr(p, kind.party_id), "payment_date": fmt_date(p.payment_date),
        kind.cash_account: getattr(p, kind.cash_account), "currency_code": p.currency_code,
        "amount": fmt4(p.amount), "method": p.method, "reference": p.reference, "status": p.status,
        "amount_applied": fmt4(p.applied), "unapplied": fmt4(p.amount - p.applied),
        "journal_entry_id": p.journal_entry_id,
    }


def list_payments(kind):
    return [payment_json(kind, p) for p in _payments(kind).order_by("-payment_date", "-id")]


def get_payment(kind, id):
    p = _payments(kind).filter(pk=id).first()
    if p is None:
        raise NotFound(f"{kind.noun} not found")
    return p


def payment_applications(kind, id):
    get_payment(kind, id)
    return applications_json(kind.applications.objects.filter(settler_id=id), kind.documents)
