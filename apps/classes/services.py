from datetime import date, datetime, time, timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import has_role, is_owner
from apps.branches.audit import record_event
from apps.branches.services import can_access_branch
from apps.members.models import Member
from apps.memberships.models import Membership
from .models import (
    ClassBooking,
    ClassScheduleException,
    ClassScheduleTemplate,
    ClassSession,
    ClassType,
)


def get_member_eligible_membership(member, target_date=None, target_branch=None):
    """
    Finds and validates an active, paid, unexpired, and unfrozen membership for the member.
    Checks package group class eligibility, payment clearance (including allow_access_with_balance),
    and branch permissions.
    """
    target_date = target_date or timezone.localdate()
    memberships = (
        Membership.objects.filter(
            member=member,
            status=Membership.Status.ACTIVE,
            start_date__lte=target_date,
            end_date__gte=target_date,
        )
        .select_related("package", "home_branch")
        .order_by("-end_date")
    )

    from apps.core.models import GymSettings
    settings_obj = GymSettings.objects.first()
    allow_balance = settings_obj.allow_access_with_balance if settings_obj else False

    for ms in memberships:
        # Check freeze
        if hasattr(ms, "is_frozen") and ms.is_frozen:
            continue

        # Check payment clearance status
        if ms.payment_clearance_status in [
            Membership.PaymentClearance.CLEARED,
            Membership.PaymentClearance.WAIVED_COMPLIMENTARY,
        ]:
            pass  # Fully cleared or complimentary
        elif ms.payment_clearance_status == Membership.PaymentClearance.PENDING:
            has_verified_payment = False
            if allow_balance:
                try:
                    from apps.billing.models import Payment
                    has_verified_payment = Payment.objects.filter(
                        invoice__membership=ms,
                        status=Payment.Status.VERIFIED,
                    ).exists()
                except Exception:
                    has_verified_payment = False
            if not (allow_balance and has_verified_payment):
                continue
        else:
            continue

        # Check package class eligibility
        if not getattr(ms.package, "classes_included", True):
            continue
        # Check branch authorization
        if target_branch:
            if not ms.package.cross_branch_enabled and ms.home_branch_id != target_branch.id:
                continue
        return ms

    return None


