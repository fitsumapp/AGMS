import csv
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.roles import is_owner, has_role
from apps.branches.models import Branch, AuditEvent
from apps.branches.services import accessible_branches, active_branch
from .models import GymProfile, GymSettings, FeatureFlag, BackupConfiguration
from .services import get_system_health_metrics, get_backup_readiness_report
from .importers import (
    import_members,
    import_products,
    import_equipment,
    export_branch_data,
    sanitize_csv_cell,
)

logger = logging.getLogger("gym.operations")


def logo(request):
    """Serve gym logo securely."""
    profile = GymProfile.objects.first()
    if not profile or not profile.logo:
        raise Http404
    try:
        response = FileResponse(profile.logo.open("rb"))
    except FileNotFoundError:
        raise Http404
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def can_manage_operations(user) -> bool:
    return user.is_authenticated and (user.is_superuser or is_owner(user) or has_role(user, "Branch Manager"))


def can_manage_technical(user) -> bool:
    return user.is_authenticated and (user.is_superuser or is_owner(user))


# ==============================================================================
# 1. Operational System Health View
# ==============================================================================

@login_required
def system_health_view(request):
    if not can_manage_operations(request.user):
        raise PermissionDenied(_("You do not have permission to view the Operational Health Dashboard."))

    metrics = get_system_health_metrics()
    profile = GymProfile.objects.first()

    context = {
        "title": _("System Operations & Health"),
        "metrics": metrics,
        "profile": profile,
        "is_owner": is_owner(request.user) or request.user.is_superuser,
    }
    return render(request, "admin/operations/system_health.html", context)


# ==============================================================================
# 2. Backup Readiness & Disaster Recovery View
# ==============================================================================

@login_required
def backup_readiness_view(request):
    if not can_manage_technical(request.user):
        raise PermissionDenied(_("Only Gym Owners or Technical Administrators may review backup readiness."))

    report = get_backup_readiness_report()

    context = {
        "title": _("PostgreSQL Backup Readiness & Disaster Recovery"),
        "report": report,
        "config": report["config"],
    }
    return render(request, "admin/operations/backup_readiness.html", context)


# ==============================================================================
# 3. Data Management & Bulk Import View
# ==============================================================================

@login_required
def data_management_view(request):
    if not can_manage_operations(request.user):
        raise PermissionDenied(_("You do not have permission to access data management."))

    branches = list(accessible_branches(request.user))
    if not branches and not request.user.is_superuser and not is_owner(request.user):
        raise PermissionDenied(_("No assigned branches available for import/export."))

    selected_branch = active_branch(request) or (branches[0] if branches else Branch.objects.first())
    import_result = None

    if request.method == "POST":
        import_type = request.POST.get("import_type")
        dry_run = request.POST.get("dry_run") == "true"
        csv_file = request.FILES.get("csv_file")
        branch_id = request.POST.get("branch_id")

        target_branch = selected_branch
        if branch_id:
            candidate = Branch.objects.filter(pk=branch_id).first()
            if candidate and (is_owner(request.user) or request.user.is_superuser or candidate in branches):
                target_branch = candidate

        if not csv_file:
            messages.error(request, _("Please select a valid CSV file to upload."))
        else:
            try:
                if import_type == "members":
                    import_result = import_members(actor=request.user, branch=target_branch, file_obj=csv_file, dry_run=dry_run)
                elif import_type == "products":
                    import_result = import_products(actor=request.user, branch=target_branch, file_obj=csv_file, dry_run=dry_run)
                elif import_type == "equipment":
                    import_result = import_equipment(actor=request.user, branch=target_branch, file_obj=csv_file, dry_run=dry_run)
                else:
                    messages.error(request, _("Unsupported import entity type selected."))

                if import_result:
                    if dry_run:
                        messages.info(
                            request,
                            _("Dry-run preview completed: %d valid rows, %d duplicates, %d validation errors.")
                            % (import_result["valid_count"], len(import_result["duplicates"]), len(import_result["errors"])),
                        )
                    elif import_result["created_count"] > 0:
                        messages.success(
                            request,
                            _("Successfully imported %d %s into branch %s.")
                            % (import_result["created_count"], import_type, target_branch.name),
                        )
                    else:
                        messages.warning(request, _("No records were imported due to validation errors or duplicates."))

            except ValidationError as e:
                messages.error(request, str(e))
            except Exception as e:
                logger.error("Import failure: %s", e, exc_info=True)
                messages.error(request, _("Failed to process file: %s") % str(e))

    context = {
        "title": _("Data Management, Import & Export"),
        "branches": branches if (not is_owner(request.user) and not request.user.is_superuser) else Branch.objects.all(),
        "selected_branch": selected_branch,
        "import_result": import_result,
    }
    return render(request, "admin/operations/data_management.html", context)


