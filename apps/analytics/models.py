import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.branches.models import Branch


class ReportExportLog(models.Model):
    """
    Audit log for sensitive analytical and financial report exports.
    Tracks user identity, timestamp, scope, and parameters for every export.
    """
    class ExportFormat(models.TextChoices):
        PDF = "PDF", "PDF Document"
        XLSX = "XLSX", "Excel Spreadsheet"
        CSV = "CSV", "CSV File"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_export_logs",
    )
    report_type = models.CharField(max_length=60, db_index=True)
    report_title = models.CharField(max_length=150, blank=True)
    export_format = models.CharField(max_length=10, choices=ExportFormat.choices)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_exports",
    )
    date_preset = models.CharField(max_length=40, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    file_size_bytes = models.PositiveIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    exported_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-exported_at",)
        verbose_name = "Report Export Log"
        verbose_name_plural = "Report Export Logs"

    def __str__(self):
        user_str = self.actor.email if self.actor else "System"
        return f"{self.report_type} ({self.export_format}) by {user_str} at {self.exported_at:%Y-%m-%d %H:%M}"


class ReportPreset(models.Model):
    """
    Saved custom report preferences and filter combinations.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    report_type = models.CharField(max_length=60, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_report_presets",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_presets",
    )
    date_preset = models.CharField(max_length=40, default="this_month")
    custom_start_date = models.DateField(null=True, blank=True)
    custom_end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Report Preset"
        verbose_name_plural = "Report Presets"

    def __str__(self):
        return f"{self.name} ({self.report_type})"
