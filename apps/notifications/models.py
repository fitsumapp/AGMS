from decimal import Decimal
import os
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.members.models import Member
from apps.memberships.models import MembershipPackage


class NotificationChannel(models.TextChoices):
    SMS = "SMS", _("SMS")
    IN_APP = "IN_APP", _("In-App Notification")
    EMAIL = "EMAIL", _("Email")


class NotificationPriority(models.TextChoices):
    LOW = "LOW", _("Low")
    NORMAL = "NORMAL", _("Normal")
    HIGH = "HIGH", _("High")
    URGENT = "URGENT", _("Urgent")


class NotificationStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    QUEUED = "QUEUED", _("Queued")
    SENDING = "SENDING", _("Sending")
    SENT = "SENT", _("Sent (Provider Accepted)")
    DELIVERED = "DELIVERED", _("Confirmed Delivered")
    FAILED = "FAILED", _("Failed")
    CANCELLED = "CANCELLED", _("Cancelled")


class NotificationCategory(models.TextChoices):
    MEMBERSHIP = "MEMBERSHIP", _("Membership & Renewal")
    BILLING = "BILLING", _("Billing & Payments")
    CLASS_TRAINING = "CLASS_TRAINING", _("Classes & Personal Training")
    STAFF_MANAGEMENT = "STAFF_MANAGEMENT", _("Staff & Operations")
    POS_INVENTORY = "POS_INVENTORY", _("POS & Inventory")
    MARKETING_BULK = "MARKETING_BULK", _("Bulk & Announcements")
    SYSTEM_OPERATIONAL = "SYSTEM_OPERATIONAL", _("System Alerts")


class LanguageChoice(models.TextChoices):
    ENGLISH = "EN", _("English")
    AMHARIC = "AM", _("Amharic (አማርኛ)")


class ProviderType(models.TextChoices):
    MOCK = "MOCK", _("Mock / Test Simulator")
    AFROMESSAGE = "AFROMESSAGE", _("AfroMessage Ethiopia")
    CUSTOM_HTTP = "CUSTOM_HTTP", _("Generic HTTP Gateway")
    SMTP_EMAIL = "SMTP_EMAIL", _("SMTP Email Gateway")


class TriggerEvent(models.TextChoices):
    # Membership triggers
    MEMBERSHIP_EXPIRY_7D = "MEMBERSHIP_EXPIRY_7D", _("Membership Expiring in 7 Days")
    MEMBERSHIP_EXPIRY_3D = "MEMBERSHIP_EXPIRY_3D", _("Membership Expiring in 3 Days")
    MEMBERSHIP_EXPIRY_1D = "MEMBERSHIP_EXPIRY_1D", _("Membership Expiring Tomorrow")
    MEMBERSHIP_EXPIRED = "MEMBERSHIP_EXPIRED", _("Membership Expired")
    MEMBERSHIP_ACTIVATED = "MEMBERSHIP_ACTIVATED", _("Membership Activated")
    MEMBERSHIP_RENEWED = "MEMBERSHIP_RENEWED", _("Membership Renewed")
    MEMBERSHIP_FROZEN = "MEMBERSHIP_FROZEN", _("Membership Frozen")
    MEMBERSHIP_REACTIVATED = "MEMBERSHIP_REACTIVATED", _("Membership Reactivated")

    # Billing triggers
    INVOICE_ISSUED = "INVOICE_ISSUED", _("Invoice Issued")
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED", _("Payment Confirmed")
    PARTIAL_PAYMENT_BALANCE = "PARTIAL_PAYMENT_BALANCE", _("Partial Payment Balance Due")
    PAYMENT_OVERDUE = "PAYMENT_OVERDUE", _("Invoice Overdue Reminder")
    REFUND_COMPLETED = "REFUND_COMPLETED", _("Refund Completed")

    # Class & Training triggers
    CLASS_BOOKED = "CLASS_BOOKED", _("Class Booking Confirmed")
    CLASS_CANCELLED = "CLASS_CANCELLED", _("Class Session Cancelled")
    WAITLIST_PROMOTED = "WAITLIST_PROMOTED", _("Waitlist Spot Available")
    CLASS_REMINDER = "CLASS_REMINDER", _("Class Starting Soon")
    TRAINING_SESSION_BOOKED = "TRAINING_SESSION_BOOKED", _("Personal Training Booked")
    TRAINING_SESSION_CANCELLED = "TRAINING_SESSION_CANCELLED", _("Personal Training Cancelled")

    # POS & Inventory triggers
    LOW_STOCK_ALERT = "LOW_STOCK_ALERT", _("Low Stock Threshold Alert")
    OUT_OF_STOCK_ALERT = "OUT_OF_STOCK_ALERT", _("Out of Stock Alert")
    EXPIRING_PRODUCT_ALERT = "EXPIRING_PRODUCT_ALERT", _("Product Expiring Soon")
    PURCHASE_ORDER_APPROVED = "PURCHASE_ORDER_APPROVED", _("Purchase Order Approved")
    GOODS_RECEIVED_ALERT = "GOODS_RECEIVED_ALERT", _("Goods Received Note Posted")
    POS_REFUND_APPROVED = "POS_REFUND_APPROVED", _("POS Refund Approved")

    # Staff & Operations triggers
    EXPENSE_APPROVAL_PENDING = "EXPENSE_APPROVAL_PENDING", _("Pending Expense Approval")
    LEAVE_REQUEST_PENDING = "LEAVE_REQUEST_PENDING", _("Pending Leave Request")
    RECONCILIATION_EXCEPTION = "RECONCILIATION_EXCEPTION", _("Reconciliation Exception")


