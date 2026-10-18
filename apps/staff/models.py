import uuid
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditFieldMixin, UUIDTimeStampedModel
from apps.core.validators import phone_validator


def staff_photo_path(instance, filename):
    staff_id = instance.pk or uuid.uuid4()
    ext = Path(filename).suffix.lower()
    return f"staff/{staff_id}/photos/{uuid.uuid4()}{ext}"


def staff_document_path(instance, filename):
    staff_id = instance.staff_id or uuid.uuid4()
    ext = Path(filename).suffix.lower()
    return f"staff/{staff_id}/documents/{uuid.uuid4()}{ext}"


class StaffSequence(models.Model):
    """Atomic counter for sequential employee numbers (e.g. EMP-0001)."""
    name = models.CharField(max_length=50, unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Staff sequence"
        verbose_name_plural = "Staff sequences"

    def __str__(self):
        return f"{self.name}: {self.last_number}"

    @classmethod
    def next_employee_number(cls, prefix="EMP"):
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(name="employee_number")
            seq.last_number += 1
            seq.save(update_fields=["last_number", "updated_at"])
            return f"{prefix}-{seq.last_number:04d}"


class StaffProfile(UUIDTimeStampedModel, AuditFieldMixin):
    class Department(models.TextChoices):
        FITNESS = "FITNESS", "Fitness & Training"
        OPERATIONS = "OPERATIONS", "Gym Operations"
        FRONT_DESK = "FRONT_DESK", "Front Desk & Reception"
        MANAGEMENT = "MANAGEMENT", "Management"
        MAINTENANCE = "MAINTENANCE", "Maintenance & Facilities"
        FINANCE = "FINANCE", "Finance & Accounting"
        OTHER = "OTHER", "Other"

    class EmploymentType(models.TextChoices):
        FULL_TIME = "FULL_TIME", "Full-time"
        PART_TIME = "PART_TIME", "Part-time"
        CONTRACT = "CONTRACT", "Contract"
        INTERN = "INTERN", "Intern"
        CASUAL = "CASUAL", "Casual"

    class EmploymentStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ON_LEAVE = "ON_LEAVE", "On Leave"
        PROBATION = "PROBATION", "Probation"
        SUSPENDED = "SUSPENDED", "Suspended"
        TERMINATED = "TERMINATED", "Terminated"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_profile",
    )
    employee_number = models.CharField(max_length=32, unique=True, db_index=True)
    primary_branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_staff",
    )
    job_title = models.CharField(max_length=120)
    department = models.CharField(
        max_length=50,
        choices=Department.choices,
        default=Department.FITNESS,
        db_index=True,
    )
    employment_type = models.CharField(
        max_length=30,
        choices=EmploymentType.choices,
        default=EmploymentType.FULL_TIME,
    )
    employment_status = models.CharField(
        max_length=30,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
        db_index=True,
    )
    hire_date = models.DateField(default=timezone.localdate)
    termination_date = models.DateField(null=True, blank=True)

    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(
        max_length=25,
        blank=True,
        validators=[phone_validator],
    )
    emergency_contact_relation = models.CharField(max_length=50, blank=True)

    photo = models.ImageField(upload_to=staff_photo_path, null=True, blank=True)
    notes = models.TextField(blank=True)
    qualifications = models.TextField(blank=True)

    class Meta:
        ordering = ("employee_number",)
        indexes = [
            models.Index(fields=["department", "employment_status"], name="staff_dept_status_idx"),
            models.Index(fields=["primary_branch", "employment_status"], name="staff_branch_status_idx"),
        ]

    def clean(self):
        super().clean()
        if self.termination_date and self.hire_date and self.termination_date < self.hire_date:
            raise ValidationError({"termination_date": "Termination date cannot be before hire date."})
        if not self.employee_number:
            self.employee_number = StaffSequence.next_employee_number()

    def save(self, *args, **kwargs):
        if not self.employee_number:
            self.employee_number = StaffSequence.next_employee_number()

        # Security: When staff is terminated, immediately revoke login and branch access
        if self.employment_status == self.EmploymentStatus.TERMINATED and self.user_id:
            if not self.termination_date:
                self.termination_date = timezone.localdate()
            if self.user.is_staff or self.user.is_active:
                self.user.is_staff = False
                self.user.is_active = False
                self.user.save(update_fields=["is_staff", "is_active"])
            self.user.branch_access.filter(is_active=True).update(is_active=False, is_primary=False)
            if hasattr(self, "trainer_profile"):
                trainer = self.trainer_profile
                if trainer.status != "INACTIVE":
                    trainer.status = "INACTIVE"
                    trainer.save(update_fields=["status"])
                trainer.member_assignments.filter(
                    status="ACTIVE"
                ).update(status="CANCELLED")
                upcoming_sessions = trainer.sessions.filter(
                    status="SCHEDULED",
                    session_date__gte=self.termination_date,
                )
                for s in upcoming_sessions:
                    s.status = "CANCELLED"
                    s.cancellation_reason = "Staff employment terminated."
                    s.cancelled_at = timezone.now()
                    s.save(update_fields=["status", "cancellation_reason", "cancelled_at"])
            self.shifts.filter(
                status="SCHEDULED",
                date__gte=self.termination_date,
            ).update(status="CANCELLED")

        super().save(*args, **kwargs)

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.email

    @property
    def email(self):
        return self.user.email

    @property
    def phone(self):
        return self.user.phone

    def get_assigned_branches(self):
        from apps.branches.models import Branch
        assigned_ids = set(self.user.branch_access.filter(is_active=True).values_list("branch_id", flat=True))
        if self.primary_branch_id:
            assigned_ids.add(self.primary_branch_id)
        return Branch.objects.filter(id__in=assigned_ids)

    def is_assigned_to_branch(self, branch):
        if not branch:
            return False
        if self.primary_branch_id == branch.id:
            return True
        return self.user.branch_access.filter(branch=branch, is_active=True).exists()

    def __str__(self):
        return f"{self.employee_number} · {self.full_name} ({self.job_title})"


