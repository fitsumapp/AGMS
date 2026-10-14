from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from apps.core.views import logo
from apps.branches.views import switch_branch

urlpatterns = [path("", RedirectView.as_view(pattern_name="admin:index", permanent=False)),
    path("admin/branding/logo/", admin.site.admin_view(logo), name="gym-logo"),
    path("admin/branches/switch/", switch_branch, name="switch-branch"),
    path("admin/", admin.site.urls)]
