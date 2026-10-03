"""The JSON API endpoints (spec/api.md). Each one is a thin shell over the
service layer: decode the path and body, call the service, encode the
result."""

from django.db import connection
from django.http import HttpResponse, JsonResponse

from .. import auth, printing
from ..errors import BadRequest, Unauthorized
from ..services import banking, calendar, documents, master, orders, posting, reports, settlement, stock, users, yearend
from ..services.kinds import DOCUMENTS, ORDERS, PAYMENTS
from ..values import parse_date, positive_int
from .http import body, created, no_content, ok, route


def pid(id):
    return positive_int(id)


def date_param(request, name):
    v = request.GET.get(name, "")
    return parse_date(v, name, status=BadRequest) if v else None


# ---------------------------------------------------------------------------
# Authentication (§3) and users (§5.1)
# ---------------------------------------------------------------------------


@route("POST", "auth/login")
def login(request):
    b = body(request)
    email, password = b.str("email"), b.str("password")
    if not email or not email.strip() or not password:
        raise BadRequest("email and password are required")
    user = auth.authenticate(email, password)
    if user is None:
        raise Unauthorized("invalid email or password")
    token = auth.start_session(user)
    response = ok(auth.CurrentUser(user.id, user.email, user.full_name, user.is_admin).as_json())
    auth.set_cookie(request, response, token)
    return response


@route("POST", "auth/logout")
def logout(request):
    auth.end_session(request.COOKIES.get(auth.COOKIE))
    response = no_content()
    auth.clear_cookie(response)
    return response


@route("GET", "auth/me")
def me(request):
    return ok(request.current_user.as_json())


@route("GET", "users", admin=True)
def list_users(request):
    return ok(users.list_users())


@route("POST", "users", admin=True)
def create_user(request):
    return created({"id": users.create_user(body(request))})


@route("GET", "users/<str:id>", admin=True)
def get_user(request, id):
    return ok(users.user_json(users.get_user(pid(id))))


@route("PUT", "users/<str:id>", admin=True)
def update_user(request, id):
    id = pid(id)
    users.update_user(request.current_user, id, body(request))
    return no_content()


@route("POST", "users/<str:id>/password", admin=True)
def set_password(request, id):
    id = pid(id)
    users.set_password(id, body(request))
    return no_content()


# ---------------------------------------------------------------------------
# Master data (§5.2–5.6)
# ---------------------------------------------------------------------------


def crud(collection, list_fn, get_fn, to_json, create_fn, update_fn, key=pid):
    """Register the list/get/create/update quartet of a master-data collection."""

    @route("GET", collection)
    def list_view(request):
        return ok(list_fn())

    @route("GET", collection + "/<str:id>")
    def get_view(request, id):
        return ok(to_json(get_fn(key(id))))

    @route("POST", collection)
    def create_view(request):
        new = create_fn(body(request))
        return created({"id": new} if key is pid else {"code": new})

    @route("PUT", collection + "/<str:id>")
    def update_view(request, id):
        k = key(id)
        update_fn(k, body(request))
        return no_content()


def code(text):
    return text


crud("organizations", master.list_organizations, master.get_organization, master.organization_json,
     master.create_organization, master.update_organization)
crud("customers", master.list_customers, master.get_customer, master.customer_json,
     master.create_customer, master.update_customer)
crud("suppliers", master.list_suppliers, master.get_supplier, master.supplier_json,
     master.create_supplier, master.update_supplier)
crud("products", master.list_products, master.get_product, master.product_json,
     master.create_product, master.update_product)
crud("accounts", master.list_accounts, master.get_account, master.account_json,
     master.create_account, master.update_account)
crud("warehouses", master.list_warehouses, master.get_warehouse, master.warehouse_json,
     master.create_warehouse, master.update_warehouse)
crud("tax-codes", master.list_tax_codes, master.get_tax_code, master.tax_code_json,
     master.create_tax_code, master.update_tax_code, key=code)
crud("payment-terms", master.list_payment_terms, master.get_payment_term, master.payment_term_json,
     master.create_payment_term, master.update_payment_term, key=code)
