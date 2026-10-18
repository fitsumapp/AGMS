from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .models import (
    AutomationRule,
    BulkCampaign,
    DeliveryQueueItem,
    InAppNotification,
    MessageTemplate,
    NotificationMessage,
    NotificationPreference,
    ProviderConfig,
    ScheduledNotification,
)


@admin.register(ProviderConfig)
class ProviderConfigAdmin(ModelAdmin):
    list_display = ("name", "provider_type", "branch", "sender_id", "cost_per_sms_etb", "is_default", "is_active")
    list_filter = ("provider_type", "is_active", "is_default", "branch")
    search_fields = ("name", "sender_id", "api_url")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MessageTemplate)
class MessageTemplateAdmin(ModelAdmin):
    list_display = ("code", "name", "category", "channel", "language", "branch", "is_active")
    list_filter = ("category", "channel", "language", "is_active", "branch")
    search_fields = ("code", "name", "subject", "body")
    readonly_fields = ("created_at", "updated_at")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(ModelAdmin):
    list_display = ("__str__", "allow_sms", "allow_email", "allow_in_app", "opt_out_marketing", "updated_at")
    list_filter = ("allow_sms", "allow_email", "allow_in_app", "opt_out_marketing")
    search_fields = ("member__first_name", "member__last_name", "user__username")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AutomationRule)
class AutomationRuleAdmin(ModelAdmin):
    list_display = ("name", "trigger_event", "days_offset", "template", "channel", "branch", "is_active")
    list_filter = ("trigger_event", "channel", "is_active", "branch")
    search_fields = ("name", "template__name", "template__code")
    readonly_fields = ("created_at",)


@admin.register(BulkCampaign)
class BulkCampaignAdmin(ModelAdmin):
    list_display = ("title", "target_audience", "branch", "channel", "total_recipients", "sent_count", "failed_count", "status_badge", "created_at")
    list_filter = ("status", "channel", "target_audience", "branch")
    search_fields = ("title", "message_body")
    readonly_fields = ("total_recipients", "sent_count", "failed_count", "confirmed_at", "confirmed_by", "created_at")

    def status_badge(self, obj):
        colors = {
            "DRAFT": "gray",
            "SCHEDULED": "blue",
            "PROCESSING": "yellow",
            "COMPLETED": "green",
            "CANCELLED": "red",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display(),
        )
    status_badge.short_description = "Status"


@admin.register(NotificationMessage)
class NotificationMessageAdmin(ModelAdmin):
    list_display = ("id", "channel", "recipient_address", "category", "status_badge", "segment_count", "estimated_cost_etb", "branch", "created_at")
    list_filter = ("channel", "status", "category", "branch", "created_at")
    search_fields = ("recipient_address", "subject", "body", "provider_message_id", "idempotency_key")
    readonly_fields = (
        "sent_at", "delivered_at", "failed_at", "provider_message_id",
        "provider_response", "error_message", "idempotency_key", "created_at", "updated_at"
    )

    def status_badge(self, obj):
        colors = {
            "PENDING": "gray",
            "QUEUED": "orange",
            "SENDING": "blue",
            "SENT": "teal",
            "DELIVERED": "green",
            "FAILED": "red",
            "CANCELLED": "purple",
        }
        color = colors.get(obj.status, "gray")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display(),
        )
    status_badge.short_description = "Status"


@admin.register(DeliveryQueueItem)
class DeliveryQueueItemAdmin(ModelAdmin):
    list_display = ("id", "notification_link", "priority", "status", "retry_count", "max_retries", "scheduled_at", "next_retry_at")
    list_filter = ("status", "priority", "created_at")
    search_fields = ("notification__recipient_address", "locked_by")
    readonly_fields = ("locked_at", "locked_by", "created_at", "updated_at")

    def notification_link(self, obj):
        return f"Msg #{obj.notification_id} ({obj.notification.recipient_address})"
    notification_link.short_description = "Notification"


@admin.register(ScheduledNotification)
class ScheduledNotificationAdmin(ModelAdmin):
    list_display = ("id", "scheduled_for", "target_recipient", "channel", "category", "status", "created_at")
    list_filter = ("status", "channel", "category", "branch")
    search_fields = ("phone", "email", "idempotency_key")
    readonly_fields = ("dispatched_notification", "created_at")

    def target_recipient(self, obj):
        return obj.phone or obj.email or "Recipient"
    target_recipient.short_description = "Recipient"


@admin.register(InAppNotification)
class InAppNotificationAdmin(ModelAdmin):
    list_display = ("id", "recipient", "title", "category", "is_read", "branch", "created_at")
    list_filter = ("is_read", "category", "branch", "created_at")
    search_fields = ("recipient__username", "title", "message")
    readonly_fields = ("read_at", "created_at")
