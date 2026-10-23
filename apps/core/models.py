from decimal import Decimal
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
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
    name = models.CharField(max_length=150, verbose_name=_("Gym Display Name"))
    legal_name = models.CharField(max_length=200, blank=True, verbose_name=_("Legal Entity Name"))
    tax_id = models.CharField(max_length=50, blank=True, verbose_name=_("TIN / Business Reg Number"))
    commercial_registration_number = models.CharField(max_length=50, blank=True, verbose_name=_("Commercial Registration Number"))
    phone = models.CharField(max_length=25, blank=True, validators=[phone_validator], verbose_name=_("Contact Phone"))
    email = models.EmailField(blank=True, verbose_name=_("Contact Email"))
    website = models.URLField(max_length=200, blank=True, verbose_name=_("Official Website"))
    address = models.TextField(blank=True, verbose_name=_("Headquarters Address"))
    logo = models.ImageField(upload_to=logo_path, blank=True, validators=[validate_logo], verbose_name=_("Gym Logo"))
    favicon = models.ImageField(upload_to="branding/favicons/", blank=True, verbose_name=_("Browser Favicon"))
    receipt_header = models.TextField(blank=True, verbose_name=_("Receipt Header Message"), help_text=_("Message printed at the top of member receipts."))
    receipt_footer = models.TextField(blank=True, verbose_name=_("Receipt Footer Disclaimer"), help_text=_("Disclaimers or terms printed at the bottom of receipts."))
    invoice_terms = models.TextField(blank=True, verbose_name=_("Invoice Terms & Conditions"), help_text=_("Default payment terms printed on formal tax invoices."))
    default_language = models.CharField(max_length=10, blank=True, default="en", choices=[("en", _("English")), ("am", _("Amharic (አማርኛ)"))], verbose_name=_("Default System Language"))
    default_currency = models.CharField(max_length=3, default=default_currency, validators=[currency_validator], verbose_name=_("Currency (ETB)"))
    default_timezone = models.CharField(max_length=64, default=default_timezone, validators=[validate_timezone], verbose_name=_("Timezone"))
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        default=1,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        verbose_name=_("Fiscal Year Start Month (1-12)"),
    )
    member_id_prefix = models.CharField(max_length=10, blank=True, default="MEM-", verbose_name=_("Member ID Prefix"))
    member_id_sequence_length = models.PositiveSmallIntegerField(default=5, blank=True, verbose_name=_("Member ID Sequence Digits"))
    min_member_age = models.PositiveSmallIntegerField(default=14, blank=True, verbose_name=_("Minimum Registration Age"))
    require_emergency_contact = models.BooleanField(default=True, verbose_name=_("Require Emergency Contact"))
    require_member_photo = models.BooleanField(default=False, verbose_name=_("Require Member Profile Photo"))
    terms_and_conditions = models.TextField(blank=True, verbose_name=_("Membership Terms & Conditions"))
    privacy_policy = models.TextField(blank=True, verbose_name=_("Member Privacy Policy"))

    def clean(self):
        super().clean()
        if not self.default_language:
            self.default_language = "en"
        if not self.fiscal_year_start_month:
            self.fiscal_year_start_month = 1
        if not self.member_id_prefix:
            self.member_id_prefix = "MEM-"
        if not self.member_id_sequence_length:
            self.member_id_sequence_length = 5
        if not self.min_member_age:
            self.min_member_age = 14

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(singleton=1), name="gym_profile_singleton_one")]
        verbose_name = _("Gym Profile")
        verbose_name_plural = _("Gym Profile")

    def __str__(self):
        return self.name


class GymBranding(GymProfile):
    """Owner-editable subset of gym identity, with technical fields hidden in its admin."""

    class Meta:
        proxy = True
        verbose_name = _("Gym branding")
        verbose_name_plural = _("Gym branding")