class StaffDocument(UUIDTimeStampedModel):
    class DocumentType(models.TextChoices):
        CONTRACT = "CONTRACT", "Employment Contract"
        CERTIFICATE = "CERTIFICATE", "Certification / Diploma"
        ID_PROOF = "ID_PROOF", "Government ID / Passport"
        RESUME = "RESUME", "Resume / CV"
        OTHER = "OTHER", "Other"

    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=150)
    document_type = models.CharField(
        max_length=50,
        choices=DocumentType.choices,
        default=DocumentType.CONTRACT,
    )
    file = models.FileField(upload_to=staff_document_path)
    uploaded_at = models.DateTimeField(default=timezone.now)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ("-uploaded_at",)

    def __str__(self):
        return f"{self.staff.employee_number} - {self.title} ({self.document_type})"


class TrainerProfile(UUIDTimeStampedModel, AuditFieldMixin):
    class TrainerStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        UNAVAILABLE = "UNAVAILABLE", "Temporarily Unavailable"

    staff = models.OneToOneField(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="trainer_profile",
    )
    specializations = models.TextField(
        blank=True,
        help_text="e.g. Strength & Conditioning, HIIT, Weight Loss, Bodybuilding, Rehabilitation",
    )
    certifications = models.TextField(blank=True, help_text="e.g. ACE Certified, NASM-CPT, First Aid & CPR")
    experience_years = models.PositiveIntegerField(default=0)
    bio = models.TextField(blank=True)
    status = models.CharField(
        max_length=30,
        choices=TrainerStatus.choices,
        default=TrainerStatus.ACTIVE,
        db_index=True,
    )
    max_active_clients = models.PositiveIntegerField(
        default=15,
        help_text="Maximum concurrent personal training clients allowed.",
    )
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Standard hourly PT session rate in ETB.",
    )
    availability_schedule = models.JSONField(
        default=dict,
        blank=True,
        help_text="Weekly availability matrix (e.g. {'Monday': ['06:00-12:00', '16:00-20:00']}).",
    )

    class Meta:
        ordering = ("staff__employee_number",)

    @property
    def full_name(self):
        return self.staff.full_name

    @property
    def employee_number(self):
        return self.staff.employee_number

    @property
    def primary_branch(self):
        return self.staff.primary_branch

    def get_assigned_branches(self):
        return self.staff.get_assigned_branches()

    def is_assigned_to_branch(self, branch):
        return self.staff.is_assigned_to_branch(branch)

    def active_client_count(self):
        return self.member_assignments.filter(status=TrainerMemberAssignment.AssignmentStatus.ACTIVE).count()

    def __str__(self):
        return f"Trainer {self.full_name} ({self.employee_number})"


