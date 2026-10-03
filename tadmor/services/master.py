"""Master data (spec/api.md §5.2–5.6, §5.8; domain §3).

Updates are full replacements: whatever the body omits becomes null or
false. Uniqueness and foreign keys are enforced by the schema, and their
violations surface as 409 and 422 through tadmor.errors.
"""

from django.db import transaction

from ..errors import BadRequest, Conflict, NotFound, Unprocessable
from ..models import (
    Account,
    Currency,
    Customer,
    ExchangeRate,
    GLSettings,
    JournalEntry,
    Organization,
    PaymentTerm,
    Product,
    Supplier,
    TaxCode,
    Warehouse,
)
from ..values import FX, MONEY, RATE, fmt4, fmt_date, fmt_rate, parse_date


def _get(model, key, what):
    obj = model.objects.filter(pk=key).first()
    if obj is None:
        raise NotFound(f"{what} not found")
    return obj


def _update(model, key, fields):
    model.objects.filter(pk=key).update(**fields)


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


def organization_json(o):
    return {
        "id": o.id, "name": o.name, "legal_name": o.legal_name, "tax_id": o.tax_id,
        "country_code": o.country_code, "default_currency": o.default_currency,
        "email": o.email, "is_self": o.is_self,
    }


def _organization_fields(b):
    return {
        "name": b.required_str("name"),
        "legal_name": b.text("legal_name"),
        "tax_id": b.text("tax_id"),
        "country_code": b.text("country_code"),
        "default_currency": b.text("default_currency"),
        "email": b.text("email"),
        "is_self": b.bool("is_self"),
    }


def _check_self(fields, id=None):
    if fields["is_self"] and Organization.objects.filter(is_self=True).exclude(pk=id).exists():
        raise Conflict("another organization is already marked as our own")


def list_organizations():
    return [organization_json(o) for o in Organization.objects.order_by("name", "id")]


def get_organization(id):
    return _get(Organization, id, "organization")


def create_organization(b):
    fields = _organization_fields(b)
    _check_self(fields)
    return Organization.objects.create(**fields).id


def update_organization(id, b):
    fields = _organization_fields(b)
    get_organization(id)
    _check_self(fields, id)
    _update(Organization, id, fields)


# ---------------------------------------------------------------------------
# Customers and suppliers
# ---------------------------------------------------------------------------


def customer_json(c):
    return {
        "id": c.id, "organization_id": c.organization_id, "customer_number": c.customer_number,
        "ar_account_id": c.ar_account_id, "payment_terms_code": c.payment_terms_code,
        "currency_code": c.currency_code, "tax_code": c.tax_code,
        "credit_limit": fmt4(c.credit_limit), "is_active": c.is_active,
    }


def supplier_json(s):
    return {
        "id": s.id, "organization_id": s.organization_id, "supplier_number": s.supplier_number,
        "ap_account_id": s.ap_account_id, "payment_terms_code": s.payment_terms_code,
        "currency_code": s.currency_code, "tax_code": s.tax_code, "is_active": s.is_active,
    }


def _party_fields(b, number, control):
    return {
        "organization_id": b.required_id("organization_id"),
        number: b.text(number),
        control: b.int(control),
        "payment_terms_code": b.text("payment_terms_code"),
        "currency_code": b.text("currency_code"),
        "tax_code": b.text("tax_code"),
    }


def _customer_fields(b):
    fields = _party_fields(b, "customer_number", "ar_account_id")
    limit = b.decimal("credit_limit", MONEY)
    if limit is not None and limit < 0:
        raise Unprocessable("credit_limit must not be negative")
    fields["credit_limit"] = limit
    return fields


def list_customers():
    return [customer_json(c) for c in Customer.objects.order_by("id")]


def get_customer(id):
    return _get(Customer, id, "customer")


def create_customer(b):
    b.required_id("organization_id")
    return Customer.objects.create(**_customer_fields(b)).id


def update_customer(id, b):
    b.required_id("organization_id")
    get_customer(id)
    fields = _customer_fields(b)
    fields["is_active"] = b.bool("is_active")
    _update(Customer, id, fields)


def list_suppliers():
    return [supplier_json(s) for s in Supplier.objects.order_by("id")]


def get_supplier(id):
    return _get(Supplier, id, "supplier")


def create_supplier(b):
    return Supplier.objects.create(**_party_fields(b, "supplier_number", "ap_account_id")).id


