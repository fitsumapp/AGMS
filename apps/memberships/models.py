import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditFieldMixin, UUIDTimeStampedModel


class MembershipSequence(models.Model):
    """Atomic counter for concurrency-safe sequential membership numbers."""
    name = models.CharField(max_length=50, unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Membership sequence"
        verbose_name_plural = "Membership sequences"

    def __str__(self):
        return f"{self.name}: {self.last_number}"


class MembershipPackage(UUIDTimeStampedModel, AuditFieldMixin):
    class DurationUnit(models.TextChoices):
        DAY = "DAY", "Day(s)"
        WEEK = "WEEK", "Week(s)"
        MONTH = "MONTH", "Month(s)"
        YEAR = "YEAR", "Year(s)"

    code = models.CharField(max_length=30, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    duration_unit = models.CharField(
        max_length=10,
        choices=DurationUnit.choices,
        default=DurationUnit.MONTH,
    )
    duration_value = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="ETB")
    allowed_visits = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Leave blank for unlimited visits.",
    )
    cross_branch_enabled = models.BooleanField(
        default=False,
        help_text="Authorizes attendance across all enabled branches.",
    )
    freeze_allowed = models.BooleanField(
        default=True,
        help_text="Specifies if memberships of this package can be frozen.",
    )
    max_freeze_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=30,
        help_text="Maximum total freeze days allowed per membership.",
    )
    max_freeze_requests = models.PositiveIntegerField(
        null=True,
        blank=True,
        default=2,
        help_text="Maximum number of freeze requests allowed per membership.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name", "code")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=Decimal("0.00")),
                name="membership_package_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_value__gt=0),
                name="membership_package_duration_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(code=""),
                name="membership_package_code_not_blank",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="membership_package_name_not_blank",
            ),
        ]

    def clean(self):
        super().clean()
        self.code = (self.code or "").strip().upper()
        self.name = (self.name or "").strip()
        if not self.code or not self.name:
            raise ValidationError("Package code and name are required.")
        if self.price < Decimal("0.00"):
            raise ValidationError({"price": "Price cannot be negative."})
        if self.duration_value <= 0:
            raise ValidationError({"duration_value": "Duration value must be greater than zero."})

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        self.name = (self.name or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code}) · {self.price} {self.currency}"