def book_class_session(actor, session, member):
    """
    Concurrency-safe transactional booking of a member into a group class session.
    Enforces membership validity, class eligibility, weekly class quota, capacity limits, and waitlist promotion.
    Locks ClassSession row to eliminate race conditions.
    """
    if member.status != Member.MemberStatus.ACTIVE:
        raise ValidationError({"member": f"Member {member.full_name} is not currently active."})

    if session.status != ClassSession.SessionStatus.SCHEDULED:
        raise ValidationError({"session": f"Class session is {session.get_status_display().lower()} and cannot accept bookings."})

    # Validate booking cutoff window
    now = timezone.now()
    if session.booking_opens_at and now < session.booking_opens_at:
        raise ValidationError({"session": f"Bookings for this class open on {session.booking_opens_at.strftime('%Y-%m-%d %H:%M')}."})
    if session.booking_closes_at and now > session.booking_closes_at:
        raise ValidationError({"session": "Bookings for this class are closed."})

    # Validate membership eligibility
    eligible_membership = get_member_eligible_membership(
        member=member,
        target_date=session.session_date,
        target_branch=session.branch,
    )
    if not eligible_membership:
        raise ValidationError(
            {"member": f"Member {member.full_name} does not have an active, paid membership authorizing group classes at {session.branch.name}."}
        )

    with transaction.atomic():
        # Acquire row locks to serialize concurrent bookings
        locked_session = ClassSession.objects.select_for_update().get(pk=session.pk)
        locked_member = Member.objects.select_for_update().get(pk=member.pk)

        # Check duplicate booking
        if ClassBooking.objects.filter(
            session=locked_session,
            member=locked_member,
            status__in=[ClassBooking.BookingStatus.BOOKED, ClassBooking.BookingStatus.WAITLISTED],
        ).exists():
            raise ValidationError({"member": f"{locked_member.full_name} is already booked or waitlisted for this class."})

        # Check overlapping class bookings for member
        overlapping_classes = ClassBooking.objects.filter(
            member=locked_member,
            status=ClassBooking.BookingStatus.BOOKED,
            session__session_date=locked_session.session_date,
            session__status=ClassSession.SessionStatus.SCHEDULED,
            session__start_time__lt=locked_session.end_time,
            session__end_time__gt=locked_session.start_time,
        )
        if overlapping_classes.exists():
            raise ValidationError({"member": f"{locked_member.full_name} is already booked for another class at this time."})

        # Check overlapping personal training sessions
        from apps.staff.models import TrainingSession
        pt_overlaps = TrainingSession.objects.filter(
            member=locked_member,
            session_date=locked_session.session_date,
            status=TrainingSession.SessionStatus.SCHEDULED,
            start_time__lt=locked_session.end_time,
            end_time__gt=locked_session.start_time,
        )
        if pt_overlaps.exists():
            raise ValidationError({"member": f"{locked_member.full_name} has a conflicting personal training session at this time."})

        # Check weekly class limits on membership package
        allowed_per_week = getattr(eligible_membership.package, "allowed_classes_per_week", None)
        if allowed_per_week is not None:
            week_start = locked_session.session_date - timedelta(days=locked_session.session_date.weekday())
            week_end = week_start + timedelta(days=6)
            weekly_booked_count = ClassBooking.objects.filter(
                member=locked_member,
                session__session_date__gte=week_start,
                session__session_date__lte=week_end,
                status__in=[
                    ClassBooking.BookingStatus.BOOKED,
                    ClassBooking.BookingStatus.ATTENDED,
                    ClassBooking.BookingStatus.NO_SHOW,
                ],
            ).count()
            if weekly_booked_count >= allowed_per_week:
                raise ValidationError({
                    "member": (
                        f"Member {locked_member.full_name} has reached the weekly class limit "
                        f"of {allowed_per_week} class(es) for the week of {week_start} to {week_end}."
                    )
                })

        # Check capacity under row lock
        active_booked_count = ClassBooking.objects.filter(
            session=locked_session,
            status=ClassBooking.BookingStatus.BOOKED,
        ).count()

        if active_booked_count < locked_session.capacity:
            # Confirmed spot
            booking = ClassBooking.objects.create(
                session=locked_session,
                member=locked_member,
                membership=eligible_membership,
                status=ClassBooking.BookingStatus.BOOKED,
                booked_by=actor,
            )
            ClassSession.objects.filter(pk=locked_session.pk).update(updated_at=timezone.now())
            record_event(
                actor,
                "class_booking_created",
                booking,
                {
                    "member_number": locked_member.member_number,
                    "class_name": locked_session.class_type.name,
                    "branch_code": locked_session.branch.code,
                    "status": "BOOKED",
                },
            )
            return booking

        # Class is full -> check waitlist capacity
        active_waitlist_count = ClassBooking.objects.filter(
            session=locked_session,
            status=ClassBooking.BookingStatus.WAITLISTED,
        ).count()

        if active_waitlist_count < locked_session.waitlist_capacity:
            waitlist_position = active_waitlist_count + 1
            booking = ClassBooking.objects.create(
                session=locked_session,
                member=locked_member,
                membership=eligible_membership,
                status=ClassBooking.BookingStatus.WAITLISTED,
                waitlist_position=waitlist_position,
                booked_by=actor,
            )
            ClassSession.objects.filter(pk=locked_session.pk).update(updated_at=timezone.now())
            record_event(
                actor,
                "class_booking_created",
                booking,
                {
                    "member_number": locked_member.member_number,
                    "class_name": locked_session.class_type.name,
                    "branch_code": locked_session.branch.code,
                    "status": "WAITLISTED",
                },
            )
            return booking

        raise ValidationError({"capacity": f"Class '{locked_session.class_type.name}' and its waitlist are completely full."})