class GymSettings(UUIDTimeStampedModel, AuditFieldMixin):
    class CrossBranchAccess(models.TextChoices):
        PACKAGE_BASED = "PACKAGE_BASED", _("Package based")
    class EarlyRenewal(models.TextChoices):
        EXTEND_EXISTING_END_DATE = "EXTEND_EXISTING_END_DATE", _("Extend existing end date")

    profile = models.OneToOneField(GymProfile, on_delete=models.PROTECT, related_name="policies")
    
    # Membership & Access policies
    allow_access_with_balance = models.BooleanField(default=False, verbose_name=_("Allow Gym Entry With Outstanding Balance"))
    membership_freeze_enabled = models.BooleanField(default=True, verbose_name=_("Enable Membership Freezing"))
    require_freeze_approval = models.BooleanField(default=True, verbose_name=_("Require Manager Approval For Freezes"))
    cross_branch_access_mode = models.CharField(max_length=30, choices=CrossBranchAccess.choices, default=CrossBranchAccess.PACKAGE_BASED)
    early_renewal_mode = models.CharField(max_length=40, choices=EarlyRenewal.choices, default=EarlyRenewal.EXTEND_EXISTING_END_DATE)
    attendance_qr_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enable QR Code Attendance"),
        help_text=_("Allow members to check in and out using dynamic or printed QR credentials."),
    )
    attendance_manual_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enable Name & Phone Attendance"),
        help_text=_("Allow front-desk receptionists to search members by name, phone, or member ID for check-in and check-out."),
    )

    # Billing & Finance policies
    invoice_due_days = models.PositiveSmallIntegerField(default=7, blank=True, verbose_name=_("Standard Invoice Due Period (Days)"))
    invoice_prefix = models.CharField(max_length=10, default="INV-", blank=True, verbose_name=_("Invoice Number Prefix"))
    receipt_prefix = models.CharField(max_length=10, default="REC-", blank=True, verbose_name=_("Receipt Number Prefix"))
    cashier_shift_required = models.BooleanField(default=True, verbose_name=_("Require Active Cashier Shift for POS Checkout"))
    pos_max_discount_percent = models.DecimalField(
        default=Decimal("20.00"),
        max_digits=5,
        decimal_places=2,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        verbose_name=_("Max Cashier POS Discount (%)"),
    )
    stock_adjustment_approval_required = models.BooleanField(default=True, verbose_name=_("Require Manager Approval for Stock Discrepancy Adjustments"))
    require_refund_approval = models.BooleanField(default=True, verbose_name=_("Enforce Separation of Duties for Refund Approvals"))

    # Notification policies
    sms_enabled = models.BooleanField(default=True, verbose_name=_("Enable Automated SMS Gateway"))
    email_enabled = models.BooleanField(default=False, verbose_name=_("Enable Email Gateway"))
    sms_quiet_hours_start = models.TimeField(null=True, blank=True, verbose_name=_("SMS Quiet Hours Start (e.g. 21:00)"))
    sms_quiet_hours_end = models.TimeField(null=True, blank=True, verbose_name=_("SMS Quiet Hours End (e.g. 07:00)"))
    max_daily_sms_per_member = models.PositiveSmallIntegerField(default=5, blank=True, verbose_name=_("Max Automated SMS Per Member Daily"))
    membership_expiry_reminder_days = models.CharField(
        max_length=50,
        default="7,3,1",
        blank=True,
        verbose_name=_("Expiry Reminder Intervals (Days Before)"),
        help_text=_("Comma-separated days before expiry to send reminder notifications."),
    )

    # Staff & Class policies
    trainer_max_active_clients = models.PositiveSmallIntegerField(default=25, blank=True, verbose_name=_("Max Active 1-on-1 PT Clients Per Trainer"))
    class_booking_window_days = models.PositiveSmallIntegerField(default=7, blank=True, verbose_name=_("Class Advance Booking Window (Days)"))
    class_cancellation_cutoff_hours = models.PositiveSmallIntegerField(default=2, blank=True, verbose_name=_("Class Cancellation Cutoff (Hours Before Start)"))
    class_waitlist_max_size = models.PositiveSmallIntegerField(default=10, blank=True, verbose_name=_("Max Class Waitlist Capacity"))
    staff_leave_manager_approval_required = models.BooleanField(default=True, verbose_name=_("Require Branch Manager Approval for Staff Leave"))

    # Facility & Equipment policies
    default_locker_deposit_etb = models.DecimalField(
        default=Decimal("200.00"),
        max_digits=10,
        decimal_places=2,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Default Refundable Locker Deposit (ETB)"),
    )
    lost_key_penalty_etb = models.DecimalField(
        default=Decimal("150.00"),
        max_digits=10,
        decimal_places=2,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("Lost Key / Lock Mechanism Damage Penalty (ETB)"),
    )
    facility_inspection_interval_days = models.PositiveSmallIntegerField(default=7, blank=True, verbose_name=_("Facility Safety Inspection Frequency (Days)"))
    preventive_maintenance_lead_days = models.PositiveSmallIntegerField(default=3, blank=True, verbose_name=_("Preventive Work Order Advance Lead (Days)"))

    class Meta:
        verbose_name = _("Gym Settings")
        verbose_name_plural = _("Gym settings")
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
        if not self.invoice_due_days:
            self.invoice_due_days = 7
        if not self.invoice_prefix:
            self.invoice_prefix = "INV-"
        if not self.receipt_prefix:
            self.receipt_prefix = "REC-"
        if self.pos_max_discount_percent is None:
            self.pos_max_discount_percent = Decimal("20.00")
        if not self.max_daily_sms_per_member:
            self.max_daily_sms_per_member = 5
        if not self.membership_expiry_reminder_days:
            self.membership_expiry_reminder_days = "7,3,1"
        if not self.trainer_max_active_clients:
            self.trainer_max_active_clients = 25
        if not self.class_booking_window_days:
            self.class_booking_window_days = 7
        if not self.class_cancellation_cutoff_hours:
            self.class_cancellation_cutoff_hours = 2
        if not self.class_waitlist_max_size:
            self.class_waitlist_max_size = 10
        if self.default_locker_deposit_etb is None:
            self.default_locker_deposit_etb = Decimal("200.00")
        if self.lost_key_penalty_etb is None:
            self.lost_key_penalty_etb = Decimal("150.00")
        if not self.facility_inspection_interval_days:
            self.facility_inspection_interval_days = 7
        if not self.preventive_maintenance_lead_days:
            self.preventive_maintenance_lead_days = 3
        if not self.attendance_qr_enabled and not self.attendance_manual_enabled:
            raise ValidationError("At least one attendance method (QR Code or Name/Phone) must be enabled.")

    def __str__(self):
        return f"{self.profile} policies"


