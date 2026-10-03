"""Walk the server-rendered UI through the checklist of spec/domain.md §13.

These drive the HTML views with Django's test client: every screen
renders, forms save through the service layer, and refusals come back as
the server's message next to the action. The JSON API itself is covered
by the conformance suite.
"""

import datetime
import re

from django.test import TestCase
from django.urls import reverse

from .. import auth
from ..models import (
    Account,
    BankStatement,
    Customer,
    CustomerPayment,
    FiscalYear,
    Organization,
    Product,
    SalesInvoice,
    SalesOrder,
    StockMovement,
    Supplier,
    User,
    Warehouse,
)
from ..values import today


class UITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create(email="admin@example.com", full_name="Ada Admin",
                                        password_hash=auth.hash_password("admin-pass"), is_admin=True)
        cls.clerk = User.objects.create(email="clerk@example.com", full_name="Carl Clerk",
                                        password_hash=auth.hash_password("clerk-pass"))
        acct = {a.code: a.id for a in Account.objects.all()}
        cls.acct = acct
        year = today().year
        FiscalYear.objects.create(name=f"FY{year}", start_date=datetime.date(year, 1, 1), end_date=datetime.date(year, 12, 31))
        org = Organization.objects.create(name="Acme Ltd", email="ap@acme.example")
        cls.customer = Customer.objects.create(organization=org, ar_account_id=acct["1100"], currency_code="USD")
        sup = Organization.objects.create(name="Parts Co")
        cls.supplier = Supplier.objects.create(organization=sup, ap_account_id=acct["2000"], currency_code="USD")
        cls.product = Product.objects.create(sku="WID", name="Widget", unit_price="12.5000", revenue_account_id=acct["4000"],
                                             track_inventory=True, inventory_account_id=acct["1200"],
                                             cogs_account_id=acct["5000"], tax_code="STD")
        cls.warehouse = Warehouse.objects.create(code="MAIN", name="Main")

    def login(self, user=None):
        self.client.cookies[auth.COOKIE] = auth.start_session(user or self.admin)

    def page(self, url, status=200):
        r = self.client.get(url)
        self.assertEqual(r.status_code, status, f"GET {url}")
        return r.content.decode()

    def invoice(self, number="INV-1", qty="2", price="10", date=None, due=None):
        r = self.client.post(reverse("sales-invoices-new"), {
            "customer_id": self.customer.id, "invoice_number": number, "invoice_date": (date or today()).isoformat(),
            "due_date": due.isoformat() if due else "",
            "currency_code": "USD", "line_product_id": [str(self.product.id)], "line_description": ["Widgets"],
            "line_quantity": [qty], "line_price": [price], "line_account": [""], "line_tax_code": [""], "line_tax_rate": ["0"],
        })
        self.assertEqual(r.status_code, 302, r.content.decode()[:2000])
        return SalesInvoice.objects.get(invoice_number=number)

    # -- G: general -------------------------------------------------------

    def test_login_is_the_only_page_without_a_session(self):  # G1
        r = self.client.get("/sales-invoices/")
        self.assertRedirects(r, "/login?next=%2Fsales-invoices%2F", fetch_redirect_response=False)
        r = self.client.post("/login", {"email": "admin@example.com", "password": "wrong"})
        self.assertContains(r, "Invalid email or password")
        r = self.client.post("/login", {"email": " ADMIN@example.com ", "password": "admin-pass", "next": "/products/"})
        self.assertRedirects(r, "/products/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/"), "Ada Admin")  # G2
        self.client.post("/logout")
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_every_navigation_link_works(self):  # G3
        self.login()
        home = self.page("/")
        links = set(re.findall(r'<a href="(/[^"]*)"', home.split('<nav class="sidebar"')[1].split("</nav>")[0]))
        self.assertGreater(len(links), 25)
        for url in links:
            self.page(url)

    def test_admin_actions_are_hidden_from_users(self):  # G4
        self.login(self.clerk)
        home = self.page("/")
        self.assertNotIn('href="/users/"', home)
        self.page("/users/", status=403)
        self.assertIn("Only administrators can change these", self.page("/settings"))
        inv = self.invoice()
        self.client.post(reverse("sales-invoices-post", args=[inv.id]))
        self.assertNotIn(">Unpost<", self.page(reverse("sales-invoices-detail", args=[inv.id])))

    def test_refusals_show_the_server_message(self):  # G5
        self.login()
        r = self.client.post(reverse("organizations-new"), {"name": ""})
        self.assertContains(r, "name is required")
        r = self.client.post(reverse("organizations-new"), {"name": "X", "country_code": "ZZ"})
        self.assertContains(r, "unknown country_code")
        inv = self.invoice(price="0")
        r = self.client.post(reverse("sales-invoices-post", args=[inv.id]))
        self.assertContains(r, "total must be greater than zero")

    def test_deletes_ask_first(self):  # G6
        self.login()
        inv = self.invoice()
        self.assertIn("cannot be undone", self.page(reverse("sales-invoices-delete", args=[inv.id])))
        self.assertTrue(SalesInvoice.objects.filter(pk=inv.id).exists())
        self.client.post(reverse("sales-invoices-delete", args=[inv.id]))
        self.assertFalse(SalesInvoice.objects.filter(pk=inv.id).exists())

    def test_amounts_are_exact(self):  # G7
        self.login()
        inv = self.invoice(qty="1.00005", price="10.0001")
        detail = self.page(reverse("sales-invoices-detail", args=[inv.id]))
        self.assertIn("10.0011", detail)  # round(1.0001 × 10.0001, 4)
        self.assertIn("USD", detail)

    def test_unknown_address_is_not_found(self):  # G8
        self.login()
        self.assertIn("Not found", self.page("/no-such-page", status=404))
        self.page("/sales-invoices/999999/", status=404)

    # -- H: home ------------------------------------------------------------

    def test_home_shows_outstanding_and_overdue(self):  # H1–H5
        self.login()
        inv = self.invoice(date=today() - datetime.timedelta(days=40), due=today() - datetime.timedelta(days=5))
        self.client.post(reverse("sales-invoices-post", args=[inv.id]))
        home = self.page("/")
        self.assertIn("USD 20.00", home)
        self.assertIn(reverse("sales-invoices-detail", args=[inv.id]), home)
        for name in ("sales-invoices-new", "customer-payments-new", "purchase-bills-new", "supplier-payments-new",
                     "sales-orders-new", "purchase-orders-new"):
            self.assertIn(reverse(name), home)

    # -- M: master data -----------------------------------------------------

    def test_master_data_forms(self):  # M1–M6
        self.login()
        r = self.client.post(reverse("organizations-new"), {"name": "Globex", "country_code": "US"})
        self.assertRedirects(r, reverse("organizations"), fetch_redirect_response=False)
        org = Organization.objects.get(name="Globex")
        self.client.post(reverse("customers-new"), {"organization_id": org.id, "customer_number": "C-9", "credit_limit": "500"})
        c = Customer.objects.get(organization=org)
        # The organization is read-only on edit, and deactivation is through the form.
        self.client.post(reverse("customers-edit", args=[c.id]), {"organization_id": 1, "customer_number": "C-9"})
        c.refresh_from_db()
        self.assertEqual((c.organization_id, c.is_active, c.credit_limit), (org.id, False, None))
        self.assertIn("Inactive", self.page(reverse("customers")))
        form = self.page(reverse("accounts-edit", args=[self.acct["1000"]]))
        parent_select = form.split('name="parent_id"')[1].split("</select>")[0]
        self.assertNotIn(f'value="{self.acct["1000"]}"', parent_select)  # M4
        self.page(reverse("tax-codes-edit", args=["STD"]))
        self.page(reverse("payment-terms-edit", args=["NET30"]))
        self.page(reverse("warehouses-edit", args=[self.warehouse.id]))

    def test_users_and_settings(self):  # M7, M8
        self.login()
        r = self.client.post(reverse("users-new"), {"email": "new@example.com", "full_name": "New", "password": "short"})
        self.assertContains(r, "at least 8 characters")
        r = self.client.post(reverse("users-edit", args=[self.admin.id]), {"email": "admin@example.com", "full_name": "Ada"})
        self.assertContains(r, "cannot deactivate yourself")
        self.client.post(reverse("users-password", args=[self.clerk.id]), {"password": "another-pass"})
        self.assertIsNotNone(auth.authenticate("clerk@example.com", "another-pass"))
        self.assertIn("FX gain/loss", self.page("/settings"))

    # -- D, P: documents and payments ----------------------------------------

    def test_invoice_lifecycle(self):  # D1–D7
        self.login()
        form = self.page(reverse("sales-invoices-new"))
        self.assertIn('id="client-data"', form)  # product, tax-code, and currency prefill data for app.js
        inv = self.invoice()
        self.assertIn("INV-1", self.page(reverse("sales-invoices")))
        detail = self.page(reverse("sales-invoices-detail", args=[inv.id]))
        for text in ("Post", "Edit", "Delete", "/pdf", "Email"):
            self.assertIn(text, detail)
        self.client.post(reverse("sales-invoices-post", args=[inv.id]))
        inv.refresh_from_db()
        self.assertEqual(inv.status, "posted")
        detail = self.page(reverse("sales-invoices-detail", args=[inv.id]))
        self.assertIn(reverse("journal-entry", args=[inv.journal_entry_id]), detail)
        self.assertIn("Unpost", detail)
        r = self.client.post(reverse("sales-invoices-email", args=[inv.id]), {"to": ""})
        self.assertContains(r, "not configured")  # D7: 501 when sending is disabled
        self.assertEqual(self.client.get(f"/api/sales-invoices/{inv.id}/pdf")["Content-Type"], "application/pdf")
        self.client.post(reverse("sales-invoices-unpost", args=[inv.id]))
        inv.refresh_from_db()
        self.assertEqual(inv.status, "draft")

    def test_payment_and_apply(self):  # P1–P4
        self.login()
        inv = self.invoice()
        self.client.post(reverse("sales-invoices-post", args=[inv.id]))
        r = self.client.post(reverse("customer-payments-new"), {
            "customer_id": self.customer.id, "payment_date": today().isoformat(), "currency_code": "USD",
            "amount": "15", "method": "transfer", "deposit_account_id": self.acct["1000"]})
        pay = CustomerPayment.objects.get()
        self.assertRedirects(r, reverse("customer-payments-detail", args=[pay.id]), fetch_redirect_response=False)
        self.client.post(reverse("customer-payments-post", args=[pay.id]))
        self.client.post(reverse("customer-payments-apply", args=[pay.id]))
        detail = self.page(reverse("customer-payments-detail", args=[pay.id]))
        self.assertIn("INV-1", detail)
        self.assertIn("15.00", detail)
        self.assertIn("Transfer", self.page(reverse("customer-payments")))

    # -- O: orders ----------------------------------------------------------

    def test_order_fulfilment(self):  # O1–O7
        self.login()
        r = self.client.post(reverse("sales-orders-new"), {
            "customer_id": self.customer.id, "order_number": "SO-1", "order_date": today().isoformat(),
            "currency_code": "USD", "line_product_id": [str(self.product.id)], "line_description": ["Widgets"],
            "line_quantity": ["5"], "line_price": ["12.5"], "line_account": [""], "line_tax_code": [""], "line_tax_rate": ["0"]})
        order = SalesOrder.objects.get(order_number="SO-1")
        self.assertRedirects(r, reverse("sales-orders-detail", args=[order.id]), fetch_redirect_response=False)
        self.client.post(reverse("sales-orders-confirm", args=[order.id]))
        form = self.page(reverse("sales-orders-bill", args=[order.id]))
        line = order.lines.get()
        self.assertIn(f'name="qty_{line.id}"', form)
        r = self.client.post(reverse("sales-orders-bill", args=[order.id]),
                             {"number": "INV-SO-1", "date": today().isoformat(), f"qty_{line.id}": "2"})
        inv = SalesInvoice.objects.get(invoice_number="INV-SO-1")
        self.assertRedirects(r, reverse("sales-invoices-detail", args=[inv.id]), fetch_redirect_response=False)
        self.assertIn("cannot be edited", self.page(reverse("sales-invoices-detail", args=[inv.id])))
        r = self.client.post(reverse("sales-orders-move", args=[order.id]),
                             {"warehouse_id": self.warehouse.id, "movement_date": today().isoformat(), f"qty_{line.id}": "5"})
        self.assertContains(r, "Stock movement")
        detail = self.page(reverse("sales-orders-detail", args=[order.id]))
        self.assertIn("Partial", detail)
        self.assertNotIn("Cancel order", detail)  # O4: fulfilled orders cannot be cancelled

    # -- S: inventory ---------------------------------------------------------

    def test_stock_movements(self):  # S1–S3
        self.login()
        r = self.client.post(reverse("stock-movements-new"), {
            "product_id": self.product.id, "warehouse_id": self.warehouse.id, "movement_type": "issue",
            "movement_date": today().isoformat(), "quantity": "3", "unit_cost": "2"})
        sm = StockMovement.objects.get()
        self.assertEqual(str(sm.quantity), "-3.0000")  # S2: signed by the type
        self.assertRedirects(r, reverse("stock-movements-detail", args=[sm.id]), fetch_redirect_response=False)
        self.client.post(reverse("stock-movements-post", args=[sm.id]))
        sm.refresh_from_db()
        self.assertIsNotNone(sm.journal_entry_id)
        self.assertIn("Unpost", self.page(reverse("stock-movements-detail", args=[sm.id])))
        self.assertIn("WID", self.page(reverse("inventory-valuation")))

    # -- R: reports -----------------------------------------------------------

    def test_reports(self):  # R1–R8
        self.login()
        inv = self.invoice()
        self.client.post(reverse("sales-invoices-post", args=[inv.id]))
        inv.refresh_from_db()
        self.assertIn("Net income", self.page(reverse("profit-and-loss")))
        self.assertIn("Current earnings", self.page(reverse("balance-sheet") + "?as_of=" + today().isoformat()))
        self.assertIn("Opening cash", self.page(reverse("cash-flow")))
        self.assertIn(reverse("ledger", args=[self.acct["1100"]]), self.page(reverse("trial-balance")))
        self.assertIn("20.00", self.page(reverse("ledger", args=[self.acct["1100"]])))
        self.assertIn("Accounts Receivable", self.page(reverse("journal-entry", args=[inv.journal_entry_id])))
        self.assertIn("Acme Ltd", self.page(reverse("ar-aging")))
        self.page(reverse("ap-aging"))
        self.assertIn("must be a YYYY-MM-DD", self.page(reverse("profit-and-loss") + "?from=yesterday"))

    # -- A: accounting --------------------------------------------------------

    def test_periods_and_year_end(self):  # A1, A2
        self.login()
        r = self.client.post(reverse("fiscal-years-new"), {"name": "FY1990", "start_date": "1990-01-01", "end_date": "1990-12-31"})
        self.assertRedirects(r, reverse("periods"), fetch_redirect_response=False)
        y = FiscalYear.objects.get(name="FY1990")
        self.client.post(reverse("accounting-periods-new"), {"fiscal_year_id": y.id, "name": "1990-01",
                                                            "start_date": "1990-01-01", "end_date": "1990-01-31"})
        form = self.page(reverse("accounting-periods-new"))
        self.assertIn('value="1990-02"', form)  # proposes the month after the latest period
        close = self.page(reverse("fiscal-years-close", args=[y.id]))
        self.assertIn("Retained Earnings", close)
        self.client.post(reverse("fiscal-years-close", args=[y.id]), {"retained_earnings_account_id": self.acct["3000"]})
        y.refresh_from_db()
        self.assertEqual(y.status, "closed")
        self.login(self.clerk)
        self.page(reverse("fiscal-years-close", args=[y.id]), status=403)

    def test_exchange_rates(self):  # A3
        self.login()
        self.client.post(reverse("exchange-rates-new"), {"currency_code": "EUR", "rate_date": "2026-01-02", "rate": "1.125"})
        self.assertIn("1.125", self.page(reverse("exchange-rates")))
        self.client.post(reverse("exchange-rates-edit", args=["EUR", "2026-01-02"]), {"rate": "1.2"})
        self.assertIn("1.2", self.page(reverse("exchange-rates")))
        self.client.post(reverse("exchange-rates-delete", args=["EUR", "2026-01-02"]))
        self.assertNotIn("EUR", self.page(reverse("exchange-rates")).split("<table")[-1])

    def test_bank_reconciliation(self):  # A4, A5
        self.login()
        form = self.page(reverse("bank-statements-new"))
        account_select = form.split('name="account_id"')[1].split("</select>")[0]
        self.assertIn("1000 Cash", account_select)
        self.assertNotIn("1100", account_select)  # cash accounts only
        pay = CustomerPayment.objects.create(customer=self.customer, payment_date=today(), currency_code="USD",
                                             amount="40", deposit_account_id=self.acct["1000"])
        from ..services import posting
        from ..services.kinds import CUSTOMER_PAYMENT

        posting.post_payment(CUSTOMER_PAYMENT, pay.id)
        self.client.post(reverse("bank-statements-new"), {"account_id": self.acct["1000"], "statement_date": today().isoformat(),
                                                          "opening_balance": "0", "closing_balance": "40", "reference": "S1"})
        st = BankStatement.objects.get()
        r = self.client.post(reverse("bank-statements-import", args=[st.id]), {"csv": "date,description,amount\nnot-a-date,x,1\n"})
        self.assertContains(r, "not a YYYY-MM-DD date")
        self.client.post(reverse("bank-statements-import", args=[st.id]),
                         {"csv": f"date,description,amount\n{today().isoformat()},Deposit,40\n"})
        detail = self.page(reverse("bank-statements-detail", args=[st.id]))
        self.assertIn("Match", detail)
        self.client.post(reverse("bank-statements-auto-match", args=[st.id]))
        self.client.post(reverse("bank-statements-reconcile", args=[st.id]))
        st.refresh_from_db()
        self.assertEqual(st.status, "reconciled")
        self.assertIn("Reopen", self.page(reverse("bank-statements-detail", args=[st.id])))
