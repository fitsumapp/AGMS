import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.models import AuditFieldMixin, UUIDTimeStampedModel
from apps.core.validators import phone_validator


class Branch(UUIDTimeStampedModel, AuditFieldMixin):
    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    phone = models.CharField(max_length=25, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    address = models.TextField()
    city = models.CharField(max_length=100, blank=True)
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="managed_branches")
    opening_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("code",)
        constraints = [
            models.UniqueConstraint(Lower("code"), name="branch_code_case_insensitive_unique"),
            models.CheckConstraint(condition=~models.Q(code=""), name="branch_code_not_blank"),
            models.CheckConstraint(condition=~models.Q(name=""), name="branch_name_not_blank"),
        ]
        indexes = [models.Index(fields=["is_active", "city"], name="branch_active_city_idx")]

    @staticmethod
    def normalize_code(value):
        return (value or "").strip().upper()

    def clean(self):
        super().clean()
        self.code = self.normalize_code(self.code)
        self.name = self.name.strip()
        if not self.code or not self.name or not self.address.strip():
            raise ValidationError("Branch code, name, and address are required.")
        if self.manager_id:
            from .services import is_branch_manager
            if not self.pk or not is_branch_manager(self.manager, self):
                raise ValidationError({"manager": "Assign an active Branch Manager to this branch first."})

    def save(self, *args, **kwargs):
        self.code = self.normalize_code(self.code)
        self.name = self.name.strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} · {self.name}"


class UserBranchAccess(UUIDTimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="branch_access")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="user_access")
    is_active = models.BooleanField(default=True)
    is_primary = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(default=timezone.now, editable=False)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name="branch_access_assigned")

    class Meta:
        verbose_name_plural = "User branch access"
        constraints = [
            models.UniqueConstraint(fields=["user", "branch"], name="unique_user_branch_access"),
            models.UniqueConstraint(fields=["user"], condition=models.Q(is_active=True, is_primary=True),
                name="one_active_primary_branch_per_user"),
            models.CheckConstraint(condition=models.Q(is_active=True) | models.Q(is_primary=False),
                name="inactive_assignment_not_primary"),
        ]
        indexes = [models.Index(fields=["branch", "is_active"], name="access_branch_active_idx")]

    def clean(self):
        super().clean()
        if self.is_primary and not self.is_active:
            raise ValidationError({"is_primary": "A primary assignment must be active."})
        if self.user_id and self.is_active and (self.user.is_superuser or not self.user.is_staff or not self.user.is_active):
            raise ValidationError({"user": "Select an active business staff account."})
        if self.user_id and self.is_active and not self.user.groups.filter(name__in=BUSINESS_ROLES).exists():
            raise ValidationError({"user": "Assign a business role before branch access."})
        if self.branch_id and self.is_active and not self.branch.is_active:
            raise ValidationError({"branch": "Inactive branches cannot receive active access."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} → {self.branch}"


class BranchSettings(UUIDTimeStampedModel, AuditFieldMixin):
    branch = models.OneToOneField(Branch, on_delete=models.CASCADE, related_name="settings")
    opens_at = models.TimeField(null=True, blank=True)
    closes_at = models.TimeField(null=True, blank=True)
    operating_days = models.CharField(max_length=120, default="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday", blank=True)
    receipt_header_override = models.TextField(blank=True, help_text="Branch-specific header message on receipts")
    receipt_footer_override = models.TextField(blank=True, help_text="Branch-specific footer message on receipts")
    allow_access_with_balance_override = models.BooleanField(null=True, blank=True, help_text="Override global partial-payment access policy. Leave blank to inherit.")
    attendance_qr_enabled = models.BooleanField(default=True, help_text="Enable QR code scanning at this branch.")
    attendance_manual_enabled = models.BooleanField(default=True, help_text="Enable receptionist manual lookup check-in at this branch.")
    max_discount_percent_override = models.DecimalField(null=True, blank=True, max_digits=5, decimal_places=2, help_text="Override cashier discount ceiling for this branch.")
    locker_deposit_etb_override = models.DecimalField(null=True, blank=True, max_digits=10, decimal_places=2, help_text="Override standard locker deposit amount.")
    low_stock_threshold_override = models.PositiveIntegerField(null=True, blank=True, help_text="Override inventory reorder trigger threshold.")
    sms_enabled_override = models.BooleanField(null=True, blank=True, help_text="Enable or disable automated member SMS from this branch.")
    cleaning_inspection_frequency_hours = models.PositiveSmallIntegerField(default=24, help_text="Target hours between facility sanitation reviews.")
    manager_notes = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "Branch settings"

    def clean(self):
        super().clean()
        if bool(self.opens_at) != bool(self.closes_at):
            raise ValidationError("Set both opening and closing times, or neither.")
        if not self.attendance_qr_enabled and not self.attendance_manual_enabled:
            raise ValidationError("At least one attendance method (QR Code or Name/Phone) must remain enabled for this branch.")

    def __str__(self):
        return f"{self.branch} configuration"


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="branch_audit_events")
    branch = models.ForeignKey(Branch, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_events")
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=100)
    target_id = models.UUIDField()
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [
            models.Index(fields=["target_type", "target_id"], name="audit_target_idx"),
            models.Index(fields=["action", "occurred_at"], name="audit_action_time_idx"),
        ]

    def __str__(self):
        return f"{self.action} {self.target_type} {self.target_id}"


BUSINESS_ROLES = (
    "Gym Owner", "Branch Manager", "Receptionist", "Accountant", "Trainer", "POS Staff",
)
