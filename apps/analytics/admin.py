from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from apps.accounts.roles import is_owner
from .models import ReportExportLog, ReportPreset


@admin.register(ReportExportLog)
class ReportExportLogAdmin(ModelAdmin):
    list_display = (
        "report_title",
        "export_format_badge",
        "actor_email",
        "branch",
        "row_count",
        "date_preset",
        "exported_at",
    )
    list_filter = ("export_format", "branch", "report_type")
    search_fields = ("report_title", "report_type", "actor__email")
    readonly_fields = (
        "id",
        "actor",
        "report_type",
        "report_title",
        "export_format",
        "branch",
        "date_preset",
        "start_date",
        "end_date",
        "row_count",
        "file_size_bytes",
        "ip_address",
        "exported_at",
    )

    @display(description="Format", label={"PDF": "danger", "XLSX": "success", "CSV": "info"})
    def export_format_badge(self, obj):
        return obj.export_format

    @display(description="Exported By")
    def actor_email(self, obj):
        return obj.actor.email if obj.actor else "System"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReportPreset)
class ReportPresetAdmin(ModelAdmin):
    list_display = ("name", "report_type", "created_by", "branch", "date_preset", "updated_at")
    list_filter = ("report_type", "branch")
    search_fields = ("name", "report_type", "created_by__email")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