class GymPolicy(GymSettings):
    """Business-managed gym policies and attendance configuration."""

    class Meta:
        proxy = True
        verbose_name = _("Gym policy")
        verbose_name_plural = _("Gym policies")


class FeatureFlag(UUIDTimeStampedModel, AuditFieldMixin):
    """Controlled feature flag registry for optional business modules and operational workflows."""
    class Category(models.TextChoices):
        GENERAL = "GENERAL", _("General Operations")
        MEMBERSHIP = "MEMBERSHIP", _("Membership & Access")
        ATTENDANCE = "ATTENDANCE", _("Attendance Methods")
        BILLING = "BILLING", _("Billing & Financials")
        POS = "POS", _("POS & Retail")
        NOTIFICATIONS = "NOTIFICATIONS", _("Messaging & Notifications")
        CLASSES = "CLASSES", _("Classes & Booking")
        FACILITIES = "FACILITIES", _("Facility & Equipment")

    code = models.CharField(max_length=60, unique=True, db_index=True, verbose_name=_("Feature Code"))
    name = models.CharField(max_length=120, verbose_name=_("Feature Name"))
    description = models.TextField(blank=True, verbose_name=_("Description"))
    category = models.CharField(max_length=40, choices=Category.choices, default=Category.GENERAL, verbose_name=_("Category"))
    is_enabled = models.BooleanField(default=True, verbose_name=_("Is Active / Enabled"))
    rollout_percentage = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name=_("Rollout Percentage (0-100%)"),
        help_text=_("Percentage of users or branches eligible when enabled."),
    )
    requires_features = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Required Feature Dependencies"),
        help_text=_("List of feature flag codes this feature depends on."),
    )
    branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="feature_flags",
        verbose_name=_("Branch Scope (Optional)"),
        help_text=_("Leave blank for global system-wide feature flag, or select a specific branch."),
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_("Configuration Metadata"))

    class Meta:
        ordering = ["category", "code"]
        verbose_name = _("Feature Flag")
        verbose_name_plural = _("Feature Flags")

    def __str__(self):
        status = "ENABLED" if self.is_enabled else "DISABLED"
        scope = f"[{self.branch.code}]" if self.branch else "[GLOBAL]"
        return f"{self.code} {scope} ({status})"

    def clean(self):
        super().clean()
        self.code = (self.code or "").strip().upper()
        if not self.code:
            raise ValidationError({"code": _("Feature code is required.")})

        # Dependency validation
        if self.is_enabled and self.requires_features:
            for req_code in self.requires_features:
                dep_flag = FeatureFlag.objects.filter(code=req_code).first()
                if not dep_flag:
                    raise ValidationError(
                        _(f"Cannot depend on non-existent feature '{req_code}'.")
                    )
                elif not dep_flag.is_enabled:
                    raise ValidationError(
                        _(f"Cannot enable '{self.code}' because required dependency '{req_code}' is currently disabled.")
                    )


