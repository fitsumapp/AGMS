import uuid
from datetime import date, datetime, time, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import AuditFieldMixin, UUIDTimeStampedModel


class ClassType(UUIDTimeStampedModel, AuditFieldMixin):
    """
    Configurable class catalog types (e.g. Yoga, Zumba, HIIT, Spinning, Aerobics).
    Not hardcoded; admins can add, rename, or deactivate class types.
    """
    class Category(models.TextChoices):
        CARDIO = "CARDIO", "Cardio"
        STRENGTH = "STRENGTH", "Strength & Conditioning"
        MIND_BODY = "MIND_BODY", "Mind & Body (Yoga / Pilates)"
        DANCE = "DANCE", "Dance & Aerobics"
        COMBAT = "COMBAT", "Boxing & Combat Fitness"
        FUNCTIONAL = "FUNCTIONAL", "Functional & Cross Training"
        CYCLING = "CYCLING", "Spinning & Cycling"
        OTHER = "OTHER", "Other Specialty"

    class Difficulty(models.TextChoices):
        ALL_LEVELS = "ALL_LEVELS", "All Levels"
        BEGINNER = "BEGINNER", "Beginner"
        INTERMEDIATE = "INTERMEDIATE", "Intermediate"
        ADVANCED = "ADVANCED", "Advanced"

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=50,
        choices=Category.choices,
        default=Category.CARDIO,
        db_index=True,
    )
    difficulty_level = models.CharField(
        max_length=30,
        choices=Difficulty.choices,
        default=Difficulty.ALL_LEVELS,
    )
    default_duration_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Standard duration in minutes.",
    )
    default_capacity = models.PositiveIntegerField(
        default=20,
        help_text="Standard maximum participant capacity.",
    )
    default_waitlist_capacity = models.PositiveIntegerField(
        default=5,
        help_text="Standard maximum waitlist capacity.",
    )
    trainer_qualifications = models.TextField(
        blank=True,
        help_text="Required trainer certifications, expertise, or prerequisites.",
    )
    applicable_branches = models.ManyToManyField(
        "branches.Branch",
        blank=True,
        related_name="available_classes",
        help_text="Branches authorized to offer this class type. Leave empty for all branches.",
    )
    color_code = models.CharField(
        max_length=20,
        default="#10b981",
        help_text="Hex color code for calendar visualization.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name",)

    def clean(self):
        super().clean()
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "Class name is required."})
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.name)
        if self.default_duration_minutes < 15:
            raise ValidationError({"default_duration_minutes": "Duration must be at least 15 minutes."})
        if self.default_capacity < 1:
            raise ValidationError({"default_capacity": "Capacity must be at least 1."})

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            from django.utils.text import slugify
            self.slug = slugify(self.name)
        self.full_clean()
        super().save(*args, **kwargs)

    def is_available_at_branch(self, branch):
        if not self.applicable_branches.exists():
            return True
        return self.applicable_branches.filter(pk=branch.pk).exists()

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class ClassScheduleTemplate(UUIDTimeStampedModel, AuditFieldMixin):
    """
    Recurring weekly class schedule template (e.g. Every Monday & Wednesday at 09:00).
    """
    class DayOfWeek(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    class_type = models.ForeignKey(
        ClassType,
        on_delete=models.CASCADE,
        related_name="schedule_templates",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="class_templates",
    )
    room = models.CharField(
        max_length=100,
        default="Studio A",
        help_text="Studio, hall, or room name (e.g. Studio A, Spinning Room, Main Floor).",
    )
    trainer = models.ForeignKey(
        "staff.TrainerProfile",
        on_delete=models.PROTECT,
        related_name="class_templates",
    )
    day_of_week = models.PositiveSmallIntegerField(
        choices=DayOfWeek.choices,
        db_index=True,
    )
    start_time = models.TimeField()
    end_time = models.TimeField()
    capacity = models.PositiveIntegerField(default=20)
    waitlist_capacity = models.PositiveIntegerField(default=5)
    booking_window_days_ahead = models.PositiveIntegerField(
        default=7,
        help_text="How many days before the class date booking opens.",
    )
    booking_cutoff_minutes = models.PositiveIntegerField(
        default=30,
        help_text="Minutes before start_time when booking closes.",
    )
    cancellation_cutoff_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Minutes before start_time for penalty-free member cancellation.",
    )
    start_date = models.DateField(
        default=timezone.localdate,
        help_text="Effective start date of this recurring schedule.",
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional end date when this recurring schedule expires.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("day_of_week", "start_time")

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "Class end time must be after start time."})
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot precede start date."})
        if self.trainer_id and self.branch_id:
            if not self.trainer.is_assigned_to_branch(self.branch):
                raise ValidationError({"trainer": f"Trainer {self.trainer.full_name} is not authorized for branch {self.branch.name}."})
            if self.trainer.status != "ACTIVE":
                raise ValidationError({"trainer": f"Trainer {self.trainer.full_name} is not active."})
        if self.class_type_id and self.branch_id:
            if not self.class_type.is_available_at_branch(self.branch):
                raise ValidationError({"class_type": f"Class {self.class_type.name} is not authorized for branch {self.branch.name}."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.class_type.name} · {self.get_day_of_week_display()} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} ({self.branch.code})"


