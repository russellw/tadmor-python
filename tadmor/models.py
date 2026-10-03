"""Django models over the shared schema (db/migrations, owned by tadmor).

Every model is unmanaged: the tables, views, triggers, and generated
columns come from the shared SQL migrations, and Django never creates or
alters them. Columns the application never reads (created_at, created_by,
and the like) are left out, so the database fills in their defaults.
Generated columns are declared as GeneratedField so they are read but
never written. Views get models of their own, keyed on the row they
describe.
"""

from decimal import Decimal

from django.db import models
from django.db.models import F, Func, Value

DO_NOTHING = models.DO_NOTHING


def money(**kw):
    return models.DecimalField(max_digits=19, decimal_places=4, **kw)


def fk(to, column, **kw):
    return models.ForeignKey(to, DO_NOTHING, db_column=column, related_name="+", **kw)


class Round4(Func):
    function = "round"
    template = "%(function)s(%(expressions)s, 4)"


def generated(expression):
    return models.GeneratedField(expression=expression, output_field=money(), db_persist=True)


def line_money(price):
    """The generated subtotal/tax/total columns of a document line (domain §2)."""
    subtotal = Round4(F("quantity") * F(price))
    tax = Round4(F("quantity") * F(price) * F("tax_rate") * Value(Decimal("0.01")))
    return generated(subtotal), generated(tax), generated(subtotal + tax)


class Unmanaged(models.Model):
    class Meta:
        abstract = True
        managed = False


# ---------------------------------------------------------------------------
# Reference data, users, organizations
# ---------------------------------------------------------------------------


class Country(Unmanaged):
    code = models.CharField(max_length=2, primary_key=True)
    name = models.TextField()

    class Meta(Unmanaged.Meta):
        db_table = "countries"


class Currency(Unmanaged):
    code = models.CharField(max_length=3, primary_key=True)
    name = models.TextField()
    minor_unit = models.SmallIntegerField()

    class Meta(Unmanaged.Meta):
        db_table = "currencies"


class User(Unmanaged):
    email = models.TextField(unique=True)  # citext
    full_name = models.TextField()
    password_hash = models.TextField()
    is_active = models.BooleanField(default=True)
    is_admin = models.BooleanField(default=False)

    class Meta(Unmanaged.Meta):
        db_table = "users"


class Session(Unmanaged):
    token_hash = models.BinaryField(primary_key=True)
    user = fk(User, "user_id")
    expires_at = models.DateTimeField()

    class Meta(Unmanaged.Meta):
        db_table = "sessions"


class Organization(Unmanaged):
    name = models.TextField()
    legal_name = models.TextField(null=True)
    tax_id = models.TextField(null=True)
    country_code = models.CharField(max_length=2, null=True)
    default_currency = models.CharField(max_length=3, null=True)
    email = models.TextField(null=True)
    is_self = models.BooleanField(default=False)

    class Meta(Unmanaged.Meta):
        db_table = "organizations"


class Address(Unmanaged):
    organization = fk(Organization, "organization_id")
    label = models.TextField(null=True)
    line1 = models.TextField()
    line2 = models.TextField(null=True)
    city = models.TextField()
    region = models.TextField(null=True)
    postal_code = models.TextField(null=True)
    country_code = models.CharField(max_length=2)

    class Meta(Unmanaged.Meta):
        db_table = "addresses"


# ---------------------------------------------------------------------------
# General ledger
# ---------------------------------------------------------------------------


class Account(Unmanaged):
    code = models.TextField(unique=True)
    name = models.TextField()
    account_type = models.TextField()
    parent_id = models.IntegerField(null=True)
    currency_code = models.CharField(max_length=3, null=True)
    is_postable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    is_cash = models.BooleanField(default=False)
    cash_flow_activity = models.TextField(default="operating")

    class Meta(Unmanaged.Meta):
        db_table = "accounts"


