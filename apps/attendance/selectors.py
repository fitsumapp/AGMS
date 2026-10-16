from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from apps.branches.services import accessible_branches, is_owner
from .models import AccessAttempt, Attendance


def get_attendance_metrics(user, active_branch=None) -> dict:
    """
    Branch-scoped attendance aggregation engine.
    Calculates today's entries, active occupants, unique visitors, denials,
    peak attendance hours, and method breakdowns.
    """
    tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Africa/Addis_Ababa")

    now_local = timezone.now().astimezone(tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    # Scoped base querysets
    user_branches = accessible_branches(user)
    if active_branch:
        branch_scope = user_branches.filter(pk=active_branch.pk)
    else:
        branch_scope = user_branches

    att_qs = Attendance.objects.filter(branch__in=branch_scope)
    attempt_qs = AccessAttempt.objects.filter(branch__in=branch_scope)

    # Today's check-ins
    today_att = att_qs.filter(check_in_datetime__gte=today_start, check_in_datetime__lt=today_end)
    today_checkins = today_att.exclude(status=Attendance.Status.VOIDED).count()

    # Currently inside
    currently_inside = att_qs.filter(status=Attendance.Status.PRESENT, check_out_datetime__isnull=True).count()

    # Unique visitors today
    unique_visitors_today = (
        today_att.exclude(status=Attendance.Status.VOIDED)
        .values("member")
        .distinct()
        .count()
    )

    # Denied attempts today
    denied_attempts_today = attempt_qs.filter(
        timestamp__gte=today_start,
        timestamp__lt=today_end,
        result=AccessAttempt.Result.DENIED,
    ).count()

    # Peak hours today (0 to 23)
    peak_hours_data = []
    # Query checkins by hour using PostgreSQL EXTRACT(hour FROM check_in_datetime) or in-memory
    hour_counts = {h: 0 for h in range(6, 23)}  # 6 AM to 10 PM gym operating hours
    for dt in today_att.values_list("check_in_datetime", flat=True):
        if dt:
            h = dt.astimezone(tz).hour
            if h in hour_counts:
                hour_counts[h] += 1
    for h, cnt in sorted(hour_counts.items()):
        peak_hours_data.append({"hour": f"{h:02d}:00", "count": cnt})

    # Method breakdown
    methods_counts = (
        today_att.values("check_in_method")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    methods_display = {
        m["check_in_method"]: m["total"] for m in methods_counts
    }

    # Explicit QR vs Manual Check-in counts
    qr_checkins = today_att.filter(check_in_method=Attendance.Method.QR).exclude(status=Attendance.Status.VOIDED).count()
    manual_checkins = today_att.filter(check_in_method=Attendance.Method.MANUAL).exclude(status=Attendance.Status.VOIDED).count()

    # Checkouts today
    today_checkouts_qs = att_qs.filter(check_out_datetime__gte=today_start, check_out_datetime__lt=today_end)
    today_checkouts = today_checkouts_qs.count()
    qr_checkouts = today_checkouts_qs.filter(check_out_method=Attendance.CheckoutMethod.QR).count()
    manual_checkouts = today_checkouts_qs.filter(check_out_method=Attendance.CheckoutMethod.MANUAL).count()

    # Duration analytics (distinguishing verified physical checkouts vs auto-closed / estimated)
    verified_completed = today_att.filter(
        status=Attendance.Status.COMPLETED,
        is_estimated_duration=False,
        check_out_datetime__isnull=False,
    )
    verified_durations = [
        max(0, int((att.check_out_datetime - att.check_in_datetime).total_seconds() // 60))
        for att in verified_completed
        if att.check_out_datetime and att.check_in_datetime
    ]
    avg_verified_duration = round(sum(verified_durations) / len(verified_durations)) if verified_durations else 0

    estimated_sessions = att_qs.filter(
        Q(check_in_datetime__gte=today_start, check_in_datetime__lt=today_end)
        | Q(check_out_datetime__gte=today_start, check_out_datetime__lt=today_end)
    ).filter(
        Q(status=Attendance.Status.AUTO_CLOSED) | Q(is_estimated_duration=True)
    )
    estimated_sessions_count = estimated_sessions.distinct().count()

    branch_counts = (
        today_att.values("branch__name", "branch__code")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    return {
        "today_checkins": today_checkins,
        "qr_checkins": qr_checkins,
        "manual_checkins": manual_checkins,
        "today_checkouts": today_checkouts,
        "qr_checkouts": qr_checkouts,
        "manual_checkouts": manual_checkouts,
        "currently_inside": currently_inside,
        "unique_visitors_today": unique_visitors_today,
        "denied_attempts_today": denied_attempts_today,
        "peak_hours": peak_hours_data,
        "methods_breakdown": methods_display,
        "branch_breakdown": list(branch_counts),
        "verified_completed_count": len(verified_durations),
        "avg_verified_duration_minutes": avg_verified_duration,
        "estimated_sessions_count": estimated_sessions_count,
    }


def get_currently_inside(user, active_branch=None):
    """Retrieve list of members currently inside the facility."""
    branches = accessible_branches(user)
    if active_branch:
        branches = branches.filter(pk=active_branch.pk)

    return (
        Attendance.objects.filter(branch__in=branches, status=Attendance.Status.PRESENT, check_out_datetime__isnull=True)
        .select_related("member", "membership", "membership__package", "branch")
        .order_by("-check_in_datetime")
    )
