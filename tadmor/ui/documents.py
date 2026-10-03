"""Invoices, bills, credit notes, payments, and orders (domain §13.4–13.6).

One set of views serves the four line-item documents and, with small
differences, the two kinds of order: a list, a header-and-lines form, a
detail screen, and the actions each state allows. Payments get the
generic form and their own detail screen.
"""

from decimal import Decimal

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from ..models import Customer, GLSettings, Product, Supplier, TaxCode
from ..services import documents, orders, posting, settlement
from ..services.kinds import DOCUMENTS, ORDERS, PAYMENTS, PAYMENT_METHODS, DocKind, OrderKind
from ..values import Body, fmt4, today
from .. import printing
from . import choices as ch
from .base import Column, Field, attempt, crud_form, list_page, login_required


def _party_names(sales):
    model = Customer if sales else Supplier
    return dict(model.objects.select_related("organization").values_list("id", "organization__name"))


def _base_currency():
    return GLSettings.objects.get(pk=1).base_currency


def _line_rows(post, kind):
    """The line-item table of a posted form, as API line objects. Blank rows are dropped."""
    lk = kind.lines
    cols = {k: post.getlist("line_" + k) for k in ("product_id", "description", "quantity", "price", "account", "tax_code", "tax_rate")}
    out = []
    for i in range(len(cols["description"])):
        get = lambda k: (cols[k][i] if i < len(cols[k]) else "").strip()  # noqa: E731
        if not (get("product_id") or get("description") or get("price")):
            continue
        out.append({
            "product_id": int(get("product_id")) if get("product_id").isdigit() else None,
            "description": get("description"),
            "quantity": get("quantity") or None,
            lk.price: get("price") or None,
            lk.account: int(get("account")) if get("account").isdigit() else None,
            "tax_code": get("tax_code") or None,
            "tax_rate": get("tax_rate") or None,
        })
    return out


def _header_fields(kind):
    sales = kind.sales
    number = "order_number" if isinstance(kind, OrderKind) else kind.number
    date = "order_date" if isinstance(kind, OrderKind) else kind.date
    second = kind.expected_date if isinstance(kind, OrderKind) else ("due_date" if kind.has_due_date else None)
    fs = [
        Field(kind.party_id, "Customer" if sales else "Supplier", "ref", ch.customers if sales else ch.suppliers, required=True),
        Field(number, "Number", required=True),
        Field(date, "Date", "date", required=True),
    ]
    if second:
        fs.append(Field(second, "Due date" if second == "due_date" else "Expected " + ("ship" if sales else "receipt") + " date", "date"))
    fs += [
        Field("currency_code", "Currency", "select", ch.currencies, required=True),
        Field("reference", "Reference"),
        Field("memo", "Memo", "textarea"),
    ]
    return fs


def _form_context(kind, header_values, lines, error, title, back):
    from .base import Form

    lk = kind.lines
    sales = kind.sales
    products = {
        p.id: {"description": p.name, "tax_code": p.tax_code,
               "price": fmt4(p.unit_price) if sales else None,
               "account": p.revenue_account_id if sales else None}
        for p in Product.objects.filter(is_active=True)
    }
    taxes = {t.code: fmt4(t.rate) for t in TaxCode.objects.all()}
    model = Customer if sales else Supplier
    party_currency = {p.id: p.currency_code for p in model.objects.filter(is_active=True)}
    return {
        "title": title, "back": back, "kind": kind, "error": error,
        "header": Form(_header_fields(kind), header_values).rows(),
        "lines": lines or [], "price_label": "Unit price" if sales else "Unit cost",
        "account_label": "Revenue account" if sales else "Expense account",
        "product_choices": ch.products()(), "account_choices": ch.postable_accounts(),
        "tax_choices": ch.tax_codes(), "price_field": lk.price, "account_field": lk.account,
        "client_data": {"products": products, "taxes": taxes, "partyCurrency": party_currency},
        "is_order": isinstance(kind, OrderKind),
    }


