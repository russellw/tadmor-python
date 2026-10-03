"""Stock movements (domain §13.7, S1–S3)."""

from decimal import Decimal, InvalidOperation

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from ..models import Account, Product, Warehouse
from ..services import posting, stock
from ..values import today
from . import choices as ch
from .base import Column, Field, attempt, crud_form, list_page, login_required

TYPE_LABELS = [("receipt", "Receipt (in)"), ("issue", "Issue (out)"), ("adjustment", "Adjustment (signed as typed)"),
               ("transfer_in", "Transfer in"), ("transfer_out", "Transfer out")]

FIELDS = [
    Field("product_id", "Product", "ref", ch.products(track_inventory=True), required=True,
          help="Only active, inventory-tracked products."),
    Field("warehouse_id", "Warehouse", "ref", ch.warehouses, required=True),
    Field("movement_type", "Type", "select", ch.static(*TYPE_LABELS), required=True),
    Field("movement_date", "Date", "date"),
    Field("quantity", "Quantity", "decimal", required=True,
          help="Enter the amount moved; receipts add stock and issues remove it. An adjustment keeps the sign you type."),
    Field("unit_cost", "Unit cost", "decimal"),
    Field("reference", "Reference"),
    Field("notes", "Notes", "textarea"),
]


def _signed(body):
    """S2: the quantity is entered as a magnitude and signed by the type."""
    mtype, q = body.data.get("movement_type"), body.data.get("quantity")
    if q and mtype in stock.POSITIVE + stock.NEGATIVE:
        try:
            magnitude = abs(Decimal(q))
        except InvalidOperation:
            return body
        body.data["quantity"] = f"{-magnitude if mtype in stock.NEGATIVE else magnitude}"
    return body


@login_required
def list_view(request):
    products = dict(Product.objects.values_list("id", "sku"))
    warehouses = dict(Warehouse.objects.values_list("id", "code"))
    rows = stock.list_movements()
    for r in rows:
        r["product"] = products.get(r["product_id"])
        r["warehouse"] = warehouses.get(r["warehouse_id"])
    return list_page(
        request, title="Stock movements", rows=rows,
        columns=[Column("Date", "movement_date"), Column("Product", "product"), Column("Warehouse", "warehouse"),
                 Column("Type", "movement_type", kind="status"), Column("Quantity", "quantity", True, "qty"),
                 Column("Unit cost", "unit_cost", True, "amount"), Column("Total cost", "total_cost", True, "amount"),
                 Column("Posted", lambda r: r["status"] == "posted", kind="bool")],
        link=lambda r: reverse("stock-movements-detail", args=[r["id"]]),
        new={"url": reverse("stock-movements-new"), "text": "New stock movement"},
    )


@login_required
def new_view(request):
    return crud_form(request, title="New stock movement", fields=FIELDS, initial={"movement_date": today().isoformat()},
                     save=lambda b: stock.create_movement(_signed(b), request.current_user),
                     done=lambda id: reverse("stock-movements-detail", args=[id]), back=reverse("stock-movements"))


@login_required
def edit_view(request, id):
    m = stock.movement_json(stock.get_movement(id))
    initial = dict(m)
    if m["movement_type"] != "adjustment":
        initial["quantity"] = f"{abs(Decimal(m['quantity']))}"

    def save(b):
        stock.update_movement(id, _signed(b))

    return crud_form(request, title=f"Edit stock movement {id}", fields=FIELDS, initial=initial, save=save,
                     done=lambda _: reverse("stock-movements-detail", args=[id]),
                     back=reverse("stock-movements-detail", args=[id]))


def detail(request, id, action_error=None):
    sm = stock.get_movement(id)
    m = stock.movement_json(sm)
    grni = Account.objects.filter(code="2150").values_list("id", flat=True).first()
    return render(request, "ui/movement_detail.html", {
        "title": f"Stock movement {id}", "m": m, "product": sm.product, "warehouse": sm.warehouse,
        "credit_choices": ch.postable_accounts(), "default_credit": request.POST.get("credit_account_id") or grni,
        "action_error": action_error or {},
    })


@login_required
def detail_view(request, id):
    return detail(request, id)


def action(fn, name, admin=False):
    @login_required
    def view(request, id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("stock-movements-detail", args=[id]))
        if admin and not request.current_user.is_admin:
            return detail(request, id, {name: "Only administrators can do this."})
        _, error = attempt(fn, request, id)
        if error:
            return detail(request, id, {name: error})
        return HttpResponseRedirect(reverse("stock-movements-detail", args=[id]))
    return view


def _post(request, id):
    raw = request.POST.get("credit_account_id", "")
    return posting.post_movement(id, int(raw) if raw.isdigit() else None)


@login_required
def delete_view(request, id):
    stock.get_movement(id)
    error = None
    if request.method == "POST":
        _, error = attempt(stock.delete_movement, id)
        if error is None:
            return HttpResponseRedirect(reverse("stock-movements"))
    return render(request, "ui/confirm.html", {
        "title": f"Delete stock movement {id}?", "error": error,
        "question": "This deletes the unposted movement. A movement made by order fulfilment returns its quantity to the order.",
        "back": reverse("stock-movements-detail", args=[id])})


urlpatterns = [
    path("stock-movements/", list_view, name="stock-movements"),
    path("stock-movements/new", new_view, name="stock-movements-new"),
    path("stock-movements/<int:id>/", detail_view, name="stock-movements-detail"),
    path("stock-movements/<int:id>/edit", edit_view, name="stock-movements-edit"),
    path("stock-movements/<int:id>/delete", delete_view, name="stock-movements-delete"),
    path("stock-movements/<int:id>/post", action(_post, "post"), name="stock-movements-post"),
    path("stock-movements/<int:id>/unpost", action(lambda r, id: posting.unpost_movement(id), "unpost", admin=True),
         name="stock-movements-unpost"),
]