class BackupConfiguration(UUIDTimeStampedModel, AuditFieldMixin):
    """PostgreSQL backup storage destination, retention policies, and readiness verification."""
    class Compression(models.TextChoices):
        GZIP = "GZIP", _("Gzip (.sql.gz)")
        TAR = "TAR", _("Tarball (.tar)")
        CUSTOM = "CUSTOM", _("Uncompressed (.sql)")

    class Status(models.TextChoices):
        NOT_RUN = "NOT_RUN", _("Not Run")
        SUCCESS = "SUCCESS", _("Success")
        FAILED = "FAILED", _("Failed")
        RUNNING = "RUNNING", _("Running")

    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    backup_directory = models.CharField(max_length=255, default="./backups", verbose_name=_("Local Backup Directory"))
    retention_days = models.PositiveIntegerField(default=30, verbose_name=_("Retention Policy (Days)"))
    compression_algorithm = models.CharField(max_length=20, choices=Compression.choices, default=Compression.GZIP, verbose_name=_("Compression Method"))
    offsite_storage_reference = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Encrypted Offsite Storage Reference"),
        help_text=_("Reference to offsite cold storage destination (e.g., s3://agms-backups-vault/ or sftp://archive.corp/)."),
    )
    last_backup_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Last Backup Timestamp"))
    last_backup_status = models.CharField(max_length=30, choices=Status.choices, default=Status.NOT_RUN, verbose_name=_("Last Backup Status"))
    last_backup_file_name = models.CharField(max_length=255, blank=True, verbose_name=_("Last Backup Filename"))
    last_backup_file_size_bytes = models.BigIntegerField(default=0, verbose_name=_("Last Backup Size (Bytes)"))
    last_verified_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Last Restore Verification Timestamp"))
    notes = models.TextField(blank=True, verbose_name=_("Operational Notes"))

    class Meta:
        verbose_name = _("Backup Configuration")
        verbose_name_plural = _("Backup Configuration")
        constraints = [
            models.CheckConstraint(condition=models.Q(singleton=1), name="backup_config_singleton_one")
        ]

    def __str__(self):
        return f"PostgreSQL Backup Configuration ({self.get_last_backup_status_display()})"
