from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from unfold.admin import ModelAdmin

from apps.accounts.roles import is_owner, has_role
from .audit import record_event
from .models import AuditEvent, Branch, BranchSettings, UserBranchAccess
from .services import accessible_branches, can_access_branch, can_manage_branch


class BranchScopedAdmin(ModelAdmin):
    list_per_page = settings.ADMIN_PAGE_SIZE
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Branch)
class BranchAdmin(BranchScopedAdmin):
    list_display = ("code", "name", "city", "manager", "is_active", "updated_at")
    search_fields = ("code", "name", "city", "address")
    list_filter = ("is_active", "city")
    fieldsets = (
        ("Branch Identity / የቅርንጫፍ መለያ", {
            "classes": ("collapse",),
            "fields": (
                ("code", "name"),
                ("is_active", "opening_date"),
                "description",
            ),
        }),
        ("Contact & Location / አድራሻ እና ግንኙነት", {
            "classes": ("collapse",),
            "fields": (
                ("phone", "email"),
                ("city", "address"),
            ),
        }),
        ("Branch Leadership / አስተዳደር እና አመራር", {
            "classes": ("collapse",),
            "fields": ("manager",),
        }),
        ("Audit Metadata / ኦዲት እና ቁጥጥር", {
            "classes": ("collapse",),
            "fields": (
                ("created_at", "updated_at"),
                ("created_by", "updated_by"),
            ),
        }),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_owner(request.user):
            return queryset
        return queryset.filter(pk__in=accessible_branches(request.user).values("pk"))

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_staff and
            (is_owner(request.user) or accessible_branches(request.user).exists()))

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or
            is_owner(request.user) or can_access_branch(request.user, obj))

    def has_add_permission(self, request):
        return is_owner(request.user)

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return is_owner(request.user) or has_role(request.user, "Branch Manager")
        return can_manage_branch(request.user, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not is_owner(request.user):
            fields += ["code", "manager", "is_active", "opening_date"]
        return fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "manager":
            from apps.accounts.models import User
            kwargs["queryset"] = User.objects.filter(is_active=True, is_staff=True,
                groups__name="Branch Manager").distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not self.has_change_permission(request, obj) and change or not change and not self.has_add_permission(request):
            raise PermissionDenied
        before_active = None
        if change:
            before_active = Branch.objects.get(pk=obj.pk).is_active
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        action = "branch_created" if not change else "branch_updated"
        if change and before_active != obj.is_active:
            action = "branch_activated" if obj.is_active else "branch_deactivated"
        record_event(request.user, action, obj, {"branch_code": obj.code})


@admin.register(UserBranchAccess)
class UserBranchAccessAdmin(BranchScopedAdmin):
    list_display = ("user", "branch", "is_active", "is_primary", "assigned_at")
    search_fields = ("user__email", "branch__code", "branch__name")
    list_filter = ("is_active", "is_primary", "branch")
    readonly_fields = ("assigned_at", "assigned_by", "created_at", "updated_at")
    fieldsets = (
        ("Assignment / የተጠቃሚ የቅርንጫፍ ምደባ", {
            "classes": ("collapse",),
            "fields": (
                ("user", "branch"),
                ("is_active", "is_primary"),
            ),
        }),
        ("Audit Metadata / ኦዲት እና ቁጥጥር", {
            "classes": ("collapse",),
            "fields": (
                ("assigned_at", "assigned_by"),
                ("created_at", "updated_at"),
            ),
        }),
    )

    def has_module_permission(self, request):
        return is_owner(request.user)

    def has_view_permission(self, request, obj=None):
        return is_owner(request.user)

    def has_add_permission(self, request):
        return is_owner(request.user)

    def has_change_permission(self, request, obj=None):
        return is_owner(request.user)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields += ["user", "branch"]
        return fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user":
            from apps.accounts.models import User
            kwargs["queryset"] = User.objects.filter(is_active=True, is_staff=True,
                is_superuser=False, groups__name__in=["Gym Owner", "Branch Manager", "Receptionist",
                    "Accountant", "Trainer", "POS Staff"]).distinct()
        if db_field.name == "branch":
            kwargs["queryset"] = Branch.objects.filter(is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if not is_owner(request.user):
            raise PermissionDenied
        if obj.is_primary and obj.is_active:
            UserBranchAccess.objects.filter(user=obj.user, is_active=True, is_primary=True).exclude(pk=obj.pk).update(is_primary=False)
        if not change:
            obj.assigned_by = request.user
        previous = UserBranchAccess.objects.get(pk=obj.pk) if change else None
        super().save_model(request, obj, form, change)
        action = "branch_access_assigned" if not change else "branch_access_updated"
        if previous and previous.is_active and not obj.is_active:
            action = "branch_access_revoked"
        record_event(request.user, action, obj, {"branch_code": obj.branch.code})


@admin.register(BranchSettings)
class BranchSettingsAdmin(BranchScopedAdmin):
    list_display = ("branch", "opens_at", "closes_at", "attendance_qr_enabled", "attendance_manual_enabled")
    search_fields = ("branch__code", "branch__name")
    list_filter = ("attendance_qr_enabled", "attendance_manual_enabled")
    fieldsets = (
        ("Operating Schedule / የሥራ ሰዓት እና ቀናት", {
            "classes": ("collapse",),
            "fields": ("branch", "operating_days", ("opens_at", "closes_at")),
        }),
        ("Attendance & Entry Overrides / የመግቢያ እና ምዝገባ ደንቦች", {
            "classes": ("collapse",),
            "description": "Configure branch-specific access and verification rules. At least one attendance method must remain active.",
            "fields": (
                ("attendance_qr_enabled", "attendance_manual_enabled"),
                "allow_access_with_balance_override",
            ),
        }),
        ("POS, Retail & Inventory Overrides / የሽያጭ እና ክምችት ደንቦች", {
            "classes": ("collapse",),
            "fields": (
                ("max_discount_percent_override", "low_stock_threshold_override"),
            ),
        }),
        ("Facility & Operations Overrides / የተቋም እና አገልግሎት ደንቦች", {
            "classes": ("collapse",),
            "fields": (
                "locker_deposit_etb_override",
                "cleaning_inspection_frequency_hours",
                "sms_enabled_override",
            ),
        }),
        ("Receipt Customization", {
            "fields": (
                "receipt_header_override",
                "receipt_footer_override",
            ),
        }),
        ("Manager Notes", {
            "fields": ("manager_notes",),
        }),
        ("Audit Metadata", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
        }),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if is_owner(request.user):
            return queryset
        return queryset.filter(branch__in=accessible_branches(request.user))

    def has_module_permission(self, request):
        return is_owner(request.user) or has_role(request.user, "Branch Manager")

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or is_owner(request.user) or can_access_branch(request.user, obj.branch))

    def has_add_permission(self, request):
        return is_owner(request.user) or has_role(request.user, "Branch Manager")

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request) and (obj is None or can_manage_branch(request.user, obj.branch))

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.append("branch")
        return fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "branch":
            kwargs["queryset"] = accessible_branches(request.user) if not is_owner(request.user) else Branch.objects.all()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not can_manage_branch(request.user, obj.branch):
            raise PermissionDenied
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        record_event(request.user, "branch_settings_updated", obj, {"branch_code": obj.branch.code}, branch=obj.branch)


@admin.register(AuditEvent)
class AuditEventAdmin(ModelAdmin):
    list_display = ("occurred_at", "action", "actor", "branch", "target_type", "target_id")
    list_filter = ("action", "branch", "target_type")
    search_fields = ("action", "target_type", "target_id", "actor__email", "branch__name", "branch__code")
    readonly_fields = ("id", "occurred_at", "action", "actor", "branch", "target_type", "target_id", "metadata")
    def has_module_permission(self, request):
        return is_owner(request.user) or has_role(request.user, "Branch Manager")
    def has_view_permission(self, request, obj=None):
        return is_owner(request.user) or has_role(request.user, "Branch Manager")
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        return qs.filter(branch__in=accessible_branches(request.user))

