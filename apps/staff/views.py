from datetime import date, timedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.roles import has_role, is_owner
from apps.branches.audit import record_event
from apps.branches.models import Branch
from apps.branches.services import accessible_branches
from apps.staff.models import (
    LeaveRequest,
    StaffDocument,
    StaffProfile,
    StaffShift,
    TrainerMemberAssignment,
    TrainerProfile,
    TrainingSession,
)
from apps.staff.selectors import (
    get_branch_staffing_summary,
    get_staff_scheduling_metrics,
    get_trainer_metrics,
)
from apps.staff.services import authorize_staff_document_download


def _resolve_staff_scope(request):
    """Returns (branch_obj, accessible_branches_qs) based on user and request parameters."""
    user = request.user
    user_branches = accessible_branches(user)
    is_admin = is_owner(user) or user.is_superuser

    branch_id = request.GET.get("branch")
    selected_branch = None

    if branch_id and branch_id != "all":
        try:
            branch = Branch.objects.get(pk=branch_id, is_active=True)
            if not is_admin and not user_branches.filter(pk=branch.pk).exists():
                raise PermissionDenied("You do not have access to this branch.")
            selected_branch = branch
        except Branch.DoesNotExist:
            pass

    if not selected_branch and not is_admin:
        selected_branch = user_branches.first()

    return selected_branch, user_branches


@staff_member_required
def trainer_performance_view(request):
    """
    Detailed Trainer Performance & Utilization View.
    Displays client load, training sessions completed/cancelled,
    session hours delivered, and branch comparison.
    """
    user = request.user
    selected_branch, user_branches = _resolve_staff_scope(request)

    # Date filter: default to current month
    today = timezone.localdate()
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    try:
        start_date = date.fromisoformat(start_date_str) if start_date_str else today.replace(day=1)
    except (ValueError, TypeError):
        start_date = today.replace(day=1)

    try:
        end_date = date.fromisoformat(end_date_str) if end_date_str else today
    except (ValueError, TypeError):
        end_date = today

    trainers_qs = TrainerProfile.objects.select_related("staff__user", "staff__primary_branch")

    # If user is a Trainer, restrict to own profile
    if has_role(user, "Trainer") and not is_owner(user) and not user.is_superuser:
        trainers_qs = trainers_qs.filter(staff__user=user)
    elif selected_branch:
        trainers_qs = trainers_qs.filter(
            Q(staff__primary_branch=selected_branch)
            | Q(staff__user__branch_access__branch=selected_branch, staff__user__branch_access__is_active=True)
        ).distinct()

    trainer_stats = []
    for trainer in trainers_qs:
        metrics = get_trainer_metrics(
            trainer=trainer,
            start_date=start_date,
            end_date=end_date,
            branch=selected_branch,
        )
        trainer_stats.append(metrics)

    summary = get_branch_staffing_summary(branch=selected_branch)

    context = {
        "title": "Trainer Performance & Utilization",
        "trainer_stats": trainer_stats,
        "summary": summary,
        "selected_branch": selected_branch,
        "user_branches": user_branches,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "is_owner": is_owner(user) or user.is_superuser,
    }
    return render(request, "admin/staff/performance.html", context)


@staff_member_required
def staff_reports_view(request):
    """
    Operational Staffing, Shift Schedules, and Leave Reports View.
    Supports CSV export and web preview.
    """
    selected_branch, user_branches = _resolve_staff_scope(request)
    report_type = request.GET.get("report_type", "shifts")
    export_format = request.GET.get("export")

    today = timezone.localdate()
    start_date = today - timedelta(days=30)
    end_date = today

    shifts_qs = StaffShift.objects.select_related("staff__user", "branch").filter(
        date__gte=start_date, date__lte=end_date
    )
    if selected_branch:
        shifts_qs = shifts_qs.filter(branch=selected_branch)

    leaves_qs = LeaveRequest.objects.select_related("staff__user", "reviewed_by").filter(
        start_date__gte=start_date
    )
    if selected_branch:
        leaves_qs = leaves_qs.filter(staff__primary_branch=selected_branch)

    # Handle CSV export
    if export_format == "csv":
        import csv
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="staff_{report_type}_{today.isoformat()}.csv"'
        writer = csv.writer(response)

        if report_type == "leaves":
            writer.writerow(["Staff Name", "Employee #", "Leave Type", "Start Date", "End Date", "Days", "Status", "Reviewed By"])
            for leave in leaves_qs:
                reviewer_name = leave.reviewed_by.get_full_name() if leave.reviewed_by else "-"
                writer.writerow([
                    leave.staff.full_name,
                    leave.staff.employee_number,
                    leave.get_leave_type_display(),
                    leave.start_date.isoformat(),
                    leave.end_date.isoformat(),
                    leave.total_days,
                    leave.get_status_display(),
                    reviewer_name,
                ])
        else:
            writer.writerow(["Date", "Staff Name", "Employee #", "Branch", "Start Time", "End Time", "Rest Day", "Status"])
            for shift in shifts_qs:
                st = shift.start_time.strftime("%H:%M") if shift.start_time else "-"
                et = shift.end_time.strftime("%H:%M") if shift.end_time else "-"
                writer.writerow([
                    shift.date.isoformat(),
                    shift.staff.full_name,
                    shift.staff.employee_number,
                    shift.branch.name,
                    st,
                    et,
                    "Yes" if shift.is_rest_day else "No",
                    shift.get_status_display(),
                ])
        return response

    metrics = get_staff_scheduling_metrics(branch=selected_branch, start_date=start_date, end_date=end_date)

    context = {
        "title": "Staff Schedules & Operational Reports",
        "report_type": report_type,
        "shifts": shifts_qs[:100],
        "leaves": leaves_qs[:100],
        "metrics": metrics,
        "selected_branch": selected_branch,
        "user_branches": user_branches,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    return render(request, "admin/staff/reports.html", context)


def download_staff_document(request, document_id):
    """
    Securely download a staff member's private document (contract, ID, credentials).
    Enforces staff authentication, object-level role authorization, and branch scoping.
    Prevents IDOR and public access to confidential staff employment files.
    """
    if not request.user.is_authenticated or not request.user.is_staff or not request.user.is_active:
        raise PermissionDenied("Authentication required.")

    document = (
        StaffDocument.objects.filter(pk=document_id)
        .select_related("staff__primary_branch", "staff__user")
        .first()
    )
    if not document or not document.file:
        raise Http404("Document not found.")

    # Call service authorization
    try:
        authorize_staff_document_download(request.user, document)
    except PermissionDenied as e:
        record_event(
            request.user,
            "document_download_unauthorized",
            document,
            {
                "employee_number": document.staff.employee_number,
                "reason": "unauthorized_staff_document_access",
            },
        )
        raise e

    try:
        file_handle = document.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise Http404("Document file missing from private storage.")

    record_event(
        request.user,
        "staff_document_downloaded",
        document,
        {
            "employee_number": document.staff.employee_number,
            "document_type": document.document_type,
        },
    )

    filename = document.file.name.split("/")[-1]
    response = FileResponse(file_handle, as_attachment=True, filename=filename)
    response["Cache-Control"] = "private, no-store, must-revalidate"
    response["X-Content-Type-Options"] = "nosniff"
    return response
