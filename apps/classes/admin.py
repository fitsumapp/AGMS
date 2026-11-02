from django.contrib import admin, messages
from django.db.models import Q
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from apps.accounts.roles import has_role, is_owner
from apps.branches.services import accessible_branches
from .models import (
    ClassBooking,
    ClassScheduleException,
    ClassScheduleTemplate,
    ClassSession,
    ClassType,
)
from .services import (
    cancel_class_booking,
    generate_sessions_from_schedule,
    mark_class_attendance,
)


class ClassScheduleExceptionInline(TabularInline):
    model = ClassScheduleException
    extra = 1
    classes = ("collapse",)
    collapsible = True
    fields = ("exception_date", "reason", "is_cancelled")


class ClassBookingInline(TabularInline):
    model = ClassBooking
    extra = 0
    classes = ("collapse",)
    collapsible = True
    fields = (
        "member",
        "status",
        "waitlist_position",
        "booked_at",
        "attendance_marked_at",
        "notes",
    )
    readonly_fields = ("booked_at", "attendance_marked_at", "waitlist_position")


@admin.register(ClassType)
class ClassTypeAdmin(ModelAdmin):
    list_display = (
        "name",
        "category",
        "difficulty_level",
        "default_duration_minutes",
        "default_capacity",
        "default_waitlist_capacity",
        "is_active",
    )
    list_filter = ("category", "difficulty_level", "is_active", "applicable_branches")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("applicable_branches",)

    fieldsets = (
        ("Class Identity / የስልጠና ዓይነት መለያ", {
            "classes": ("collapse",),
            "fields": (
                ("name", "slug"),
                ("category", "difficulty_level"),
                "description",
            ),
        }),
        ("Capacity & Schedule Defaults / የሰዓት እና የቦታ መጠን", {
            "classes": ("collapse",),
            "fields": (
                ("default_duration_minutes", "default_capacity"),
                ("default_waitlist_capacity", "color_code"),
                "is_active",
            ),
        }),
        ("Branch Availability & Requirements / ቅርንጫፍ እና መስፈርቶች", {
            "classes": ("collapse",),
            "fields": (
                "applicable_branches",
                "trainer_qualifications",
            ),
        }),
    )


@admin.register(ClassScheduleTemplate)
class ClassScheduleTemplateAdmin(ModelAdmin):
    list_display = (
        "class_type",
        "branch",
        "room",
        "trainer",
        "day_of_week",
        "time_slot",
        "capacity",
        "is_active",
    )
    list_filter = ("branch", "day_of_week", "is_active", "class_type")
    search_fields = (
        "class_type__name",
        "trainer__staff__user__first_name",
        "trainer__staff__user__last_name",
        "room",
    )
    inlines = [ClassScheduleExceptionInline]
    actions = ["generate_sessions_action"]

    fieldsets = (
        ("Schedule Template / የስልጠና መርሃ-ግብር", {
            "classes": ("collapse",),
            "fields": (
                ("class_type", "branch"),
                ("room", "trainer"),
                ("day_of_week", "start_time", "end_time"),
            ),
        }),
        ("Capacity & Booking Rules / የቦታ እና የቦታ ማስያዣ ደንቦች", {
            "classes": ("collapse",),
            "fields": (
                ("capacity", "waitlist_capacity"),
                ("booking_window_days_ahead", "booking_cutoff_minutes"),
                ("cancellation_cutoff_minutes", "is_active"),
                ("start_date", "end_date"),
            ),
        }),
    )

    def time_slot(self, obj):
        return f"{obj.start_time.strftime('%H:%M')} - {obj.end_time.strftime('%H:%M')}"
    time_slot.short_description = "Time"

    @admin.action(description="Generate class sessions for the next 4 weeks from selected templates")
    def generate_sessions_action(self, request, queryset):
        total_created = 0
        for template in queryset:
            sessions = generate_sessions_from_schedule(template=template, actor=request.user)
            total_created += len(sessions)
        self.message_user(
            request,
            f"Successfully generated {total_created} class session(s) from {queryset.count()} schedule template(s).",
            messages.SUCCESS,
        )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("class_type", "branch", "trainer__staff__user")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            branches = accessible_branches(request.user)
            return qs.filter(branch__in=branches)
        if has_role(request.user, "Trainer"):
            return qs.filter(trainer__staff__user=request.user)
        return qs


