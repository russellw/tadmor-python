"""Pickers over active records (domain §13: "a picker over active records
for each reference")."""

from ..models import Account, Currency, Country, Customer, Organization, PaymentTerm, Product, Supplier, TaxCode, Warehouse


def accounts(**filters):
    def choices():
        return [(a.id, f"{a.code} {a.name}") for a in Account.objects.filter(is_active=True, **filters).order_by("code")]
    return choices


def postable_accounts():
    return accounts(is_postable=True)()


def currencies():
    return [(c.code, f"{c.code} {c.name}") for c in Currency.objects.order_by("code")]


def countries():
    return [(c.code, f"{c.code} {c.name}") for c in Country.objects.order_by("code")]


def organizations():
    return [(o.id, o.name) for o in Organization.objects.order_by("name")]


def customers():
    return [(c.id, f"{c.organization.name}" + (f" ({c.customer_number})" if c.customer_number else ""))
            for c in Customer.objects.filter(is_active=True).select_related("organization").order_by("organization__name")]


def suppliers():
    return [(s.id, f"{s.organization.name}" + (f" ({s.supplier_number})" if s.supplier_number else ""))
            for s in Supplier.objects.filter(is_active=True).select_related("organization").order_by("organization__name")]


def tax_codes():
    return [(t.code, f"{t.code} {t.name}") for t in TaxCode.objects.filter(is_active=True).order_by("code")]


def payment_terms():
    return [(p.code, p.name) for p in PaymentTerm.objects.order_by("due_days", "code")]


def products(**filters):
    def choices():
        return [(p.id, f"{p.sku} {p.name}") for p in Product.objects.filter(is_active=True, **filters).order_by("sku")]
    return choices


def warehouses():
    return [(w.id, f"{w.code} {w.name}") for w in Warehouse.objects.filter(is_active=True).order_by("code")]


def static(*pairs):
    return lambda: list(pairs)