def cancel_class_booking(actor, booking, reason=""):
    """
    Concurrency-safe cancellation of a class booking.
    Locks session and booking to prevent duplicate cancellation or phantom promotions.
    If a confirmed participant cancels, automatically promotes the next eligible waitlisted member.
    """
    with transaction.atomic():
        locked_session = ClassSession.objects.select_for_update().get(pk=booking.session_id)
        locked_booking = ClassBooking.objects.select_for_update().get(pk=booking.pk)

        if locked_booking.status == ClassBooking.BookingStatus.CANCELLED:
            raise ValidationError({"booking": "This booking has already been cancelled."})

        original_status = locked_booking.status

        locked_booking.status = ClassBooking.BookingStatus.CANCELLED
        locked_booking.cancelled_at = timezone.now()
        locked_booking.cancelled_by = actor
        locked_booking.cancellation_reason = reason or "Cancelled by user"
        locked_booking.waitlist_position = None
        locked_booking.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancellation_reason", "waitlist_position"])

        ClassSession.objects.filter(pk=locked_session.pk).update(updated_at=timezone.now())

        record_event(
            actor,
            "class_booking_cancelled",
            locked_booking,
            {
                "member_number": locked_booking.member.member_number,
                "class_name": locked_session.class_type.name,
                "reason": reason or "Cancelled",
            },
        )

        # If a confirmed booking was cancelled, promote from waitlist
        if original_status == ClassBooking.BookingStatus.BOOKED:
            promote_from_waitlist(actor=actor, session=locked_session)

        # If a waitlisted booking was cancelled, renumber remaining waitlist
        elif original_status == ClassBooking.BookingStatus.WAITLISTED:
            renumber_waitlist(locked_session)

        return locked_booking


def promote_from_waitlist(actor, session):
    """
    Promotes the highest-ranking eligible waitlisted member into a confirmed class spot.
    Re-validates membership eligibility and weekly class quota at promotion time.
    Locks waitlist candidates to prevent race conditions during simultaneous cancellations.
    """
    # Verify open capacity under session row lock
    active_booked_count = ClassBooking.objects.filter(
        session=session,
        status=ClassBooking.BookingStatus.BOOKED,
    ).count()

    if active_booked_count >= session.capacity:
        return None

    waitlisted_bookings = (
        ClassBooking.objects.select_for_update()
        .filter(
            session=session,
            status=ClassBooking.BookingStatus.WAITLISTED,
        )
        .select_related("member")
        .order_by("waitlist_position", "booked_at")
    )

    promoted_booking = None
    for candidate in waitlisted_bookings:
        # Revalidate eligibility
        eligible_membership = get_member_eligible_membership(
            member=candidate.member,
            target_date=session.session_date,
            target_branch=session.branch,
        )
        if not eligible_membership:
            candidate.notes = f"{candidate.notes}\n[Waitlist Skip]: Ineligible membership at promotion time.".strip()
            candidate.save(update_fields=["notes"])
            continue

        # Revalidate weekly class limit
        allowed_per_week = getattr(eligible_membership.package, "allowed_classes_per_week", None)
        if allowed_per_week is not None:
            week_start = session.session_date - timedelta(days=session.session_date.weekday())
            week_end = week_start + timedelta(days=6)
            weekly_booked_count = ClassBooking.objects.filter(
                member=candidate.member,
                session__session_date__gte=week_start,
                session__session_date__lte=week_end,
                status__in=[
                    ClassBooking.BookingStatus.BOOKED,
                    ClassBooking.BookingStatus.ATTENDED,
                    ClassBooking.BookingStatus.NO_SHOW,
                ],
            ).count()
            if weekly_booked_count >= allowed_per_week:
                candidate.notes = f"{candidate.notes}\n[Waitlist Skip]: Weekly class limit reached at promotion time.".strip()
                candidate.save(update_fields=["notes"])
                continue

        # Promote candidate
        candidate.status = ClassBooking.BookingStatus.BOOKED
        candidate.membership = eligible_membership
        candidate.promoted_from_waitlist_at = timezone.now()
        candidate.waitlist_position = None
        candidate.save(update_fields=["status", "membership", "promoted_from_waitlist_at", "waitlist_position"])

        ClassSession.objects.filter(pk=session.pk).update(updated_at=timezone.now())

        record_event(
            actor,
            "class_waitlist_promoted",
            candidate,
            {
                "member_number": candidate.member.member_number,
                "class_name": session.class_type.name,
                "branch_code": session.branch.code,
            },
        )
        promoted_booking = candidate
        break

    renumber_waitlist(session)
    return promoted_booking