@admin.register(ClassSession)
class ClassSessionAdmin(ModelAdmin):
    list_display = (
        "class_name_display",
        "session_date",
        "time_slot",
        "branch",
        "room",
        "trainer_display",
        "capacity_progress",
        "status",
    )
    list_filter = ("status", "branch", "session_date", "class_type")
    search_fields = (
        "class_type__name",
        "trainer__staff__user__first_name",
        "trainer__staff__user__last_name",
        "room",
    )
    inlines = [ClassBookingInline]
    actions = ["mark_completed_action", "cancel_sessions_action"]

    fieldsets = (
        ("Session Information / የስልጠና ክፍል መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("class_type", "branch"),
                ("room", "trainer"),
                ("session_date", "start_time", "end_time"),
                ("capacity", "waitlist_capacity"),
                "status",
            ),
        }),
        ("Booking Window & Notes / የምዝገባ ሰዓት እና ማስታወሻ", {
            "classes": ("collapse",),
            "fields": (
                ("booking_opens_at", "booking_closes_at"),
                ("cancellation_reason", "cancelled_by", "cancelled_at"),
                "notes",
            ),
        }),
    )

    def class_name_display(self, obj):
        color = obj.class_type.color_code or "#10b981"
        return format_html(
            '<span class="inline-flex items-center gap-1.5"><span class="w-2.5 h-2.5 rounded-full" style="background-color: {};"></span><span class="font-medium">{}</span></span>',
            color,
            obj.class_type.name,
        )
    class_name_display.short_description = "Class"

    def time_slot(self, obj):
        return f"{obj.start_time.strftime('%H:%M')} - {obj.end_time.strftime('%H:%M')}"
    time_slot.short_description = "Time"

    def trainer_display(self, obj):
        return obj.trainer.full_name
    trainer_display.short_description = "Trainer"

    def capacity_progress(self, obj):
        booked = obj.booked_count
        cap = obj.capacity
        waitlist = obj.waitlist_count
        wait_text = f" (+{waitlist} waitlist)" if waitlist > 0 else ""
        return f"{booked} / {cap}{wait_text}"
    capacity_progress.short_description = "Capacity"

    @admin.action(description="Mark selected sessions as Completed")
    def mark_completed_action(self, request, queryset):
        count = queryset.filter(status=ClassSession.SessionStatus.SCHEDULED).update(
            status=ClassSession.SessionStatus.COMPLETED
        )
        self.message_user(request, f"Marked {count} session(s) as Completed.", messages.SUCCESS)

    @admin.action(description="Cancel selected sessions")
    def cancel_sessions_action(self, request, queryset):
        for s in queryset:
            if s.status == ClassSession.SessionStatus.SCHEDULED:
                s.cancel_session(user=request.user, reason="Cancelled via admin action")
        self.message_user(request, f"Cancelled {queryset.count()} session(s).", messages.WARNING)

    def get_queryset(self, request):
        qs = (
            super().get_queryset(request)
            .select_related("class_type", "branch", "trainer__staff__user")
            .prefetch_related("bookings")
        )
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager") or has_role(request.user, "Receptionist"):
            branches = accessible_branches(request.user)
            return qs.filter(branch__in=branches)
        if has_role(request.user, "Trainer"):
            return qs.filter(trainer__staff__user=request.user)
        return qs


@admin.register(ClassBooking)
class ClassBookingAdmin(ModelAdmin):
    list_display = (
        "member_display",
        "session_summary",
        "branch_display",
        "status",
        "waitlist_display",
        "booked_at",
    )
    list_filter = ("status", "session__branch", "session__class_type", "session__session_date")
    search_fields = (
        "member__first_name",
        "member__last_name",
        "member__member_number",
        "session__class_type__name",
        "session__room",
    )
    actions = [
        "mark_attended_action",
        "mark_noshow_action",
        "cancel_bookings_action",
    ]

    fieldsets = (
        ("Booking Details / የቦታ ማስያዣ መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("session", "member"),
                ("membership", "status"),
                ("waitlist_position", "notes"),
            ),
        }),
        ("Audit Timestamps / የሰዓት መዝገብ", {
            "classes": ("collapse",),
            "fields": (
                ("booked_at", "booked_by"),
                ("attendance_marked_at", "attendance_marked_by"),
                ("cancellation_reason", "cancelled_at", "cancelled_by"),
            ),
        }),
    )

    def member_display(self, obj):
        return f"{obj.member.full_name} ({obj.member.member_number})"
    member_display.short_description = "Member"

    def session_summary(self, obj):
        return f"{obj.session.class_type.name} · {obj.session.session_date} {obj.session.start_time.strftime('%H:%M')}"
    session_summary.short_description = "Class Session"

    def branch_display(self, obj):
        return obj.session.branch.name
    branch_display.short_description = "Branch"

    def waitlist_display(self, obj):
        if obj.status == ClassBooking.BookingStatus.WAITLISTED:
            return f"#{obj.waitlist_position}"
        return "-"
    waitlist_display.short_description = "Waitlist Pos"

    @admin.action(description="Mark selected bookings as Attended")
    def mark_attended_action(self, request, queryset):
        count = 0
        for b in queryset:
            if b.status == ClassBooking.BookingStatus.BOOKED:
                mark_class_attendance(request.user, b, status=ClassBooking.BookingStatus.ATTENDED)
                count += 1
        self.message_user(request, f"Marked {count} booking(s) as Attended.", messages.SUCCESS)

    @admin.action(description="Mark selected bookings as No-show")
    def mark_noshow_action(self, request, queryset):
        count = 0
        for b in queryset:
            if b.status == ClassBooking.BookingStatus.BOOKED:
                mark_class_attendance(request.user, b, status=ClassBooking.BookingStatus.NO_SHOW)
                count += 1
        self.message_user(request, f"Marked {count} booking(s) as No-show.", messages.WARNING)

    @admin.action(description="Cancel selected bookings (with automatic waitlist promotion)")
    def cancel_bookings_action(self, request, queryset):
        count = 0
        for b in queryset:
            if b.status in [ClassBooking.BookingStatus.BOOKED, ClassBooking.BookingStatus.WAITLISTED]:
                cancel_class_booking(request.user, b, reason="Admin bulk cancellation")
                count += 1
        self.message_user(request, f"Cancelled {count} booking(s) and re-evaluated waitlist.", messages.SUCCESS)

    def get_queryset(self, request):
        qs = (
            super().get_queryset(request)
            .select_related("member", "session__class_type", "session__branch", "session__trainer__staff__user")
        )
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager") or has_role(request.user, "Receptionist"):
            branches = accessible_branches(request.user)
            return qs.filter(session__branch__in=branches)
        if has_role(request.user, "Trainer"):
            return qs.filter(session__trainer__staff__user=request.user)
        return qs
