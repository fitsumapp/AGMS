from django.urls import path
from .views import class_calendar_view, class_reports_view

app_name = "classes"

urlpatterns = [
    path("calendar/", class_calendar_view, name="class-calendar"),
    path("reports/", class_reports_view, name="class-reports"),
]