class TargetAudience(models.TextChoices):
    ALL_ACTIVE_MEMBERS = "ALL_ACTIVE", _("All Active Members")
    BRANCH_ACTIVE_MEMBERS = "BRANCH_ACTIVE", _("Active Members of Selected Branch")
    EXPIRING_MEMBERS = "EXPIRING_MEMBERS", _("Members Expiring in Next 7 Days")
    SELECTED_PACKAGE = "SELECTED_PACKAGE", _("Members on Selected Package")
    EXPLICIT_LIST = "EXPLICIT_LIST", _("Explicitly Selected Members")


class CampaignStatus(models.TextChoices):
    DRAFT = "DRAFT", _("Draft")
    SCHEDULED = "SCHEDULED", _("Scheduled")
    PROCESSING = "PROCESSING", _("Processing Queue")
    COMPLETED = "COMPLETED", _("Completed")
    CANCELLED = "CANCELLED", _("Cancelled")


class QueueStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PROCESSING = "PROCESSING", _("Processing")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")
    DEAD_LETTER = "DEAD_LETTER", _("Dead Letter")


class ScheduledStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", _("Scheduled")
    DISPATCHED = "DISPATCHED", _("Dispatched")
    CANCELLED = "CANCELLED", _("Cancelled")


class ProviderConfig(models.Model):
    """Configuration for external SMS and messaging gateways."""
    name = models.CharField(max_length=100)
    provider_type = models.CharField(max_length=30, choices=ProviderType.choices, default=ProviderType.MOCK)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name="sms_providers",
                               help_text="Leave blank to use as gym-wide default.")
    api_url = models.URLField(blank=True, help_text="HTTP Gateway Endpoint URL.")
    api_key_env_var = models.CharField(
        max_length=100, default="AFROMESSAGE_API_KEY",
        help_text="Environment variable name providing the secret API token (credentials are never stored in plain text)."
    )
    sender_id = models.CharField(max_length=30, blank=True, default="AGMS")
    rate_limit_per_minute = models.PositiveIntegerField(default=120)
    timeout_seconds = models.PositiveIntegerField(default=10)
    cost_per_sms_etb = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.35"))
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]
        verbose_name = _("Provider Configuration")
        verbose_name_plural = _("Provider Configurations")

    def __str__(self):
        branch_str = f" ({self.branch.name})" if self.branch else " (Global)"
        return f"{self.name} [{self.get_provider_type_display()}]{branch_str}"

    def get_api_key(self) -> str:
        """Fetch API key from environment variable safely."""
        if not self.api_key_env_var:
            return ""
        return os.environ.get(self.api_key_env_var, "")