def renumber_waitlist(session):
    """Ensures sequential 1, 2, 3 waitlist numbering for a class session."""
    active_waitlist = (
        ClassBooking.objects.filter(
            session=session,
            status=ClassBooking.BookingStatus.WAITLISTED,
        )
        .order_by("waitlist_position", "booked_at")
    )
    for idx, wb in enumerate(active_waitlist, start=1):
        if wb.waitlist_position != idx:
            wb.waitlist_position = idx
            wb.save(update_fields=["waitlist_position"])


def mark_class_attendance(actor, booking, status, notes=""):
    """
    Marks a member's attendance status for a class session.
    Allowed statuses: ATTENDED or NO_SHOW.
    """
    if status not in [ClassBooking.BookingStatus.ATTENDED, ClassBooking.BookingStatus.NO_SHOW]:
        raise ValidationError({"status": "Status must be either Attended or No-show."})

    booking.status = status
    booking.attendance_marked_at = timezone.now()
    booking.attendance_marked_by = actor
    if notes:
        booking.notes = f"{booking.notes}\n{notes}".strip()
    booking.save(update_fields=["status", "attendance_marked_at", "attendance_marked_by", "notes"])

    record_event(
        actor,
        "class_attendance_marked",
        booking,
        {
            "member_number": booking.member.member_number,
            "class_name": booking.session.class_type.name,
            "status": status,
        },
    )
    return booking


def schedule_class_session(
    actor,
    class_type,
    branch,
    room,
    trainer,
    session_date,
    start_time,
    end_time,
    capacity=None,
    waitlist_capacity=None,
    notes="",
    template=None,
):
    """
    Transactional creation of a group class session.
    Acquires consistent row locks on Branch and TrainerProfile to serialize concurrent scheduling
    and guarantee deadlock-free execution with personal training sessions.
    """
    capacity = capacity if capacity is not None else class_type.default_capacity
    waitlist_capacity = waitlist_capacity if waitlist_capacity is not None else class_type.default_waitlist_capacity

    from apps.branches.models import Branch
    from apps.staff.models import TrainerProfile

    with transaction.atomic():
        # Consistent lock hierarchy: 1. Branch, 2. TrainerProfile
        locked_branch = Branch.objects.select_for_update().get(pk=branch.pk)
        locked_trainer = TrainerProfile.objects.select_for_update().get(pk=trainer.pk)

        session = ClassSession(
            class_type=class_type,
            branch=locked_branch,
            room=room.strip(),
            trainer=locked_trainer,
            session_date=session_date,
            start_time=start_time,
            end_time=end_time,
            capacity=capacity,
            waitlist_capacity=waitlist_capacity,
            notes=notes,
            schedule_template=template,
            status=ClassSession.SessionStatus.SCHEDULED,
        )
        session.clean()
        session.save()

        record_event(
            actor,
            "class_session_created",
            session,
            {
                "class_name": class_type.name,
                "branch_code": locked_branch.code,
                "employee_number": locked_trainer.employee_number,
            },
        )
        return session


def generate_sessions_from_schedule(template, start_date=None, end_date=None, actor=None):
    """
    Generates individual ClassSession occurrences from a ClassScheduleTemplate.
    Respects start_date, end_date, schedule exceptions, and prevents duplicate generations.
    """
    start_date = start_date or template.start_date
    end_date = end_date or (start_date + timedelta(days=28))  # default 4 weeks
    if template.end_date and end_date > template.end_date:
        end_date = template.end_date

    exceptions = set(
        ClassScheduleException.objects.filter(
            schedule_template=template,
            exception_date__gte=start_date,
            exception_date__lte=end_date,
            is_cancelled=True,
        ).values_list("exception_date", flat=True)
    )

    created_sessions = []
    current_date = start_date
    while current_date <= end_date:
        if current_date.weekday() == template.day_of_week and current_date not in exceptions:
            # Check if session already generated
            if not ClassSession.objects.filter(schedule_template=template, session_date=current_date).exists():
                session = schedule_class_session(
                    actor=actor,
                    class_type=template.class_type,
                    branch=template.branch,
                    room=template.room,
                    trainer=template.trainer,
                    session_date=current_date,
                    start_time=template.start_time,
                    end_time=template.end_time,
                    capacity=template.capacity,
                    waitlist_capacity=template.waitlist_capacity,
                    notes=f"Generated from recurring schedule {template.pk}",
                    template=template,
                )
                created_sessions.append(session)
        current_date += timedelta(days=1)

    return created_sessions
