from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from apps.core.views import logo
from apps.branches.views import switch_branch
from apps.members.views import download_member_document, view_member_photo

urlpatterns = [path("", RedirectView.as_view(pattern_name="admin:index", permanent=False)),
    path("admin/branding/logo/", admin.site.admin_view(logo), name="gym-logo"),
    path("admin/branches/switch/", switch_branch, name="switch-branch"),
    path("admin/members/documents/<uuid:document_id>/download/", admin.site.admin_view(download_member_document), name="member-document-download"),
    path("admin/members/photos/<uuid:member_id>/", admin.site.admin_view(view_member_photo), name="member-photo-view"),
    path("admin/", admin.site.urls)]