def update_supplier(id, b):
    b.required_id("organization_id")
    get_supplier(id)
    fields = _party_fields(b, "supplier_number", "ap_account_id")
    fields["is_active"] = b.bool("is_active")
    _update(Supplier, id, fields)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def product_json(p):
    return {
        "id": p.id, "sku": p.sku, "name": p.name, "description": p.description,
        "unit_price": fmt4(p.unit_price), "currency_code": p.currency_code,
        "revenue_account_id": p.revenue_account_id, "tax_code": p.tax_code,
        "track_inventory": p.track_inventory, "inventory_account_id": p.inventory_account_id,
        "cogs_account_id": p.cogs_account_id, "is_active": p.is_active,
    }


def _product_required(b):
    b.required_str("sku")
    b.required_str("name")


def _product_fields(b):
    return {
        "sku": b.required_str("sku"),
        "name": b.required_str("name"),
        "description": b.text("description"),
        "unit_price": b.decimal("unit_price", MONEY, default=0),
        "currency_code": b.text("currency_code"),
        "revenue_account_id": b.int("revenue_account_id"),
        "tax_code": b.text("tax_code"),
        "track_inventory": b.bool("track_inventory"),
        "inventory_account_id": b.int("inventory_account_id"),
        "cogs_account_id": b.int("cogs_account_id"),
    }


def list_products():
    return [product_json(p) for p in Product.objects.order_by("sku", "id")]


def get_product(id):
    return _get(Product, id, "product")


def create_product(b):
    _product_required(b)
    return Product.objects.create(**_product_fields(b)).id


def update_product(id, b):
    _product_required(b)
    get_product(id)
    fields = _product_fields(b)
    fields["is_active"] = b.bool("is_active")
    _update(Product, id, fields)


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------

ACCOUNT_TYPES = ("asset", "liability", "equity", "revenue", "expense")
ACTIVITIES = ("operating", "investing", "financing")


def account_json(a):
    return {
        "id": a.id, "code": a.code, "name": a.name, "account_type": a.account_type,
        "parent_id": a.parent_id, "currency_code": a.currency_code,
        "is_postable": a.is_postable, "is_active": a.is_active, "is_cash": a.is_cash,
        "cash_flow_activity": a.cash_flow_activity,
    }


def _account_required(b):
    b.required_str("code")
    b.required_str("name")
    b.required_str("account_type")


def _account_fields(b, id=None):
    fields = {
        "code": b.required_str("code"),
        "name": b.required_str("name"),
        "account_type": b.required_str("account_type"),
        "parent_id": b.int("parent_id"),
        "currency_code": b.text("currency_code"),
        "is_postable": b.bool("is_postable"),
        "is_cash": b.bool("is_cash"),
        "cash_flow_activity": b.text("cash_flow_activity") or "operating",
    }
    if fields["account_type"] not in ACCOUNT_TYPES:
        raise Unprocessable("account_type must be one of " + ", ".join(ACCOUNT_TYPES))
    if fields["cash_flow_activity"] not in ACTIVITIES:
        raise Unprocessable("cash_flow_activity must be one of " + ", ".join(ACTIVITIES))
    if fields["is_cash"] and fields["account_type"] != "asset":
        raise Unprocessable("only an asset account can be a cash account")
    parent = fields["parent_id"]
    if parent is not None:
        if id is not None and parent == id:
            raise Unprocessable("an account cannot be its own parent")
        if not Account.objects.filter(pk=parent).exists():
            raise Unprocessable("unknown parent_id")
    return fields


def list_accounts():
    return [account_json(a) for a in Account.objects.order_by("code", "id")]


def get_account(id):
    return _get(Account, id, "account")


def create_account(b):
    _account_required(b)
    return Account.objects.create(**_account_fields(b)).id


def update_account(id, b):
    _account_required(b)
    get_account(id)
    fields = _account_fields(b, id)
    fields["is_active"] = b.bool("is_active")
    _update(Account, id, fields)


# ---------------------------------------------------------------------------
# Tax codes, payment terms, warehouses
# ---------------------------------------------------------------------------


def tax_code_json(t):
    return {"code": t.code, "name": t.name, "rate": fmt4(t.rate), "tax_account_id": t.tax_account_id, "is_active": t.is_active}


def _tax_code_fields(b):
    rate = b.decimal("rate", RATE, default=0)
    if rate < 0:
        raise Unprocessable("rate must not be negative")
    return {"name": b.required_str("name"), "rate": rate, "tax_account_id": b.int("tax_account_id")}


def list_tax_codes():
    return [tax_code_json(t) for t in TaxCode.objects.order_by("code")]


def get_tax_code(code):
    return _get(TaxCode, code, "tax code")


def create_tax_code(b):
    code = b.required_str("code")
    b.required_str("name")
    fields = _tax_code_fields(b)
    if TaxCode.objects.filter(pk=code).exists():
        raise Conflict("tax code already exists")
    TaxCode.objects.create(code=code, **fields)
    return code