class PackageBranch(UUIDTimeStampedModel):
    """Explicit branch authorization for a membership package."""
    package = models.ForeignKey(
        MembershipPackage,
        on_delete=models.CASCADE,
        related_name="package_branches",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="branch_packages",
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("package", "branch")
        constraints = [
            models.UniqueConstraint(
                fields=["package", "branch"],
                name="unique_package_branch_mapping",
            ),
        ]

    def __str__(self):
        status = "Enabled" if self.is_enabled else "Disabled"
        return f"{self.package.code} ↔ {self.branch.code} [{status}]"


class Membership(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Billing"
        ACTIVE = "ACTIVE", "Active"
        FROZEN = "FROZEN", "Frozen"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    class ActivationStatus(models.TextChoices):
        NOT_ACTIVATED = "NOT_ACTIVATED", "Not Activated"
        ACTIVATED = "ACTIVATED", "Activated"

    class PaymentClearance(models.TextChoices):
        PENDING = "PENDING", "Pending Payment"
        CLEARED = "CLEARED", "Payment Cleared"
        WAIVED_COMPLIMENTARY = "WAIVED_COMPLIMENTARY", "Complimentary / Waived"

    PaymentClearanceStatus = PaymentClearance

    membership_number = models.CharField(max_length=24, unique=True, db_index=True)
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    package = models.ForeignKey(
        MembershipPackage,
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    home_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="branch_memberships",
    )
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # Immutability snapshots from package at time of purchase
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    duration_unit_snapshot = models.CharField(max_length=10)
    duration_value_snapshot = models.PositiveIntegerField()
    allowed_visits_snapshot = models.PositiveIntegerField(null=True, blank=True)
    cross_branch_snapshot = models.BooleanField(default=False)

    activation_status = models.CharField(
        max_length=20,
        choices=ActivationStatus.choices,
        default=ActivationStatus.NOT_ACTIVATED,
    )
    payment_clearance_status = models.CharField(
        max_length=30,
        choices=PaymentClearance.choices,
        default=PaymentClearance.PENDING,
        db_index=True,
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memberships_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="memberships_updated",
    )

    class Meta:
        ordering = ("-created_at", "-start_date")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="membership_end_date_after_start_date",
            ),
            models.CheckConstraint(
                condition=models.Q(price_snapshot__gte=Decimal("0.00")),
                name="membership_price_snapshot_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["home_branch", "status"], name="mship_branch_status_idx"),
            models.Index(fields=["member", "status"], name="mship_member_status_idx"),
            models.Index(fields=["start_date", "end_date"], name="mship_dates_idx"),
            models.Index(fields=["status", "end_date"], name="mship_status_end_idx"),
        ]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": "Exclusive end date must be after start date."})

        # Member number immutability
        if not self._state.adding and self.pk:
            original = Membership.objects.filter(pk=self.pk).values("membership_number").first()
            if original and original["membership_number"] and self.membership_number != original["membership_number"]:
                raise ValidationError({"membership_number": "Membership number is immutable after registration."})

    def save(self, *args, **kwargs):
        if not self.membership_number:
            from .sequence import generate_next_membership_number
            self.membership_number = generate_next_membership_number()
        elif not self._state.adding and self.pk:
            original = Membership.objects.filter(pk=self.pk).values("membership_number").first()
            if original and original["membership_number"] and self.membership_number != original["membership_number"]:
                raise ValidationError({"membership_number": "Membership number is immutable after registration."})
        self.full_clean()
        return super().save(*args, **kwargs)

    def is_currently_valid(self, check_date=None) -> bool:
        """Return True if membership is currently ACTIVE and within inclusive-start / exclusive-end window."""
        if check_date is None:
            from .dates import get_gym_today
            check_date = get_gym_today()
        return self.status == self.Status.ACTIVE and self.start_date <= check_date < self.end_date

    def __str__(self):
        return f"{self.membership_number} · {self.member.full_name} [{self.get_status_display()}]"


class MembershipRenewal(UUIDTimeStampedModel):
    class RenewalStatus(models.TextChoices):
        PENDING_BILLING = "PENDING_BILLING", "Pending Billing Clearance"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    membership = models.ForeignKey(
        Membership,
        on_delete=models.CASCADE,
        related_name="renewals",
    )
    previous_end_date = models.DateField()
    new_end_date = models.DateField()
    duration_unit_snapshot = models.CharField(max_length=10)
    duration_value_snapshot = models.PositiveIntegerField()
    price_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    renewal_status = models.CharField(
        max_length=20,
        choices=RenewalStatus.choices,
        default=RenewalStatus.PENDING_BILLING,
        db_index=True,
    )
    billing_reference = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True, db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="renewals_requested",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="renewals_approved",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def clean(self):
        super().clean()
        if self.new_end_date and self.previous_end_date and self.new_end_date <= self.previous_end_date:
            raise ValidationError({"new_end_date": "New end date must extend beyond previous end date."})

    def __str__(self):
        return f"Renewal for {self.membership.membership_number} -> {self.new_end_date} [{self.renewal_status}]"


class MembershipFreeze(UUIDTimeStampedModel):
    class FreezeStatus(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested (Pending Approval)"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"
        COMPLETED = "COMPLETED", "Completed"

    Status = FreezeStatus

    membership = models.ForeignKey(
        Membership,
        on_delete=models.CASCADE,
        related_name="freezes",
    )
    requested_start_date = models.DateField()
    requested_end_date = models.DateField()
    approved_start_date = models.DateField(null=True, blank=True)
    approved_end_date = models.DateField(null=True, blank=True)
    freeze_days = models.PositiveIntegerField(default=0)
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=FreezeStatus.choices,
        default=FreezeStatus.REQUESTED,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="freezes_requested",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="freezes_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="freeze_reason_not_blank",
            ),
        ]

    def clean(self):
        super().clean()
        self.reason = (self.reason or "").strip()
        if not self.reason:
            raise ValidationError({"reason": "A reason is mandatory for freeze requests."})
        if self.requested_start_date and self.requested_end_date:
            if self.requested_end_date <= self.requested_start_date:
                raise ValidationError({"requested_end_date": "End date must be after start date."})

    def __str__(self):
        return f"Freeze for {self.membership.membership_number} ({self.status}) [{self.requested_start_date} -> {self.requested_end_date}]"


class MembershipAdjustment(UUIDTimeStampedModel):
    class AdjustmentType(models.TextChoices):
        EXPIRY_EXTENSION = "EXPIRY_EXTENSION", "Expiry Extension"
        VISIT_ALLOWANCE = "VISIT_ALLOWANCE", "Visit Allowance"
        POLICY_OVERRIDE = "POLICY_OVERRIDE", "Policy Override"

    membership = models.ForeignKey(
        Membership,
        on_delete=models.CASCADE,
        related_name="adjustments",
    )
    adjustment_type = models.CharField(max_length=30, choices=AdjustmentType.choices)
    previous_value = models.CharField(max_length=255)
    new_value = models.CharField(max_length=255)
    reason = models.TextField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adjustments_approved",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adjustments_created",
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.get_adjustment_type_display()} for {self.membership.membership_number}"


class MembershipEvent(UUIDTimeStampedModel):
    class EventType(models.TextChoices):
        CREATED = "CREATED", "Membership Created"
        ACTIVATED = "ACTIVATED", "Membership Activated"
        FROZEN = "FROZEN", "Membership Frozen"
        RESUMED = "RESUMED", "Membership Resumed"
        RENEWED = "RENEWED", "Membership Renewed"
        EXPIRED = "EXPIRED", "Membership Expired"
        CANCELLED = "CANCELLED", "Membership Cancelled"
        ADJUSTED = "ADJUSTED", "Membership Adjusted"

    membership = models.ForeignKey(
        Membership,
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=30, choices=EventType.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-timestamp",)
        verbose_name = "Membership event"
        verbose_name_plural = "Membership events"

    def __str__(self):
        return f"{self.membership.membership_number} · {self.get_event_type_display()} ({self.timestamp:%Y-%m-%d %H:%M})"
