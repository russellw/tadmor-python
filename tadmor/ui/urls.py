from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve

from . import accounting, auth, documents, home, master, reports, stock

urlpatterns = [
    path("", home.home, name="home"),
    path("login", auth.login, name="login"),
    path("logout", auth.logout, name="logout"),
    re_path(r"^static/(?P<path>.+)$", serve, {"document_root": settings.BASE_DIR / "tadmor" / "static"}),
    *master.urlpatterns,
    *documents.urlpatterns,
    *stock.urlpatterns,
    *reports.urlpatterns,
    *accounting.urlpatterns,
]
