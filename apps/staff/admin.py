from django.contrib import admin, messages
from django.db.models import Q
from django.utils import timezone
from unfold.admin import ModelAdmin, TabularInline

from apps.accounts.roles import has_role, is_owner
from apps.branches.services import accessible_branches
from .models import (
    LeaveRequest,
    ShiftTemplate,
    StaffDocument,
    StaffProfile,
    StaffShift,
    TrainerMemberAssignment,
    TrainerProfile,
    TrainingSession,
)


class StaffDocumentInline(TabularInline):
    model = StaffDocument
    extra = 1
    fields = ("title", "document_type", "file", "uploaded_at", "uploaded_by")
    readonly_fields = ("uploaded_at",)


@admin.register(StaffProfile)
class StaffProfileAdmin(ModelAdmin):
    list_display = (
        "employee_number",
        "full_name",
        "job_title",
        "department",
        "employment_type",
        "employment_status",
        "primary_branch",
        "hire_date",
    )
    list_filter = ("department", "employment_type", "employment_status", "primary_branch")
    search_fields = (
        "employee_number",
        "user__first_name",
        "user__last_name",
        "user__email",
        "job_title",
    )
    inlines = [StaffDocumentInline]
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("user", "primary_branch")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            branches = accessible_branches(request.user)
            return qs.filter(
                Q(primary_branch__in=branches)
                | Q(user__branch_access__branch__in=branches, user__branch_access__is_active=True)
            ).distinct()
        return qs


@admin.register(TrainerProfile)
class TrainerProfileAdmin(ModelAdmin):
    list_display = (
        "employee_number",
        "full_name",
        "status",
        "experience_years",
        "hourly_rate",
        "active_client_count",
        "primary_branch",
    )
    list_filter = ("status", "staff__primary_branch")
    search_fields = (
        "staff__employee_number",
        "staff__user__first_name",
        "staff__user__last_name",
        "specializations",
    )
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("staff__user", "staff__primary_branch")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            branches = accessible_branches(request.user)
            return qs.filter(
                Q(staff__primary_branch__in=branches)
                | Q(staff__user__branch_access__branch__in=branches, staff__user__branch_access__is_active=True)
            ).distinct()
        if has_role(request.user, "Trainer"):
            return qs.filter(staff__user=request.user)
        return qs


@admin.register(TrainerMemberAssignment)
class TrainerMemberAssignmentAdmin(ModelAdmin):
    list_display = ("trainer", "member", "branch", "start_date", "end_date", "status")
    list_filter = ("status", "branch", "start_date")
    search_fields = (
        "trainer__staff__user__first_name",
        "trainer__staff__user__last_name",
        "member__first_name",
        "member__last_name",
        "member__member_number",
    )
    actions = ["mark_completed", "mark_cancelled"]

    @admin.action(description="Mark selected assignments as Completed")
    def mark_completed(self, request, queryset):
        updated = queryset.update(status=TrainerMemberAssignment.AssignmentStatus.COMPLETED)
        self.message_user(request, f"{updated} assignments marked as completed.", messages.SUCCESS)

    @admin.action(description="Mark selected assignments as Cancelled")
    def mark_cancelled(self, request, queryset):
        updated = queryset.update(status=TrainerMemberAssignment.AssignmentStatus.CANCELLED)
        self.message_user(request, f"{updated} assignments cancelled.", messages.WARNING)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "trainer__staff__user", "member", "branch"
        )
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            return qs.filter(branch__in=accessible_branches(request.user))
        if has_role(request.user, "Trainer"):
            return qs.filter(trainer__staff__user=request.user)
        return qs


