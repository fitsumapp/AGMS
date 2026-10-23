from datetime import datetime, timedelta
from typing import Optional, Tuple
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import is_owner
from apps.branches.services import can_access_branch as user_can_access_branch
from apps.memberships.eligibility import can_access_branch as member_can_access_branch, AccessResult
from .credentials import lookup_credential
from .models import AccessAttempt, AccessDevice, Attendance, AttendanceOverride, MemberCredential
from .permissions import assert_attendance_method_enabled, can_checkin_members, can_override_checkin


def process_check_in(
    *,
    branch,
    actor=None,
    raw_token: Optional[str] = None,
    member=None,
    method: str = Attendance.Method.QR,
    device: Optional[AccessDevice] = None,
    notes: str = "",
) -> Tuple[Attendance, AccessResult]:
    """
    Core atomic check-in execution engine.
    Validates QR credential (if provided), member eligibility, visit caps,
    and open-session concurrency before recording attendance.
    """
    assert_attendance_method_enabled(method, branch=branch)

    if actor and actor.is_authenticated and not can_checkin_members(actor, branch):
        raise PermissionDenied("Staff member is not authorized to record attendance for this branch.")

    operator_str = str(actor.email if actor and actor.is_authenticated else (device.device_code if device else "SYSTEM"))

    # 1. Resolve member from token if raw_token is provided
    if raw_token:
        credential = lookup_credential(raw_token)
        if not credential:
            AccessAttempt.objects.create(
                branch=branch,
                result=AccessAttempt.Result.DENIED,
                reason_code="INVALID_CREDENTIAL",
                message="Credential token is invalid or unrecognized.",
                check_in_method=method,
                device_or_operator=operator_str,
                metadata={"token_provided": True},
            )
            raise ValidationError("Invalid or unrecognized QR credential.")

        if credential.status == MemberCredential.Status.REVOKED:
            AccessAttempt.objects.create(
                member=credential.member,
                branch=branch,
                result=AccessAttempt.Result.DENIED,
                reason_code="CREDENTIAL_REVOKED",
                message="This credential has been revoked and cannot be used for entry.",
                check_in_method=method,
                device_or_operator=operator_str,
                metadata={"revoked_date": str(credential.revoked_date)},
            )
            raise ValidationError("QR credential has been revoked.")

        if not credential.is_valid:
            AccessAttempt.objects.create(
                member=credential.member,
                branch=branch,
                result=AccessAttempt.Result.DENIED,
                reason_code="CREDENTIAL_EXPIRED",
                message="This credential has expired.",
                check_in_method=method,
                device_or_operator=operator_str,
            )
            raise ValidationError("QR credential has expired.")

        member = credential.member

    if not member:
        raise ValidationError("Member record is required for check-in.")

    now = timezone.now()
    denial_reason = None
    reason_code = None

    with transaction.atomic():
        # Concurrency protection: serialize all scans for this member
        from apps.members.models import Member
        Member.objects.select_for_update().get(pk=member.pk)

        open_session = (
            Attendance.objects.select_for_update()
            .filter(
                member=member,
                status=Attendance.Status.PRESENT,
                check_out_datetime__isnull=True,
            )
            .order_by("-check_in_datetime")
            .first()
        )

        if open_session:
            if (now - open_session.check_in_datetime) < timedelta(minutes=5):
                denial_reason = f"Member was already checked in at {open_session.check_in_datetime.strftime('%H:%M')}."
                reason_code = "DUPLICATE_SCAN"
            else:
                denial_reason = f"Member already has an open session started at {open_session.check_in_datetime.strftime('%H:%M')}."
                reason_code = "ALREADY_CHECKED_IN"

        if not denial_reason:
            eligibility_result = member_can_access_branch(member=member, branch=branch, at_datetime=now)
            if not eligibility_result.allowed:
                denial_reason = eligibility_result.message
                reason_code = eligibility_result.reason_code

        if not denial_reason:
            attendance = Attendance.objects.create(
                member=member,
                membership=eligibility_result.membership,
                branch=branch,
                check_in_datetime=now,
                check_in_method=method,
                recorded_by=actor if actor and actor.is_authenticated else None,
                access_device=device,
                status=Attendance.Status.PRESENT,
                notes=notes.strip(),
            )
            AccessAttempt.objects.create(
                member=member,
                branch=branch,
                result=AccessAttempt.Result.GRANTED,
                reason_code=eligibility_result.reason_code,
                message=eligibility_result.message,
                check_in_method=method,
                device_or_operator=operator_str,
                metadata={
                    "attendance_id": str(attendance.pk),
                    "membership": eligibility_result.membership.membership_number if eligibility_result.membership else None,
                },
            )
            return attendance, eligibility_result

    # Persist denied attempt and raise validation error
    AccessAttempt.objects.create(
        member=member,
        branch=branch,
        result=AccessAttempt.Result.DENIED,
        reason_code=reason_code,
        message=denial_reason,
        check_in_method=method,
        device_or_operator=operator_str,
    )
    if reason_code == "DUPLICATE_SCAN":
        raise ValidationError("Member is already checked in (duplicate scan ignored).")
    elif reason_code == "ALREADY_CHECKED_IN":
        raise ValidationError("Member has an active open session. Please check out first.")
    else:
        raise ValidationError(denial_reason)