class FiscalYear(Unmanaged):
    name = models.TextField(unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.TextField(default="open")
    closing_entry_id = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "fiscal_years"


class AccountingPeriod(Unmanaged):
    fiscal_year = fk(FiscalYear, "fiscal_year_id")
    name = models.TextField()
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.TextField(default="open")

    class Meta(Unmanaged.Meta):
        db_table = "accounting_periods"


class JournalEntry(Unmanaged):
    entry_date = models.DateField()
    period = fk(AccountingPeriod, "period_id")
    currency_code = models.CharField(max_length=3)
    exchange_rate = models.DecimalField(max_digits=19, decimal_places=8, default=1)
    reference = models.TextField(null=True)
    memo = models.TextField(null=True)
    status = models.TextField(default="draft")
    posted_at = models.DateTimeField(null=True)
    created_by = models.IntegerField(null=True)
    reverses_entry_id = models.IntegerField(null=True)
    is_closing = models.BooleanField(default=False)

    class Meta(Unmanaged.Meta):
        db_table = "journal_entries"


class JournalLine(Unmanaged):
    journal_entry = models.ForeignKey(JournalEntry, DO_NOTHING, db_column="journal_entry_id", related_name="lines")
    line_no = models.IntegerField()
    account = fk(Account, "account_id")
    debit = money(default=0)
    credit = money(default=0)
    base_debit = money(default=0)
    base_credit = money(default=0)
    memo = models.TextField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "journal_lines"


class GLSettings(Unmanaged):
    base_currency = models.CharField(max_length=3)
    fx_gain_loss_account_id = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "gl_settings"


class ExchangeRate(Unmanaged):
    pk = models.CompositePrimaryKey("currency_code", "rate_date")
    currency_code = models.CharField(max_length=3)
    rate_date = models.DateField()
    rate = models.DecimalField(max_digits=19, decimal_places=8)

    class Meta(Unmanaged.Meta):
        db_table = "exchange_rates"


class TrialBalance(Unmanaged):
    account = models.OneToOneField(Account, DO_NOTHING, primary_key=True, db_column="account_id")
    code = models.TextField()
    name = models.TextField()
    account_type = models.TextField()
    total_debit = money()
    total_credit = money()
    balance = money()

    class Meta(Unmanaged.Meta):
        db_table = "trial_balance"


# ---------------------------------------------------------------------------
# Lookups, catalog, parties
# ---------------------------------------------------------------------------


class PaymentTerm(Unmanaged):
    code = models.TextField(primary_key=True)
    name = models.TextField()
    due_days = models.IntegerField(default=0)

    class Meta(Unmanaged.Meta):
        db_table = "payment_terms"


class TaxCode(Unmanaged):
    code = models.TextField(primary_key=True)
    name = models.TextField()
    rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    tax_account_id = models.IntegerField(null=True)
    is_active = models.BooleanField(default=True)

    class Meta(Unmanaged.Meta):
        db_table = "tax_codes"


class Product(Unmanaged):
    sku = models.TextField(unique=True)
    name = models.TextField()
    description = models.TextField(null=True)
    unit_price = money(default=0)
    currency_code = models.CharField(max_length=3, null=True)
    revenue_account_id = models.IntegerField(null=True)
    tax_code = models.TextField(null=True)
    track_inventory = models.BooleanField(default=False)
    inventory_account_id = models.IntegerField(null=True)
    cogs_account_id = models.IntegerField(null=True)
    is_active = models.BooleanField(default=True)

    class Meta(Unmanaged.Meta):
        db_table = "products"


class Customer(Unmanaged):
    organization = fk(Organization, "organization_id")
    customer_number = models.TextField(null=True)
    ar_account_id = models.IntegerField(null=True)
    payment_terms_code = models.TextField(null=True)
    currency_code = models.CharField(max_length=3, null=True)
    tax_code = models.TextField(null=True)
    credit_limit = money(null=True)
    is_active = models.BooleanField(default=True)

    class Meta(Unmanaged.Meta):
        db_table = "customers"


class Supplier(Unmanaged):
    organization = fk(Organization, "organization_id")
    supplier_number = models.TextField(null=True)
    ap_account_id = models.IntegerField(null=True)
    payment_terms_code = models.TextField(null=True)
    currency_code = models.CharField(max_length=3, null=True)
    tax_code = models.TextField(null=True)
    is_active = models.BooleanField(default=True)

    class Meta(Unmanaged.Meta):
        db_table = "suppliers"


class Warehouse(Unmanaged):
    code = models.TextField(unique=True)
    name = models.TextField()
    address_id = models.IntegerField(null=True)
    is_active = models.BooleanField(default=True)

    class Meta(Unmanaged.Meta):
        db_table = "warehouses"


# ---------------------------------------------------------------------------
# Subledger documents: invoices, bills, credit notes
# ---------------------------------------------------------------------------


class Document(Unmanaged):
    """Fields shared by invoices, bills, and both kinds of credit note."""

    currency_code = models.CharField(max_length=3)
    status = models.TextField(default="draft")
    period_id = models.IntegerField(null=True)
    journal_entry_id = models.IntegerField(null=True)
    subtotal = money(default=0)
    tax_total = money(default=0)
    total = money(default=0)
    reference = models.TextField(null=True)
    memo = models.TextField(null=True)
    created_by = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        abstract = True


class DocumentLine(Unmanaged):
    line_no = models.IntegerField()
    product_id = models.IntegerField(null=True)
    description = models.TextField()
    quantity = money(default=1)
    tax_code = models.TextField(null=True)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)

    class Meta(Unmanaged.Meta):
        abstract = True


