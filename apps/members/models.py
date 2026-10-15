import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditFieldMixin, UUIDTimeStampedModel
from .validators import (
    normalize_ethiopian_phone,
    validate_document_file,
    validate_photo_file,
)


def member_photo_path(instance, filename):
    member_id = instance.pk or uuid.uuid4()
    ext = Path(filename).suffix.lower()
    return f"members/{member_id}/photos/{uuid.uuid4()}{ext}"


def member_document_path(instance, filename):
    member_id = instance.member_id or uuid.uuid4()
    ext = Path(filename).suffix.lower()
    return f"members/{member_id}/documents/{uuid.uuid4()}{ext}"


class MemberSequence(models.Model):
    """Atomic counter for concurrency-safe sequential member numbers."""
    name = models.CharField(max_length=50, unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Member sequence"
        verbose_name_plural = "Member sequences"

    def __str__(self):
        return f"{self.name}: {self.last_number}"


class Member(UUIDTimeStampedModel, AuditFieldMixin):
    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"
        OTHER = "OTHER", "Other"

    class MemberStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        SUSPENDED = "SUSPENDED", "Suspended"
        ARCHIVED = "ARCHIVED", "Archived"

    class RegistrationSource(models.TextChoices):
        WALK_IN = "WALK_IN", "Walk-in"
        REFERRAL = "REFERRAL", "Referral"
        ONLINE = "ONLINE", "Online"
        OTHER = "OTHER", "Other"

    # Identity
    member_number = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    first_name = models.CharField(max_length=60)
    middle_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    photo = models.ImageField(upload_to=member_photo_path, blank=True, validators=[validate_photo_file])

    # Contact
    phone = models.CharField(max_length=25, db_index=True)
    alternative_phone = models.CharField(max_length=25, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)

    # Gym Information
    home_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="members",
    )
    joined_at = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=MemberStatus.choices,
        default=MemberStatus.ACTIVE,
        db_index=True,
    )
    registration_source = models.CharField(
        max_length=30,
        choices=RegistrationSource.choices,
        default=RegistrationSource.WALK_IN,
    )
    referral_source = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ("-joined_at", "-created_at")
        constraints = [
            models.CheckConstraint(condition=~models.Q(first_name=""), name="member_first_name_not_blank"),
            models.CheckConstraint(condition=~models.Q(last_name=""), name="member_last_name_not_blank"),
            models.CheckConstraint(condition=~models.Q(phone=""), name="member_phone_not_blank"),
        ]
        indexes = [
            models.Index(fields=["home_branch", "status"], name="member_branch_status_idx"),
            models.Index(fields=["last_name", "first_name"], name="member_name_idx"),
            models.Index(fields=["phone"], name="member_phone_idx"),
            models.Index(fields=["joined_at"], name="member_joined_idx"),
            models.Index(fields=["status"], name="member_status_idx"),
        ]

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p.strip() for p in parts if p and p.strip())

    def clean(self):
        super().clean()
        self.first_name = (self.first_name or "").strip()
        self.middle_name = (self.middle_name or "").strip()
        self.last_name = (self.last_name or "").strip()
        if not self.first_name or not self.last_name:
            raise ValidationError("First name and last name are required.")
        
        # Phone normalization
        if self.phone:
            self.phone = normalize_ethiopian_phone(self.phone)
        else:
            raise ValidationError({"phone": "Phone number is required."})

        if self.alternative_phone:
            self.alternative_phone = normalize_ethiopian_phone(self.alternative_phone)

        # Immutability of member_number once set
        if not self._state.adding and self.pk:
            original = Member.objects.filter(pk=self.pk).values("member_number").first()
            if original and original["member_number"] and self.member_number != original["member_number"]:
                raise ValidationError({"member_number": "Member number is immutable after registration."})

        # Ensure home_branch is active for new members
        if self._state.adding and self.home_branch_id:
            branch = getattr(self, "home_branch", None)
            if branch and not branch.is_active:
                raise ValidationError({"home_branch": "Cannot assign a member to an inactive branch."})

    def save(self, *args, **kwargs):
        if not self.member_number:
            from .sequence import generate_next_member_number
            self.member_number = generate_next_member_number()
        elif not self._state.adding and self.pk:
            original = Member.objects.filter(pk=self.pk).values("member_number").first()
            if original and original["member_number"] and self.member_number != original["member_number"]:
                raise ValidationError({"member_number": "Member number is immutable after registration."})
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.member_number} · {self.full_name}"


