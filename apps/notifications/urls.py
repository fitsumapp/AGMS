from django.urls import path
from . import views

urlpatterns = [
    path("dashboard/", views.notification_dashboard_view, name="notification-dashboard"),
    path("sendsms/", views.send_sms_view, name="send-sms"),
    path("bulkcampaign/", views.bulk_campaign_view, name="bulk-campaign"),
    path("reports/", views.communication_reports_view, name="notification-reports"),
    path("api/inapp/unread/", views.get_unread_in_app_notifications_api, name="inapp-unread-api"),
    path("api/inapp/<int:notification_id>/read/", views.mark_in_app_notification_read, name="inapp-mark-read"),
    path("callbacks/<str:provider_code>/", views.sms_delivery_callback_view, name="sms-delivery-callback"),
]
