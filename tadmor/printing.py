"""Printable documents: one shared A4 layout for invoices, bills, credit
notes, and orders (spec/api.md §5.11, domain §11), and emailing them."""

import re
from decimal import Decimal
from dataclasses import dataclass, field

from django.conf import settings
from django.core.mail import EmailMessage

from . import pdf
from .errors import NotConfigured, Unprocessable
from .models import Address, Country, Organization
from .services import documents, orders
from .services.kinds import DOCUMENTS, ORDERS

PAGE_W, PAGE_H = pdf.A4
MARGIN = 54.0
RIGHT = PAGE_W - MARGIN
TOP = PAGE_H - 54
BOTTOM = 72.0
NUM_X, DESC_X, QTY_X, PRICE_X, TAX_X = MARGIN, MARGIN + 26, 360.0, 432.0, 472.0
DESC_MAX = QTY_X - 60 - DESC_X
GRAY = 0.45

# collection -> (number label, date label, second-date label, party heading,
# applied label, balance label); the title comes from the kind.
LABELS = {
    "sales-invoices": ("Invoice no.", "Invoice date", "Due date", "BILL TO", "Amount paid", "Balance due"),
    "purchase-bills": ("Bill no.", "Bill date", "Due date", "SUPPLIER", "Amount paid", "Balance due"),
    "sales-credit-notes": ("Credit note no.", "Credit note date", None, "CREDIT TO", "Amount applied", "Unapplied"),
    "purchase-credit-notes": ("Credit note no.", "Credit note date", None, "SUPPLIER", "Amount applied", "Unapplied"),
    "sales-orders": ("Order no.", "Order date", "Expected ship", "CUSTOMER", None, None),
    "purchase-orders": ("Order no.", "Order date", "Expected receipt", "SUPPLIER", None, None),
}
TITLES = {"purchase-credit-notes": "Supplier Credit"}


@dataclass
class Party:
    name: str
    legal: str = None
    tax_id: str = None
    address: list = field(default_factory=list)


@dataclass
class Printable:
    kind: str
    number: str
    status: str
    currency: str
    meta: list
    party_label: str
    party: Party
    seller: Party
    unit_label: str
    subtotal: object
    tax_total: object
    total: object
    applied: object
    balance: object
    applied_label: str
    balance_label: str
    reference: str
    memo: str
    lines: list
    email: str  # the counterparty's address on file


def _party(org):
    a = Address.objects.filter(organization_id=org.id).order_by("id").first()
    lines = []
    if a:
        lines = [l for l in (a.line1, a.line2) if l]
        city = ", ".join(x for x in (a.city, a.region) if x)
        if a.postal_code:
            city = f"{city} {a.postal_code}".strip()
        if city:
            lines.append(city)
        country = Country.objects.filter(pk=a.country_code).first()
        lines.append(country.name if country else a.country_code)
    return Party(org.name, org.legal_name, org.tax_id, lines)


def load(collection, id):
    """Gather what the printed form of a document shows."""
    number_label, date_label, second_label, party_label, applied_label, balance_label = LABELS[collection]
    if collection in DOCUMENTS:
        kind = DOCUMENTS[collection]
        doc = documents.get_document(kind, id)
        lines = documents.list_lines(kind, id)
        number, date = getattr(doc, kind.number), getattr(doc, kind.date)
        second = doc.due_date if kind.has_due_date else None
        applied, balance = doc.bal.amount_applied, doc.bal.balance
        org = getattr(doc, kind.party).organization
        unit, title = kind.lines.price, TITLES.get(collection, kind.label)
    else:
        kind = ORDERS[collection]
        doc = orders.get_order(kind, id)
        lines = orders.order_lines(kind, id)
        number, date, second = doc.order_number, doc.order_date, getattr(doc, kind.expected_date)
        applied = balance = None
        org = (doc.customer if kind.sales else doc.supplier).organization
        unit, title = kind.lines.price, kind.label
    meta = [(number_label, number), (date_label, date.isoformat())]
    if second_label and second is not None:
        meta.append((second_label, second.isoformat()))
    meta.append(("Currency", doc.currency_code))
    me = Organization.objects.filter(is_self=True).first()
    return Printable(
        kind=title, number=number, status=doc.status, currency=doc.currency_code, meta=meta,
        party_label=party_label, party=_party(org), seller=_party(me) if me else None,
        unit_label="UNIT PRICE" if unit == "unit_price" else "UNIT COST",
        subtotal=doc.subtotal, tax_total=doc.tax_total, total=doc.total, applied=applied, balance=balance,
        applied_label=applied_label, balance_label=balance_label, reference=doc.reference, memo=doc.memo,
        lines=[(l["line_no"], l["description"], l["quantity"], l[unit], l["tax_rate"], l["line_subtotal"]) for l in lines],
        email=org.email,
    )


def filename(collection, number):
    prefix = (DOCUMENTS.get(collection) or ORDERS[collection]).pdf_prefix
    return f"{prefix}-{re.sub(r'[^A-Za-z0-9._-]', '-', number)}.pdf"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def amount(v):
    """1234.5 -> "1,234.50": grouped, at least two decimals, none lost."""
    s = f"{Decimal(v):f}"
    sign = "-" if s.startswith("-") else ""
    whole, _, frac = s.lstrip("-").partition(".")
    frac = frac.rstrip("0").ljust(2, "0")
    return f"{sign}{int(whole):,}.{frac}"


def qty(v):
    s = f"{v}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _truncate(font, size, limit, s):
    if pdf.width(font, size, s) <= limit:
        return s
    while s and pdf.width(font, size, s + "…") > limit:
        s = s[:-1]
    return s + "…"


