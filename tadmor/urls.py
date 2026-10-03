from django.urls import include, path

from .api import endpoints
from .api.http import urlpatterns as api_urlpatterns

urlpatterns = [
    path("healthz", endpoints.healthz),
    path("readyz", endpoints.readyz),
    *api_urlpatterns(),
    path("", include("tadmor.ui.urls")),
]