crud("fiscal-years", calendar.list_fiscal_years, calendar.get_fiscal_year, calendar.fiscal_year_json,
     calendar.create_fiscal_year, calendar.update_fiscal_year)
crud("accounting-periods", calendar.list_periods, calendar.get_period, calendar.period_json,
     calendar.create_period, calendar.update_period)


# ---------------------------------------------------------------------------
# Ledger settings and exchange rates (§5.8)
# ---------------------------------------------------------------------------


@route("GET", "settings")
def get_settings(request):
    return ok(master.settings_json(master.settings()))


@route("PUT", "settings", admin=True)
def update_settings(request):
    master.update_settings(body(request))
    return no_content()


@route("GET", "exchange-rates")
def list_exchange_rates(request):
    return ok(master.list_exchange_rates())


@route("POST", "exchange-rates")
def create_exchange_rate(request):
    return created(master.create_exchange_rate(body(request)))


@route("PUT", "exchange-rates/<str:currency>/<str:date>")
def update_exchange_rate(request, currency, date):
    master.update_exchange_rate(currency, date, body(request))
    return no_content()


@route("DELETE", "exchange-rates/<str:currency>/<str:date>")
def delete_exchange_rate(request, currency, date):
    master.delete_exchange_rate(currency, date)
    return no_content()


# ---------------------------------------------------------------------------
# Probes (§2), served at the root rather than under /api/
# ---------------------------------------------------------------------------


def healthz(request):
    return ok({"status": "ok"})


def readyz(request):
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "database unavailable"}, status=503)
    return ok({"status": "ready"})


# ---------------------------------------------------------------------------
# Invoices, bills, credit notes (§5.9)
# ---------------------------------------------------------------------------

def register_documents(kind):
    c = kind.collection

    @route("GET", c)
    def list_view(request):
        return ok(documents.list_documents(kind))

    @route("POST", c)
    def create_view(request):
        return created({"id": documents.create(kind, body(request), request.current_user)})

    @route("GET", c + "/<str:id>")
    def get_view(request, id):
        return ok(documents.document_json(kind, documents.get_document(kind, pid(id))))

    @route("PUT", c + "/<str:id>")
    def update_view(request, id):
        id = pid(id)
        documents.update(kind, id, body(request))
        return no_content()

    @route("DELETE", c + "/<str:id>")
    def delete_view(request, id):
        documents.delete(kind, pid(id))
        return no_content()

    @route("GET", c + "/<str:id>/lines")
    def lines_view(request, id):
        return ok(documents.list_lines(kind, pid(id)))

    @route("POST", c + "/<str:id>/post")
    def post_view(request, id):
        return ok({"journal_entry_id": posting.post_document(kind, pid(id))})

    @route("POST", c + "/<str:id>/unpost", admin=True)
    def unpost_view(request, id):
        return ok({"reversal_entry_id": posting.unpost_document(kind, pid(id))})

    if kind.credit:

        @route("GET", c + "/<str:id>/applications")
        def applications_view(request, id):
            return ok(documents.credit_note_applications(kind, pid(id)))

        @route("POST", c + "/<str:id>/apply")
        def apply_view(request, id):
            return ok({"applications": settlement.apply(c, pid(id))})


for _kind in DOCUMENTS.values():
    register_documents(_kind)


# ---------------------------------------------------------------------------
# Payments (§5.9)
# ---------------------------------------------------------------------------


def register_payments(kind):
    c = kind.collection

    @route("GET", c)
    def list_view(request):
        return ok(documents.list_payments(kind))

    @route("POST", c)
    def create_view(request):
        return created({"id": documents.create_payment(kind, body(request), request.current_user)})

    @route("GET", c + "/<str:id>")
    def get_view(request, id):
        return ok(documents.payment_json(kind, documents.get_payment(kind, pid(id))))

    @route("PUT", c + "/<str:id>")
    def update_view(request, id):
        id = pid(id)
        documents.update_payment(kind, id, body(request))
        return no_content()

    @route("DELETE", c + "/<str:id>")
    def delete_view(request, id):
        documents.delete_payment(kind, pid(id))
        return no_content()

    @route("GET", c + "/<str:id>/applications")
    def applications_view(request, id):
        return ok(documents.payment_applications(kind, pid(id)))

    @route("POST", c + "/<str:id>/post")
    def post_view(request, id):
        return ok({"journal_entry_id": posting.post_payment(kind, pid(id))})

    @route("POST", c + "/<str:id>/unpost", admin=True)
    def unpost_view(request, id):
        return ok({"reversal_entry_id": posting.unpost_payment(kind, pid(id))})

    @route("POST", c + "/<str:id>/apply")
    def apply_view(request, id):
        return ok({"applications": settlement.apply(c, pid(id))})


