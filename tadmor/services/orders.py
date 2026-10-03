"""Sales and purchase orders: lifecycle and fulfilment (domain §6).

Orders never post. Fulfilment creates draft documents and stock
movements linked back to the order lines, and the fulfilment views derive
what has been invoiced, billed, shipped, or received from those links.
"""

from django.db import connection, transaction

from ..errors import BadRequest, Conflict, NotFound, Unprocessable
from ..models import Product, StockMovement
from ..values import MONEY, fmt4, fmt_date, round4, today
from .documents import _check_number_free, line_json, lines_of
from .kinds import OrderKind
from .posting import rate_for


def order_json(kind: OrderKind, o):
    f = o.fulfilment
    return {
        "id": o.id, "order_number": o.order_number, kind.party_id: getattr(o, kind.party_id),
        "order_date": fmt_date(o.order_date), kind.expected_date: fmt_date(getattr(o, kind.expected_date)),
        "currency_code": o.currency_code, "status": o.status, "total": fmt4(o.total),
        kind.billed + "_status": getattr(f, kind.billed + "_status"),
        kind.moved + "_status": getattr(f, kind.moved + "_status"),
        "reference": o.reference, "memo": o.memo,
    }


def _orders(kind):
    party = "customer" if kind.sales else "supplier"
    return kind.model.objects.select_related("fulfilment", party + "__organization")


def list_orders(kind):
    return [order_json(kind, o) for o in _orders(kind).order_by("-order_date", "-id")]


def get_order(kind, id):
    o = _orders(kind).filter(pk=id).first()
    if o is None:
        raise NotFound(f"{kind.noun} not found")
    return o


def order_lines(kind, id):
    get_order(kind, id)
    out = []
    for line in lines_of(kind, id).select_related("fulfilment"):
        j = line_json(kind, line)
        j["order_line_id"] = line.id
        for name in ("qty_" + kind.billed, "qty_" + kind.moved, "qty_to_" + kind.bill_verb, "qty_to_" + kind.move_verb):
            j[name] = fmt4(getattr(line.fulfilment, name))
        out.append(j)
    return out


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _lock(kind, id):
    o = kind.model.objects.select_for_update().filter(pk=id).first()
    if o is None:
        raise NotFound(f"{kind.noun} not found")
    return o


def confirm(kind, id):
    with transaction.atomic():
        o = _lock(kind, id)
        if o.status != "draft":
            raise Conflict(f"the {kind.noun} is {o.status}, not draft")
        if not lines_of(kind, id).exists():
            raise Unprocessable(f"the {kind.noun} has no lines")
        kind.model.objects.filter(pk=id).update(status="open")


def close(kind, id):
    with transaction.atomic():
        o = _lock(kind, id)
        if o.status != "open":
            raise Conflict(f"the {kind.noun} is {o.status}, not open")
        kind.model.objects.filter(pk=id).update(status="closed")


def _fulfilled(kind, id):
    rows = kind.line_fulfilment.objects.filter(order_line__order_id=id)
    return rows.filter(**{f"qty_{kind.billed}__gt": 0}).exists() or rows.filter(**{f"qty_{kind.moved}__gt": 0}).exists()


def cancel(kind, id):
    with transaction.atomic():
        o = _lock(kind, id)
        if o.status not in ("draft", "open"):
            raise Conflict(f"the {kind.noun} is {o.status}")
        if o.status == "open" and _fulfilled(kind, id):
            raise Conflict(f"the {kind.noun} has been partly fulfilled and cannot be cancelled")
        kind.model.objects.filter(pk=id).update(status="cancelled")


def is_fulfilled(kind, id):
    return _fulfilled(kind, id)


# ---------------------------------------------------------------------------
# Fulfilment
# ---------------------------------------------------------------------------


def _requested(b):
    """{order_line_id: quantity} from a fulfilment body; empty means everything."""
    out = {}
    for i, line in enumerate(b.list("lines"), 1):
        line_id = line.int("order_line_id")
        if line_id is None:
            raise BadRequest(f"line {i}: order_line_id is required")
        out[line_id] = line.decimal("quantity", MONEY, default=0)
    return out