@transaction.atomic
def process_check_out(
    *,
    branch,
    actor=None,
    raw_token: Optional[str] = None,
    member=None,
    attendance_id=None,
    method: str = Attendance.CheckoutMethod.QR,
    notes: str = "",
) -> Attendance:
    """
    Complete an active attendance session.
    """
    assert_attendance_method_enabled(method, branch=branch)
    operator_str = str(actor.email if actor and actor.is_authenticated else "SYSTEM")

    if raw_token:
        credential = lookup_credential(raw_token)
        if not credential:
            raise ValidationError("Invalid or unrecognized QR credential for checkout.")
        member = credential.member

    if attendance_id:
        attendance = Attendance.objects.select_for_update().filter(pk=attendance_id, branch=branch).first()
        if not attendance:
            raise ValidationError("Attendance record not found.")
    elif member:
        attendance = (
            Attendance.objects.select_for_update()
            .filter(
                member=member,
                branch=branch,
                status=Attendance.Status.PRESENT,
                check_out_datetime__isnull=True,
            )
            .order_by("-check_in_datetime")
            .first()
        )
        if not attendance:
            raise ValidationError("No active check-in session found for this member.")
    else:
        raise ValidationError("Specify either member, credential, or attendance ID.")

    if attendance.status != Attendance.Status.PRESENT:
        raise ValidationError(f"Cannot check out session with status '{attendance.status}'.")

    now = timezone.now()
    attendance.check_out_datetime = now
    attendance.check_out_method = method
    attendance.status = Attendance.Status.COMPLETED
    if notes:
        attendance.notes = f"{attendance.notes}\n[Checkout Notes]: {notes}".strip()
    attendance.save(update_fields=["check_out_datetime", "check_out_method", "status", "notes", "updated_at"])

    return attendance


@transaction.atomic
def record_manual_override(
    *,
    actor,
    branch,
    member,
    override_reason: str,
    notes: str = "",
) -> Attendance:
    """
    Allow Branch Managers and Gym Owners to grant manual access override with a recorded reason.
    """
    if not can_override_checkin(actor, branch):
        raise PermissionDenied("Only Branch Managers or Gym Owners are authorized to grant manual overrides.")

    assert_attendance_method_enabled(Attendance.Method.OVERRIDE, branch=branch)

    if not override_reason or not override_reason.strip():
        raise ValidationError("A clear override reason is mandatory.")

    now = timezone.now()
    operator_str = str(actor.email)

    # Close any lingering open session for this member with estimated flag
    Attendance.objects.filter(
        member=member,
        status=Attendance.Status.PRESENT,
        check_out_datetime__isnull=True,
    ).update(
        check_out_datetime=now,
        check_out_method=Attendance.CheckoutMethod.MANUAL,
        is_estimated_duration=True,
        status=Attendance.Status.AUTO_CLOSED,
        notes="Closed automatically due to manual override check-in.",
    )

    attendance = Attendance.objects.create(
        member=member,
        membership=None,
        branch=branch,
        check_in_datetime=now,
        check_in_method=Attendance.Method.OVERRIDE,
        recorded_by=actor,
        status=Attendance.Status.PRESENT,
        notes=notes.strip(),
    )

    AttendanceOverride.objects.create(
        attendance=attendance,
        member=member,
        branch=branch,
        override_reason=override_reason.strip(),
        requested_by=actor,
        approved_by=actor,
        created_at=now,
    )

    AccessAttempt.objects.create(
        member=member,
        branch=branch,
        result=AccessAttempt.Result.GRANTED,
        reason_code="MANUAL_OVERRIDE",
        message=f"Manual override approved by {actor.email}: {override_reason.strip()}",
        check_in_method=Attendance.Method.OVERRIDE,
        device_or_operator=operator_str,
        metadata={"attendance_id": str(attendance.pk), "override_reason": override_reason.strip()},
    )

    return attendance


@transaction.atomic
def auto_checkout_stale_sessions(*, branch=None, max_hours: int = 12) -> int:
    """
    Automatically close forgotten sessions older than max_hours.
    Marks session as AUTO_CLOSED with is_estimated_duration=True.
    Preserves actual check_in_datetime without inventing a verified physical checkout time.
    """
    cutoff = timezone.now() - timedelta(hours=max_hours)
    qs = Attendance.objects.filter(
        status=Attendance.Status.PRESENT,
        check_out_datetime__isnull=True,
        check_in_datetime__lt=cutoff,
    )
    if branch:
        qs = qs.filter(branch=branch)

    count = 0
    now = timezone.now()
    for att in qs.select_for_update():
        att.check_out_datetime = att.check_in_datetime + timedelta(hours=2)  # Statistical estimated duration window
        att.check_out_method = Attendance.CheckoutMethod.AUTO
        att.is_estimated_duration = True
        att.status = Attendance.Status.AUTO_CLOSED
        att.notes = f"{att.notes}\n[Auto-Closed Session]: Unconfirmed departure after exceeding {max_hours}h. Duration estimated at 2h.".strip()
        att.save(update_fields=["check_out_datetime", "check_out_method", "is_estimated_duration", "status", "notes", "updated_at"])
        count += 1

    return count
