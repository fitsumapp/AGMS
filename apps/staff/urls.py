from django.urls import path
from .views import staff_reports_view, trainer_performance_view

urlpatterns = [
    path("performance/", trainer_performance_view, name="staff-performance"),
    path("reports/", staff_reports_view, name="staff-reports"),
]
