from collections import defaultdict
from datetime import timedelta
from typing import Dict, List
from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.analytics.dates import DateFilterRange, get_gym_timezone, get_today_in_gym_tz
from apps.attendance.models import AccessAttempt, Attendance


def get_attendance_analytics(
    branch_qs,
    date_range: DateFilterRange,
) -> Dict:
    """
    Compute attendance analytics with strict verified vs auto-closed separation,
    method breakdown (QR vs Manual), peak hours, and denied attempt trends.
    """
    today = get_today_in_gym_tz()
    tz = get_gym_timezone()

    att_qs = Attendance.objects.filter(branch__in=branch_qs)
    attempt_qs = AccessAttempt.objects.filter(branch__in=branch_qs)

    # 1. Date-range Scoped Attendance
    range_att = att_qs.filter(
        check_in_datetime__gte=date_range.start_datetime,
        check_in_datetime__lte=date_range.end_datetime,
    ).exclude(status=Attendance.Status.VOIDED)

    total_checkins = range_att.count()

    # Unique visitors in date range
    unique_visitors = range_att.values("member").distinct().count()

    # 2. Currently Inside (Real-time snapshot)
    currently_inside = att_qs.filter(
        status=Attendance.Status.PRESENT,
        check_out_datetime__isnull=True,
    ).count()

    # 3. Method Breakdown (QR vs Manual)
    qr_checkins = range_att.filter(check_in_method=Attendance.Method.QR).count()
    manual_checkins = range_att.filter(check_in_method=Attendance.Method.MANUAL).count()

    qr_checkin_pct = round((qr_checkins / total_checkins) * 100, 1) if total_checkins > 0 else 0.0
    manual_checkin_pct = round((manual_checkins / total_checkins) * 100, 1) if total_checkins > 0 else 0.0

    # Checkouts in range
    range_checkouts = att_qs.filter(
        check_out_datetime__gte=date_range.start_datetime,
        check_out_datetime__lte=date_range.end_datetime,
    )
    total_checkouts = range_checkouts.count()
    qr_checkouts = range_checkouts.filter(check_out_method=Attendance.CheckoutMethod.QR).count()
    manual_checkouts = range_checkouts.filter(check_out_method=Attendance.CheckoutMethod.MANUAL).count()

    # 4. Verified Checkouts vs Auto-Closed (Estimated) Durations
    # Verified physical completed sessions
    verified_completed = range_att.filter(
        status=Attendance.Status.COMPLETED,
        is_estimated_duration=False,
        check_out_datetime__isnull=False,
    )
    verified_completed_count = verified_completed.count()

    verified_durations = [
        max(0, int((att.check_out_datetime - att.check_in_datetime).total_seconds() // 60))
        for att in verified_completed
        if att.check_out_datetime and att.check_in_datetime
    ]
    avg_verified_duration = (
        round(sum(verified_durations) / len(verified_durations))
        if verified_durations
        else 0
    )

    # Auto-closed sessions (estimated durations strictly separated)
    auto_closed_sessions = range_att.filter(
        Q(status=Attendance.Status.AUTO_CLOSED) | Q(is_estimated_duration=True)
    )
    auto_closed_count = auto_closed_sessions.count()

    # 5. Peak Attendance Hours (Gym Operating Hours: 06:00 to 22:00)
    hour_counts = {h: 0 for h in range(6, 23)}
    for dt in range_att.values_list("check_in_datetime", flat=True):
        if dt:
            h = dt.astimezone(tz).hour
            if h in hour_counts:
                hour_counts[h] += 1

    peak_hours_distribution = [
        {"hour": f"{h:02d}:00", "hour_int": h, "count": hour_counts[h]}
        for h in sorted(hour_counts.keys())
    ]

    # Find peak hour
    peak_hour_entry = max(peak_hours_distribution, key=lambda x: x["count"], default=None)
    busiest_hour_label = (
        f"{peak_hour_entry['hour']} ({peak_hour_entry['count']} visits)"
        if peak_hour_entry and peak_hour_entry["count"] > 0
        else "N/A"
    )

    # 6. Daily Attendance Trend across date range
    daily_map = defaultdict(lambda: {"total": 0, "qr": 0, "manual": 0})
    for p in range_att.values("check_in_datetime", "check_in_method"):
        dt = p["check_in_datetime"]
        meth = p["check_in_method"]
        if dt:
            d = dt.astimezone(tz).date()
            daily_map[d]["total"] += 1
            if meth == Attendance.Method.QR:
                daily_map[d]["qr"] += 1
            elif meth == Attendance.Method.MANUAL:
                daily_map[d]["manual"] += 1

    daily_attendance_trend = []
    cur_d = date_range.start_date
    while cur_d <= date_range.end_date:
        stats = daily_map[cur_d]
        daily_attendance_trend.append({
            "date": cur_d.strftime("%Y-%m-%d"),
            "display": cur_d.strftime("%b %d"),
            "total": stats["total"],
            "qr": stats["qr"],
            "manual": stats["manual"],
        })
        cur_d += timedelta(days=1)

    # 7. Branch Attendance Breakdown
    branch_attendance = list(
        range_att.values("branch__name", "branch__code")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    # 8. Denied Access Attempts in Date Range
    range_denials = attempt_qs.filter(
        timestamp__gte=date_range.start_datetime,
        timestamp__lte=date_range.end_datetime,
        result=AccessAttempt.Result.DENIED,
    )
    total_denied_attempts = range_denials.count()

    denial_reasons = list(
        range_denials.values("reason_code")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    return {
        "total_checkins": total_checkins,
        "unique_visitors": unique_visitors,
        "currently_inside": currently_inside,
        "qr_checkins": qr_checkins,
        "qr_checkin_pct": qr_checkin_pct,
        "manual_checkins": manual_checkins,
        "manual_checkin_pct": manual_checkin_pct,
        "total_checkouts": total_checkouts,
        "qr_checkouts": qr_checkouts,
        "manual_checkouts": manual_checkouts,
        "verified_completed_count": verified_completed_count,
        "avg_verified_duration_minutes": avg_verified_duration,
        "auto_closed_count": auto_closed_count,
        "auto_closed_sessions_count": auto_closed_count,
        "peak_hours_distribution": peak_hours_distribution,
        "busiest_hour_label": busiest_hour_label,
        "daily_attendance_trend": daily_attendance_trend,
        "branch_attendance": branch_attendance,
        "total_denied_attempts": total_denied_attempts,
        "denied_access_count": total_denied_attempts,
        "denial_reasons": denial_reasons,
    }
