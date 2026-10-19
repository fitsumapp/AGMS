import csv
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.roles import is_owner
from apps.facilities.models import (
    Locker,
    LockerZone,
    LockerAssignment,
    Equipment,
    MaintenanceWorkOrder,
    CleaningTaskExecution,
    FacilityInspection,
)
from apps.facilities.permissions import (
    can_view_facility,
    can_manage_facility,
    get_user_facility_branches,
)
from apps.facilities.selectors import (
    get_facility_dashboard_metrics,
    get_locker_utilization_by_zone,
)


def sanitize_csv_cell(value) -> str:
    """Neutralize formula injection in CSV cells."""
    val_str = str(value or "")
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str


@login_required
def facility_dashboard_view(request):
    """Executive & Branch Facility & Equipment Dashboard."""
    if not can_view_facility(request.user):
        raise PermissionDenied("You do not have permission to view the facility dashboard.")

    allowed_branches = get_user_facility_branches(request.user)
    branch_id = request.GET.get("branch")
    selected_branch = None
    branch_ids = None

    if branch_id and branch_id != "all":
        selected_branch = allowed_branches.filter(id=branch_id).first()
        if selected_branch:
            branch_ids = [selected_branch.id]
        else:
            branch_ids = list(allowed_branches.values_list("id", flat=True))
    else:
        if not is_owner(request.user):
            branch_ids = list(allowed_branches.values_list("id", flat=True))

    metrics = get_facility_dashboard_metrics(branch_ids=branch_ids)
    zone_utilization = get_locker_utilization_by_zone(branch_ids=branch_ids)

    # Recent active work orders
    recent_work_orders = MaintenanceWorkOrder.objects.filter(
        status__in=[
            MaintenanceWorkOrder.Status.REPORTED,
            MaintenanceWorkOrder.Status.ASSIGNED,
            MaintenanceWorkOrder.Status.IN_PROGRESS,
        ]
    )
    if branch_ids is not None:
        recent_work_orders = recent_work_orders.filter(branch_id__in=branch_ids)
    recent_work_orders = recent_work_orders.select_related("equipment", "facility_area", "branch").order_by("-reported_at")[:8]

    # Recent inspections
    recent_inspections = FacilityInspection.objects.all()
    if branch_ids is not None:
        recent_inspections = recent_inspections.filter(branch_id__in=branch_ids)
    recent_inspections = recent_inspections.select_related("facility_area", "inspector", "branch").order_by("-inspection_date")[:8]

    context = {
        "title": "Facility & Equipment Dashboard",
        "metrics": metrics,
        "zone_utilization": zone_utilization,
        "recent_work_orders": recent_work_orders,
        "recent_inspections": recent_inspections,
        "branches": allowed_branches,
        "selected_branch": selected_branch,
        "can_manage": can_manage_facility(request.user),
    }
    return render(request, "admin/facilities/dashboard.html", context)


@login_required
def facility_reports_view(request):
    """Facility, Locker, Equipment & Maintenance Analytics & CSV Reports."""
    if not can_view_facility(request.user):
        raise PermissionDenied("You do not have permission to view facility reports.")

    allowed_branches = get_user_facility_branches(request.user)
    branch_id = request.GET.get("branch")
    selected_branch = None
    branch_ids = None

    if branch_id and branch_id != "all":
        selected_branch = allowed_branches.filter(id=branch_id).first()
        if selected_branch:
            branch_ids = [selected_branch.id]
        else:
            branch_ids = list(allowed_branches.values_list("id", flat=True))
    else:
        if not is_owner(request.user):
            branch_ids = list(allowed_branches.values_list("id", flat=True))

    export_type = request.GET.get("export")
    if export_type:
        today_str = timezone.localdate().isoformat()
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        writer = csv.writer(response)

        if export_type == "lockers":
            response["Content-Disposition"] = f'attachment; filename="lockers_inventory_{today_str}.csv"'
            writer.writerow(["Branch", "Zone", "Locker Number", "Type", "Status", "Daily Fee (ETB)", "Monthly Fee (ETB)", "Deposit (ETB)"])
            qs = Locker.objects.select_related("branch", "zone").all()
            if branch_ids is not None:
                qs = qs.filter(branch_id__in=branch_ids)
            for item in qs.order_by("branch__name", "locker_number"):
                writer.writerow([
                    sanitize_csv_cell(item.branch.name),
                    sanitize_csv_cell(item.zone.name),
                    sanitize_csv_cell(item.locker_number),
                    sanitize_csv_cell(item.get_locker_type_display()),
                    sanitize_csv_cell(item.get_status_display()),
                    sanitize_csv_cell(item.daily_fee_etb),
                    sanitize_csv_cell(item.monthly_fee_etb),
                    sanitize_csv_cell(item.deposit_amount_etb),
                ])
            return response

        elif export_type == "equipment":
            response["Content-Disposition"] = f'attachment; filename="equipment_registry_{today_str}.csv"'
            writer.writerow(["Branch", "Asset Code", "Name", "Category", "Location", "Status", "Condition", "Cost (ETB)", "Downtime (Hours)"])
            qs = Equipment.objects.select_related("branch", "category", "facility_area").all()
            if branch_ids is not None:
                qs = qs.filter(branch_id__in=branch_ids)
            for item in qs.order_by("branch__name", "asset_code"):
                writer.writerow([
                    sanitize_csv_cell(item.branch.name),
                    sanitize_csv_cell(item.asset_code),
                    sanitize_csv_cell(item.name),
                    sanitize_csv_cell(item.category.name if item.category else "-"),
                    sanitize_csv_cell(item.facility_area.name if item.facility_area else "-"),
                    sanitize_csv_cell(item.get_status_display()),
                    sanitize_csv_cell(item.get_condition_display()),
                    sanitize_csv_cell(item.purchase_cost_etb),
                    sanitize_csv_cell(item.total_downtime_hours),
                ])
            return response

        elif export_type == "maintenance":
            response["Content-Disposition"] = f'attachment; filename="maintenance_work_orders_{today_str}.csv"'
            writer.writerow(["Work Order #", "Branch", "Equipment", "Order Type", "Priority", "Status", "Total Cost (ETB)", "Downtime (Hours)", "Reported At"])
            qs = MaintenanceWorkOrder.objects.select_related("branch", "equipment").all()
            if branch_ids is not None:
                qs = qs.filter(branch_id__in=branch_ids)
            for item in qs.order_by("-reported_at"):
                writer.writerow([
                    sanitize_csv_cell(item.work_order_number),
                    sanitize_csv_cell(item.branch.name),
                    sanitize_csv_cell(item.equipment.asset_code if item.equipment else "-"),
                    sanitize_csv_cell(item.get_order_type_display()),
                    sanitize_csv_cell(item.get_priority_display()),
                    sanitize_csv_cell(item.get_status_display()),
                    sanitize_csv_cell(item.total_cost_etb),
                    sanitize_csv_cell(item.downtime_hours),
                    sanitize_csv_cell(item.reported_at.strftime("%Y-%m-%d %H:%M") if item.reported_at else "-"),
                ])
            return response

    metrics = get_facility_dashboard_metrics(branch_ids=branch_ids)
    zone_utilization = get_locker_utilization_by_zone(branch_ids=branch_ids)

    context = {
        "title": "Facility & Equipment Reports",
        "metrics": metrics,
        "zone_utilization": zone_utilization,
        "branches": allowed_branches,
        "selected_branch": selected_branch,
    }
    return render(request, "admin/facilities/reports.html", context)