class SalesInvoice(Document):
    invoice_number = models.TextField(unique=True)
    customer = fk(Customer, "customer_id")
    invoice_date = models.DateField()
    due_date = models.DateField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "sales_invoices"


class SalesInvoiceLine(DocumentLine):
    invoice = models.ForeignKey(SalesInvoice, DO_NOTHING, db_column="invoice_id", related_name="lines")
    unit_price = money(default=0)
    revenue_account_id = models.IntegerField(null=True)
    order_line_id = models.IntegerField(null=True)
    line_subtotal, tax_amount, line_total = line_money("unit_price")

    class Meta(Unmanaged.Meta):
        db_table = "sales_invoice_lines"


class PurchaseBill(Document):
    bill_number = models.TextField()
    supplier = fk(Supplier, "supplier_id")
    bill_date = models.DateField()
    due_date = models.DateField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "purchase_bills"


class PurchaseBillLine(DocumentLine):
    bill = models.ForeignKey(PurchaseBill, DO_NOTHING, db_column="bill_id", related_name="lines")
    unit_cost = money(default=0)
    expense_account_id = models.IntegerField(null=True)
    order_line_id = models.IntegerField(null=True)
    line_subtotal, tax_amount, line_total = line_money("unit_cost")

    class Meta(Unmanaged.Meta):
        db_table = "purchase_bill_lines"


class SalesCreditNote(Document):
    credit_note_number = models.TextField(unique=True)
    customer = fk(Customer, "customer_id")
    credit_note_date = models.DateField()

    class Meta(Unmanaged.Meta):
        db_table = "sales_credit_notes"


class SalesCreditNoteLine(DocumentLine):
    credit_note = models.ForeignKey(SalesCreditNote, DO_NOTHING, db_column="credit_note_id", related_name="lines")
    unit_price = money(default=0)
    revenue_account_id = models.IntegerField(null=True)
    line_subtotal, tax_amount, line_total = line_money("unit_price")

    class Meta(Unmanaged.Meta):
        db_table = "sales_credit_note_lines"


class PurchaseCreditNote(Document):
    credit_note_number = models.TextField()
    supplier = fk(Supplier, "supplier_id")
    credit_note_date = models.DateField()

    class Meta(Unmanaged.Meta):
        db_table = "purchase_credit_notes"


class PurchaseCreditNoteLine(DocumentLine):
    credit_note = models.ForeignKey(PurchaseCreditNote, DO_NOTHING, db_column="credit_note_id", related_name="lines")
    unit_cost = money(default=0)
    expense_account_id = models.IntegerField(null=True)
    line_subtotal, tax_amount, line_total = line_money("unit_cost")

    class Meta(Unmanaged.Meta):
        db_table = "purchase_credit_note_lines"


class Balance(Unmanaged):
    """A row of one of the *_balances views."""

    status = models.TextField()
    total = money()
    amount_applied = money()
    balance = money()

    class Meta(Unmanaged.Meta):
        abstract = True


