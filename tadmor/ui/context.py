"""What every page needs: the signed-in user and the navigation."""

from django.urls import reverse

NAV = [
    ("Overview", [("Home", "home")]),
    ("Sales", [("Invoices", "sales-invoices"), ("Credit notes", "sales-credit-notes"),
               ("Customer payments", "customer-payments"), ("Sales orders", "sales-orders")]),
    ("Purchases", [("Bills", "purchase-bills"), ("Supplier credits", "purchase-credit-notes"),
                   ("Supplier payments", "supplier-payments"), ("Purchase orders", "purchase-orders")]),
    ("Inventory", [("Stock movements", "stock-movements"), ("Inventory valuation", "inventory-valuation")]),
    ("Accounting", [("Bank statements", "bank-statements"), ("Exchange rates", "exchange-rates"),
                    ("Periods and year-end", "periods")]),
    ("Reports", [("Profit and loss", "profit-and-loss"), ("Balance sheet", "balance-sheet"),
                 ("Cash flow", "cash-flow"), ("Trial balance", "trial-balance"),
                 ("AR aging", "ar-aging"), ("AP aging", "ap-aging")]),
    ("Master data", [("Organizations", "organizations"), ("Customers", "customers"), ("Suppliers", "suppliers"),
                     ("Products", "products"), ("Chart of accounts", "accounts"), ("Tax codes", "tax-codes"),
                     ("Payment terms", "payment-terms"), ("Warehouses", "warehouses")]),
    ("Administration", [("Users", "users"), ("Settings", "settings")]),
]
ADMIN_ONLY = {"users"}


def nav(request):
    user = getattr(request, "current_user", None)
    if user is None:
        return {"user": None}
    path = request.path
    groups = []
    for title, items in NAV:
        links = []
        for text, name in items:
            if name in ADMIN_ONLY and not user.is_admin:
                continue
            url = reverse(name)
            active = path == url if url == "/" else path.startswith(url)
            links.append({"text": text, "url": url, "active": active})
        groups.append({"title": title, "links": links})
    return {"user": user, "nav": groups}
