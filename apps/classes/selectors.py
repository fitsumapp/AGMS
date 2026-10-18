from datetime import date, timedelta
from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from .models import ClassBooking, ClassSession, ClassType


def get_class_scheduling_metrics(branch=None, start_date=None, end_date=None):
    """
    Computes comprehensive operational and analytical metrics for group classes.
    """
    today = timezone.localdate()
    start_date = start_date or (today - timedelta(days=30))
    end_date = end_date or today

    sessions_qs = ClassSession.objects.filter(
        session_date__gte=start_date,
        session_date__lte=end_date,
    )
    if branch:
        sessions_qs = sessions_qs.filter(branch=branch)

    total_classes = sessions_qs.count()
    completed_classes = sessions_qs.filter(status=ClassSession.SessionStatus.COMPLETED).count()
    cancelled_classes = sessions_qs.filter(status=ClassSession.SessionStatus.CANCELLED).count()
    scheduled_classes = sessions_qs.filter(status=ClassSession.SessionStatus.SCHEDULED).count()

    total_capacity = sessions_qs.aggregate(total=Sum("capacity"))["total"] or 0

    bookings_qs = ClassBooking.objects.filter(
        session__session_date__gte=start_date,
        session__session_date__lte=end_date,
    )
    if branch:
        bookings_qs = bookings_qs.filter(session__branch=branch)

    total_bookings = bookings_qs.filter(
        status__in=[
            ClassBooking.BookingStatus.BOOKED,
            ClassBooking.BookingStatus.ATTENDED,
            ClassBooking.BookingStatus.NO_SHOW,
        ]
    ).count()

    attended_count = bookings_qs.filter(status=ClassBooking.BookingStatus.ATTENDED).count()
    no_show_count = bookings_qs.filter(status=ClassBooking.BookingStatus.NO_SHOW).count()
    cancelled_bookings = bookings_qs.filter(status=ClassBooking.BookingStatus.CANCELLED).count()
    waitlisted_count = bookings_qs.filter(status=ClassBooking.BookingStatus.WAITLISTED).count()
    promoted_count = bookings_qs.filter(promoted_from_waitlist_at__isnull=False).count()

    attendance_rate = round((attended_count / total_bookings * 100), 1) if total_bookings > 0 else 0.0
    no_show_rate = round((no_show_count / total_bookings * 100), 1) if total_bookings > 0 else 0.0
    capacity_utilization = round((total_bookings / total_capacity * 100), 1) if total_capacity > 0 else 0.0

    return {
        "total_classes": total_classes,
        "completed_classes": completed_classes,
        "cancelled_classes": cancelled_classes,
        "scheduled_classes": scheduled_classes,
        "total_capacity": total_capacity,
        "total_bookings": total_bookings,
        "attended_count": attended_count,
        "no_show_count": no_show_count,
        "cancelled_bookings": cancelled_bookings,
        "waitlisted_count": waitlisted_count,
        "promoted_count": promoted_count,
        "attendance_rate": attendance_rate,
        "no_show_rate": no_show_rate,
        "capacity_utilization": capacity_utilization,
    }


def get_popular_class_types(branch=None, start_date=None, end_date=None, limit=10):
    """
    Ranks class catalog types by total bookings and attendance.
    """
    today = timezone.localdate()
    start_date = start_date or (today - timedelta(days=30))
    end_date = end_date or today

    sessions_qs = ClassSession.objects.filter(
        session_date__gte=start_date,
        session_date__lte=end_date,
    )
    if branch:
        sessions_qs = sessions_qs.filter(branch=branch)

    class_type_ids = sessions_qs.values_list("class_type_id", flat=True).distinct()

    results = []
    for ct in ClassType.objects.filter(id__in=class_type_ids):
        ct_sessions = sessions_qs.filter(class_type=ct)
        total_sessions = ct_sessions.count()

        ct_bookings = ClassBooking.objects.filter(session__in=ct_sessions)
        total_booked = ct_bookings.filter(status__in=[ClassBooking.BookingStatus.BOOKED, ClassBooking.BookingStatus.ATTENDED, ClassBooking.BookingStatus.NO_SHOW]).count()
        attended = ct_bookings.filter(status=ClassBooking.BookingStatus.ATTENDED).count()
        total_cap = ct_sessions.aggregate(s=Sum("capacity"))["s"] or 0
        utilization = round((total_booked / total_cap * 100), 1) if total_cap > 0 else 0.0

        results.append({
            "class_type": ct,
            "name": ct.name,
            "category": ct.get_category_display(),
            "total_sessions": total_sessions,
            "total_booked": total_booked,
            "attended": attended,
            "utilization": utilization,
        })

    results.sort(key=lambda x: x["total_booked"], reverse=True)
    return results[:limit]


def get_trainer_class_workload(branch=None, start_date=None, end_date=None):
    """
    Calculates class sessions conducted, teaching hours, and attendees per trainer.
    """
    today = timezone.localdate()
    start_date = start_date or (today - timedelta(days=30))
    end_date = end_date or today

    sessions_qs = ClassSession.objects.filter(
        session_date__gte=start_date,
        session_date__lte=end_date,
    ).select_related("trainer__staff__user", "branch")

    if branch:
        sessions_qs = sessions_qs.filter(branch=branch)

    trainers = {}
    for session in sessions_qs:
        t_id = session.trainer_id
        if t_id not in trainers:
            trainers[t_id] = {
                "trainer": session.trainer,
                "full_name": session.trainer.full_name,
                "total_classes": 0,
                "completed_classes": 0,
                "total_minutes": 0,
                "total_attendees": 0,
            }
        trainers[t_id]["total_classes"] += 1
        if session.status == ClassSession.SessionStatus.COMPLETED:
            trainers[t_id]["completed_classes"] += 1
        trainers[t_id]["total_minutes"] += session.duration_minutes
        trainers[t_id]["total_attendees"] += session.attended_count

    workload = list(trainers.values())
    for item in workload:
        item["total_hours"] = round(item["total_minutes"] / 60.0, 1)
    workload.sort(key=lambda x: x["total_classes"], reverse=True)
    return workload
