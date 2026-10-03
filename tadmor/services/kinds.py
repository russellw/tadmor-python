"""Descriptors for the document kinds that share code paths.

Invoices, bills, and both kinds of credit note share one lifecycle and one
line shape; sales and purchase orders share that line shape too; customer
and supplier payments mirror each other. Each Kind records the names that
differ, so the services are written once (spec/api.md §5.9–5.10).
"""

from dataclasses import dataclass, field

from .. import models as m


@dataclass(frozen=True)
class LineKind:
    model: type
    parent: str  # the line's FK to its document, e.g. "invoice"
    price: str  # "unit_price" or "unit_cost"
    account: str  # "revenue_account_id" or "expense_account_id"
    has_order_line: bool = False


SALES_LINE = dict(price="unit_price", account="revenue_account_id")
PURCHASE_LINE = dict(price="unit_cost", account="expense_account_id")


@dataclass(frozen=True)
class DocKind:
    collection: str  # URL collection, e.g. "sales-invoices"
    label: str  # "Invoice", for titles, PDFs, and email subjects
    noun: str  # "invoice", in messages
    model: type
    balance: type  # the *_balances view
    lines: LineKind
    sales: bool  # customer side (A/R) rather than supplier side (A/P)
    credit: bool  # a credit note: posts on the opposite sides
    number: str
    date: str
    has_due_date: bool
    status_field: str  # "payment_status" or "application_status"
    pdf_prefix: str
    number_per_party: bool  # bill numbers are the supplier's own, unique per supplier
    applications: type = None  # for credit notes: the applications they make

    @property
    def party(self):
        return "customer" if self.sales else "supplier"

    @property
    def party_id(self):
        return self.party + "_id"

    @property
    def control_account(self):
        return "ar_account_id" if self.sales else "ap_account_id"

    @property
    def control_debit(self):
        """Whether posting debits the party's control account."""
        return self.sales != self.credit


SALES_INVOICE = DocKind(
    "sales-invoices", "Invoice", "invoice", m.SalesInvoice, m.SalesInvoiceBalance,
    LineKind(m.SalesInvoiceLine, "invoice", has_order_line=True, **SALES_LINE),
    sales=True, credit=False, number="invoice_number", date="invoice_date", has_due_date=True,
    status_field="payment_status", pdf_prefix="invoice", number_per_party=False,
)
PURCHASE_BILL = DocKind(
    "purchase-bills", "Bill", "bill", m.PurchaseBill, m.PurchaseBillBalance,
    LineKind(m.PurchaseBillLine, "bill", has_order_line=True, **PURCHASE_LINE),
    sales=False, credit=False, number="bill_number", date="bill_date", has_due_date=True,
    status_field="payment_status", pdf_prefix="bill", number_per_party=True,
)
SALES_CREDIT_NOTE = DocKind(
    "sales-credit-notes", "Credit Note", "credit note", m.SalesCreditNote, m.SalesCreditNoteBalance,
    LineKind(m.SalesCreditNoteLine, "credit_note", **SALES_LINE),
    sales=True, credit=True, number="credit_note_number", date="credit_note_date", has_due_date=False,
    status_field="application_status", pdf_prefix="credit-note", number_per_party=False,
    applications=m.SalesCreditApplication,
)
PURCHASE_CREDIT_NOTE = DocKind(
    "purchase-credit-notes", "Credit Note", "supplier credit", m.PurchaseCreditNote, m.PurchaseCreditNoteBalance,
    LineKind(m.PurchaseCreditNoteLine, "credit_note", **PURCHASE_LINE),
    sales=False, credit=True, number="credit_note_number", date="credit_note_date", has_due_date=False,
    status_field="application_status", pdf_prefix="supplier-credit", number_per_party=True,
    applications=m.PurchaseCreditApplication,
)

DOCUMENTS = {k.collection: k for k in (SALES_INVOICE, PURCHASE_BILL, SALES_CREDIT_NOTE, PURCHASE_CREDIT_NOTE)}


@dataclass(frozen=True)
class OrderKind:
    collection: str
    label: str
    noun: str
    model: type
    fulfilment: type  # header statuses view
    line_fulfilment: type
    lines: LineKind
    sales: bool
    expected_date: str
    billed: str  # "invoiced" or "billed"
    moved: str  # "shipped" or "received"
    bill_verb: str  # "invoice" or "bill"
    move_verb: str  # "ship" or "receive"
    pdf_prefix: str
    document: DocKind  # what invoicing/billing produces

    @property
    def party_id(self):
        return ("customer" if self.sales else "supplier") + "_id"


SALES_ORDER = OrderKind(
    "sales-orders", "Sales Order", "sales order", m.SalesOrder, m.SalesOrderFulfilment, m.SalesOrderLineFulfilment,
    LineKind(m.SalesOrderLine, "order", **SALES_LINE),
    sales=True, expected_date="expected_ship_date", billed="invoiced", moved="shipped",
    bill_verb="invoice", move_verb="ship",
    pdf_prefix="sales-order", document=SALES_INVOICE,
)
PURCHASE_ORDER = OrderKind(
    "purchase-orders", "Purchase Order", "purchase order", m.PurchaseOrder, m.PurchaseOrderFulfilment,
    m.PurchaseOrderLineFulfilment,
    LineKind(m.PurchaseOrderLine, "order", **PURCHASE_LINE),
    sales=False, expected_date="expected_receipt_date", billed="billed", moved="received",
    bill_verb="bill", move_verb="receive",
    pdf_prefix="purchase-order", document=PURCHASE_BILL,
)

ORDERS = {k.collection: k for k in (SALES_ORDER, PURCHASE_ORDER)}


@dataclass(frozen=True)
class PaymentKind:
    collection: str
    noun: str
    model: type
    applications: type
    sales: bool
    cash_account: str  # "deposit_account_id" or "payment_account_id"
    documents: DocKind = field(default=None)

    @property
    def party_id(self):
        return ("customer" if self.sales else "supplier") + "_id"

    @property
    def control_account(self):
        return "ar_account_id" if self.sales else "ap_account_id"


CUSTOMER_PAYMENT = PaymentKind(
    "customer-payments", "customer payment", m.CustomerPayment, m.PaymentApplication,
    sales=True, cash_account="deposit_account_id", documents=SALES_INVOICE,
)
SUPPLIER_PAYMENT = PaymentKind(
    "supplier-payments", "supplier payment", m.SupplierPayment, m.BillApplication,
    sales=False, cash_account="payment_account_id", documents=PURCHASE_BILL,
)

PAYMENTS = {k.collection: k for k in (CUSTOMER_PAYMENT, SUPPLIER_PAYMENT)}

PAYMENT_METHODS = ("cash", "check", "card", "transfer", "other")