class SalesInvoiceBalance(Balance):
    invoice = models.OneToOneField(SalesInvoice, DO_NOTHING, primary_key=True, db_column="invoice_id", related_name="bal")
    payment_status = models.TextField()

    class Meta(Unmanaged.Meta):
        db_table = "sales_invoice_balances"


class PurchaseBillBalance(Balance):
    bill = models.OneToOneField(PurchaseBill, DO_NOTHING, primary_key=True, db_column="bill_id", related_name="bal")
    payment_status = models.TextField()

    class Meta(Unmanaged.Meta):
        db_table = "purchase_bill_balances"


class SalesCreditNoteBalance(Balance):
    credit_note = models.OneToOneField(SalesCreditNote, DO_NOTHING, primary_key=True, db_column="credit_note_id", related_name="bal")
    application_status = models.TextField()

    class Meta(Unmanaged.Meta):
        db_table = "sales_credit_note_balances"


class PurchaseCreditNoteBalance(Balance):
    credit_note = models.OneToOneField(PurchaseCreditNote, DO_NOTHING, primary_key=True, db_column="credit_note_id", related_name="bal")
    application_status = models.TextField()

    class Meta(Unmanaged.Meta):
        db_table = "purchase_credit_note_balances"


# ---------------------------------------------------------------------------
# Payments and applications
# ---------------------------------------------------------------------------


class Payment(Unmanaged):
    payment_date = models.DateField()
    currency_code = models.CharField(max_length=3)
    amount = money()
    method = models.TextField(null=True)
    reference = models.TextField(null=True)
    status = models.TextField(default="draft")
    period_id = models.IntegerField(null=True)
    journal_entry_id = models.IntegerField(null=True)
    created_by = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        abstract = True


class CustomerPayment(Payment):
    customer = fk(Customer, "customer_id")
    deposit_account_id = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "customer_payments"


class SupplierPayment(Payment):
    supplier = fk(Supplier, "supplier_id")
    payment_account_id = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "supplier_payments"


class Application(Unmanaged):
    amount_applied = money()
    fx_journal_entry_id = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        abstract = True


class PaymentApplication(Application):
    settler = models.ForeignKey(CustomerPayment, DO_NOTHING, db_column="payment_id", related_name="applications")
    document = fk(SalesInvoice, "invoice_id")

    class Meta(Unmanaged.Meta):
        db_table = "payment_applications"


class BillApplication(Application):
    settler = models.ForeignKey(SupplierPayment, DO_NOTHING, db_column="payment_id", related_name="applications")
    document = fk(PurchaseBill, "bill_id")

    class Meta(Unmanaged.Meta):
        db_table = "bill_applications"


class SalesCreditApplication(Application):
    settler = models.ForeignKey(SalesCreditNote, DO_NOTHING, db_column="credit_note_id", related_name="applications")
    document = fk(SalesInvoice, "invoice_id")

    class Meta(Unmanaged.Meta):
        db_table = "sales_credit_applications"


class PurchaseCreditApplication(Application):
    settler = models.ForeignKey(PurchaseCreditNote, DO_NOTHING, db_column="credit_note_id", related_name="applications")
    document = fk(PurchaseBill, "bill_id")

    class Meta(Unmanaged.Meta):
        db_table = "purchase_credit_applications"


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class Order(Unmanaged):
    order_number = models.TextField(unique=True)
    order_date = models.DateField()
    currency_code = models.CharField(max_length=3)
    status = models.TextField(default="draft")
    subtotal = money(default=0)
    tax_total = money(default=0)
    total = money(default=0)
    reference = models.TextField(null=True)
    memo = models.TextField(null=True)
    created_by = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        abstract = True


class SalesOrder(Order):
    customer = fk(Customer, "customer_id")
    expected_ship_date = models.DateField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "sales_orders"


class SalesOrderLine(DocumentLine):
    order = models.ForeignKey(SalesOrder, DO_NOTHING, db_column="order_id", related_name="lines")
    unit_price = money(default=0)
    revenue_account_id = models.IntegerField(null=True)
    line_subtotal, tax_amount, line_total = line_money("unit_price")

    class Meta(Unmanaged.Meta):
        db_table = "sales_order_lines"