class MemberEmergencyContact(UUIDTimeStampedModel):
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="emergency_contacts",
    )
    full_name = models.CharField(max_length=150)
    relationship = models.CharField(max_length=60)
    phone = models.CharField(max_length=25)
    alternative_phone = models.CharField(max_length=25, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ("-is_primary", "full_name")
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(is_primary=True),
                name="one_primary_emergency_contact_per_member",
            ),
            models.CheckConstraint(
                condition=~models.Q(full_name=""),
                name="emergency_contact_name_not_blank",
            ),
            models.CheckConstraint(
                condition=~models.Q(relationship=""),
                name="emergency_contact_rel_not_blank",
            ),
        ]

    def clean(self):
        super().clean()
        self.full_name = (self.full_name or "").strip()
        self.relationship = (self.relationship or "").strip()
        if not self.full_name or not self.relationship:
            raise ValidationError("Full name and relationship are required for emergency contact.")
        if self.phone:
            self.phone = normalize_ethiopian_phone(self.phone)
        else:
            raise ValidationError({"phone": "Phone number is required."})
        if self.alternative_phone:
            self.alternative_phone = normalize_ethiopian_phone(self.alternative_phone)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_primary and self.member_id:
                # Row-lock parent member to serialize primary contact reassignment under concurrency
                Member.objects.select_for_update().filter(pk=self.member_id).values("id").first()
                MemberEmergencyContact.objects.filter(
                    member_id=self.member_id, is_primary=True
                ).exclude(pk=self.pk).update(is_primary=False)
            self.full_clean()
            return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.relationship}) → {self.member.member_number}"


class MemberDocument(UUIDTimeStampedModel):
    class DocumentType(models.TextChoices):
        ID_PASSPORT = "ID_PASSPORT", "National ID / Passport"
        MEDICAL_CLEARANCE = "MEDICAL_CLEARANCE", "Medical Clearance"
        AGREEMENT_WAIVER = "AGREEMENT_WAIVER", "Agreement & Waiver"
        CORPORATE_STUDENT_PROOF = "CORPORATE_STUDENT_PROOF", "Corporate / Student Proof"
        OTHER = "OTHER", "Other Document"

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    document_type = models.CharField(
        max_length=50,
        choices=DocumentType.choices,
        default=DocumentType.OTHER,
    )
    document_title = models.CharField(max_length=150)
    file = models.FileField(upload_to=member_document_path, validators=[validate_document_file])
    description = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="member_documents_uploaded",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-uploaded_at",)
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(document_title=""),
                name="member_document_title_not_blank",
            ),
        ]

    def clean(self):
        super().clean()
        self.document_title = (self.document_title or "").strip()
        if not self.document_title:
            raise ValidationError({"document_title": "Document title is required."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.document_title} ({self.get_document_type_display()}) → {self.member.member_number}"


class MemberNote(UUIDTimeStampedModel):
    class NoteCategory(models.TextChoices):
        GENERAL = "GENERAL", "General / Public"
        RESTRICTED = "RESTRICTED", "Staff Only (Restricted)"
        SENSITIVE = "SENSITIVE", "Management Only (Sensitive)"

    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    note = models.TextField()
    category = models.CharField(
        max_length=30,
        choices=NoteCategory.choices,
        default=NoteCategory.GENERAL,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="member_notes_created",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(note=""),
                name="member_note_not_blank",
            ),
        ]

    def clean(self):
        super().clean()
        self.note = (self.note or "").strip()
        if not self.note:
            raise ValidationError({"note": "Note text cannot be empty."})
        if self.created_by_id and self.category != self.NoteCategory.GENERAL:
            from apps.accounts.roles import has_role, is_owner
            if (has_role(self.created_by, "Receptionist") or has_role(self.created_by, "Accountant")) and not is_owner(self.created_by) and not has_role(self.created_by, "Branch Manager"):
                raise ValidationError({"category": "Staff with your role can only create General notes."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.get_category_display()}] Note for {self.member.member_number}"


class MemberStatusHistory(UUIDTimeStampedModel):
    member = models.ForeignKey(
        Member,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    previous_status = models.CharField(max_length=20, choices=Member.MemberStatus.choices)
    new_status = models.CharField(max_length=20, choices=Member.MemberStatus.choices)
    reason = models.TextField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="member_status_changes",
    )
    changed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-changed_at",)
        verbose_name = "Member status history"
        verbose_name_plural = "Member status history records"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=""),
                name="member_status_reason_not_blank",
            ),
        ]

    def clean(self):
        super().clean()
        self.reason = (self.reason or "").strip()
        if not self.reason:
            raise ValidationError({"reason": "A reason is required for status changes."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.member.member_number}: {self.previous_status} → {self.new_status}"
