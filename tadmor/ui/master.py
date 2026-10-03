"""Master data screens (domain §13.3, M1–M8)."""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from ..services import master, users
from . import choices as ch
from .base import Column, Field, Form, admin_required, attempt, crud_form, form_values, list_page, login_required

ACTIVE = Field("is_active", "Active", "bool")


def register(name, *, title, singular, list_rows, columns, fields, get_json, create, update, key=int, keep=()):
    """URL patterns for a master-data collection: list, new, and edit.

    `fields` is a callable taking the record being edited (or None), so a
    form can depend on it. `keep` names body fields the form does not show
    but must send back unchanged, since an update replaces the whole record.
    """

    @login_required
    def list_view(request):
        return list_page(request, title=title, rows=list_rows(), columns=columns,
                         link=lambda r: reverse(name + "-edit", args=[r.get("id", r.get("code"))]),
                         new={"url": reverse(name + "-new"), "text": f"New {singular}"})

    @login_required
    def new_view(request):
        return crud_form(request, title=f"New {singular}", fields=fields(None), initial={},
                         save=create, done=lambda _: reverse(name), back=reverse(name))

    @login_required
    def edit_view(request, id):
        record = get_json(key(id))
        fs = fields(record)

        def save(body):
            for k in keep:
                body.data[k] = record.get(k)
            return update(key(id), body)

        return crud_form(request, title=f"Edit {singular}", fields=fs, initial=record, save=save,
                         done=lambda _: reverse(name), back=reverse(name), editing=True)

    conv = "int" if key is int else "str"
    return [
        path(f"{name}/", list_view, name=name),
        path(f"{name}/new", new_view, name=name + "-new"),
        path(f"{name}/<{conv}:id>/", edit_view, name=name + "-edit"),
    ]


def _with_active(fs, record):
    return fs + [ACTIVE] if record is not None else fs


def _org_names():
    from ..models import Organization

    return dict(Organization.objects.values_list("id", "name"))


# ---------------------------------------------------------------------------
# M1 Organizations
# ---------------------------------------------------------------------------

ORGANIZATION_FIELDS = [
    Field("name", "Name", required=True),
    Field("legal_name", "Legal name"),
    Field("tax_id", "Tax ID"),
    Field("country_code", "Country", "select", ch.countries),
    Field("default_currency", "Default currency", "select", ch.currencies),
    Field("email", "Email", "email", help="Documents are emailed here unless another recipient is given."),
    Field("is_self", "This is our own company (shown as the issuer on printed documents)", "bool"),
]

urlpatterns = register(
    "organizations", title="Organizations", singular="organization", list_rows=master.list_organizations,
    columns=[Column("Name", "name"), Column("Legal name", "legal_name"), Column("Tax ID", "tax_id"),
             Column("Country", "country_code"), Column("Currency", "default_currency"),
             Column("Own company", "is_self", kind="bool")],
    fields=lambda r: ORGANIZATION_FIELDS,
    get_json=lambda id: master.organization_json(master.get_organization(id)),
    create=master.create_organization, update=master.update_organization,
)


# ---------------------------------------------------------------------------
# M2 Customers and suppliers
# ---------------------------------------------------------------------------


def _party_rows(rows):
    names = _org_names()
    for r in rows:
        r["organization_name"] = names.get(r["organization_id"])
    return rows


def _party_fields(number, control, control_label):
    def fields(record):
        fs = [
            Field("organization_id", "Organization", "ref", ch.organizations, required=True, readonly_on_edit=True),
            Field(number, "Number"),
            Field(control, control_label, "ref", ch.accounts(is_postable=True),
                  help="The control account its documents post to; posting needs it."),
            Field("payment_terms_code", "Payment terms", "select", ch.payment_terms),
            Field("currency_code", "Currency", "select", ch.currencies),
            Field("tax_code", "Default tax code", "select", ch.tax_codes),
        ]
        if number == "customer_number":
            fs.append(Field("credit_limit", "Credit limit", "decimal"))
        return _with_active(fs, record)

    return fields


urlpatterns += register(
    "customers", title="Customers", singular="customer",
    list_rows=lambda: _party_rows(master.list_customers()),
    columns=[Column("Organization", "organization_name"), Column("Number", "customer_number"),
             Column("Currency", "currency_code"), Column("Tax code", "tax_code"),
             Column("Terms", "payment_terms_code"), Column("Credit limit", "credit_limit", True, "amount"),
             Column("Status", "is_active", kind="active")],
    fields=_party_fields("customer_number", "ar_account_id", "A/R account"),
    get_json=lambda id: master.customer_json(master.get_customer(id)),
    create=master.create_customer, update=master.update_customer,
)

