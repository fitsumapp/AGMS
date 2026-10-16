from datetime import date
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import (
    LeaveRequest,
    ShiftTemplate,
    StaffProfile,
    StaffShift,
    TrainerMemberAssignment,
    TrainerProfile,
    TrainingSession,
)


def get_trainer_metrics(trainer, start_date=None, end_date=None, branch=None):
    """
    Computes real operational metrics for a trainer:
    - Active assigned members
    - Total completed sessions in period
    - Total cancelled sessions in period
    - No-show sessions in period
    - Scheduled upcoming sessions
    - Total session hours delivered
    """
    active_assignments = trainer.member_assignments.filter(
        status=TrainerMemberAssignment.AssignmentStatus.ACTIVE
    )
    if branch:
        active_assignments = active_assignments.filter(branch=branch)
    active_client_count = active_assignments.count()

    sessions_qs = trainer.sessions.all()
    if branch:
        sessions_qs = sessions_qs.filter(branch=branch)
    if start_date:
        sessions_qs = sessions_qs.filter(session_date__gte=start_date)
    if end_date:
        sessions_qs = sessions_qs.filter(session_date__lte=end_date)

    session_counts = sessions_qs.aggregate(
        completed=Count("id", filter=Q(status=TrainingSession.SessionStatus.COMPLETED)),
        cancelled=Count("id", filter=Q(status=TrainingSession.SessionStatus.CANCELLED)),
        no_show=Count("id", filter=Q(status=TrainingSession.SessionStatus.NO_SHOW)),
        scheduled=Count("id", filter=Q(status=TrainingSession.SessionStatus.SCHEDULED)),
        total=Count("id"),
    )

    completed_sessions = sessions_qs.filter(status=TrainingSession.SessionStatus.COMPLETED)
    total_minutes = sum(s.duration_minutes for s in completed_sessions)
    total_hours = round(total_minutes / 60, 1)

    total_sessions = session_counts["total"] or 0
    completed_count = session_counts["completed"] or 0
    completion_rate = round((completed_count / total_sessions * 100), 1) if total_sessions > 0 else 0.0

    return {
        "trainer": trainer,
        "active_clients": active_client_count,
        "max_clients": trainer.max_active_clients,
        "capacity_utilization": round((active_client_count / trainer.max_active_clients * 100), 1) if trainer.max_active_clients else 0.0,
        "completed_sessions": completed_count,
        "cancelled_sessions": session_counts["cancelled"] or 0,
        "no_show_sessions": session_counts["no_show"] or 0,
        "scheduled_sessions": session_counts["scheduled"] or 0,
        "total_sessions": total_sessions,
        "completion_rate": completion_rate,
        "training_hours": total_hours,
    }


def get_staff_scheduling_metrics(branch=None, start_date=None, end_date=None):
    """
    Computes real scheduling and shift coverage metrics:
    - Total scheduled shifts
    - Completed shifts
    - Absent shifts
    - Cancelled shifts
    - Rest days recorded
    - Pending leave requests
    - Approved leave requests in period
    """
    shifts_qs = StaffShift.objects.all()
    leaves_qs = LeaveRequest.objects.all()

    if branch:
        shifts_qs = shifts_qs.filter(branch=branch)
        leaves_qs = leaves_qs.filter(staff__primary_branch=branch)

    if start_date:
        shifts_qs = shifts_qs.filter(date__gte=start_date)
        leaves_qs = leaves_qs.filter(end_date__gte=start_date)
    if end_date:
        shifts_qs = shifts_qs.filter(date__lte=end_date)
        leaves_qs = leaves_qs.filter(start_date__lte=end_date)

    shift_stats = shifts_qs.aggregate(
        scheduled=Count("id", filter=Q(status=StaffShift.ShiftStatus.SCHEDULED, is_rest_day=False)),
        completed=Count("id", filter=Q(status=StaffShift.ShiftStatus.COMPLETED, is_rest_day=False)),
        absent=Count("id", filter=Q(status=StaffShift.ShiftStatus.ABSENT, is_rest_day=False)),
        cancelled=Count("id", filter=Q(status=StaffShift.ShiftStatus.CANCELLED, is_rest_day=False)),
        rest_days=Count("id", filter=Q(is_rest_day=True)),
        total_working=Count("id", filter=Q(is_rest_day=False)),
    )

    leave_stats = leaves_qs.aggregate(
        pending=Count("id", filter=Q(status=LeaveRequest.LeaveStatus.PENDING)),
        approved=Count("id", filter=Q(status=LeaveRequest.LeaveStatus.APPROVED)),
        rejected=Count("id", filter=Q(status=LeaveRequest.LeaveStatus.REJECTED)),
        total=Count("id"),
    )

    total_shifts = shift_stats["total_working"] or 0
    completed_shifts = shift_stats["completed"] or 0
    coverage_rate = round((completed_shifts / total_shifts * 100), 1) if total_shifts > 0 else 0.0

    return {
        "scheduled_shifts": shift_stats["scheduled"] or 0,
        "completed_shifts": completed_shifts,
        "absent_shifts": shift_stats["absent"] or 0,
        "cancelled_shifts": shift_stats["cancelled"] or 0,
        "rest_days": shift_stats["rest_days"] or 0,
        "total_working_shifts": total_shifts,
        "coverage_rate": coverage_rate,
        "pending_leaves": leave_stats["pending"] or 0,
        "approved_leaves": leave_stats["approved"] or 0,
    }


def get_branch_staffing_summary(branch=None):
    """
    Summarizes active staff distribution by department, employment type,
    and trainer workload at a branch.
    """
    staff_qs = StaffProfile.objects.all()
    trainers_qs = TrainerProfile.objects.all()

    if branch:
        staff_qs = staff_qs.filter(
            Q(primary_branch=branch) | Q(user__branch_access__branch=branch, user__branch_access__is_active=True)
        ).distinct()
        trainers_qs = trainers_qs.filter(
            Q(staff__primary_branch=branch) | Q(staff__user__branch_access__branch=branch, staff__user__branch_access__is_active=True)
        ).distinct()

    total_staff = staff_qs.count()
    active_staff = staff_qs.filter(employment_status=StaffProfile.EmploymentStatus.ACTIVE).count()
    on_leave_staff = staff_qs.filter(employment_status=StaffProfile.EmploymentStatus.ON_LEAVE).count()

    dept_counts = (
        staff_qs.values("department")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    dept_breakdown = {row["department"]: row["count"] for row in dept_counts}

    total_trainers = trainers_qs.count()
    active_trainers = trainers_qs.filter(status=TrainerProfile.TrainerStatus.ACTIVE).count()

    total_active_assignments = TrainerMemberAssignment.objects.filter(
        status=TrainerMemberAssignment.AssignmentStatus.ACTIVE
    )
    if branch:
        total_active_assignments = total_active_assignments.filter(branch=branch)
    total_active_clients = total_active_assignments.count()

    avg_clients_per_trainer = round(total_active_clients / active_trainers, 1) if active_trainers > 0 else 0.0

    return {
        "total_staff": total_staff,
        "active_staff": active_staff,
        "on_leave_staff": on_leave_staff,
        "department_breakdown": dept_breakdown,
        "total_trainers": total_trainers,
        "active_trainers": active_trainers,
        "total_active_clients": total_active_clients,
        "avg_clients_per_trainer": avg_clients_per_trainer,
    }
