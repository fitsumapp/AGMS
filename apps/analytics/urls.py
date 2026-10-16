from django.urls import path
from . import views

app_name = "analytics"

urlpatterns = [
    path("dashboard/", views.executive_dashboard_view, name="dashboard"),
    path("financial/", views.financial_analytics_view, name="financial"),
    path("memberships/", views.membership_analytics_view, name="memberships"),
    path("attendance/", views.attendance_analytics_view, name="attendance"),
    path("reports/", views.report_center_view, name="report_center"),
]
