from django.urls import path
from apps.facilities.views import (
    facility_dashboard_view,
    facility_reports_view,
)

app_name = "facilities"

urlpatterns = [
    path("dashboard/", facility_dashboard_view, name="dashboard"),
    path("reports/", facility_reports_view, name="reports"),
]
