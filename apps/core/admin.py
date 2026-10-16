import logging
from django import forms
from django.conf import settings
from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import GymProfile, GymSettings, GymBranding, GymPolicy
from apps.accounts.roles import has_role, is_owner
from apps.branches.audit import record_event

logger = logging.getLogger("gym.admin")


class SuperuserOnlyMixin:
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser
    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)
    def has_add_permission(self, request):
        return self.has_module_permission(request)
    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)
    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)

class FoundationAdmin(SuperuserOnlyMixin, ModelAdmin):
    list_per_page = settings.ADMIN_PAGE_SIZE
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    def has_delete_permission(self, request, obj=None):
        return False
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        logger.info("admin_save actor=%s model=%s object=%s", request.user.pk, obj._meta.label, obj.pk)

@admin.register(GymProfile)
class GymProfileAdmin(FoundationAdmin):
    list_display = ("name", "default_currency", "default_timezone")
    def has_add_permission(self, request):
        return super().has_add_permission(request) and not GymProfile.objects.exists()


class GymSettingsAdminForm(forms.ModelForm):
    class Meta:
        model = GymSettings
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        qr = cleaned_data.get("attendance_qr_enabled")
        manual = cleaned_data.get("attendance_manual_enabled")
        if qr is False and manual is False:
            raise forms.ValidationError(
                "Both attendance methods cannot be disabled simultaneously. "
                "Enable at least one attendance method (QR Code or Name/Phone) to preserve gym operations."
            )
        return cleaned_data


@admin.register(GymSettings)
class GymSettingsAdmin(FoundationAdmin):
    form = GymSettingsAdminForm
    list_display = (
        "profile",
        "qr_attendance_status",
        "manual_attendance_status",
        "allow_access_with_balance",
        "membership_freeze_enabled",
    )
    fieldsets = (
        ("Facility Profile", {
            "fields": ("profile",),
        }),
        ("Attendance Configuration", {
            "description": "Configure authorized member entry verification methods. At least one method must remain active gym-wide.",
            "fields": (
                ("attendance_qr_enabled", "attendance_manual_enabled"),
            ),
        }),
        ("Membership & Branch Access Policies", {
            "fields": (
                "allow_access_with_balance",
                "cross_branch_access_mode",
                "membership_freeze_enabled",
                "require_freeze_approval",
                "early_renewal_mode",
            ),
        }),
        ("Audit Metadata", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
        }),
    )

    @display(description="QR Attendance", boolean=True)
    def qr_attendance_status(self, obj):
        return obj.attendance_qr_enabled

    @display(description="Name/Phone Attendance", boolean=True)
    def manual_attendance_status(self, obj):
        return obj.attendance_manual_enabled

    def has_add_permission(self, request):
        return super().has_add_permission(request) and GymProfile.objects.exists() and not GymSettings.objects.exists()


@admin.register(GymPolicy)
class GymPolicyAdmin(ModelAdmin):
    form = GymSettingsAdminForm
    list_display = (
        "profile",
        "qr_attendance_status",
        "manual_attendance_status",
        "allow_access_with_balance",
        "membership_freeze_enabled",
    )
    fieldsets = (
        ("Facility Profile", {
            "fields": ("profile",),
        }),
        ("Attendance Configuration", {
            "description": "Configure authorized member entry verification methods. At least one method must remain active gym-wide.",
            "fields": (
                ("attendance_qr_enabled", "attendance_manual_enabled"),
            ),
        }),
        ("Membership & Branch Access Policies", {
            "fields": (
                "allow_access_with_balance",
                "cross_branch_access_mode",
                "membership_freeze_enabled",
                "require_freeze_approval",
                "early_renewal_mode",
            ),
        }),
        ("Audit Metadata", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
        }),
    )
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")

    @display(description="QR Attendance", boolean=True)
    def qr_attendance_status(self, obj):
        return obj.attendance_qr_enabled

    @display(description="Name/Phone Attendance", boolean=True)
    def manual_attendance_status(self, obj):
        return obj.attendance_manual_enabled

    def has_module_permission(self, request):
        return request.user.is_authenticated and (
            request.user.is_superuser
            or is_owner(request.user)
            or has_role(request.user, "Branch Manager")
            or has_role(request.user, "Receptionist")
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_authenticated and (
            request.user.is_superuser or is_owner(request.user)
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        record_event(request.user, "gym_policy_updated", obj)


@admin.register(GymBranding)
class GymBrandingAdmin(ModelAdmin):
    list_display = ("name", "phone", "email", "updated_at")
    fields = ("name", "legal_name", "phone", "email", "address", "logo")

    def has_module_permission(self, request):
        return is_owner(request.user)

    def has_view_permission(self, request, obj=None):
        return is_owner(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return is_owner(request.user)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        record_event(request.user, "gym_branding_updated", obj)
