import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import UUIDTimeStampedModel


class MemberCredential(UUIDTimeStampedModel):
    class CredentialType(models.TextChoices):
        QR = "QR", "QR Code"
        CARD = "CARD", "RFID / NFC Card"
        MANUAL = "MANUAL", "Manual PIN / Passcode"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REVOKED = "REVOKED", "Revoked"
        EXPIRED = "EXPIRED", "Expired"

    member = models.ForeignKey(
        "members.Member",
        on_delete=models.CASCADE,
        related_name="credentials",
    )
    credential_type = models.CharField(
        max_length=20,
        choices=CredentialType.choices,
        default=CredentialType.QR,
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hash of the credential secret token.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    issued_date = models.DateTimeField(default=timezone.now)
    expiry_date = models.DateTimeField(null=True, blank=True)
    revoked_date = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.TextField(blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credentials_issued",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-issued_date",)
        indexes = [
            models.Index(fields=["member", "status"], name="cred_member_status_idx"),
            models.Index(fields=["status", "expiry_date"], name="cred_status_expiry_idx"),
        ]

    def __str__(self):
        return f"{self.member.member_number} · {self.get_credential_type_display()} [{self.status}]"

    @property
    def is_valid(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        if self.expiry_date and timezone.now() > self.expiry_date:
            return False
        return True


class AccessDevice(UUIDTimeStampedModel):
    class DeviceType(models.TextChoices):
        KIOSK = "KIOSK", "Self-Service Kiosk"
        TURNSTILE = "TURNSTILE", "Turnstile / Gate"
        RECEPTION_DESK = "RECEPTION_DESK", "Reception Desk Scanner"
        HANDHELD = "HANDHELD", "Handheld Mobile Scanner"
        OTHER = "OTHER", "Other Device"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        MAINTENANCE = "MAINTENANCE", "Under Maintenance"

    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="access_devices",
    )
    device_name = models.CharField(max_length=100)
    device_code = models.CharField(max_length=50, unique=True, db_index=True)
    device_type = models.CharField(
        max_length=30,
        choices=DeviceType.choices,
        default=DeviceType.RECEPTION_DESK,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    api_key_hash = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("device_name",)
        indexes = [
            models.Index(fields=["branch", "status"], name="device_branch_status_idx"),
        ]

    def __str__(self):
        return f"{self.device_name} ({self.device_code}) · {self.branch.code}"


class Attendance(UUIDTimeStampedModel):
    class Method(models.TextChoices):
        QR = "QR", "QR Code Scan"
        CARD = "CARD", "RFID Card"
        MANUAL = "MANUAL", "Manual Front-Desk"
        OVERRIDE = "OVERRIDE", "Authorized Override"

    class CheckoutMethod(models.TextChoices):
        QR = "QR", "QR Code Scan"
        CARD = "CARD", "RFID Card"
        MANUAL = "MANUAL", "Manual Checkout"
        AUTO = "AUTO", "Automatic / End-of-Day"
        FORGOTTEN = "FORGOTTEN", "Forgotten Checkout"

    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Currently Inside"
        COMPLETED = "COMPLETED", "Completed"
        VOIDED = "VOIDED", "Voided / Invalidated"

    member = models.ForeignKey(
        "members.Member",
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    membership = models.ForeignKey(
        "memberships.Membership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendances",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="attendances",
    )
    check_in_datetime = models.DateTimeField(default=timezone.now, db_index=True)
    check_out_datetime = models.DateTimeField(null=True, blank=True, db_index=True)
    check_in_method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.QR,
    )
    check_out_method = models.CharField(
        max_length=20,
        choices=CheckoutMethod.choices,
        null=True,
        blank=True,
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="recorded_attendances",
    )
    access_device = models.ForeignKey(
        AccessDevice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendances",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PRESENT,
        db_index=True,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-check_in_datetime",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(check_out_datetime__isnull=True) | models.Q(check_out_datetime__gte=models.F("check_in_datetime")),
                name="attendance_checkout_after_checkin",
            ),
        ]
        indexes = [
            models.Index(fields=["branch", "check_in_datetime"], name="att_branch_checkin_idx"),
            models.Index(fields=["member", "status"], name="att_member_status_idx"),
            models.Index(fields=["status", "check_in_datetime"], name="att_status_checkin_idx"),
        ]

    def __str__(self):
        return f"{self.member.member_number} @ {self.branch.code} · {self.check_in_datetime.strftime('%Y-%m-%d %H:%M')}"

    @property
    def duration_minutes(self):
        if self.check_out_datetime and self.check_in_datetime:
            diff = self.check_out_datetime - self.check_in_datetime
            return max(0, int(diff.total_seconds() // 60))
        return None


class AttendanceOverride(UUIDTimeStampedModel):
    attendance = models.OneToOneField(
        Attendance,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="override",
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.CASCADE,
        related_name="attendance_overrides",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="attendance_overrides",
    )
    override_reason = models.TextField()
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="overrides_requested",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="overrides_approved",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Override for {self.member.member_number} by {self.approved_by} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"


class AccessAttempt(UUIDTimeStampedModel):
    class Result(models.TextChoices):
        GRANTED = "GRANTED", "Access Granted"
        DENIED = "DENIED", "Access Denied"

    member = models.ForeignKey(
        "members.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_attempts",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="access_attempts",
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    result = models.CharField(
        max_length=20,
        choices=Result.choices,
        db_index=True,
    )
    reason_code = models.CharField(
        max_length=64,
        db_index=True,
    )
    message = models.TextField()
    check_in_method = models.CharField(
        max_length=20,
        choices=Attendance.Method.choices,
        default=Attendance.Method.QR,
    )
    device_or_operator = models.CharField(max_length=150, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["branch", "timestamp"], name="attempt_branch_time_idx"),
            models.Index(fields=["result", "timestamp"], name="attempt_result_time_idx"),
            models.Index(fields=["member", "timestamp"], name="attempt_member_time_idx"),
        ]

    def __str__(self):
        return f"{self.result}: {self.reason_code} ({self.timestamp.strftime('%Y-%m-%d %H:%M:%S')})"
