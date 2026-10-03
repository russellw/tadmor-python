"""Stock movements (spec/api.md §5.12, domain §3, §4).

The movement ledger is append-only in spirit: quantity on hand and value
are sums over movements. A movement is posted exactly when it carries a
journal entry; only receipts and issues post.
"""

from django.db import transaction

from ..errors import BadRequest, Conflict, NotFound, Unprocessable
from ..models import Product, StockMovement
from ..values import MONEY, fmt4, fmt_date, round4, today, check_magnitude

TYPES = ("receipt", "issue", "adjustment", "transfer_in", "transfer_out")
POSITIVE = ("receipt", "transfer_in")
NEGATIVE = ("issue", "transfer_out")


def movement_json(sm):
    return {
        "id": sm.id, "product_id": sm.product_id, "warehouse_id": sm.warehouse_id,
        "movement_date": fmt_date(sm.movement_date), "movement_type": sm.movement_type,
        "status": "posted" if sm.journal_entry_id else "draft", "quantity": fmt4(sm.quantity),
        "unit_cost": fmt4(sm.unit_cost), "total_cost": fmt4(sm.total_cost), "reference": sm.reference,
        "notes": sm.notes, "journal_entry_id": sm.journal_entry_id, "source_type": sm.source_type,
    }


def _movements():
    return StockMovement.objects.select_related("product", "warehouse")


def list_movements():
    return [movement_json(sm) for sm in _movements().order_by("-movement_date", "-id")]


def get_movement(id):
    sm = _movements().filter(pk=id).first()
    if sm is None:
        raise NotFound("stock movement not found")
    return sm


def _required(b):
    b.required_id("product_id")
    b.required_id("warehouse_id")
    b.required_str("movement_type")
    if not b.str("quantity"):
        raise BadRequest("quantity is required")


def _fields(b):
    mtype = b.str("movement_type")
    if mtype not in TYPES:
        raise Unprocessable("movement_type must be one of " + ", ".join(TYPES))
    qty = b.decimal("quantity", MONEY)
    cost = b.decimal("unit_cost", MONEY, default=0)
    if qty == 0:
        raise Unprocessable("quantity must not be zero")
    if (mtype in POSITIVE and qty < 0) or (mtype in NEGATIVE and qty > 0):
        raise Unprocessable(f"a {mtype} must have a {'positive' if mtype in POSITIVE else 'negative'} quantity")
    if cost < 0:
        raise Unprocessable("unit_cost must not be negative")
    check_magnitude(round4(qty * cost), "total cost")
    product = Product.objects.filter(pk=b.int("product_id")).first()
    if product is None:
        raise Unprocessable("unknown product_id")
    if not product.track_inventory or not product.is_active:
        raise Unprocessable("the product must be active and inventory-tracked")
    return {
        "product_id": product.id, "warehouse_id": b.int("warehouse_id"), "movement_type": mtype,
        "movement_date": b.date("movement_date") or today(), "quantity": qty, "unit_cost": cost,
        "reference": b.text("reference"), "notes": b.text("notes"),
    }


def create_movement(b, user=None):
    _required(b)
    return StockMovement.objects.create(created_by=user.id if user else None, **_fields(b)).id


def _lock(id):
    sm = StockMovement.objects.select_for_update().filter(pk=id).first()
    if sm is None:
        raise NotFound("stock movement not found")
    if sm.journal_entry_id is not None:
        raise Conflict("the stock movement is posted")
    return sm


def update_movement(id, b):
    _required(b)
    with transaction.atomic():
        sm = _lock(id)
        if sm.source_type is not None:
            raise Conflict("the stock movement was produced by order fulfilment and cannot be edited")
        StockMovement.objects.filter(pk=id).update(**_fields(b))


def delete_movement(id):
    with transaction.atomic():
        _lock(id)
        StockMovement.objects.filter(pk=id).delete()
