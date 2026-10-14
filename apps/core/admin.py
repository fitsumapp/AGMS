import logging
from django.conf import settings
from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import GymProfile, GymSettings

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

@admin.register(GymSettings)
class GymSettingsAdmin(FoundationAdmin):
    list_display = ("profile", "allow_access_with_balance", "membership_freeze_enabled")
    def has_add_permission(self, request):
        return super().has_add_permission(request) and GymProfile.objects.exists() and not GymSettings.objects.exists()
