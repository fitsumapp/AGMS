from decimal import Decimal
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from apps.accounts.roles import is_owner, has_role
from apps.facilities.models import (
    LockerZone,
    Locker,
    LockerAssignment,
    LockerIncident,
    EquipmentCategory,
    FacilityArea,
    Equipment,
    MaintenanceSchedule,
    FaultReport,
    MaintenanceWorkOrder,
    CleaningTaskSchedule,
    CleaningTaskExecution,
    FacilityInspection,
    IncidentReport,
)
from apps.facilities.permissions import (
    get_user_facility_branches,
    can_manage_facility,
    can_assign_locker,
    can_manage_maintenance,
)
from apps.facilities.services import (
    release_locker,
    update_work_order_status,
    post_work_order_expense_to_finance,
)


class BranchScopedModelAdmin(ModelAdmin):
    """Base Unfold admin ensuring strict branch scoping on querysets and forms."""
    branch_field = "branch"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if is_owner(request.user):
            return qs
        allowed_branches = get_user_facility_branches(request.user)
        return qs.filter(**{f"{self.branch_field}__in": allowed_branches})

    def formfield_for_foreign_key(self, db_field, request, **kwargs):
        if not is_owner(request.user):
            allowed_branches = get_user_facility_branches(request.user)
            if db_field.name == "branch":
                kwargs["queryset"] = allowed_branches
            elif db_field.name in ("zone", "locker"):
                kwargs["queryset"] = db_field.related_model.objects.filter(branch__in=allowed_branches)
            elif db_field.name in ("facility_area", "equipment"):
                kwargs["queryset"] = db_field.related_model.objects.filter(branch__in=allowed_branches)
        return super().formfield_for_foreign_key(db_field, request, **kwargs)


class LockerInline(TabularInline):
    model = Locker
    extra = 0
    fields = ("locker_number", "locker_type", "status", "lock_type", "daily_fee_etb", "monthly_fee_etb", "is_active")
    readonly_fields = ("status",)


class LockerIncidentInline(TabularInline):
    model = LockerIncident
    extra = 0
    fields = ("incident_number", "incident_type", "penalty_fee_etb", "is_settled", "incident_date")
    readonly_fields = ("incident_number",)


@admin.register(LockerZone)
class LockerZoneAdmin(BranchScopedModelAdmin):
    list_display = ("name", "code", "branch", "gender_restriction", "lockers_count", "is_active")
    list_filter = ("branch", "gender_restriction", "is_active")
    search_fields = ("name", "code", "branch__name")
    inlines = [LockerInline]

    @display(description=_("Lockers"))
    def lockers_count(self, obj):
        return obj.lockers.count()


@admin.register(Locker)
class LockerAdmin(BranchScopedModelAdmin):
    list_display = (
        "locker_number",
        "zone",
        "branch",
        "locker_type",
        "lock_type",
        "status_badge",
        "daily_fee_etb",
        "monthly_fee_etb",
        "deposit_amount_etb",
        "is_active",
    )
    list_filter = ("branch", "zone", "locker_type", "lock_type", "status", "is_active")
    search_fields = ("locker_number", "zone__name", "branch__name", "key_card_code")
    readonly_fields = ("created_at", "updated_at")

    @display(description=_("Status"))
    def status_badge(self, obj):
        colors = {
            Locker.LockerStatus.AVAILABLE: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
            Locker.LockerStatus.OCCUPIED: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
            Locker.LockerStatus.RESERVED: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
            Locker.LockerStatus.OUT_OF_SERVICE: "bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-400",
            Locker.LockerStatus.MAINTENANCE: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())