def _wrap(font, size, limit, s):
    lines, line = [], ""
    for word in s.split():
        candidate = f"{line} {word}" if line else word
        if line and pdf.width(font, size, candidate) > limit:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + [line] if line else lines


def _party_text(page, x, y, name_size, p):
    page.text(pdf.BOLD, name_size, x, y, p.name)
    y -= 13
    if p.legal and p.legal != p.name:
        page.text(pdf.REGULAR, 9, x, y, p.legal, GRAY)
        y -= 12
    for line in p.address:
        page.text(pdf.REGULAR, 9, x, y, line, GRAY)
        y -= 12
    if p.tax_id:
        page.text(pdf.REGULAR, 9, x, y, "Tax ID: " + p.tax_id, GRAY)
        y -= 12
    return y


def _table_header(page, y, unit_label):
    for x, label, right in ((NUM_X, "#", False), (DESC_X, "DESCRIPTION", False), (QTY_X, "QTY", True),
                            (PRICE_X, unit_label, True), (TAX_X, "TAX %", True), (RIGHT, "AMOUNT", True)):
        page.text(pdf.BOLD, 8, x - (pdf.width(pdf.BOLD, 8, label) if right else 0), y, label, GRAY)
    y -= 6
    page.line(MARGIN, y, RIGHT, y, 0.8, 0.2)
    return y - 14


def render(d: Printable):
    doc = pdf.Document()
    page = doc.add_page()
    title = d.kind.upper()
    if d.status in ("draft", "void", "cancelled"):
        title = f"{d.status.upper()} {title}"
    page.text(pdf.BOLD, 20, RIGHT - pdf.width(pdf.BOLD, 20, title), TOP - 6, title)

    meta_y = TOP - 36
    for label, value in d.meta:
        page.text(pdf.REGULAR, 9, RIGHT - 140, meta_y, label, GRAY)
        page.text(pdf.REGULAR, 9, RIGHT - pdf.width(pdf.REGULAR, 9, value), meta_y, value)
        meta_y -= 13

    y = TOP - 6
    if d.seller:
        y = _party_text(page, MARGIN, y, 11, d.seller)
    y = min(y, meta_y) - 28
    page.text(pdf.BOLD, 8, MARGIN, y, d.party_label, GRAY)
    y = _party_text(page, MARGIN, y - 14, 10, d.party) - 24

    y = _table_header(page, y, d.unit_label)
    for no, desc, q, unit, rate, subtotal in d.lines:
        if y < BOTTOM + 20:
            page = doc.add_page()
            y = _table_header(page, TOP, d.unit_label)
        page.text(pdf.REGULAR, 9, NUM_X, y, str(no), GRAY)
        page.text(pdf.REGULAR, 9, DESC_X, y, _truncate(pdf.REGULAR, 9, DESC_MAX, desc))
        for x, s in ((QTY_X, qty(q)), (PRICE_X, amount(unit)), (TAX_X, qty(rate)), (RIGHT, amount(subtotal))):
            page.text(pdf.REGULAR, 9, x - pdf.width(pdf.REGULAR, 9, s), y, s)
        y -= 6
        page.line(MARGIN, y, RIGHT, y, 0.4, 0.9)
        y -= 12

    if y < BOTTOM + 110:
        page = doc.add_page()
        y = TOP
    y -= 8
    totals_x = 400.0

    def total(label, value, font):
        nonlocal y
        page.text(font, 9, totals_x, y, label)
        page.text(font, 9, RIGHT - pdf.width(font, 9, value), y, value)
        y -= 14

    total("Subtotal", amount(d.subtotal), pdf.REGULAR)
    total("Tax", amount(d.tax_total), pdf.REGULAR)
    page.line(totals_x, y + 9, RIGHT, y + 9, 0.8, 0.2)
    y -= 2
    total("Total", f"{d.currency} {amount(d.total)}", pdf.BOLD)
    if d.applied_label and d.applied:
        total(d.applied_label, amount(d.applied), pdf.REGULAR)
        total(d.balance_label, f"{d.currency} {amount(d.balance)}", pdf.BOLD)

    note_y = y - 14
    if d.reference:
        page.text(pdf.REGULAR, 9, MARGIN, note_y, "Reference: " + d.reference, GRAY)
        note_y -= 13
    for line in _wrap(pdf.REGULAR, 9, RIGHT - MARGIN, d.memo or ""):
        page.text(pdf.REGULAR, 9, MARGIN, note_y, line, GRAY)
        note_y -= 13

    for i, p in enumerate(doc.pages, 1):
        footer = f"{d.kind} {d.number}  ·  Page {i} of {len(doc.pages)}"
        p.text(pdf.REGULAR, 8, (PAGE_W - pdf.width(pdf.REGULAR, 8, footer)) / 2, 40, footer, GRAY)
    return doc.bytes()


def pdf_for(collection, id):
    d = load(collection, id)
    return render(d), filename(collection, d.number)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def email(collection, id, to):
    """Send a document's PDF to `to`, or to the counterparty's address on file."""
    d = load(collection, id)
    if not to:
        if not d.email:
            raise Unprocessable("this counterparty has no email address on file; supply a recipient or set one on the organization")
        to = [d.email]
    if not settings.SMTP_ADDR:
        raise NotConfigured("email sending is not configured")
    label = (DOCUMENTS.get(collection) or ORDERS[collection]).label
    msg = EmailMessage(subject=f"{label} {d.number}", body=f"Please find attached {label.lower()} {d.number}.",
                       to=to)
    msg.attach(filename(collection, d.number), render(d), "application/pdf")
    msg.send()
    return to