class TrainerMemberAssignment(UUIDTimeStampedModel, AuditFieldMixin):
    class AssignmentStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.PROTECT,
        related_name="member_assignments",
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.PROTECT,
        related_name="trainer_assignments",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="trainer_assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=30,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.ACTIVE,
        db_index=True,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-start_date",)
        indexes = [
            models.Index(fields=["trainer", "status"], name="tma_trainer_status_idx"),
            models.Index(fields=["member", "status"], name="tma_member_status_idx"),
            models.Index(fields=["branch", "status"], name="tma_branch_status_idx"),
        ]

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot precede assignment start date."})

        # Ensure branch matches trainer authorization
        if self.trainer_id and self.branch_id:
            if not self.trainer.is_assigned_to_branch(self.branch):
                raise ValidationError({"branch": f"Trainer {self.trainer.full_name} is not authorized for branch {self.branch.name}."})

        # Prevent duplicate active assignment
        if self.trainer_id and self.member_id and self.status == self.AssignmentStatus.ACTIVE:
            existing = TrainerMemberAssignment.objects.filter(
                trainer=self.trainer,
                member=self.member,
                status=self.AssignmentStatus.ACTIVE,
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError({"member": f"{self.member.full_name} already has an active assignment with Trainer {self.trainer.full_name}."})

        # Enforce trainer active status
        if self.trainer_id and self.status == self.AssignmentStatus.ACTIVE:
            if self.trainer.status != TrainerProfile.TrainerStatus.ACTIVE:
                raise ValidationError({"trainer": f"Trainer {self.trainer.full_name} is not active and cannot accept new client assignments."})

        # Enforce trainer client capacity limit
        if self.trainer_id and self.status == self.AssignmentStatus.ACTIVE:
            active_qs = TrainerMemberAssignment.objects.filter(
                trainer=self.trainer,
                status=self.AssignmentStatus.ACTIVE,
            )
            if self.pk:
                active_qs = active_qs.exclude(pk=self.pk)
            if active_qs.count() >= self.trainer.max_active_clients:
                raise ValidationError({"trainer": f"Trainer {self.trainer.full_name} has reached maximum client capacity ({self.trainer.max_active_clients})."})

    def save(self, *args, **kwargs):
        if self.status == self.AssignmentStatus.ACTIVE and self.trainer_id:
            with transaction.atomic():
                # Acquire row lock on TrainerProfile to serialize concurrent assignments
                TrainerProfile.objects.select_for_update().get(pk=self.trainer_id)
                self.full_clean()
                return super().save(*args, **kwargs)
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.member.full_name} → {self.trainer.full_name} ({self.status})"


