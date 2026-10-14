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

    class Meta:
        verbose_name_plural = "Branch settings"

    def clean(self):
        super().clean()
        if bool(self.opens_at) != bool(self.closes_at):
            raise ValidationError("Set both opening and closing times, or neither.")

    def __str__(self):
        return f"{self.branch} operating hours"


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="branch_audit_events")
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=100)
    target_id = models.UUIDField()
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [models.Index(fields=["target_type", "target_id"], name="audit_target_idx")]

    def __str__(self):
        return f"{self.action} {self.target_type} {self.target_id}"


BUSINESS_ROLES = (
    "Gym Owner", "Branch Manager", "Receptionist", "Accountant", "Trainer", "POS Staff",
)
