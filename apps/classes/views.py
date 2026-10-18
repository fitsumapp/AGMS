import csv
from datetime import date, datetime, timedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.roles import has_role, is_owner
from apps.branches.models import Branch
from apps.branches.services import accessible_branches
from .models import ClassBooking, ClassSession, ClassType
from .selectors import (
    get_class_scheduling_metrics,
    get_popular_class_types,
    get_trainer_class_workload,
)


def _resolve_classes_branch_scope(request):
    """Returns (selected_branch_or_none, accessible_branches_qs) based on user and request parameters."""
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
def class_calendar_view(request):
    """
    Weekly and daily Class Calendar view.
    Displays sessions categorized by day and time, capacity indicators,
    trainer, and room, with branch and class type filters.
    """
    user = request.user
    selected_branch, user_branches = _resolve_classes_branch_scope(request)

    # Date navigation: default to current week start (Monday)
    today = timezone.localdate()
    date_str = request.GET.get("date")
    try:
        current_date = date.fromisoformat(date_str) if date_str else today
    except (ValueError, TypeError):
        current_date = today

    # Calculate Monday of the week
    week_start = current_date - timedelta(days=current_date.weekday())
    week_end = week_start + timedelta(days=6)

    prev_week = (week_start - timedelta(days=7)).isoformat()
    next_week = (week_start + timedelta(days=7)).isoformat()

    sessions_qs = (
        ClassSession.objects.filter(
            session_date__gte=week_start,
            session_date__lte=week_end,
        )
        .select_related("class_type", "branch", "trainer__staff__user")
        .prefetch_related("bookings")
        .order_by("session_date", "start_time")
    )

    if selected_branch:
        sessions_qs = sessions_qs.filter(branch=selected_branch)

    # Class type filter
    class_type_id = request.GET.get("class_type")
    if class_type_id:
        sessions_qs = sessions_qs.filter(class_type_id=class_type_id)

    # If user is a Trainer, highlight or filter
    if has_role(user, "Trainer") and not is_owner(user) and not user.is_superuser and not has_role(user, "Branch Manager"):
        # Trainer sees classes at authorized branches, but can filter for own
        if request.GET.get("own") == "1":
            sessions_qs = sessions_qs.filter(trainer__staff__user=user)

    # Group sessions by day of week (0..6)
    days = []
    for day_offset in range(7):
        day_date = week_start + timedelta(days=day_offset)
        day_sessions = [s for s in sessions_qs if s.session_date == day_date]
        days.append({
            "date": day_date,
            "day_name": day_date.strftime("%A"),
            "day_number": day_date.strftime("%d %b"),
            "is_today": day_date == today,
            "sessions": day_sessions,
        })

    all_class_types = ClassType.objects.filter(is_active=True)

    context = {
        "title": "Class Schedule Calendar",
        "week_start": week_start,
        "week_end": week_end,
        "prev_week": prev_week,
        "next_week": next_week,
        "today_iso": today.isoformat(),
        "days": days,
        "selected_branch": selected_branch,
        "user_branches": user_branches,
        "all_class_types": all_class_types,
        "selected_class_type": class_type_id,
        "is_trainer": has_role(user, "Trainer"),
    }
    return render(request, "admin/classes/calendar.html", context)


@staff_member_required
def class_reports_view(request):
    """
    Class Analytics & Operational Reports view.
    Provides capacity utilization, attendance rate, no-show rate,
    popular class breakdown, trainer workload, and CSV export.
    """
    user = request.user
    selected_branch, user_branches = _resolve_classes_branch_scope(request)

    today = timezone.localdate()
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    try:
        start_date = date.fromisoformat(start_date_str) if start_date_str else (today - timedelta(days=30))
    except (ValueError, TypeError):
        start_date = today - timedelta(days=30)

    try:
        end_date = date.fromisoformat(end_date_str) if end_date_str else today
    except (ValueError, TypeError):
        end_date = today

    metrics = get_class_scheduling_metrics(branch=selected_branch, start_date=start_date, end_date=end_date)
    popular_classes = get_popular_class_types(branch=selected_branch, start_date=start_date, end_date=end_date)
    trainer_workload = get_trainer_class_workload(branch=selected_branch, start_date=start_date, end_date=end_date)

    # Handle CSV Export
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="class_report_{today.isoformat()}.csv"'
        writer = csv.writer(response)

        report_type = request.GET.get("report_type", "summary")
        if report_type == "popular":
            writer.writerow(["Class Name", "Category", "Sessions Held", "Total Booked", "Attended", "Capacity Utilization %"])
            for item in popular_classes:
                writer.writerow([
                    item["name"],
                    item["category"],
                    item["total_sessions"],
                    item["total_booked"],
                    item["attended"],
                    item["utilization"],
                ])
        elif report_type == "trainers":
            writer.writerow(["Trainer Name", "Classes Scheduled", "Classes Completed", "Total Teaching Hours", "Total Attendees"])
            for t in trainer_workload:
                writer.writerow([
                    t["full_name"],
                    t["total_classes"],
                    t["completed_classes"],
                    t["total_hours"],
                    t["total_attendees"],
                ])
        else:
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Total Classes", metrics["total_classes"]])
            writer.writerow(["Completed Classes", metrics["completed_classes"]])
            writer.writerow(["Cancelled Classes", metrics["cancelled_classes"]])
            writer.writerow(["Total Bookings", metrics["total_bookings"]])
            writer.writerow(["Total Attended", metrics["attended_count"]])
            writer.writerow(["Total No-shows", metrics["no_show_count"]])
            writer.writerow(["Attendance Rate %", metrics["attendance_rate"]])
            writer.writerow(["No-show Rate %", metrics["no_show_rate"]])
            writer.writerow(["Capacity Utilization %", metrics["capacity_utilization"]])
            writer.writerow(["Waitlist Promotions", metrics["promoted_count"]])

        return response

    context = {
        "title": "Group Class Analytics & Reports",
        "metrics": metrics,
        "popular_classes": popular_classes,
        "trainer_workload": trainer_workload,
        "selected_branch": selected_branch,
        "user_branches": user_branches,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    return render(request, "admin/classes/reports.html", context)
