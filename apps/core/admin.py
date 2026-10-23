import logging
from django import forms
from django.conf import settings
from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import GymProfile, GymSettings, GymBranding, GymPolicy, FeatureFlag, BackupConfiguration
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
    list_display = ("name", "default_currency", "default_timezone", "tax_id", "default_language")
    search_fields = ("name", "legal_name", "tax_id", "commercial_registration_number")
    fieldsets = (
        ("Legal & Commercial Identity", {
            "fields": (
                "name",
                "legal_name",
                ("tax_id", "commercial_registration_number"),
                ("phone", "email"),
                "website",
                "address",
            ),
        }),
        ("Branding & Assets", {
            "fields": (
                ("logo", "favicon"),
                "default_language",
            ),
        }),
        ("Receipt & Invoice Customization", {
            "fields": (
                "receipt_header",
                "receipt_footer",
                "invoice_terms",
            ),
        }),
        ("Financial & Regional Defaults", {
            "fields": (
                ("default_currency", "default_timezone"),
                "fiscal_year_start_month",
            ),
        }),
        ("Member Registration Defaults", {
            "fields": (
                ("member_id_prefix", "member_id_sequence_length"),
                ("min_member_age", "require_emergency_contact", "require_member_photo"),
            ),
        }),
        ("Terms & Privacy Policy", {
            "classes": ("collapse",),
            "fields": ("terms_and_conditions", "privacy_policy"),
        }),
        ("Audit Metadata", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field in ("default_language", "fiscal_year_start_month", "member_id_prefix", "member_id_sequence_length", "min_member_age"):
            if field in form.base_fields:
                form.base_fields[field].required = False
        return form

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
        ("Billing & POS Policies", {
            "fields": (
                ("invoice_due_days", "invoice_prefix", "receipt_prefix"),
                ("cashier_shift_required", "pos_max_discount_percent"),
                ("stock_adjustment_approval_required", "require_refund_approval"),
            ),
        }),
        ("Notification & SMS Policies", {
            "fields": (
                ("sms_enabled", "email_enabled"),
                ("sms_quiet_hours_start", "sms_quiet_hours_end"),
                ("max_daily_sms_per_member", "membership_expiry_reminder_days"),
            ),
        }),
        ("Staff & Class Policies", {
            "fields": (
                "trainer_max_active_clients",
                ("class_booking_window_days", "class_cancellation_cutoff_hours"),
                ("class_waitlist_max_size", "staff_leave_manager_approval_required"),
            ),
        }),
        ("Facility & Locker Policies", {
            "fields": (
                ("default_locker_deposit_etb", "lost_key_penalty_etb"),
                ("facility_inspection_interval_days", "preventive_maintenance_lead_days"),
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
        ("Billing & POS Policies", {
            "fields": (
                ("invoice_due_days", "invoice_prefix", "receipt_prefix"),
                ("cashier_shift_required", "pos_max_discount_percent"),
                ("stock_adjustment_approval_required", "require_refund_approval"),
            ),
        }),
        ("Notification & SMS Policies", {
            "fields": (
                ("sms_enabled", "email_enabled"),
                ("sms_quiet_hours_start", "sms_quiet_hours_end"),
                ("max_daily_sms_per_member", "membership_expiry_reminder_days"),
            ),
        }),
        ("Staff & Class Policies", {
            "fields": (
                "trainer_max_active_clients",
                ("class_booking_window_days", "class_cancellation_cutoff_hours"),
                ("class_waitlist_max_size", "staff_leave_manager_approval_required"),
            ),
        }),
        ("Facility & Locker Policies", {
            "fields": (
                ("default_locker_deposit_etb", "lost_key_penalty_etb"),
                ("facility_inspection_interval_days", "preventive_maintenance_lead_days"),
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
    fields = (
        "name", "legal_name", "tax_id", "commercial_registration_number",
        "phone", "email", "website", "address", "logo", "favicon",
        "receipt_header", "receipt_footer", "invoice_terms", "default_language",
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for field in ("default_language", "tax_id", "commercial_registration_number", "website", "address", "phone", "email", "receipt_header", "receipt_footer", "invoice_terms", "logo", "favicon"):
            if field in form.base_fields:
                form.base_fields[field].required = False
        return form

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


@admin.register(FeatureFlag)
class FeatureFlagAdmin(ModelAdmin):
    list_display = ("code", "name", "category", "is_enabled", "branch", "rollout_percentage")
    list_filter = ("category", "is_enabled", "branch")
    search_fields = ("code", "name", "description")
    fieldsets = (
        ("Feature Identifier", {
            "fields": ("code", "name", "category", "description"),
        }),
        ("Activation & Rollout", {
            "fields": ("is_enabled", "rollout_percentage", "branch"),
        }),
        ("Dependencies & Advanced Configuration", {
            "fields": ("requires_features", "metadata"),
        }),
        ("Audit Metadata", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
        }),
    )
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")

    def has_module_permission(self, request):
        return request.user.is_active and (request.user.is_superuser or is_owner(request.user))

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        record_event(
            actor=request.user,
            action="feature_flag_updated",
            target=obj,
            branch=obj.branch,
            metadata={"feature_code": obj.code, "status": "ENABLED" if obj.is_enabled else "DISABLED"},
        )


@admin.register(BackupConfiguration)
class BackupConfigurationAdmin(ModelAdmin):
    list_display = ("__str__", "backup_directory", "retention_days", "compression_algorithm", "last_backup_status", "last_backup_at")
    fieldsets = (
        ("Storage Destination", {
            "fields": ("backup_directory", "retention_days", "compression_algorithm", "offsite_storage_reference"),
        }),
        ("Backup Status & Recovery History", {
            "fields": (
                "last_backup_status",
                "last_backup_at",
                "last_backup_file_name",
                "last_backup_file_size_bytes",
                "last_verified_at",
            ),
        }),
        ("Operational Notes", {
            "fields": ("notes",),
        }),
        ("Audit Metadata", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
        }),
    )
    readonly_fields = (
        "last_backup_status",
        "last_backup_at",
        "last_backup_file_name",
        "last_backup_file_size_bytes",
        "last_verified_at",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )

    def has_module_permission(self, request):
        return request.user.is_active and (request.user.is_superuser or is_owner(request.user))

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return request.user.is_superuser and not BackupConfiguration.objects.exists()

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False