class ClassScheduleException(UUIDTimeStampedModel):
    """
    Exception or holiday override for a recurring class schedule.
    """
    schedule_template = models.ForeignKey(
        ClassScheduleTemplate,
        on_delete=models.CASCADE,
        related_name="exceptions",
    )
    exception_date = models.DateField(db_index=True)
    reason = models.CharField(max_length=200, help_text="e.g. Public Holiday, Maintenance, Special Event")
    is_cancelled = models.BooleanField(default=True)

    class Meta:
        ordering = ("exception_date",)
        constraints = [
            models.UniqueConstraint(
                fields=["schedule_template", "exception_date"],
                name="unique_schedule_exception_date",
            ),
        ]

    def __str__(self):
        return f"{self.schedule_template} on {self.exception_date} ({self.reason})"


class ClassSession(UUIDTimeStampedModel, AuditFieldMixin):
    """
    Individual scheduled occurrence of a group class.
    May be generated from a ClassScheduleTemplate or scheduled as a one-time class.
    """
    class SessionStatus(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    schedule_template = models.ForeignKey(
        ClassScheduleTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sessions",
    )
    class_type = models.ForeignKey(
        ClassType,
        on_delete=models.PROTECT,
        related_name="sessions",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="class_sessions",
    )
    room = models.CharField(max_length=100, default="Studio A")
    trainer = models.ForeignKey(
        "staff.TrainerProfile",
        on_delete=models.PROTECT,
        related_name="class_sessions",
    )
    session_date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    capacity = models.PositiveIntegerField(default=20)
    waitlist_capacity = models.PositiveIntegerField(default=5)
    status = models.CharField(
        max_length=30,
        choices=SessionStatus.choices,
        default=SessionStatus.SCHEDULED,
        db_index=True,
    )
    booking_opens_at = models.DateTimeField(null=True, blank=True)
    booking_closes_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_class_sessions",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("session_date", "start_time")
        indexes = [
            models.Index(fields=["session_date", "branch", "status"], name="cs_date_branch_status_idx"),
            models.Index(fields=["session_date", "trainer"], name="cs_date_trainer_idx"),
            models.Index(fields=["session_date", "branch", "room"], name="cs_date_room_idx"),
        ]

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "Class end time must be after start time."})

        if self.trainer_id and self.branch_id:
            if not self.trainer.is_assigned_to_branch(self.branch):
                raise ValidationError({"branch": f"Trainer is not authorized for branch {self.branch.name}."})
            if self.status == self.SessionStatus.SCHEDULED and self.trainer.status != "ACTIVE":
                raise ValidationError({"trainer": f"Trainer {self.trainer.full_name} is not active."})

        if self.class_type_id and self.branch_id:
            if not self.class_type.is_available_at_branch(self.branch):
                raise ValidationError({"class_type": f"Class {self.class_type.name} is not authorized for branch {self.branch.name}."})

        # Check trainer scheduling conflict with other ClassSessions
        if self.trainer_id and self.session_date and self.start_time and self.end_time and self.status == self.SessionStatus.SCHEDULED:
            trainer_class_conflicts = ClassSession.objects.filter(
                trainer=self.trainer,
                session_date=self.session_date,
                status=self.SessionStatus.SCHEDULED,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                trainer_class_conflicts = trainer_class_conflicts.exclude(pk=self.pk)
            if trainer_class_conflicts.exists():
                raise ValidationError({"start_time": f"Trainer {self.trainer.full_name} already has a conflicting group class at this time."})

            # Check trainer scheduling conflict with Phase 08 Personal Training sessions
            from apps.staff.models import TrainingSession
            pt_conflicts = TrainingSession.objects.filter(
                trainer=self.trainer,
                session_date=self.session_date,
                status=TrainingSession.SessionStatus.SCHEDULED,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if pt_conflicts.exists():
                raise ValidationError({"start_time": f"Trainer {self.trainer.full_name} has a conflicting personal training session at this time."})

        # Check room scheduling conflict in the same branch
        if self.branch_id and self.room and self.session_date and self.start_time and self.end_time and self.status == self.SessionStatus.SCHEDULED:
            room_conflicts = ClassSession.objects.filter(
                branch=self.branch,
                room__iexact=self.room.strip(),
                session_date=self.session_date,
                status=self.SessionStatus.SCHEDULED,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                room_conflicts = room_conflicts.exclude(pk=self.pk)
            if room_conflicts.exists():
                raise ValidationError({"room": f"Room '{self.room}' is already booked for another class at this time."})

    def save(self, *args, **kwargs):
        if self.status == self.SessionStatus.SCHEDULED and self.trainer_id:
            with transaction.atomic():
                # Acquire row locks in consistent hierarchy: 1. Branch, 2. TrainerProfile
                if self.branch_id:
                    from apps.branches.models import Branch
                    Branch.objects.select_for_update().get(pk=self.branch_id)
                from apps.staff.models import TrainerProfile
                TrainerProfile.objects.select_for_update().get(pk=self.trainer_id)
                self.full_clean()
                return super().save(*args, **kwargs)
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def booked_count(self):
        return self.bookings.filter(status=ClassBooking.BookingStatus.BOOKED).count()

    @property
    def waitlist_count(self):
        return self.bookings.filter(status=ClassBooking.BookingStatus.WAITLISTED).count()

    @property
    def attended_count(self):
        return self.bookings.filter(status=ClassBooking.BookingStatus.ATTENDED).count()

    @property
    def no_show_count(self):
        return self.bookings.filter(status=ClassBooking.BookingStatus.NO_SHOW).count()

    @property
    def available_spots(self):
        return max(0, self.capacity - self.booked_count)

    @property
    def is_full(self):
        return self.booked_count >= self.capacity

    @property
    def is_waitlist_full(self):
        return self.waitlist_count >= self.waitlist_capacity

    @property
    def duration_minutes(self):
        if not self.start_time or not self.end_time:
            return 0
        dt1 = datetime.combine(date.today(), self.start_time)
        dt2 = datetime.combine(date.today(), self.end_time)
        return int((dt2 - dt1).total_seconds() / 60)

    def cancel_session(self, user=None, reason=""):
        self.status = self.SessionStatus.CANCELLED
        self.cancelled_at = timezone.now()
        self.cancelled_by = user
        self.cancellation_reason = reason
        self.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancellation_reason"])

    def __str__(self):
        return f"{self.class_type.name} · {self.session_date} {self.start_time.strftime('%H:%M')}-{self.end_time.strftime('%H:%M')} ({self.room}, {self.branch.code})"


class ClassBooking(UUIDTimeStampedModel, AuditFieldMixin):
    """
    Member reservation and attendance record for a ClassSession.
    """
    class BookingStatus(models.TextChoices):
        BOOKED = "BOOKED", "Confirmed Spot"
        WAITLISTED = "WAITLISTED", "Waitlisted"
        ATTENDED = "ATTENDED", "Attended"
        NO_SHOW = "NO_SHOW", "No-show"
        CANCELLED = "CANCELLED", "Cancelled"

    session = models.ForeignKey(
        ClassSession,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.CASCADE,
        related_name="class_bookings",
    )
    membership = models.ForeignKey(
        "memberships.Membership",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="class_bookings",
    )
    status = models.CharField(
        max_length=30,
        choices=BookingStatus.choices,
        default=BookingStatus.BOOKED,
        db_index=True,
    )
    waitlist_position = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    booked_at = models.DateTimeField(default=timezone.now)
    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="class_bookings_made",
    )
    promoted_from_waitlist_at = models.DateTimeField(null=True, blank=True)
    attendance_marked_at = models.DateTimeField(null=True, blank=True)
    attendance_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="class_attendance_marked",
    )
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_class_bookings",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-booked_at",)
        indexes = [
            models.Index(fields=["session", "status"], name="cb_session_status_idx"),
            models.Index(fields=["member", "status"], name="cb_member_status_idx"),
            models.Index(fields=["session", "waitlist_position"], name="cb_session_waitlist_idx"),
        ]

    def clean(self):
        super().clean()
        if self.status in [self.BookingStatus.BOOKED, self.BookingStatus.WAITLISTED] and self.session_id and self.member_id:
            # Prevent duplicate active bookings
            existing = ClassBooking.objects.filter(
                session=self.session,
                member=self.member,
                status__in=[self.BookingStatus.BOOKED, self.BookingStatus.WAITLISTED],
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError({"member": f"{self.member.full_name} already has an active booking or waitlist entry for this class."})

            # Check overlapping class bookings for member
            overlapping_classes = ClassBooking.objects.filter(
                member=self.member,
                status=self.BookingStatus.BOOKED,
                session__session_date=self.session.session_date,
                session__status=ClassSession.SessionStatus.SCHEDULED,
                session__start_time__lt=self.session.end_time,
                session__end_time__gt=self.session.start_time,
            )
            if self.pk:
                overlapping_classes = overlapping_classes.exclude(pk=self.pk)
            if overlapping_classes.exists():
                raise ValidationError({"member": f"{self.member.full_name} is already booked for another class at this time."})

            # Check overlapping personal training sessions for member
            from apps.staff.models import TrainingSession
            pt_overlaps = TrainingSession.objects.filter(
                member=self.member,
                session_date=self.session.session_date,
                status=TrainingSession.SessionStatus.SCHEDULED,
                start_time__lt=self.session.end_time,
                end_time__gt=self.session.start_time,
            )
            if pt_overlaps.exists():
                raise ValidationError({"member": f"{self.member.full_name} has a scheduled personal training session at this time."})

        # Check capacity for confirmed spots
        if self.status == self.BookingStatus.BOOKED and self.session_id:
            active_spots = ClassBooking.objects.filter(
                session=self.session,
                status=self.BookingStatus.BOOKED,
            )
            if self.pk:
                active_spots = active_spots.exclude(pk=self.pk)
            if active_spots.count() >= self.session.capacity:
                raise ValidationError({"session": f"Class '{self.session.class_type.name}' has reached maximum capacity ({self.session.capacity})."})

        # Check weekly class limit if configured on membership package
        if self.status == self.BookingStatus.BOOKED and self.session_id and self.member_id and self.membership_id:
            package = getattr(self.membership, "package", None)
            allowed_per_week = getattr(package, "allowed_classes_per_week", None) if package else None
            if allowed_per_week is not None:
                session_date = self.session.session_date
                week_start = session_date - timedelta(days=session_date.weekday())
                week_end = week_start + timedelta(days=6)
                weekly_qs = ClassBooking.objects.filter(
                    member=self.member,
                    session__session_date__gte=week_start,
                    session__session_date__lte=week_end,
                    status__in=[
                        self.BookingStatus.BOOKED,
                        self.BookingStatus.ATTENDED,
                        self.BookingStatus.NO_SHOW,
                    ],
                )
                if self.pk:
                    weekly_qs = weekly_qs.exclude(pk=self.pk)
                if weekly_qs.count() >= allowed_per_week:
                    raise ValidationError({
                        "member": f"Member {self.member.full_name} has reached the weekly class limit of {allowed_per_week} class(es) for the week of {week_start} to {week_end}."
                    })

    def save(self, *args, **kwargs):
        if self.status in [self.BookingStatus.BOOKED, self.BookingStatus.WAITLISTED] and self.session_id:
            with transaction.atomic():
                # Acquire row lock on ClassSession to serialize concurrent bookings
                ClassSession.objects.select_for_update().get(pk=self.session_id)
                self.full_clean()
                return super().save(*args, **kwargs)
        self.full_clean()
        return super().save(*args, **kwargs)

    def mark_attendance(self, status, user=None, notes=""):
        if status not in [self.BookingStatus.ATTENDED, self.BookingStatus.NO_SHOW]:
            raise ValidationError("Attendance status must be either Attended or No-show.")
        self.status = status
        self.attendance_marked_at = timezone.now()
        self.attendance_marked_by = user
        if notes:
            self.notes = f"{self.notes}\n{notes}".strip()
        self.save(update_fields=["status", "attendance_marked_at", "attendance_marked_by", "notes"])

    def __str__(self):
        return f"{self.member.full_name} · {self.session.class_type.name} ({self.get_status_display()})"