class PurchaseOrder(Order):
    supplier = fk(Supplier, "supplier_id")
    expected_receipt_date = models.DateField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "purchase_orders"


class PurchaseOrderLine(DocumentLine):
    order = models.ForeignKey(PurchaseOrder, DO_NOTHING, db_column="order_id", related_name="lines")
    unit_cost = money(default=0)
    expense_account_id = models.IntegerField(null=True)
    line_subtotal, tax_amount, line_total = line_money("unit_cost")

    class Meta(Unmanaged.Meta):
        db_table = "purchase_order_lines"


class SalesOrderFulfilment(Unmanaged):
    order = models.OneToOneField(SalesOrder, DO_NOTHING, primary_key=True, db_column="order_id", related_name="fulfilment")
    invoiced_status = models.TextField()
    shipped_status = models.TextField()

    class Meta(Unmanaged.Meta):
        db_table = "sales_order_fulfilment"


class PurchaseOrderFulfilment(Unmanaged):
    order = models.OneToOneField(PurchaseOrder, DO_NOTHING, primary_key=True, db_column="order_id", related_name="fulfilment")
    billed_status = models.TextField()
    received_status = models.TextField()

    class Meta(Unmanaged.Meta):
        db_table = "purchase_order_fulfilment"


class SalesOrderLineFulfilment(Unmanaged):
    order_line = models.OneToOneField(SalesOrderLine, DO_NOTHING, primary_key=True, db_column="order_line_id", related_name="fulfilment")
    qty_invoiced = money()
    qty_shipped = money()
    qty_to_invoice = money()
    qty_to_ship = money()

    class Meta(Unmanaged.Meta):
        db_table = "sales_order_line_fulfilment"


class PurchaseOrderLineFulfilment(Unmanaged):
    order_line = models.OneToOneField(PurchaseOrderLine, DO_NOTHING, primary_key=True, db_column="order_line_id", related_name="fulfilment")
    qty_billed = money()
    qty_received = money()
    qty_to_bill = money()
    qty_to_receive = money()

    class Meta(Unmanaged.Meta):
        db_table = "purchase_order_line_fulfilment"


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


class StockMovement(Unmanaged):
    product = fk(Product, "product_id")
    warehouse = fk(Warehouse, "warehouse_id")
    movement_date = models.DateField()
    movement_type = models.TextField()
    quantity = money()
    unit_cost = money(default=0)
    total_cost = generated(Round4(F("quantity") * F("unit_cost")))
    source_type = models.TextField(null=True)
    source_id = models.IntegerField(null=True)
    period_id = models.IntegerField(null=True)
    journal_entry_id = models.IntegerField(null=True)
    reference = models.TextField(null=True)
    notes = models.TextField(null=True)
    created_by = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "stock_movements"


class StockValuation(Unmanaged):
    product = models.OneToOneField(Product, DO_NOTHING, primary_key=True, db_column="product_id", related_name="+")
    qty_on_hand = money()
    value_on_hand = money()
    avg_unit_cost = money()

    class Meta(Unmanaged.Meta):
        db_table = "stock_valuation"


# ---------------------------------------------------------------------------
# Bank reconciliation
# ---------------------------------------------------------------------------


class BankStatement(Unmanaged):
    account = fk(Account, "account_id")
    statement_date = models.DateField()
    opening_balance = money(default=0)
    closing_balance = money(default=0)
    reference = models.TextField(null=True)
    status = models.TextField(default="open")
    reconciled_at = models.DateTimeField(null=True)
    created_by = models.IntegerField(null=True)

    class Meta(Unmanaged.Meta):
        db_table = "bank_statements"


class BankStatementLine(Unmanaged):
    statement = models.ForeignKey(BankStatement, DO_NOTHING, db_column="statement_id", related_name="lines")
    line_no = models.IntegerField()
    txn_date = models.DateField()
    description = models.TextField()
    reference = models.TextField(null=True)
    amount = money()
    journal_line = models.ForeignKey(JournalLine, DO_NOTHING, db_column="journal_line_id", null=True, related_name="+")

    class Meta(Unmanaged.Meta):
        db_table = "bank_statement_lines"
