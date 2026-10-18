from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from apps.analytics.views import (
    attendance_analytics_view,
    executive_dashboard_view,
    financial_analytics_view,
    membership_analytics_view,
    report_center_view,
)
from apps.branches.views import switch_branch
from apps.core.views import logo
from apps.members.views import download_member_document, view_member_photo
from apps.staff.views import (
    download_staff_document,
    staff_reports_view,
    trainer_performance_view,
)

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="admin:index", permanent=False)),
    path("admin/branding/logo/", admin.site.admin_view(logo), name="gym-logo"),
    path("admin/branches/switch/", switch_branch, name="switch-branch"),
    path("admin/members/documents/<uuid:document_id>/download/", admin.site.admin_view(download_member_document), name="member-document-download"),
    path("admin/members/photos/<uuid:member_id>/", admin.site.admin_view(view_member_photo), name="member-photo-view"),
    path("admin/staff/documents/<uuid:document_id>/download/", admin.site.admin_view(download_staff_document), name="staff-document-download"),
    path("admin/analytics/dashboard/", admin.site.admin_view(executive_dashboard_view), name="analytics-dashboard"),
    path("admin/analytics/financial/", admin.site.admin_view(financial_analytics_view), name="analytics-financial"),
    path("admin/analytics/memberships/", admin.site.admin_view(membership_analytics_view), name="analytics-memberships"),
    path("admin/analytics/attendance/", admin.site.admin_view(attendance_analytics_view), name="analytics-attendance"),
    path("admin/analytics/reports/", admin.site.admin_view(report_center_view), name="analytics-reports"),
    path("admin/staff/performance/", admin.site.admin_view(trainer_performance_view), name="staff-performance"),
    path("admin/staff/reports/", admin.site.admin_view(staff_reports_view), name="staff-reports"),
    path("admin/", admin.site.urls),
]