def _document_form(request, kind, *, title, initial, initial_lines, save, back):
    error = None
    values, lines = dict(initial), initial_lines
    if request.method == "POST":
        fields = _header_fields(kind)
        data = {f.name: f.to_json(request.POST.get(f.name)) for f in fields}
        data["lines"] = _line_rows(request.POST, kind)
        result, error = attempt(save, Body(data))
        if error is None:
            return HttpResponseRedirect(reverse(kind.collection + "-detail", args=[result]))
        values = {f.name: request.POST.get(f.name, "") for f in fields}
        lines = data["lines"]
    if not lines:
        lines = [{}]
    lk = kind.lines
    rows = [{"product_id": l.get("product_id"), "description": l.get("description") or "",
             "quantity": l.get("quantity") or "1", "price": l.get(lk.price) or "",
             "account": l.get(lk.account), "tax_code": l.get("tax_code"), "tax_rate": l.get("tax_rate") or "0"}
            for l in lines]
    return render(request, "ui/document_form.html", _form_context(kind, values, rows, error, title, back))


# ---------------------------------------------------------------------------
# Invoices, bills, credit notes
# ---------------------------------------------------------------------------


def register_document(kind: DocKind):
    c = kind.collection
    plural = {"sales-invoices": "Invoices", "purchase-bills": "Bills",
              "sales-credit-notes": "Credit notes", "purchase-credit-notes": "Supplier credits"}[c]
    singular = {"sales-invoices": "invoice", "purchase-bills": "bill",
                "sales-credit-notes": "credit note", "purchase-credit-notes": "supplier credit"}[c]

    @login_required
    def list_view(request):
        names = _party_names(kind.sales)
        rows = documents.list_documents(kind)
        for r in rows:
            r["party_name"] = names.get(r[kind.party_id])
        cols = [Column("Number", kind.number), Column("Customer" if kind.sales else "Supplier", "party_name"),
                Column("Date", kind.date)]
        if kind.has_due_date:
            cols.append(Column("Due", "due_date"))
        cols += [Column("Currency", "currency_code"), Column("Total", "total", True, "amount"),
                 Column("Balance" if not kind.credit else "Unapplied", "balance", True, "amount"),
                 Column("Status", "status", kind="status"), Column(kind.status_field.split("_")[0].capitalize(), kind.status_field, kind="status")]
        return list_page(request, title=plural, rows=rows, columns=cols,
                         link=lambda r: reverse(c + "-detail", args=[r["id"]]),
                         new={"url": reverse(c + "-new"), "text": f"New {singular}"})

    @login_required
    def new_view(request):
        party = request.GET.get("party")
        initial = {kind.date: today().isoformat(), "currency_code": _base_currency(),
                   kind.party_id: int(party) if party and party.isdigit() else None}
        return _document_form(request, kind, title=f"New {singular}", initial=initial, initial_lines=[],
                              save=lambda b: documents.create(kind, b, request.current_user), back=reverse(c))

    @login_required
    def edit_view(request, id):
        doc = documents.document_json(kind, documents.get_document(kind, id))
        lines = documents.list_lines(kind, id)

        def save(b):
            documents.update(kind, id, b)
            return id

        return _document_form(request, kind, title=f"Edit {singular} {doc[kind.number]}", initial=doc,
                              initial_lines=lines, save=save, back=reverse(c + "-detail", args=[id]))

    def detail(request, id, action_error=None, email_result=None):
        d = documents.get_document(kind, id)
        doc = documents.document_json(kind, d)
        lines = documents.list_lines(kind, id)
        ctx = {
            "title": f"{kind.label} {doc[kind.number]}", "kind": kind, "c": c, "doc": doc,
            "number": doc[kind.number], "date": doc[kind.date],
            "party": getattr(d, kind.party).organization.name, "party_url": reverse(kind.party + "s-edit", args=[doc[kind.party_id]]),
            "lines": lines, "price_field": kind.lines.price, "subtotal": fmt4(d.subtotal), "tax_total": fmt4(d.tax_total),
            "order_linked": any(l["order_line_id"] for l in lines),
            "foreign": doc["currency_code"] != _base_currency(),
            "action_error": action_error or {}, "email_result": email_result,
            "settle_status": doc[kind.status_field],
            "can_apply": kind.credit and doc["status"] == "posted" and Decimal(doc["balance"]) > 0,
        }
        if kind.credit:
            target = DOCUMENTS["sales-invoices" if kind.sales else "purchase-bills"]
            ctx["applied_to"] = [dict(a, url=reverse(target.collection + "-detail", args=[a["document_id"]]))
                                 for a in documents.credit_note_applications(kind, id)]
        else:
            ctx["applied_from"] = [dict(a, url=reverse(a["collection"] + "-detail", args=[a["id"]]))
                                   for a in documents.applications_to(kind, id)]
        return render(request, "ui/document_detail.html", ctx)

    @login_required
    def detail_view(request, id):
        return detail(request, id)

    def action(fn, name, admin=False):
        @login_required
        def view(request, id):
            if request.method != "POST":
                return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
            if admin and not request.current_user.is_admin:
                return detail(request, id, {name: "Only administrators can do this."})
            _, error = attempt(fn, id)
            if error:
                return detail(request, id, {name: error})
            return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
        return view

    @login_required
    def delete_view(request, id):
        doc = documents.document_json(kind, documents.get_document(kind, id))
        error = None
        if request.method == "POST":
            _, error = attempt(documents.delete, kind, id)
            if error is None:
                return HttpResponseRedirect(reverse(c))
        return render(request, "ui/confirm.html", {
            "title": f"Delete {singular} {doc[kind.number]}?", "error": error,
            "question": f"This deletes draft {singular} {doc[kind.number]} and its lines. It cannot be undone.",
            "back": reverse(c + "-detail", args=[id])})

    @login_required
    def email_view(request, id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
        to = [a.strip() for a in request.POST.get("to", "").replace(";", ",").split(",") if a.strip()]
        sent, error = attempt(printing.email, c, id, to)
        if error:
            return detail(request, id, {"email": error})
        return detail(request, id, email_result=sent)

    return [
        path(f"{c}/", list_view, name=c),
        path(f"{c}/new", new_view, name=c + "-new"),
        path(f"{c}/<int:id>/", detail_view, name=c + "-detail"),
        path(f"{c}/<int:id>/edit", edit_view, name=c + "-edit"),
        path(f"{c}/<int:id>/delete", delete_view, name=c + "-delete"),
        path(f"{c}/<int:id>/post", action(lambda id: posting.post_document(kind, id), "post"), name=c + "-post"),
        path(f"{c}/<int:id>/unpost", action(lambda id: posting.unpost_document(kind, id), "unpost", admin=True), name=c + "-unpost"),
        path(f"{c}/<int:id>/apply", action(lambda id: settlement.apply(c, id), "apply"), name=c + "-apply"),
        path(f"{c}/<int:id>/email", email_view, name=c + "-email"),
    ]


urlpatterns = []
for _kind in DOCUMENTS.values():
    urlpatterns += register_document(_kind)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def register_payment(kind):
    c = kind.collection
    sales = kind.sales
    singular = "customer payment" if sales else "supplier payment"

    def fields():
        return [
            Field(kind.party_id, "Customer" if sales else "Supplier", "ref", ch.customers if sales else ch.suppliers, required=True),
            Field("payment_date", "Date", "date", required=True),
            Field("currency_code", "Currency", "select", ch.currencies, required=True),
            Field("amount", "Amount", "decimal", required=True),
            Field("method", "Method", "select", ch.static(*[(m, m.capitalize()) for m in PAYMENT_METHODS])),
            Field("reference", "Reference"),
            Field(kind.cash_account, "Deposit account" if sales else "Payment account", "ref",
                  ch.accounts(is_postable=True, is_cash=True), help="Posting needs it."),
        ]

    @login_required
    def list_view(request):
        names = _party_names(sales)
        rows = documents.list_payments(kind)
        for r in rows:
            r["party_name"] = names.get(r[kind.party_id])
        return list_page(
            request, title="Customer payments" if sales else "Supplier payments", rows=rows,
            columns=[Column("Date", "payment_date"), Column("Customer" if sales else "Supplier", "party_name"),
                     Column("Method", "method", kind="label"), Column("Currency", "currency_code"),
                     Column("Amount", "amount", True, "amount"), Column("Applied", "amount_applied", True, "amount"),
                     Column("Unapplied", "unapplied", True, "amount"), Column("Status", "status", kind="status")],
            link=lambda r: reverse(c + "-detail", args=[r["id"]]),
            new={"url": reverse(c + "-new"), "text": f"New {singular}"},
        )

    @login_required
    def new_view(request):
        party = request.GET.get("party")
        initial = {"payment_date": today().isoformat(), "currency_code": _base_currency(),
                   kind.party_id: int(party) if party and party.isdigit() else None}
        return crud_form(request, title=f"New {singular}", fields=fields(), initial=initial,
                         save=lambda b: documents.create_payment(kind, b, request.current_user),
                         done=lambda id: reverse(c + "-detail", args=[id]), back=reverse(c))

    @login_required
    def edit_view(request, id):
        p = documents.payment_json(kind, documents.get_payment(kind, id))
        return crud_form(request, title=f"Edit {singular}", fields=fields(), initial=p,
                         save=lambda b: documents.update_payment(kind, id, b),
                         done=lambda _: reverse(c + "-detail", args=[id]), back=reverse(c + "-detail", args=[id]))

    def detail(request, id, action_error=None):
        p = documents.get_payment(kind, id)
        pj = documents.payment_json(kind, p)
        apps = [dict(a, url=reverse(kind.documents.collection + "-detail", args=[a["document_id"]]))
                for a in documents.payment_applications(kind, id)]
        return render(request, "ui/payment_detail.html", {
            "title": f"{singular.capitalize()} {id}", "kind": kind, "c": c, "p": pj,
            "party": getattr(p, "customer" if sales else "supplier").organization.name,
            "cash_label": "Deposit account" if sales else "Payment account",
            "cash_account": _account_label(pj[kind.cash_account]), "applications": apps,
            "action_error": action_error or {}, "doc_label": "Invoice" if sales else "Bill",
        })

    @login_required
    def detail_view(request, id):
        return detail(request, id)

    def action(fn, name, admin=False):
        @login_required
        def view(request, id):
            if request.method != "POST":
                return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
            if admin and not request.current_user.is_admin:
                return detail(request, id, {name: "Only administrators can do this."})
            _, error = attempt(fn, id)
            if error:
                return detail(request, id, {name: error})
            return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
        return view

    @login_required
    def delete_view(request, id):
        documents.get_payment(kind, id)
        error = None
        if request.method == "POST":
            _, error = attempt(documents.delete_payment, kind, id)
            if error is None:
                return HttpResponseRedirect(reverse(c))
        return render(request, "ui/confirm.html", {
            "title": f"Delete {singular} {id}?", "error": error,
            "question": f"This deletes the draft {singular}. It cannot be undone.",
            "back": reverse(c + "-detail", args=[id])})

    return [
        path(f"{c}/", list_view, name=c),
        path(f"{c}/new", new_view, name=c + "-new"),
        path(f"{c}/<int:id>/", detail_view, name=c + "-detail"),
        path(f"{c}/<int:id>/edit", edit_view, name=c + "-edit"),
        path(f"{c}/<int:id>/delete", delete_view, name=c + "-delete"),
        path(f"{c}/<int:id>/post", action(lambda id: posting.post_payment(kind, id), "post"), name=c + "-post"),
        path(f"{c}/<int:id>/unpost", action(lambda id: posting.unpost_payment(kind, id), "unpost", admin=True), name=c + "-unpost"),
        path(f"{c}/<int:id>/apply", action(lambda id: settlement.apply(c, id), "apply"), name=c + "-apply"),
    ]


def _account_label(id):
    from ..models import Account

    a = Account.objects.filter(pk=id).first() if id else None
    return f"{a.code} {a.name}" if a else None


for _kind in PAYMENTS.values():
    urlpatterns += register_payment(_kind)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def register_order(kind: OrderKind):
    c = kind.collection
    sales = kind.sales
    singular = kind.noun

    @login_required
    def list_view(request):
        names = _party_names(sales)
        rows = orders.list_orders(kind)
        for r in rows:
            r["party_name"] = names.get(r[kind.party_id])
        return list_page(
            request, title="Sales orders" if sales else "Purchase orders", rows=rows,
            columns=[Column("Number", "order_number"), Column("Customer" if sales else "Supplier", "party_name"),
                     Column("Date", "order_date"), Column("Currency", "currency_code"),
                     Column("Total", "total", True, "amount"), Column("Status", "status", kind="status"),
                     Column(kind.billed.capitalize(), kind.billed + "_status", kind="status"),
                     Column(kind.moved.capitalize(), kind.moved + "_status", kind="status")],
            link=lambda r: reverse(c + "-detail", args=[r["id"]]),
            new={"url": reverse(c + "-new"), "text": f"New {singular}"},
        )

    @login_required
    def new_view(request):
        initial = {"order_date": today().isoformat(), "currency_code": _base_currency()}
        return _document_form(request, kind, title=f"New {singular}", initial=initial, initial_lines=[],
                              save=lambda b: documents.create(kind, b, request.current_user), back=reverse(c))

    @login_required
    def edit_view(request, id):
        o = orders.order_json(kind, orders.get_order(kind, id))
        lines = orders.order_lines(kind, id)

        def save(b):
            documents.update(kind, id, b)
            return id

        return _document_form(request, kind, title=f"Edit {singular} {o['order_number']}", initial=o,
                              initial_lines=lines, save=save, back=reverse(c + "-detail", args=[id]))

    def detail(request, id, action_error=None, email_result=None):
        o = orders.get_order(kind, id)
        oj = orders.order_json(kind, o)
        lines = orders.order_lines(kind, id)
        produced = _produced(kind, id)
        return render(request, "ui/order_detail.html", {
            "title": f"{kind.label} {oj['order_number']}", "kind": kind, "c": c, "o": oj, "lines": lines,
            "party": (o.customer if sales else o.supplier).organization.name,
            "price_field": kind.lines.price, "subtotal": fmt4(o.subtotal), "tax_total": fmt4(o.tax_total),
            "billed_q": "qty_" + kind.billed, "moved_q": "qty_" + kind.moved,
            "to_bill_q": "qty_to_" + kind.bill_verb, "to_move_q": "qty_to_" + kind.move_verb,
            "fulfilled": orders.is_fulfilled(kind, id),
            "can_bill": any(Decimal(l["qty_to_" + kind.bill_verb]) > 0 for l in lines),
            "can_move": any(Decimal(l["qty_to_" + kind.move_verb]) > 0 for l in lines),
            "produced": produced, "action_error": action_error or {}, "email_result": email_result,
        })

    @login_required
    def detail_view(request, id):
        return detail(request, id)

    def action(fn, name):
        @login_required
        def view(request, id):
            if request.method != "POST":
                return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
            _, error = attempt(fn, kind, id)
            if error:
                return detail(request, id, {name: error})
            return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
        return view

    @login_required
    def delete_view(request, id):
        o = orders.order_json(kind, orders.get_order(kind, id))
        error = None
        if request.method == "POST":
            _, error = attempt(documents.delete, kind, id)
            if error is None:
                return HttpResponseRedirect(reverse(c))
        return render(request, "ui/confirm.html", {
            "title": f"Delete {singular} {o['order_number']}?", "error": error,
            "question": f"This deletes draft {singular} {o['order_number']} and its lines. It cannot be undone.",
            "back": reverse(c + "-detail", args=[id])})

    @login_required
    def bill_view(request, id):
        """O5: invoice (or bill) the order, partially if quantities are lowered."""
        o = orders.order_json(kind, orders.get_order(kind, id))
        doc = kind.document
        lines = [l for l in orders.order_lines(kind, id) if Decimal(l["qty_to_" + kind.bill_verb]) > 0]
        error = None
        values = {"number": "", "date": today().isoformat(), "due_date": ""}
        qtys = {l["order_line_id"]: l["qty_to_" + kind.bill_verb] for l in lines}
        if request.method == "POST":
            values = {k: request.POST.get(k, "") for k in values}
            qtys = {l["order_line_id"]: request.POST.get(f"qty_{l['order_line_id']}", "0").strip() or "0" for l in lines}
            body = Body({doc.number: values["number"] or None, doc.date: values["date"] or None,
                         "due_date": values["due_date"] or None,
                         "lines": [{"order_line_id": k, "quantity": v} for k, v in qtys.items()]})
            new, error = attempt(orders.invoice, kind, id, body, request.current_user)
            if error is None:
                return HttpResponseRedirect(reverse(doc.collection + "-detail", args=[new]))
        return render(request, "ui/order_fulfil.html", {
            "title": f"{kind.bill_verb.capitalize()} {singular} {o['order_number']}", "error": error,
            "back": reverse(c + "-detail", args=[id]), "billing": True, "values": values, "kind": kind,
            "rows": [dict(l, chosen=qtys[l["order_line_id"]], remaining=l["qty_to_" + kind.bill_verb]) for l in lines],
            "number_label": "Invoice number" if sales else "Bill number", "date_label": "Invoice date" if sales else "Bill date",
        })

    @login_required
    def move_view(request, id):
        """O6: ship (or receive) the order's stocked lines into draft movements."""
        o = orders.order_json(kind, orders.get_order(kind, id))
        lines = [l for l in orders.order_lines(kind, id) if Decimal(l["qty_to_" + kind.move_verb]) > 0]
        error = created = None
        values = {"warehouse_id": "", "movement_date": today().isoformat(), "reference": o["order_number"]}
        qtys = {l["order_line_id"]: l["qty_to_" + kind.move_verb] for l in lines}
        if request.method == "POST":
            values = {k: request.POST.get(k, "") for k in values}
            qtys = {l["order_line_id"]: request.POST.get(f"qty_{l['order_line_id']}", "0").strip() or "0" for l in lines}
            wh = values["warehouse_id"]
            body = Body({"warehouse_id": int(wh) if wh.isdigit() else None, "movement_date": values["movement_date"] or None,
                         "reference": values["reference"] or None,
                         "lines": [{"order_line_id": k, "quantity": v} for k, v in qtys.items()]})
            created, error = attempt(orders.move, kind, id, body, request.current_user)
            if error is None:
                return render(request, "ui/order_moved.html", {
                    "title": f"{kind.moved.capitalize()} {singular} {o['order_number']}",
                    "movements": [(m, reverse("stock-movements-detail", args=[m])) for m in created],
                    "back": reverse(c + "-detail", args=[id]), "kind": kind})
        return render(request, "ui/order_fulfil.html", {
            "title": f"{kind.move_verb.capitalize()} {singular} {o['order_number']}", "error": error,
            "back": reverse(c + "-detail", args=[id]), "billing": False, "values": values, "kind": kind,
            "warehouses": ch.warehouses(),
            "rows": [dict(l, chosen=qtys[l["order_line_id"]], remaining=l["qty_to_" + kind.move_verb]) for l in lines],
        })

    @login_required
    def email_view(request, id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse(c + "-detail", args=[id]))
        to = [a.strip() for a in request.POST.get("to", "").replace(";", ",").split(",") if a.strip()]
        sent, error = attempt(printing.email, c, id, to)
        if error:
            return detail(request, id, {"email": error})
        return detail(request, id, email_result=sent)

    return [
        path(f"{c}/", list_view, name=c),
        path(f"{c}/new", new_view, name=c + "-new"),
        path(f"{c}/<int:id>/", detail_view, name=c + "-detail"),
        path(f"{c}/<int:id>/edit", edit_view, name=c + "-edit"),
        path(f"{c}/<int:id>/delete", delete_view, name=c + "-delete"),
        path(f"{c}/<int:id>/confirm", action(orders.confirm, "confirm"), name=c + "-confirm"),
        path(f"{c}/<int:id>/close", action(orders.close, "close"), name=c + "-close"),
        path(f"{c}/<int:id>/cancel", action(orders.cancel, "cancel"), name=c + "-cancel"),
        path(f"{c}/<int:id>/{kind.bill_verb}", bill_view, name=c + "-bill"),
        path(f"{c}/<int:id>/{kind.move_verb}", move_view, name=c + "-move"),
        path(f"{c}/<int:id>/email", email_view, name=c + "-email"),
    ]


def _produced(kind, id):
    """The documents and movements fulfilment has produced from an order."""
    from ..models import StockMovement

    doc = kind.document
    lk = doc.lines
    ids = lk.model.objects.filter(order_line_id__in=kind.lines.model.objects.filter(order_id=id).values("id")) \
        .values_list(lk.parent + "_id", flat=True).distinct()
    docs = [(getattr(d, doc.number), d.status, reverse(doc.collection + "-detail", args=[d.id]))
            for d in doc.model.objects.filter(pk__in=list(ids)).order_by("id")]
    source = "sales_order_line" if kind.sales else "purchase_order_line"
    moves = [(m.id, m.movement_date, "posted" if m.journal_entry_id else "draft", reverse("stock-movements-detail", args=[m.id]))
             for m in StockMovement.objects.filter(source_type=source, source_id__in=kind.lines.model.objects.filter(order_id=id).values("id")).order_by("id")]
    return {"documents": docs, "movements": moves}


for _kind in ORDERS.values():
    urlpatterns += register_order(_kind)