@admin.register(LockerAssignment)
class LockerAssignmentAdmin(BranchScopedModelAdmin):
    list_display = (
        "assignment_number",
        "locker",
        "member",
        "branch",
        "assignment_type",
        "status_badge",
        "start_date",
        "end_date",
        "key_issued",
        "key_returned",
        "deposit_charged_etb",
        "deposit_refunded_etb",
    )
    list_filter = ("branch", "assignment_type", "status", "key_issued", "key_returned")
    search_fields = ("assignment_number", "locker__locker_number", "member__first_name", "member__last_name", "member__member_number")
    readonly_fields = ("assignment_number", "returned_date", "created_at", "updated_at")
    inlines = [LockerIncidentInline]
    actions = ["action_release_lockers"]

    @display(description=_("Status"))
    def status_badge(self, obj):
        colors = {
            LockerAssignment.Status.ACTIVE: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
            LockerAssignment.Status.COMPLETED: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
            LockerAssignment.Status.OVERDUE: "bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-400",
            LockerAssignment.Status.CANCELLED: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-400",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())

    @action(description=_("Release selected active locker assignments"))
    def action_release_lockers(self, request, queryset):
        released_count = 0
        for assignment in queryset.filter(status=LockerAssignment.Status.ACTIVE):
            if can_assign_locker(request.user, assignment.branch):
                release_locker(assignment=assignment, user=request.user, key_returned=True, refund_deposit=True)
                released_count += 1
            else:
                self.message_user(request, f"Permission denied for branch {assignment.branch.name}.", messages.ERROR)
        if released_count:
            self.message_user(request, f"Successfully released {released_count} locker assignment(s).", messages.SUCCESS)


@admin.register(LockerIncident)
class LockerIncidentAdmin(BranchScopedModelAdmin):
    list_display = (
        "incident_number",
        "locker",
        "assignment",
        "branch",
        "incident_type",
        "penalty_fee_etb",
        "is_settled",
        "incident_date",
    )
    list_filter = ("branch", "incident_type", "is_settled")
    search_fields = ("incident_number", "locker__locker_number", "notes")
    readonly_fields = ("incident_number", "created_at")


@admin.register(EquipmentCategory)
class EquipmentCategoryAdmin(ModelAdmin):
    list_display = ("name", "code", "default_maintenance_interval_days", "equipment_count", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")

    @display(description=_("Assets Registered"))
    def equipment_count(self, obj):
        return obj.equipment.count()


@admin.register(FacilityArea)
class FacilityAreaAdmin(BranchScopedModelAdmin):
    list_display = ("name", "code", "branch", "area_type", "condition", "capacity", "is_active")
    list_filter = ("branch", "area_type", "condition", "is_active")
    search_fields = ("name", "code", "branch__name")


@admin.register(Equipment)
class EquipmentAdmin(BranchScopedModelAdmin):
    list_display = (
        "asset_code",
        "name",
        "category",
        "branch",
        "facility_area",
        "status_badge",
        "condition",
        "manufacturer",
        "purchase_cost_etb",
        "warranty_expiry_date",
        "total_downtime_hours",
    )
    list_filter = ("branch", "category", "status", "condition", "facility_area")
    search_fields = ("asset_code", "name", "manufacturer", "model_number", "serial_number")
    readonly_fields = ("total_downtime_hours", "created_at", "updated_at")

    @display(description=_("Status"))
    def status_badge(self, obj):
        colors = {
            Equipment.OperationalStatus.OPERATIONAL: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
            Equipment.OperationalStatus.UNDER_MAINTENANCE: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
            Equipment.OperationalStatus.OUT_OF_SERVICE: "bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-400",
            Equipment.OperationalStatus.RETIRED: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-400",
            Equipment.OperationalStatus.DISPOSED: "bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-400",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())


@admin.register(MaintenanceSchedule)
class MaintenanceScheduleAdmin(BranchScopedModelAdmin):
    list_display = (
        "title",
        "branch",
        "equipment",
        "category",
        "frequency",
        "assigned_technician",
        "next_run_date",
        "is_active",
    )
    list_filter = ("branch", "frequency", "is_active")
    search_fields = ("title", "equipment__asset_code", "equipment__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(FaultReport)
class FaultReportAdmin(BranchScopedModelAdmin):
    list_display = (
        "report_number",
        "title",
        "branch",
        "equipment",
        "facility_area",
        "priority",
        "status_badge",
        "reported_by",
        "reported_at",
    )
    list_filter = ("branch", "priority", "status", "reported_at")
    search_fields = ("report_number", "title", "description", "equipment__asset_code")
    readonly_fields = ("report_number", "created_at", "updated_at")

    @display(description=_("Status"))
    def status_badge(self, obj):
        colors = {
            FaultReport.Status.REPORTED: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
            FaultReport.Status.ASSIGNED: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
            FaultReport.Status.IN_PROGRESS: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
            FaultReport.Status.RESOLVED: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
            FaultReport.Status.CLOSED: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
            FaultReport.Status.CANCELLED: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-400",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())


@admin.register(MaintenanceWorkOrder)
class MaintenanceWorkOrderAdmin(BranchScopedModelAdmin):
    list_display = (
        "work_order_number",
        "title",
        "branch",
        "equipment",
        "order_type",
        "priority",
        "status_badge",
        "assigned_technician",
        "total_cost_etb",
        "downtime_hours",
        "is_reopened",
    )
    list_filter = ("branch", "order_type", "priority", "status", "is_reopened")
    search_fields = ("work_order_number", "title", "equipment__asset_code", "equipment__name")
    readonly_fields = ("work_order_number", "reported_at", "started_at", "resolved_at", "closed_at", "created_at", "updated_at")
    actions = ["action_post_expense_to_finance"]

    @display(description=_("Status"))
    def status_badge(self, obj):
        colors = {
            MaintenanceWorkOrder.Status.REPORTED: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
            MaintenanceWorkOrder.Status.ASSIGNED: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
            MaintenanceWorkOrder.Status.IN_PROGRESS: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
            MaintenanceWorkOrder.Status.RESOLVED: "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-400",
            MaintenanceWorkOrder.Status.CLOSED: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
            MaintenanceWorkOrder.Status.CANCELLED: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-400",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())

    @action(description=_("Post resolved work order expense to Phase 10 Finance"))
    def action_post_expense_to_finance(self, request, queryset):
        posted = 0
        for wo in queryset:
            try:
                expense = post_work_order_expense_to_finance(wo, request.user)
                if expense:
                    posted += 1
            except Exception as e:
                self.message_user(request, f"Error on {wo.work_order_number}: {e}", messages.ERROR)
        if posted:
            self.message_user(request, f"Successfully posted {posted} maintenance expense(s) to Finance.", messages.SUCCESS)


@admin.register(CleaningTaskSchedule)
class CleaningTaskScheduleAdmin(BranchScopedModelAdmin):
    list_display = ("task_name", "facility_area", "branch", "frequency", "assigned_staff", "is_active")
    list_filter = ("branch", "frequency", "is_active", "facility_area")
    search_fields = ("task_name", "facility_area__name")


@admin.register(CleaningTaskExecution)
class CleaningTaskExecutionAdmin(BranchScopedModelAdmin):
    list_display = (
        "task_name",
        "facility_area",
        "branch",
        "assigned_staff",
        "scheduled_date",
        "status_badge",
        "completed_at",
        "verified_by",
    )
    list_filter = ("branch", "status", "scheduled_date", "facility_area")
    search_fields = ("task_name", "facility_area__name", "assigned_staff__username")
    readonly_fields = ("completed_at", "verified_at")

    @display(description=_("Status"))
    def status_badge(self, obj):
        colors = {
            CleaningTaskExecution.Status.PENDING: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
            CleaningTaskExecution.Status.IN_PROGRESS: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
            CleaningTaskExecution.Status.COMPLETED: "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-400",
            CleaningTaskExecution.Status.MISSED: "bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-400",
            CleaningTaskExecution.Status.CANCELLED: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-400",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold {}">{}</span>', cls, obj.get_status_display())


@admin.register(FacilityInspection)
class FacilityInspectionAdmin(BranchScopedModelAdmin):
    list_display = (
        "inspection_number",
        "facility_area",
        "branch",
        "inspector",
        "inspection_date",
        "score_percent",
        "passed_badge",
        "escalation_work_order",
    )
    list_filter = ("branch", "passed", "inspection_date", "facility_area")
    search_fields = ("inspection_number", "facility_area__name", "deficiencies_found")
    readonly_fields = ("inspection_number", "created_at")

    @display(description=_("Result"))
    def passed_badge(self, obj):
        if obj.passed:
            return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400">PASSED</span>')
        return format_html('<span class="px-2 py-0.5 rounded text-xs font-semibold bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-400">FAILED</span>')


@admin.register(IncidentReport)
class IncidentReportAdmin(BranchScopedModelAdmin):
    list_display = (
        "incident_number",
        "incident_type",
        "branch",
        "facility_area",
        "severity",
        "status",
        "reported_by",
        "reported_at",
        "police_or_medical_involved",
    )
    list_filter = ("branch", "severity", "status", "police_or_medical_involved", "reported_at")
    search_fields = ("incident_number", "description", "facility_area__name")
    readonly_fields = ("incident_number", "created_at", "updated_at")