urlpatterns += register(
    "suppliers", title="Suppliers", singular="supplier",
    list_rows=lambda: _party_rows(master.list_suppliers()),
    columns=[Column("Organization", "organization_name"), Column("Number", "supplier_number"),
             Column("Currency", "currency_code"), Column("Tax code", "tax_code"),
             Column("Terms", "payment_terms_code"), Column("Status", "is_active", kind="active")],
    fields=_party_fields("supplier_number", "ap_account_id", "A/P account"),
    get_json=lambda id: master.supplier_json(master.get_supplier(id)),
    create=master.create_supplier, update=master.update_supplier,
)


# ---------------------------------------------------------------------------
# M3 Products
# ---------------------------------------------------------------------------

PRODUCT_FIELDS = [
    Field("sku", "SKU", required=True),
    Field("name", "Name", required=True),
    Field("description", "Description", "textarea"),
    Field("unit_price", "Unit price", "decimal"),
    Field("currency_code", "Currency", "select", ch.currencies),
    Field("tax_code", "Tax code", "select", ch.tax_codes),
    Field("revenue_account_id", "Revenue account", "ref", ch.accounts(is_postable=True),
          help="Used by invoice and credit-note lines that name no account."),
    Field("track_inventory", "Track inventory", "bool"),
    Field("inventory_account_id", "Inventory account", "ref", ch.accounts(is_postable=True),
          help="Stock postings use it; bill lines that name no account are expensed to it."),
    Field("cogs_account_id", "COGS account", "ref", ch.accounts(is_postable=True)),
]

urlpatterns += register(
    "products", title="Products", singular="product", list_rows=master.list_products,
    columns=[Column("SKU", "sku"), Column("Name", "name"), Column("Unit price", "unit_price", True, "amount"),
             Column("Currency", "currency_code"), Column("Tax code", "tax_code"),
             Column("Inventory", "track_inventory", kind="bool"), Column("Status", "is_active", kind="active")],
    fields=lambda r: _with_active(PRODUCT_FIELDS, r),
    get_json=lambda id: master.product_json(master.get_product(id)),
    create=master.create_product, update=master.update_product,
)


# ---------------------------------------------------------------------------
# M4 Chart of accounts
# ---------------------------------------------------------------------------


def _account_fields(record):
    own = record["id"] if record else None

    def parents():  # never offers the account itself (M4)
        return [(i, t) for i, t in ch.accounts()() if i != own]

    fs = [
        Field("code", "Code", required=True),
        Field("name", "Name", required=True),
        Field("account_type", "Type", "select", ch.static(*[(t, t.capitalize()) for t in master.ACCOUNT_TYPES]), required=True),
        Field("parent_id", "Parent", "ref", parents),
        Field("currency_code", "Currency", "select", ch.currencies),
        Field("is_postable", "Postable (unticked makes a summary account)", "bool"),
        Field("is_cash", "Cash account (assets only)", "bool"),
        Field("cash_flow_activity", "Cash-flow activity", "select",
              ch.static(*[(a, a.capitalize()) for a in master.ACTIVITIES])),
    ]
    return _with_active(fs, record)


urlpatterns += register(
    "accounts", title="Chart of accounts", singular="account", list_rows=master.list_accounts,
    columns=[Column("Code", "code"), Column("Name", "name"), Column("Type", "account_type", kind="status"),
             Column("Currency", "currency_code"), Column("Postable", "is_postable", kind="bool"),
             Column("Cash", "is_cash", kind="bool"), Column("Status", "is_active", kind="active")],
    fields=_account_fields,
    get_json=lambda id: master.account_json(master.get_account(id)),
    create=master.create_account, update=master.update_account,
)


# ---------------------------------------------------------------------------
# M5 Tax codes, payment terms, warehouses
# ---------------------------------------------------------------------------

urlpatterns += register(
    "tax-codes", title="Tax codes", singular="tax code", list_rows=master.list_tax_codes,
    columns=[Column("Code", "code"), Column("Name", "name"), Column("Rate %", "rate", True, "qty"),
             Column("Status", "is_active", kind="active")],
    fields=lambda r: _with_active([
        Field("code", "Code", required=True, readonly_on_edit=True),
        Field("name", "Name", required=True),
        Field("rate", "Rate (percent)", "decimal"),
        Field("tax_account_id", "Tax account", "ref", ch.accounts(is_postable=True),
              help="Taxed lines post here; without one, taxed lines cannot post."),
    ], r),
    get_json=lambda code: master.tax_code_json(master.get_tax_code(code)),
    create=master.create_tax_code, update=master.update_tax_code, key=str,
)

