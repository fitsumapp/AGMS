from decimal import Decimal
from django.db.models import Count, Sum, Q, Avg
from django.utils import timezone
from datetime import timedelta

from apps.facilities.models import (
    LockerZone,
    Locker,
    LockerAssignment,
    Equipment,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    FacilityArea,
    CleaningTaskExecution,
    FacilityInspection,
    IncidentReport,
)


def get_facility_dashboard_metrics(branch_ids=None) -> dict:
    """Aggregate KPIs for facility dashboard scoped by branch access."""
    locker_qs = Locker.objects.all()
    asg_qs = LockerAssignment.objects.all()
    eq_qs = Equipment.objects.all()
    wo_qs = MaintenanceWorkOrder.objects.all()
    clean_qs = CleaningTaskExecution.objects.all()
    insp_qs = FacilityInspection.objects.all()
    inc_qs = IncidentReport.objects.all()

    if branch_ids is not None:
        locker_qs = locker_qs.filter(branch_id__in=branch_ids)
        asg_qs = asg_qs.filter(branch_id__in=branch_ids)
        eq_qs = eq_qs.filter(branch_id__in=branch_ids)
        wo_qs = wo_qs.filter(branch_id__in=branch_ids)
        clean_qs = clean_qs.filter(branch_id__in=branch_ids)
        insp_qs = insp_qs.filter(branch_id__in=branch_ids)
        inc_qs = inc_qs.filter(branch_id__in=branch_ids)

    total_lockers = locker_qs.count()
    available_lockers = locker_qs.filter(status=Locker.LockerStatus.AVAILABLE).count()
    occupied_lockers = locker_qs.filter(status=Locker.LockerStatus.OCCUPIED).count()
    maintenance_lockers = locker_qs.filter(status__in=[Locker.LockerStatus.OUT_OF_SERVICE, Locker.LockerStatus.MAINTENANCE]).count()
    
    utilization_rate = round((occupied_lockers / total_lockers * 100), 1) if total_lockers > 0 else 0.0

    today = timezone.localdate()
    overdue_assignments = asg_qs.filter(
        status=LockerAssignment.Status.ACTIVE,
        end_date__lt=today,
    ).count()

    # Equipment stats
    total_equipment = eq_qs.count()
    operational_equipment = eq_qs.filter(status=Equipment.OperationalStatus.OPERATIONAL).count()
    maintenance_equipment = eq_qs.filter(status=Equipment.OperationalStatus.UNDER_MAINTENANCE).count()
    out_of_service_equipment = eq_qs.filter(status=Equipment.OperationalStatus.OUT_OF_SERVICE).count()
    total_downtime = eq_qs.aggregate(total=Sum("total_downtime_hours"))["total"] or Decimal("0.00")

    # Work orders & maintenance
    active_work_orders = wo_qs.filter(status__in=[
        MaintenanceWorkOrder.Status.REPORTED,
        MaintenanceWorkOrder.Status.ASSIGNED,
        MaintenanceWorkOrder.Status.IN_PROGRESS,
    ]).count()

    overdue_work_orders = wo_qs.filter(
        status__in=[
            MaintenanceWorkOrder.Status.REPORTED,
            MaintenanceWorkOrder.Status.ASSIGNED,
            MaintenanceWorkOrder.Status.IN_PROGRESS,
        ],
        reported_at__date__lt=today - timedelta(days=3),
    ).count()

    total_maintenance_cost = wo_qs.filter(
        status__in=[MaintenanceWorkOrder.Status.RESOLVED, MaintenanceWorkOrder.Status.CLOSED]
    ).aggregate(total=Sum("total_cost_etb"))["total"] or Decimal("0.00")

    # Cleaning & Inspections
    total_cleaning_tasks = clean_qs.count()
    completed_cleaning_tasks = clean_qs.filter(status=CleaningTaskExecution.Status.COMPLETED).count()
    cleaning_completion_rate = round((completed_cleaning_tasks / total_cleaning_tasks * 100), 1) if total_cleaning_tasks > 0 else 100.0

    total_inspections = insp_qs.count()
    failed_inspections = insp_qs.filter(passed=False).count()
    inspection_pass_rate = round(((total_inspections - failed_inspections) / total_inspections * 100), 1) if total_inspections > 0 else 100.0

    # Incident reports
    open_incidents = inc_qs.filter(status__in=[IncidentReport.Status.OPEN, IncidentReport.Status.INVESTIGATING]).count()

    return {
        "total_lockers": total_lockers,
        "available_lockers": available_lockers,
        "occupied_lockers": occupied_lockers,
        "maintenance_lockers": maintenance_lockers,
        "locker_utilization_rate": utilization_rate,
        "overdue_assignments": overdue_assignments,
        "total_equipment": total_equipment,
        "operational_equipment": operational_equipment,
        "maintenance_equipment": maintenance_equipment,
        "out_of_service_equipment": out_of_service_equipment,
        "total_downtime_hours": total_downtime,
        "active_work_orders": active_work_orders,
        "overdue_work_orders": overdue_work_orders,
        "total_maintenance_cost_etb": total_maintenance_cost,
        "total_cleaning_tasks": total_cleaning_tasks,
        "cleaning_completion_rate": cleaning_completion_rate,
        "failed_inspections": failed_inspections,
        "inspection_pass_rate": inspection_pass_rate,
        "open_incidents": open_incidents,
    }


def get_locker_utilization_by_zone(branch_ids=None) -> list[dict]:
    """Breakdown of locker utilization across locker zones."""
    qs = Locker.objects.all()
    if branch_ids is not None:
        qs = qs.filter(branch_id__in=branch_ids)

    results = []
    zones = LockerZone.objects.filter(is_active=True)
    if branch_ids is not None:
        zones = zones.filter(branch_id__in=branch_ids)

    for zone in zones:
        zone_lockers = qs.filter(zone=zone)
        total = zone_lockers.count()
        occupied = zone_lockers.filter(status=Locker.LockerStatus.OCCUPIED).count()
        available = zone_lockers.filter(status=Locker.LockerStatus.AVAILABLE).count()
        rate = round((occupied / total * 100), 1) if total > 0 else 0.0
        results.append({
            "zone_id": zone.id,
            "zone_name": zone.name,
            "branch_name": zone.branch.name,
            "total": total,
            "occupied": occupied,
            "available": available,
            "utilization_rate": rate,
        })
    return results