def _quantity(remaining, requested, line_id):
    if not requested:
        return remaining
    return min(remaining, requested.get(line_id, 0))


def _open_order(kind, id):
    o = _lock(kind, id)
    if o.status != "open":
        raise Conflict(f"the {kind.noun} is {o.status}, not open")
    return o


def invoice(kind: OrderKind, id, b, user=None):
    """Invoice a sales order or bill a purchase order (domain §6.3)."""
    doc = kind.document
    number = b.required_str(doc.number)
    b.required_str(doc.date)
    with transaction.atomic():
        o = _open_order(kind, id)
        date = b.date(doc.date)
        due = b.date("due_date")
        if due is not None and due < date:
            raise Unprocessable(f"due_date must not be before {doc.date}")
        requested = _requested(b)
        to_bill = "qty_to_" + kind.bill_verb
        lk = kind.lines
        picked = []
        for line in lines_of(kind, id).select_related("fulfilment").order_by("id"):
            qty = _quantity(getattr(line.fulfilment, to_bill), requested, line.id)
            if qty > 0:
                picked.append((line, qty))
        if not picked:
            raise Unprocessable(f"nothing is left to {kind.bill_verb} on this {kind.noun}")
        header = {doc.number: number, doc.party_id: getattr(o, kind.party_id), doc.date: date, "due_date": due,
                  "currency_code": o.currency_code, "reference": o.order_number}
        _check_number_free(doc, header)
        new = doc.model.objects.create(created_by=user.id if user else None, **header)
        doc.lines.model.objects.bulk_create(
            doc.lines.model(
                **{doc.lines.parent + "_id": new.id}, line_no=n, product_id=line.product_id,
                description=line.description, quantity=qty, tax_code=line.tax_code, tax_rate=line.tax_rate,
                order_line_id=line.id, **{lk.price: getattr(line, lk.price), lk.account: getattr(line, lk.account)},
            )
            for n, (line, qty) in enumerate(picked, 1)
        )
        return new.id


def _avg_cost(product_id, warehouse_id):
    """The moving-average unit cost of a product in a warehouse (domain §6.3)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT avg_unit_cost FROM stock_on_hand WHERE product_id = %s AND warehouse_id = %s",
            [product_id, warehouse_id],
        )
        row = cur.fetchone()
    return row[0] if row else 0


def move(kind: OrderKind, id, b, user=None):
    """Ship a sales order or receive a purchase order into draft movements."""
    warehouse = b.required_id("warehouse_id")
    with transaction.atomic():
        o = _open_order(kind, id)
        date = b.date("movement_date") or today()
        requested = _requested(b)
        rate = None if kind.sales else rate_for(o.currency_code, date)
        to_move = "qty_to_" + kind.move_verb
        lines = list(lines_of(kind, id).select_related("fulfilment").order_by("id"))
        tracked = set(
            Product.objects.filter(pk__in=[l.product_id for l in lines if l.product_id], track_inventory=True, is_active=True)
            .values_list("id", flat=True)
        )
        created = []
        for line in lines:
            if line.product_id not in tracked:
                continue
            qty = _quantity(getattr(line.fulfilment, to_move), requested, line.id)
            if qty <= 0:
                continue
            if kind.sales:
                cost, signed, mtype, source = _avg_cost(line.product_id, warehouse), -qty, "issue", "sales_order_line"
            else:
                cost, signed, mtype, source = round4(line.unit_cost * rate), qty, "receipt", "purchase_order_line"
            created.append(StockMovement.objects.create(
                product_id=line.product_id, warehouse_id=warehouse, movement_date=date, movement_type=mtype,
                quantity=signed, unit_cost=cost, source_type=source, source_id=line.id,
                reference=b.text("reference"), created_by=user.id if user else None,
            ).id)
        if not created:
            raise Unprocessable(f"nothing is left to {kind.move_verb} on this {kind.noun}")
        return created