for _kind in PAYMENTS.values():
    register_payments(_kind)


# ---------------------------------------------------------------------------
# Orders (§5.10)
# ---------------------------------------------------------------------------


def register_orders(kind):
    c = kind.collection

    @route("GET", c)
    def list_view(request):
        return ok(orders.list_orders(kind))

    @route("POST", c)
    def create_view(request):
        return created({"id": documents.create(kind, body(request), request.current_user)})

    @route("GET", c + "/<str:id>")
    def get_view(request, id):
        return ok(orders.order_json(kind, orders.get_order(kind, pid(id))))

    @route("PUT", c + "/<str:id>")
    def update_view(request, id):
        id = pid(id)
        documents.update(kind, id, body(request))
        return no_content()

    @route("DELETE", c + "/<str:id>")
    def delete_view(request, id):
        documents.delete(kind, pid(id))
        return no_content()

    @route("GET", c + "/<str:id>/lines")
    def lines_view(request, id):
        return ok(orders.order_lines(kind, pid(id)))

    for action in ("confirm", "close", "cancel"):
        def transition(request, id, action=action):
            getattr(orders, action)(kind, pid(id))
            return no_content()

        route("POST", f"{c}/<str:id>/{action}")(transition)

    @route("POST", f"{c}/<str:id>/{kind.bill_verb}")
    def invoice_view(request, id):
        id = pid(id)
        new = orders.invoice(kind, id, body(request), request.current_user)
        return created({kind.document.noun.replace(" ", "_") + "_id": new})

    @route("POST", f"{c}/<str:id>/{kind.move_verb}")
    def move_view(request, id):
        id = pid(id)
        return created({"movement_ids": orders.move(kind, id, body(request), request.current_user)})


for _kind in ORDERS.values():
    register_orders(_kind)


# ---------------------------------------------------------------------------
# Stock movements (§5.12)
# ---------------------------------------------------------------------------


@route("GET", "stock-movements")
def list_movements(request):
    return ok(stock.list_movements())


@route("POST", "stock-movements")
def create_movement(request):
    return created({"id": stock.create_movement(body(request), request.current_user)})


@route("GET", "stock-movements/<str:id>")
def get_movement(request, id):
    return ok(stock.movement_json(stock.get_movement(pid(id))))


@route("PUT", "stock-movements/<str:id>")
def update_movement(request, id):
    id = pid(id)
    stock.update_movement(id, body(request))
    return no_content()


@route("DELETE", "stock-movements/<str:id>")
def delete_movement(request, id):
    stock.delete_movement(pid(id))
    return no_content()


@route("POST", "stock-movements/<str:id>/post")
def post_movement(request, id):
    id = pid(id)
    return ok({"journal_entry_id": posting.post_movement(id, body(request).int("credit_account_id"))})


@route("POST", "stock-movements/<str:id>/unpost", admin=True)
def unpost_movement(request, id):
    return ok({"reversal_entry_id": posting.unpost_movement(pid(id))})


# ---------------------------------------------------------------------------
# Year-end (§5.7)
# ---------------------------------------------------------------------------


@route("POST", "fiscal-years/<str:id>/close", admin=True)
def close_year(request, id):
    id = pid(id)
    account = body(request).required_id("retained_earnings_account_id")
    return ok(yearend.close(id, account))


@route("POST", "fiscal-years/<str:id>/reopen", admin=True)
def reopen_year(request, id):
    return ok({"reversal_entry_id": yearend.reopen(pid(id))})


# ---------------------------------------------------------------------------
# Bank reconciliation (§5.13)
# ---------------------------------------------------------------------------


@route("GET", "bank-statements")
def list_statements(request):
    return ok(banking.list_statements())


