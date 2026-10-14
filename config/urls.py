from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from apps.core.views import logo

urlpatterns = [path("", RedirectView.as_view(pattern_name="admin:index", permanent=False)),
    path("admin/branding/logo/", admin.site.admin_view(logo), name="gym-logo"),
    path("admin/", admin.site.urls)]
