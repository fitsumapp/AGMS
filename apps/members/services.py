from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction

from apps.accounts.roles import has_role, is_owner
from apps.branches.audit import record_event
from apps.branches.services import accessible_branches, can_access_branch
from .models import (
    Member,
    MemberEmergencyContact,
    MemberStatusHistory,
)
from .sequence import generate_next_member_number
from .validators import normalize_ethiopian_phone


def check_possible_duplicates(*, phone=None, first_name=None, last_name=None, date_of_birth=None, exclude_member_id=None):
    """
    Check for potential existing member profiles based on phone or full name + DOB.
    Returns a queryset of potential matches for staff review.
    """
    q_filter = models.Q()
    if phone:
        try:
            norm_phone = normalize_ethiopian_phone(phone)
            q_filter |= models.Q(phone=norm_phone)
        except ValidationError:
            pass

    if first_name and last_name:
        name_q = models.Q(first_name__iexact=first_name.strip(), last_name__iexact=last_name.strip())
        if date_of_birth:
            name_q &= models.Q(date_of_birth=date_of_birth)
        q_filter |= name_q

    if not q_filter:
        return Member.objects.none()

    qs = Member.objects.filter(q_filter)
    if exclude_member_id:
        qs = qs.exclude(pk=exclude_member_id)
    return qs.select_related("home_branch")


@transaction.atomic
def register_member(
    *,
    actor,
    home_branch,
    first_name,
    last_name,
    phone,
    middle_name="",
    gender="",
    date_of_birth=None,
    photo=None,
    alternative_phone="",
    email="",
    address="",
    city="",
    registration_source=Member.RegistrationSource.WALK_IN,
    referral_source="",
):
    """
    Register a new gym member.
    Enforces branch authorization, phone normalization, atomic number generation,
    initial status history recording, and registration audit logging.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required to register members.")

    if not can_access_branch(actor, home_branch):
        raise PermissionDenied("You are not authorized to register members in this branch.")

    if not home_branch.is_active:
        raise ValidationError({"home_branch": "Cannot register a member in an inactive branch."})

    # Normalized phone
    normalized_phone = normalize_ethiopian_phone(phone)
    normalized_alt_phone = normalize_ethiopian_phone(alternative_phone) if alternative_phone else ""

    # Atomic sequential member number
    member_number = generate_next_member_number()

    member = Member(
        member_number=member_number,
        first_name=first_name.strip(),
        middle_name=middle_name.strip() if middle_name else "",
        last_name=last_name.strip(),
        gender=gender,
        date_of_birth=date_of_birth,
        photo=photo,
        phone=normalized_phone,
        alternative_phone=normalized_alt_phone,
        email=email.strip().lower() if email else "",
        address=address.strip() if address else "",
        city=city.strip() if city else "",
        home_branch=home_branch,
        status=Member.MemberStatus.ACTIVE,
        registration_source=registration_source,
        referral_source=referral_source.strip() if referral_source else "",
        created_by=actor,
        updated_by=actor,
    )
    member.save()

    # Initial status history record
    MemberStatusHistory.objects.create(
        member=member,
        previous_status=Member.MemberStatus.ACTIVE,
        new_status=Member.MemberStatus.ACTIVE,
        reason="Initial member registration",
        changed_by=actor,
    )

    # Audit event
    record_event(
        actor,
        "member_registered",
        member,
        {"member_number": member.member_number, "branch_code": home_branch.code},
    )

    return member


@transaction.atomic
def change_member_status(*, actor, member, new_status, reason):
    """
    Change member status with audit history.
    Enforces role restrictions: Receptionists cannot suspend or archive members.
    Suspension requires an explicit reason.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, member.home_branch):
        raise PermissionDenied("You do not have access to this member's branch.")

    # Role restrictions: Receptionist cannot suspend or archive
    if has_role(actor, "Receptionist") and not is_owner(actor) and not has_role(actor, "Branch Manager"):
        if new_status in (Member.MemberStatus.SUSPENDED, Member.MemberStatus.ARCHIVED):
            raise PermissionDenied("Receptionists are not permitted to suspend or archive members.")

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required to change member status."})

    if new_status == Member.MemberStatus.SUSPENDED and len(reason) < 3:
        raise ValidationError({"reason": "A detailed reason is required for suspension."})

    old_status = member.status
    member.status = new_status
    member.updated_by = actor
    member.save(update_fields=["status", "updated_at", "updated_by"])

    MemberStatusHistory.objects.create(
        member=member,
        previous_status=old_status,
        new_status=new_status,
        reason=reason,
        changed_by=actor,
    )

    record_event(
        actor,
        "member_status_changed",
        member,
        {
            "member_number": member.member_number,
            "status": new_status,
            "reason": reason[:100],
        },
    )

    return member


@transaction.atomic
def transfer_home_branch(*, actor, member, new_branch, reason):
    """
    Transfer a member to a new home branch.
    Only Gym Owner, Superuser, or authorized Branch Manager (with scope on both branches) may transfer.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor):
        # Must be Branch Manager with access to source branch and destination branch
        if not has_role(actor, "Branch Manager"):
            raise PermissionDenied("Only a Gym Owner or authorized Branch Manager may transfer member home branches.")
        if not can_access_branch(actor, member.home_branch) or not can_access_branch(actor, new_branch):
            raise PermissionDenied("You must have access to both source and destination branches to authorize a transfer.")

    if not new_branch.is_active:
        raise ValidationError({"new_branch": "Destination branch must be active."})

    if member.home_branch_id == new_branch.pk:
        raise ValidationError({"new_branch": "Member is already assigned to this branch."})

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for a home-branch transfer."})

    old_branch = member.home_branch
    member.home_branch = new_branch
    member.updated_by = actor
    member.save(update_fields=["home_branch", "updated_at", "updated_by"])

    MemberStatusHistory.objects.create(
        member=member,
        previous_status=member.status,
        new_status=member.status,
        reason=f"Home branch transferred from {old_branch.code} to {new_branch.code}: {reason}",
        changed_by=actor,
    )

    record_event(
        actor,
        "member_branch_transferred",
        member,
        {
            "member_number": member.member_number,
            "old_branch_code": old_branch.code,
            "new_branch_code": new_branch.code,
            "reason": reason[:100],
        },
    )

    return member


def archive_member(*, actor, member, reason):
    """Archive a member record preserving historical relationships."""
    return change_member_status(
        actor=actor,
        member=member,
        new_status=Member.MemberStatus.ARCHIVED,
        reason=reason or "Member archived by authorized staff",
    )