urlpatterns += register(
    "payment-terms", title="Payment terms", singular="payment term", list_rows=master.list_payment_terms,
    columns=[Column("Code", "code"), Column("Name", "name"), Column("Due days", "due_days", True)],
    fields=lambda r: [
        Field("code", "Code", required=True, readonly_on_edit=True),
        Field("name", "Name", required=True),
        Field("due_days", "Due days", "int"),
    ],
    get_json=lambda code: master.payment_term_json(master.get_payment_term(code)),
    create=master.create_payment_term, update=master.update_payment_term, key=str,
)

urlpatterns += register(
    "warehouses", title="Warehouses", singular="warehouse", list_rows=master.list_warehouses,
    columns=[Column("Code", "code"), Column("Name", "name"), Column("Status", "is_active", kind="active")],
    fields=lambda r: _with_active([Field("code", "Code", required=True), Field("name", "Name", required=True)], r),
    get_json=lambda id: master.warehouse_json(master.get_warehouse(id)),
    create=master.create_warehouse, update=master.update_warehouse, keep=("address_id",),
)


# ---------------------------------------------------------------------------
# M7 Users (administrators only)
# ---------------------------------------------------------------------------


@admin_required
def users_list(request):
    return list_page(
        request, title="Users", rows=users.list_users(),
        columns=[Column("Email", "email"), Column("Name", "full_name"),
                 Column("Role", lambda r: "Administrator" if r["is_admin"] else "User"),
                 Column("Status", "is_active", kind="active")],
        link=lambda r: reverse("users-edit", args=[r["id"]]),
        new={"url": reverse("users-new"), "text": "New user"},
    )


@admin_required
def users_new(request):
    return crud_form(
        request, title="New user", initial={},
        fields=[Field("email", "Email", "email", required=True), Field("full_name", "Name", required=True),
                Field("password", "Password", "password", required=True, help="At least 8 characters."),
                Field("is_admin", "Administrator", "bool")],
        save=users.create_user, done=lambda _: reverse("users"), back=reverse("users"),
    )


@admin_required
def users_edit(request, id):
    record = users.user_json(users.get_user(id))
    return crud_form(
        request, title="Edit user", initial=record,
        fields=[Field("email", "Email", "email", required=True), Field("full_name", "Name", required=True),
                ACTIVE, Field("is_admin", "Administrator", "bool")],
        save=lambda b: users.update_user(request.current_user, id, b),
        done=lambda _: reverse("users"), back=reverse("users"),
        extra={"sections": [{"template": "ui/user_password_link.html"}], "record": record},
    )


@admin_required
def users_password(request, id):
    record = users.user_json(users.get_user(id))
    return crud_form(
        request, title=f"Reset password for {record['email']}", initial={},
        fields=[Field("password", "New password", "password", required=True,
                      help="At least 8 characters. Signs the user out everywhere.")],
        save=lambda b: users.set_password(id, b),
        done=lambda _: reverse("users-edit", args=[id]), back=reverse("users-edit", args=[id]),
    )


urlpatterns += [
    path("users/", users_list, name="users"),
    path("users/new", users_new, name="users-new"),
    path("users/<int:id>/", users_edit, name="users-edit"),
    path("users/<int:id>/password", users_password, name="users-password"),
]


# ---------------------------------------------------------------------------
# M8 Settings
# ---------------------------------------------------------------------------


@login_required
def settings_view(request):
    record = master.settings_json(master.settings())
    fields = [
        Field("base_currency", "Base currency", "select", ch.currencies, required=True,
              help="Cannot change once any journal entry exists."),
        Field("fx_gain_loss_account_id", "FX gain/loss account", "ref", ch.accounts(is_postable=True),
              help="Realized exchange differences on settlement post here."),
    ]
    admin = request.current_user.is_admin
    form = Form(fields, dict(record))
    error = notice = None
    if request.method == "POST" and admin:
        _, error = attempt(master.update_settings, form.body(request.POST))
        if error is None:
            return HttpResponseRedirect(reverse("settings") + "?saved=1")
        form.values.update(form_values(request.POST, fields))
    if request.GET.get("saved"):
        notice = "Settings saved."
    return render(request, "ui/form.html", {
        "title": "Settings", "form": form.rows(), "error": error, "back": "/", "readonly": not admin,
        "notice": notice,
    })


urlpatterns += [path("settings", settings_view, name="settings")]