def update_tax_code(code, b):
    b.required_str("name")
    get_tax_code(code)
    fields = _tax_code_fields(b)
    fields["is_active"] = b.bool("is_active")
    _update(TaxCode, code, fields)


def payment_term_json(p):
    return {"code": p.code, "name": p.name, "due_days": p.due_days}


def _due_days(b):
    days = b.int("due_days") or 0
    if days < 0:
        raise Unprocessable("due_days must not be negative")
    return days


def list_payment_terms():
    return [payment_term_json(p) for p in PaymentTerm.objects.order_by("due_days", "code")]


def get_payment_term(code):
    return _get(PaymentTerm, code, "payment term")


def create_payment_term(b):
    code = b.required_str("code")
    name = b.required_str("name")
    days = _due_days(b)
    if PaymentTerm.objects.filter(pk=code).exists():
        raise Conflict("payment term already exists")
    PaymentTerm.objects.create(code=code, name=name, due_days=days)
    return code


def update_payment_term(code, b):
    name = b.required_str("name")
    get_payment_term(code)
    _update(PaymentTerm, code, {"name": name, "due_days": _due_days(b)})


def warehouse_json(w):
    return {"id": w.id, "code": w.code, "name": w.name, "address_id": w.address_id, "is_active": w.is_active}


def _warehouse_fields(b):
    return {"code": b.required_str("code"), "name": b.required_str("name"), "address_id": b.int("address_id")}


def list_warehouses():
    return [warehouse_json(w) for w in Warehouse.objects.order_by("code", "id")]


def get_warehouse(id):
    return _get(Warehouse, id, "warehouse")


def create_warehouse(b):
    return Warehouse.objects.create(**_warehouse_fields(b)).id


def update_warehouse(id, b):
    fields = _warehouse_fields(b)
    get_warehouse(id)
    fields["is_active"] = b.bool("is_active")
    _update(Warehouse, id, fields)


# ---------------------------------------------------------------------------
# Ledger settings and exchange rates
# ---------------------------------------------------------------------------


def settings():
    return GLSettings.objects.get(pk=1)


def settings_json(s):
    return {"base_currency": s.base_currency, "fx_gain_loss_account_id": s.fx_gain_loss_account_id}


def _currency(code, name="currency_code"):
    code = code.strip().upper()
    if len(code) != 3 or not code.isalpha() or not Currency.objects.filter(pk=code).exists():
        raise Unprocessable(f"unknown {name}")
    return code


def update_settings(b):
    base = _currency(b.required_str("base_currency"), "base_currency")
    fx = b.int("fx_gain_loss_account_id")
    if fx is not None and not Account.objects.filter(pk=fx, is_postable=True, is_active=True).exists():
        raise Unprocessable("fx_gain_loss_account_id must be a postable, active account")
    with transaction.atomic():
        current = GLSettings.objects.select_for_update().get(pk=1)
        if base != current.base_currency and JournalEntry.objects.exists():
            raise Unprocessable("the base currency cannot change once journal entries exist")
        _update(GLSettings, 1, {"base_currency": base, "fx_gain_loss_account_id": fx})


def exchange_rate_json(r):
    return {"currency_code": r.currency_code, "rate_date": fmt_date(r.rate_date), "rate": fmt_rate(r.rate)}


def list_exchange_rates():
    return [exchange_rate_json(r) for r in ExchangeRate.objects.order_by("currency_code", "-rate_date")]


def _rate(b):
    rate = b.decimal("rate", FX)
    if rate is None:
        raise BadRequest("rate is required")
    if rate <= 0:
        raise Unprocessable("rate must be greater than 0")
    return rate


def create_exchange_rate(b):
    currency = b.required_str("currency_code")
    rate_date = b.required_str("rate_date")
    if not b.str("rate"):
        raise BadRequest("rate is required")
    currency = _currency(currency)
    rate_date = parse_date(rate_date, "rate_date")
    rate = _rate(b)
    if ExchangeRate.objects.filter(currency_code=currency, rate_date=rate_date).exists():
        raise Conflict("a rate for that currency and date already exists")
    ExchangeRate.objects.create(currency_code=currency, rate_date=rate_date, rate=rate)
    return {"currency_code": currency, "rate_date": rate_date.isoformat()}


def _rate_key(currency, date):
    currency = currency.strip().upper()
    rate_date = parse_date(date, "rate_date", status=BadRequest)
    rows = ExchangeRate.objects.filter(currency_code=currency, rate_date=rate_date)
    if not rows.exists():
        raise NotFound("exchange rate not found")
    return rows


def update_exchange_rate(currency, date, b):
    rows = _rate_key(currency, date)
    rows.update(rate=_rate(b))


def delete_exchange_rate(currency, date):
    _rate_key(currency, date).delete()
