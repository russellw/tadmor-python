"""The home page (domain §13.2, H1–H5)."""

import datetime

from django.db.models import Count, Q, Sum
from django.shortcuts import render

from ..models import (
    PurchaseBill,
    PurchaseBillBalance,
    PurchaseOrder,
    SalesInvoice,
    SalesInvoiceBalance,
    SalesOrder,
)
from ..values import today
from .base import login_required


def _outstanding(view):
    """Posted documents with a positive balance, per currency, with the overdue part (H1)."""
    now = today()
    rows = (
        view.objects.filter(status="posted", balance__gt=0)
        .values("currency_code")
        .annotate(total=Sum("balance"), overdue=Sum("balance", filter=Q(due_date__lt=now)), n=Count("pk"))
        .order_by("currency_code")
    )
    return list(rows)


@login_required
def home(request):
    now = today()
    overdue = (
        SalesInvoiceBalance.objects.filter(status="posted", balance__gt=0, due_date__lt=now)
        .select_related("invoice__customer__organization").order_by("due_date", "invoice_id")[:10]
    )
    due_soon = (
        PurchaseBillBalance.objects.filter(status="posted", balance__gt=0, due_date__gte=now,
                                           due_date__lte=now + datetime.timedelta(days=14))
        .select_related("bill__supplier__organization").order_by("due_date", "bill_id")[:10]
    )
    return render(request, "ui/home.html", {
        "title": "Home",
        "receivables": _outstanding(SalesInvoiceBalance),
        "payables": _outstanding(PurchaseBillBalance),
        "counts": {
            "sales_orders": SalesOrder.objects.filter(status="open").count(),
            "purchase_orders": PurchaseOrder.objects.filter(status="open").count(),
            "draft_invoices": SalesInvoice.objects.filter(status="draft").count(),
            "draft_bills": PurchaseBill.objects.filter(status="draft").count(),
        },
        "overdue": overdue,
        "due_soon": due_soon,
        "today": now,
    })