@admin.register(TrainingSession)
class TrainingSessionAdmin(ModelAdmin):
    list_display = (
        "session_date",
        "start_time",
        "end_time",
        "trainer",
        "member",
        "branch",
        "session_type",
        "status",
    )
    list_filter = ("status", "session_type", "branch", "session_date")
    search_fields = (
        "trainer__staff__user__first_name",
        "trainer__staff__user__last_name",
        "member__first_name",
        "member__last_name",
        "member__member_number",
    )
    actions = ["complete_sessions", "cancel_sessions"]

    @admin.action(description="Mark selected sessions as Completed")
    def complete_sessions(self, request, queryset):
        count = 0
        for session in queryset.filter(status=TrainingSession.SessionStatus.SCHEDULED):
            session.complete_session()
            count += 1
        self.message_user(request, f"{count} training sessions completed successfully.", messages.SUCCESS)

    @admin.action(description="Mark selected sessions as Cancelled")
    def cancel_sessions(self, request, queryset):
        count = 0
        for session in queryset.filter(status=TrainingSession.SessionStatus.SCHEDULED):
            session.cancel_session(user=request.user, reason="Cancelled via administrative action.")
            count += 1
        self.message_user(request, f"{count} training sessions cancelled.", messages.WARNING)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "trainer__staff__user", "member", "branch"
        )
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            return qs.filter(branch__in=accessible_branches(request.user))
        if has_role(request.user, "Trainer"):
            return qs.filter(trainer__staff__user=request.user)
        return qs


@admin.register(ShiftTemplate)
class ShiftTemplateAdmin(ModelAdmin):
    list_display = ("name", "branch", "start_time", "end_time", "is_active")
    list_filter = ("branch", "is_active")
    search_fields = ("name", "branch__name", "branch__code")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("branch")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            return qs.filter(branch__in=accessible_branches(request.user))
        return qs


@admin.register(StaffShift)
class StaffShiftAdmin(ModelAdmin):
    list_display = (
        "date",
        "staff",
        "branch",
        "start_time",
        "end_time",
        "is_rest_day",
        "status",
    )
    list_filter = ("status", "is_rest_day", "branch", "date")
    search_fields = (
        "staff__employee_number",
        "staff__user__first_name",
        "staff__user__last_name",
        "branch__name",
    )
    actions = ["mark_completed", "mark_absent", "mark_cancelled"]

    @admin.action(description="Mark selected shifts as Completed")
    def mark_completed(self, request, queryset):
        updated = queryset.update(status=StaffShift.ShiftStatus.COMPLETED)
        self.message_user(request, f"{updated} shifts marked as completed.", messages.SUCCESS)

    @admin.action(description="Mark selected shifts as Absent")
    def mark_absent(self, request, queryset):
        updated = queryset.update(status=StaffShift.ShiftStatus.ABSENT)
        self.message_user(request, f"{updated} shifts marked as absent.", messages.WARNING)

    @admin.action(description="Mark selected shifts as Cancelled")
    def mark_cancelled(self, request, queryset):
        updated = queryset.update(status=StaffShift.ShiftStatus.CANCELLED)
        self.message_user(request, f"{updated} shifts cancelled.", messages.INFO)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("staff__user", "branch")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            return qs.filter(branch__in=accessible_branches(request.user))
        if has_role(request.user, "Trainer"):
            return qs.filter(staff__user=request.user)
        return qs


@admin.register(LeaveRequest)
class LeaveRequestAdmin(ModelAdmin):
    list_display = (
        "staff",
        "leave_type",
        "start_date",
        "end_date",
        "total_days",
        "status",
        "reviewed_by",
    )
    list_filter = ("status", "leave_type", "start_date")
    search_fields = (
        "staff__employee_number",
        "staff__user__first_name",
        "staff__user__last_name",
        "reason",
    )
    actions = ["approve_leaves", "reject_leaves"]

    @admin.action(description="Approve selected leave requests")
    def approve_leaves(self, request, queryset):
        count = 0
        for leave in queryset.filter(status=LeaveRequest.LeaveStatus.PENDING):
            leave.approve(user=request.user)
            count += 1
        self.message_user(request, f"{count} leave requests approved.", messages.SUCCESS)

    @admin.action(description="Reject selected leave requests")
    def reject_leaves(self, request, queryset):
        count = 0
        for leave in queryset.filter(status=LeaveRequest.LeaveStatus.PENDING):
            leave.reject(user=request.user, reason="Rejected via administrative review.")
            count += 1
        self.message_user(request, f"{count} leave requests rejected.", messages.WARNING)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("staff__user", "reviewed_by")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        if has_role(request.user, "Branch Manager"):
            branches = accessible_branches(request.user)
            return qs.filter(
                Q(staff__primary_branch__in=branches)
                | Q(staff__user__branch_access__branch__in=branches, staff__user__branch_access__is_active=True)
            ).distinct()
        if has_role(request.user, "Trainer"):
            return qs.filter(staff__user=request.user)
        return qs