class TrainingSession(UUIDTimeStampedModel, AuditFieldMixin):
    class SessionStatus(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        NO_SHOW = "NO_SHOW", "No-show"

    class SessionType(models.TextChoices):
        PERSONAL_TRAINING = "PERSONAL_TRAINING", "Personal Training"
        FITNESS_ASSESSMENT = "FITNESS_ASSESSMENT", "Fitness Assessment"
        CONSULTATION = "CONSULTATION", "Consultation"
        GROUP_CLASS = "GROUP_CLASS", "Small Group Class"

    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.PROTECT,
        related_name="training_sessions",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="training_sessions",
    )
    assignment = models.ForeignKey(
        TrainerMemberAssignment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sessions",
    )
    session_date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    status = models.CharField(
        max_length=30,
        choices=SessionStatus.choices,
        default=SessionStatus.SCHEDULED,
        db_index=True,
    )
    session_type = models.CharField(
        max_length=50,
        choices=SessionType.choices,
        default=SessionType.PERSONAL_TRAINING,
    )
    trainer_notes = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_sessions",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-session_date", "-start_time")
        indexes = [
            models.Index(fields=["session_date", "trainer"], name="ts_date_trainer_idx"),
            models.Index(fields=["session_date", "member"], name="ts_date_member_idx"),
            models.Index(fields=["branch", "status"], name="ts_branch_status_idx"),
        ]

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "Session end time must be after start time."})

        if self.trainer_id and self.branch_id:
            if not self.trainer.is_assigned_to_branch(self.branch):
                raise ValidationError({"branch": f"Trainer is not authorized for branch {self.branch.name}."})

        # Ensure trainer and member are active for scheduled sessions
        if self.status == self.SessionStatus.SCHEDULED:
            if self.trainer_id and self.trainer.status != TrainerProfile.TrainerStatus.ACTIVE:
                raise ValidationError({"trainer": f"Trainer {self.trainer.full_name} is not active and cannot be scheduled."})
            if self.member_id and self.member.status != "ACTIVE":
                raise ValidationError({"member": f"Member {self.member.full_name} is not active and cannot be scheduled."})

        # Check trainer scheduling conflict
        if self.trainer_id and self.session_date and self.start_time and self.end_time and self.status == self.SessionStatus.SCHEDULED:
            trainer_conflicts = TrainingSession.objects.filter(
                trainer=self.trainer,
                session_date=self.session_date,
                status=self.SessionStatus.SCHEDULED,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                trainer_conflicts = trainer_conflicts.exclude(pk=self.pk)
            if trainer_conflicts.exists():
                raise ValidationError({"start_time": f"Trainer {self.trainer.full_name} already has a conflicting scheduled session at this time."})

        # Check member scheduling conflict
        if self.member_id and self.session_date and self.start_time and self.end_time and self.status == self.SessionStatus.SCHEDULED:
            member_conflicts = TrainingSession.objects.filter(
                member=self.member,
                session_date=self.session_date,
                status=self.SessionStatus.SCHEDULED,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                member_conflicts = member_conflicts.exclude(pk=self.pk)
            if member_conflicts.exists():
                raise ValidationError({"member": f"{self.member.full_name} is already scheduled for another training session at this time."})

    def save(self, *args, **kwargs):
        if self.status == self.SessionStatus.SCHEDULED and self.trainer_id and self.member_id:
            with transaction.atomic():
                # Acquire row lock on TrainerProfile and Member to serialize concurrent bookings
                TrainerProfile.objects.select_for_update().get(pk=self.trainer_id)
                from apps.members.models import Member
                Member.objects.select_for_update().get(pk=self.member_id)
                self.full_clean()
                return super().save(*args, **kwargs)
        self.full_clean()
        return super().save(*args, **kwargs)

    def complete_session(self, notes=""):
        self.status = self.SessionStatus.COMPLETED
        self.completed_at = timezone.now()
        if notes:
            self.trainer_notes = notes
        self.save()

    def cancel_session(self, user=None, reason=""):
        self.status = self.SessionStatus.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancelled_by = user
        self.cancellation_reason = reason
        self.save()

    @property
    def duration_minutes(self):
        if not self.start_time or not self.end_time:
            return 0
        from datetime import datetime, date
        dt1 = datetime.combine(date.today(), self.start_time)
        dt2 = datetime.combine(date.today(), self.end_time)
        return int((dt2 - dt1).total_seconds() / 60)

    def __str__(self):
        return f"{self.session_date} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} · {self.trainer.full_name} & {self.member.full_name}"


class ShiftTemplate(UUIDTimeStampedModel):
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="shift_templates",
    )
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    color = models.CharField(max_length=20, default="#0d9488")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("branch", "start_time")
        constraints = [
            models.UniqueConstraint(fields=["branch", "name"], name="unique_shift_template_per_branch"),
        ]

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "Shift end time must be after start time."})

    def __str__(self):
        return f"{self.name} ({self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')}) - {self.branch.code}"