# ==============================================================================
# 4. Data Export View
# ==============================================================================

@login_required
def data_export_view(request):
    if not can_manage_operations(request.user):
        raise PermissionDenied(_("You do not have permission to export branch data."))

    data_type = request.GET.get("type", "members")
    branch_id = request.GET.get("branch")

    branches = list(accessible_branches(request.user))
    target_branch = active_branch(request)
    if branch_id:
        target_branch = Branch.objects.filter(pk=branch_id).first()

    if not target_branch:
        target_branch = branches[0] if branches else Branch.objects.first()

    if not target_branch:
        raise Http404(_("No branch available for export."))

    # Authorization verification
    if not is_owner(request.user) and not request.user.is_superuser:
        if target_branch not in branches:
            raise PermissionDenied(_("You do not have access to export data from branch %s.") % target_branch.name)

    return export_branch_data(actor=request.user, branch=target_branch, data_type=data_type)


# ==============================================================================
# 5. Sample CSV Template Download View
# ==============================================================================

@login_required
def sample_csv_download_view(request, data_type: str):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="sample_{data_type}_template.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)

    if data_type == "members":
        writer.writerow(["first_name", "last_name", "phone", "email", "gender", "date_of_birth"])
        writer.writerow(["Abebe", "Bikila", "+251911223344", "abebe@example.com", "MALE", "1990-05-15"])
        writer.writerow(["Derartu", "Tulu", "+251922334455", "derartu@example.com", "FEMALE", "1992-03-21"])
    elif data_type == "products":
        writer.writerow(["name", "sku", "category_code", "unit_price_etb", "initial_stock"])
        writer.writerow(["Whey Protein 1kg", "WHEY-01", "SUPP", "2500.00", "50"])
        writer.writerow(["Electrolyte Water", "BEV-01", "DRINK", "60.00", "120"])
    elif data_type == "equipment":
        writer.writerow(["asset_code", "name", "category_code", "area_code", "manufacturer", "model_number"])
        writer.writerow(["TRD-101", "Technogym Run Live 1000", "CARDIO", "CARDIO-FL", "Technogym", "Excite 1000"])
        writer.writerow(["BENCH-01", "Olympic Flat Bench Press", "STRENGTH", "FREE-WT", "Life Fitness", "Hammer Pro"])
    else:
        raise Http404

    return response


# ==============================================================================
# 6. Operational Reports & Audit Trail View
# ==============================================================================

@login_required
def operational_reports_view(request):
    if not can_manage_operations(request.user):
        raise PermissionDenied(_("You do not have permission to view operational reports."))

    branches = list(accessible_branches(request.user)) if not (is_owner(request.user) or request.user.is_superuser) else list(Branch.objects.all())
    selected_b = active_branch(request) or (branches[0] if branches else None)

    branch_id = request.GET.get("branch")
    if branch_id and branch_id != "all":
        candidate = Branch.objects.filter(pk=branch_id).first()
        if candidate:
            selected_b = candidate
    elif branch_id == "all":
        selected_b = None

    # Filter audit logs
    audit_qs = AuditEvent.objects.all().select_related("actor", "branch").order_by("-occurred_at")
    if selected_b:
        audit_qs = audit_qs.filter(branch=selected_b)

    action_filter = request.GET.get("action")
    if action_filter:
        audit_qs = audit_qs.filter(action__icontains=action_filter)

    recent_audits = audit_qs[:100]

    context = {
        "title": _("Operational Reports & Audit Trail"),
        "branches": branches,
        "selected_branch": selected_b,
        "recent_audits": recent_audits,
        "action_filter": action_filter or "",
    }
    return render(request, "admin/operations/operational_reports.html", context)
