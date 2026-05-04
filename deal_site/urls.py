from django.contrib import admin
from django.urls import path, include


urlpatterns = [
    path("admin/", admin.site.urls),
    # Send homepage requests to the deals app.
    path("", include("deals.urls")),
]