class MessageTemplate(models.Model):
    """Reusable notification template supporting English and Amharic with safe placeholders."""
    code = models.CharField(max_length=60, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=30, choices=NotificationCategory.choices, default=NotificationCategory.SYSTEM_OPERATIONAL)
    channel = models.CharField(max_length=20, choices=NotificationChannel.choices, default=NotificationChannel.SMS)
    language = models.CharField(max_length=10, choices=LanguageChoice.choices, default=LanguageChoice.ENGLISH)
    subject = models.CharField(max_length=200, blank=True, help_text="Email subject line or in-app title")
    body = models.TextField(help_text="Template text with safe placeholders, e.g. {{ member_name }}, {{ gym_name }}.")
    allowed_placeholders = models.JSONField(default=list, blank=True, help_text="List of allowed placeholder variable names.")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name="message_templates")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "code"]
        verbose_name = _("Message Template")
        verbose_name_plural = _("Message Templates")

    def __str__(self):
        return f"{self.code} - {self.name} ({self.get_language_display()})"


class NotificationPreference(models.Model):
    """Customer and staff communication channel preferences and marketing opt-out."""
    member = models.OneToOneField(Member, on_delete=models.CASCADE, null=True, blank=True, related_name="notification_preferences")
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name="notification_preferences")
    allow_sms = models.BooleanField(default=True)
    allow_email = models.BooleanField(default=True)
    allow_in_app = models.BooleanField(default=True)
    opt_out_marketing = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Notification Preference")
        verbose_name_plural = _("Notification Preferences")

    def __str__(self):
        target = self.member.full_name if self.member else (self.user.username if self.user else "Unknown")
        return f"Preferences for {target}"


class AutomationRule(models.Model):
    """Automated event-triggered rule for scheduling notifications."""
    name = models.CharField(max_length=120)
    trigger_event = models.CharField(max_length=60, choices=TriggerEvent.choices, db_index=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name="automation_rules",
                               help_text="Leave blank to apply gym-wide.")
    template = models.ForeignKey(MessageTemplate, on_delete=models.PROTECT, related_name="automation_rules")
    channel = models.CharField(max_length=20, choices=NotificationChannel.choices, default=NotificationChannel.SMS)
    days_offset = models.IntegerField(default=0, help_text="Days offset from event date (e.g., -7 for 7 days before, 0 for same day).")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["trigger_event", "days_offset"]
        verbose_name = _("Automation Rule")
        verbose_name_plural = _("Automation Rules")

    def __str__(self):
        offset = f" ({self.days_offset:+d}d)" if self.days_offset != 0 else ""
        return f"{self.name} [{self.get_trigger_event_display()}{offset}]"


class BulkCampaign(models.Model):
    """Bulk messaging campaign for announcements and marketing."""
    title = models.CharField(max_length=150)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="bulk_campaigns")
    target_audience = models.CharField(max_length=30, choices=TargetAudience.choices, default=TargetAudience.ALL_ACTIVE_MEMBERS)
    target_package = models.ForeignKey(MembershipPackage, on_delete=models.SET_NULL, null=True, blank=True)
    template = models.ForeignKey(MessageTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    message_body = models.TextField()
    channel = models.CharField(max_length=20, choices=NotificationChannel.choices, default=NotificationChannel.SMS)
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=CampaignStatus.choices, default=CampaignStatus.DRAFT, db_index=True)
    estimated_cost_etb = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_campaigns")
    confirmed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="confirmed_campaigns")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Bulk Campaign")
        verbose_name_plural = _("Bulk Campaigns")

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"


