"""The JSON API endpoints (spec/api.md). Each one is a thin shell over the
service layer: decode the path and body, call the service, encode the
result."""

from django.db import connection
from django.http import JsonResponse

from .. import auth
from ..errors import BadRequest, Unauthorized
from ..services import calendar, master, users
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