@route("POST", "bank-statements")
def create_statement(request):
    return created({"id": banking.create_statement(body(request), request.current_user)})


@route("GET", "bank-statements/<str:id>")
def get_statement(request, id):
    return ok(banking.statement_json(banking.get_statement(pid(id))))


@route("PUT", "bank-statements/<str:id>")
def update_statement(request, id):
    id = pid(id)
    banking.update_statement(id, body(request))
    return no_content()


@route("DELETE", "bank-statements/<str:id>")
def delete_statement(request, id):
    banking.delete_statement(pid(id))
    return no_content()


@route("GET", "bank-statements/<str:id>/lines")
def statement_lines(request, id):
    return ok(banking.list_lines(pid(id)))


@route("POST", "bank-statements/<str:id>/lines")
def add_statement_line(request, id):
    id = pid(id)
    return created({"id": banking.add_line(id, body(request))})


@route("POST", "bank-statements/<str:id>/import")
def import_statement(request, id):
    id = pid(id)
    return ok({"imported": banking.import_csv(id, body(request))})


@route("GET", "bank-statements/<str:id>/candidates")
def statement_candidates(request, id):
    return ok(banking.candidates(pid(id)))


@route("POST", "bank-statements/<str:id>/auto-match")
def auto_match(request, id):
    return ok({"matched": banking.auto_match(pid(id))})


@route("POST", "bank-statements/<str:id>/reconcile")
def reconcile(request, id):
    banking.reconcile(pid(id))
    return no_content()


@route("POST", "bank-statements/<str:id>/reopen", admin=True)
def reopen_statement(request, id):
    banking.reopen(pid(id))
    return no_content()


@route("POST", "bank-statement-lines/<str:id>/match")
def match_line(request, id):
    id = pid(id)
    banking.match(id, body(request))
    return no_content()


@route("POST", "bank-statement-lines/<str:id>/unmatch")
def unmatch_line(request, id):
    banking.unmatch(pid(id))
    return no_content()


@route("DELETE", "bank-statement-lines/<str:id>")
def delete_line(request, id):
    banking.delete_line(pid(id))
    return no_content()


# ---------------------------------------------------------------------------
# Journal and reports (§5.14)
# ---------------------------------------------------------------------------


@route("GET", "journal-entries/<str:id>")
def journal_entry(request, id):
    return ok(reports.journal_entry(pid(id)))


@route("GET", "accounts/<str:id>/ledger")
def ledger(request, id):
    id = pid(id)
    return ok(reports.ledger(id, date_param(request, "from"), date_param(request, "to")))


@route("GET", "trial-balance")
def trial_balance(request):
    return ok(reports.trial_balance())


@route("GET", "profit-and-loss")
def profit_and_loss(request):
    return ok(reports.profit_and_loss(date_param(request, "from"), date_param(request, "to")))


@route("GET", "balance-sheet")
def balance_sheet(request):
    return ok(reports.balance_sheet(date_param(request, "as_of")))


@route("GET", "cash-flow")
def cash_flow(request):
    return ok(reports.cash_flow(date_param(request, "from"), date_param(request, "to")))


@route("GET", "ar-aging")
def ar_aging(request):
    return ok(reports.aging(sales=True))


@route("GET", "ap-aging")
def ap_aging(request):
    return ok(reports.aging(sales=False))


@route("GET", "inventory-valuation")
def inventory_valuation(request):
    return ok(reports.inventory_valuation())


# ---------------------------------------------------------------------------
# Printing and email (§5.11)
# ---------------------------------------------------------------------------


def register_printing(collection):
    @route("GET", collection + "/<str:id>/pdf")
    def pdf_view(request, id):
        data, name = printing.pdf_for(collection, pid(id))
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{name}"'
        return response

    @route("POST", collection + "/<str:id>/email")
    def email_view(request, id):
        id = pid(id)
        to = body(request).raw("to") or []
        if not isinstance(to, list) or not all(isinstance(a, str) for a in to):
            raise BadRequest("to must be an array of email addresses")
        sent = printing.email(collection, id, [a.strip() for a in to if a.strip()])
        return ok({"status": "sent", "to": sent})


for _collection in printing.LABELS:
    register_printing(_collection)