class NotificationMessage(models.Model):
    """Durable delivery record and audit log for every message dispatched or attempted."""
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="notification_messages")
    recipient_member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications")
    recipient_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="system_notifications")
    recipient_address = models.CharField(max_length=120, db_index=True, help_text="Normalized phone number or email address")
    channel = models.CharField(max_length=20, choices=NotificationChannel.choices, default=NotificationChannel.SMS, db_index=True)
    category = models.CharField(max_length=30, choices=NotificationCategory.choices, default=NotificationCategory.SYSTEM_OPERATIONAL, db_index=True)
    priority = models.CharField(max_length=20, choices=NotificationPriority.choices, default=NotificationPriority.NORMAL)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=20, choices=NotificationStatus.choices, default=NotificationStatus.QUEUED, db_index=True)
    provider = models.CharField(max_length=50, default="MOCK")
    provider_message_id = models.CharField(max_length=120, blank=True, db_index=True)
    provider_response = models.JSONField(default=dict, blank=True)
    segment_count = models.PositiveIntegerField(default=1)
    estimated_cost_etb = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    idempotency_key = models.CharField(max_length=120, blank=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_notifications")
    campaign = models.ForeignKey(BulkCampaign, on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Notification Message")
        verbose_name_plural = _("Notification Messages")
        indexes = [
            models.Index(fields=["channel", "status"]),
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["category", "created_at"]),
        ]

    def __str__(self):
        return f"[{self.get_channel_display()}] {self.recipient_address} - {self.get_status_display()}"


class DeliveryQueueItem(models.Model):
    """Transaction-safe background job queue for asynchronous notification dispatch."""
    notification = models.OneToOneField(NotificationMessage, on_delete=models.CASCADE, related_name="queue_item")
    scheduled_at = models.DateTimeField(default=timezone.now, db_index=True)
    priority = models.IntegerField(default=50, db_index=True, help_text="Lower value means higher priority")
    retry_count = models.PositiveIntegerField(default=0)
    max_retries = models.PositiveIntegerField(default=3)
    backoff_factor = models.PositiveIntegerField(default=60, help_text="Base backoff in seconds")
    next_retry_at = models.DateTimeField(default=timezone.now, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=QueueStatus.choices, default=QueueStatus.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "scheduled_at"]
        verbose_name = _("Delivery Queue Item")
        verbose_name_plural = _("Delivery Queue Items")
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
        ]

    def __str__(self):
        return f"Queue Item #{self.id} for Msg #{self.notification_id} [{self.get_status_display()}]"


class ScheduledNotification(models.Model):
    """Planned future notification (e.g. 7-day membership expiry, session reminders)."""
    scheduled_for = models.DateTimeField(db_index=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="scheduled_notifications")
    recipient_member = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True)
    recipient_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    channel = models.CharField(max_length=20, choices=NotificationChannel.choices, default=NotificationChannel.SMS)
    category = models.CharField(max_length=30, choices=NotificationCategory.choices)
    template = models.ForeignKey(MessageTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    context_data = models.JSONField(default=dict, blank=True)
    custom_body = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=ScheduledStatus.choices, default=ScheduledStatus.SCHEDULED, db_index=True)
    cancelled_reason = models.TextField(blank=True)
    dispatched_notification = models.ForeignKey(NotificationMessage, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_for"]
        verbose_name = _("Scheduled Notification")
        verbose_name_plural = _("Scheduled Notifications")

    def __str__(self):
        target = self.phone or self.email or "Recipient"
        return f"Scheduled for {self.scheduled_for} to {target} [{self.get_status_display()}]"


class InAppNotification(models.Model):
    """Internal notifications for logged-in staff and managers."""
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="in_app_notifications")
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name="in_app_notifications")
    title = models.CharField(max_length=150)
    message = models.TextField()
    url = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=30, choices=NotificationCategory.choices, default=NotificationCategory.SYSTEM_OPERATIONAL)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("In-App Notification")
        verbose_name_plural = _("In-App Notifications")

    def __str__(self):
        read_badge = "Read" if self.is_read else "Unread"
        return f"[{read_badge}] {self.recipient.username}: {self.title}"