class StaffShift(UUIDTimeStampedModel, AuditFieldMixin):
    class ShiftStatus(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        ABSENT = "ABSENT", "Absent"
        CANCELLED = "CANCELLED", "Cancelled"

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="shifts",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.CASCADE,
        related_name="staff_shifts",
    )
    shift_template = models.ForeignKey(
        ShiftTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shifts",
    )
    date = models.DateField(db_index=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_rest_day = models.BooleanField(default=False)
    status = models.CharField(
        max_length=30,
        choices=ShiftStatus.choices,
        default=ShiftStatus.SCHEDULED,
        db_index=True,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-date", "start_time")
        indexes = [
            models.Index(fields=["staff", "date"], name="shift_staff_date_idx"),
            models.Index(fields=["branch", "date"], name="shift_branch_date_idx"),
            models.Index(fields=["status", "date"], name="shift_status_date_idx"),
        ]

    def clean(self):
        super().clean()
        if not self.is_rest_day:
            if not self.start_time or not self.end_time:
                if self.shift_template:
                    self.start_time = self.shift_template.start_time
                    self.end_time = self.shift_template.end_time
                else:
                    raise ValidationError("Start time and end time are required for working shifts.")
            if self.start_time and self.end_time and self.end_time <= self.start_time:
                raise ValidationError({"end_time": "Shift end time must be after start time."})

            # Check overlap on the same date for the same staff member
            if self.staff_id and self.date and self.status == self.ShiftStatus.SCHEDULED:
                conflicts = StaffShift.objects.filter(
                    staff=self.staff,
                    date=self.date,
                    status=self.ShiftStatus.SCHEDULED,
                    is_rest_day=False,
                    start_time__lt=self.end_time,
                    end_time__gt=self.start_time,
                )
                if self.pk:
                    conflicts = conflicts.exclude(pk=self.pk)
                if conflicts.exists():
                    raise ValidationError("This shift overlaps with another scheduled shift for this staff member.")

    def __str__(self):
        if self.is_rest_day:
            return f"{self.date} · {self.staff.full_name} [REST DAY]"
        times = f"{self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')}" if self.start_time and self.end_time else ""
        return f"{self.date} {times} · {self.staff.full_name} ({self.branch.code})"


class LeaveRequest(UUIDTimeStampedModel, AuditFieldMixin):
    class LeaveType(models.TextChoices):
        ANNUAL = "ANNUAL", "Annual Leave"
        SICK = "SICK", "Sick Leave"
        EMERGENCY = "EMERGENCY", "Emergency Leave"
        UNPAID = "UNPAID", "Unpaid Leave"
        MATERNITY_PATERNITY = "MATERNITY_PATERNITY", "Maternity / Paternity"
        OTHER = "OTHER", "Other"

    class LeaveStatus(models.TextChoices):
        PENDING = "PENDING", "Pending Approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    staff = models.ForeignKey(
        StaffProfile,
        on_delete=models.CASCADE,
        related_name="leave_requests",
    )
    leave_type = models.CharField(
        max_length=50,
        choices=LeaveType.choices,
        default=LeaveType.ANNUAL,
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(
        max_length=30,
        choices=LeaveStatus.choices,
        default=LeaveStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_staff_leaves",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-start_date",)
        indexes = [
            models.Index(fields=["staff", "status"], name="leave_staff_status_idx"),
            models.Index(fields=["start_date", "end_date"], name="leave_date_range_idx"),
        ]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "Leave end date cannot precede start date."})

    def approve(self, user=None):
        self.status = self.LeaveStatus.APPROVED
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.save()

    def reject(self, user=None, reason=""):
        self.status = self.LeaveStatus.REJECTED
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.rejection_reason = reason
        self.save()

    @property
    def total_days(self):
        if not self.start_date or not self.end_date:
            return 0
        return (self.end_date - self.start_date).days + 1

    def __str__(self):
        return f"{self.staff.full_name} · {self.leave_type} ({self.start_date} to {self.end_date}) [{self.status}]"
