import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from .validators import currency_validator, phone_validator, validate_timezone, validate_logo

class UUIDTimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        abstract = True

class AuditFieldMixin(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL, related_name="%(app_label)s_%(class)s_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False, on_delete=models.SET_NULL, related_name="%(app_label)s_%(class)s_updated")
    class Meta:
        abstract = True

def default_currency():
    return settings.GYM_DEFAULT_CURRENCY

def default_timezone():
    return settings.GYM_DEFAULT_TIMEZONE

def logo_path(instance, filename):
    from pathlib import Path
    return f"branding/{uuid.uuid4()}{Path(filename).suffix.lower()}"

class GymProfile(UUIDTimeStampedModel, AuditFieldMixin):
    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    name = models.CharField(max_length=150)
    legal_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=25, blank=True, validators=[phone_validator])
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    logo = models.ImageField(upload_to=logo_path, blank=True, validators=[validate_logo])
    default_currency = models.CharField(max_length=3, default=default_currency, validators=[currency_validator])
    default_timezone = models.CharField(max_length=64, default=default_timezone, validators=[validate_timezone])
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(singleton=1), name="gym_profile_singleton_one")]
    def __str__(self):
        return self.name


class GymBranding(GymProfile):
    """Owner-editable subset of gym identity, with technical fields hidden in its admin."""

    class Meta:
        proxy = True
        verbose_name = "Gym branding"
        verbose_name_plural = "Gym branding"

class GymSettings(UUIDTimeStampedModel, AuditFieldMixin):
    class CrossBranchAccess(models.TextChoices):
        PACKAGE_BASED = "PACKAGE_BASED", "Package based"
    class EarlyRenewal(models.TextChoices):
        EXTEND_EXISTING_END_DATE = "EXTEND_EXISTING_END_DATE", "Extend existing end date"
    profile = models.OneToOneField(GymProfile, on_delete=models.PROTECT, related_name="policies")
    allow_access_with_balance = models.BooleanField(default=False)
    membership_freeze_enabled = models.BooleanField(default=True)
    require_freeze_approval = models.BooleanField(default=True)
    cross_branch_access_mode = models.CharField(max_length=30, choices=CrossBranchAccess.choices, default=CrossBranchAccess.PACKAGE_BASED)
    early_renewal_mode = models.CharField(max_length=40, choices=EarlyRenewal.choices, default=EarlyRenewal.EXTEND_EXISTING_END_DATE)
    attendance_qr_enabled = models.BooleanField(
        default=True,
        verbose_name="Enable QR Code Attendance",
        help_text="Allow members to check in and out using dynamic or printed QR credentials.",
    )
    attendance_manual_enabled = models.BooleanField(
        default=True,
        verbose_name="Enable Name & Phone Attendance",
        help_text="Allow front-desk receptionists to search members by name, phone, or member ID for check-in and check-out.",
    )

    class Meta:
        verbose_name_plural = "Gym settings"
        constraints = [
            models.CheckConstraint(condition=models.Q(cross_branch_access_mode="PACKAGE_BASED"), name="valid_cross_branch_policy"),
            models.CheckConstraint(condition=models.Q(early_renewal_mode="EXTEND_EXISTING_END_DATE"), name="valid_early_renewal_policy"),
            models.CheckConstraint(
                condition=models.Q(attendance_qr_enabled=True) | models.Q(attendance_manual_enabled=True),
                name="at_least_one_attendance_method_enabled",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.attendance_qr_enabled and not self.attendance_manual_enabled:
            raise ValidationError("At least one attendance method (QR Code or Name/Phone) must be enabled.")

    def __str__(self):
        return f"{self.profile} policies"


class GymPolicy(GymSettings):
    """Business-managed gym policies and attendance configuration."""

    class Meta:
        proxy = True
        verbose_name = "Gym policy"
        verbose_name_plural = "Gym policies"
