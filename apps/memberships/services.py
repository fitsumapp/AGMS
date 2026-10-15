from datetime import date, timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import has_role, is_owner
from apps.branches.audit import record_event
from apps.branches.services import can_access_branch
from .dates import compute_membership_end_date, get_gym_today
from .models import (
    Membership,
    MembershipAdjustment,
    MembershipEvent,
    MembershipFreeze,
    MembershipPackage,
    MembershipRenewal,
    PackageBranch,
)


@transaction.atomic
def create_membership(
    *,
    actor,
    member,
    package,
    home_branch,
    start_date=None,
    is_complimentary=False,
    complimentary_reason="",
):
    """
    Register a new membership for a member.
    Enforces branch authorization, active package status, branch eligibility,
    member profile validity, and prevents overlapping active/pending memberships.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required to register memberships.")

    if not is_owner(actor) and not can_access_branch(actor, home_branch):
        raise PermissionDenied("You are not authorized to create memberships for this branch.")

    if not home_branch.is_active:
        raise ValidationError({"home_branch": "Cannot assign a membership to an inactive branch."})

    if not package.is_active:
        raise ValidationError({"package": f"Package '{package.name}' is currently inactive."})

    # Validate branch eligibility for package
    branch_authorized = PackageBranch.objects.filter(
        package=package,
        branch=home_branch,
        is_enabled=True,
    ).exists()
    if not branch_authorized:
        raise ValidationError({"package": f"Package '{package.name}' is not authorized for branch '{home_branch.code}'."})

    # Validate member profile status
    if member.status in ("SUSPENDED", "ARCHIVED"):
        raise ValidationError({"member": f"Cannot create membership for {member.status.lower()} member profile."})

    if start_date is None:
        start_date = get_gym_today()

    end_date = compute_membership_end_date(start_date, package.duration_unit, package.duration_value)

    # Concurrency lock on member to prevent overlapping active memberships
    from apps.members.models import Member
    Member.objects.select_for_update().filter(pk=member.pk).values("id").first()

    overlapping = Membership.objects.filter(
        member=member,
        status__in=[Membership.Status.ACTIVE, Membership.Status.PENDING, Membership.Status.FROZEN],
        start_date__lt=end_date,
        end_date__gt=start_date,
    ).exists()

    if overlapping:
        raise ValidationError("Member already has an active, pending, or frozen membership during this date window.")

    # Complimentary check
    if is_complimentary:
        if not is_owner(actor) and not actor.is_superuser:
            raise PermissionDenied("Only Gym Owners or Superusers can authorize complimentary memberships.")
        complimentary_reason = (complimentary_reason or "").strip()
        if not complimentary_reason:
            raise ValidationError({"complimentary_reason": "Reason is required for complimentary memberships."})

        init_status = Membership.Status.ACTIVE
        activation_stat = Membership.ActivationStatus.ACTIVATED
        payment_stat = Membership.PaymentClearance.WAIVED_COMPLIMENTARY
        act_at = timezone.now()
    else:
        # Standard paid membership starts PENDING billing clearance
        init_status = Membership.Status.PENDING
        activation_stat = Membership.ActivationStatus.NOT_ACTIVATED
        payment_stat = Membership.PaymentClearance.PENDING
        act_at = None

    membership = Membership(
        member=member,
        package=package,
        home_branch=home_branch,
        start_date=start_date,
        end_date=end_date,
        status=init_status,
        price_snapshot=package.price,
        duration_unit_snapshot=package.duration_unit,
        duration_value_snapshot=package.duration_value,
        allowed_visits_snapshot=package.allowed_visits,
        cross_branch_snapshot=package.cross_branch_enabled,
        activation_status=activation_stat,
        payment_clearance_status=payment_stat,
        activated_at=act_at,
        created_by=actor,
        updated_by=actor,
    )
    membership.save()

    # Log Membership Created Event
    MembershipEvent.objects.create(
        membership=membership,
        event_type=MembershipEvent.EventType.CREATED,
        actor=actor,
        metadata={
            "package_code": package.code,
            "package_name": package.name,
            "price": str(package.price),
            "start_date": str(start_date),
            "end_date": str(end_date),
            "is_complimentary": is_complimentary,
            "complimentary_reason": complimentary_reason if is_complimentary else None,
        },
    )

    record_event(
        actor,
        "membership_created",
        membership,
        {
            "membership_number": membership.membership_number,
            "member_number": member.member_number,
            "package_code": package.code,
            "branch_code": home_branch.code,
        },
    )

    if not is_complimentary:
        try:
            from apps.memberships.contracts import BillingContractService
            BillingContractService.create_membership_invoice(membership, actor=actor)
        except Exception:
            pass

    return membership


@transaction.atomic
def activate_membership(*, actor, membership, payment_reference=None, is_complimentary=False, bypass_reason=None, reason=None):
    """
    Centralized membership activation after server-side billing clearance
    or authorized complimentary approval.
    Transitions PENDING -> ACTIVE.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if membership.status != Membership.Status.PENDING:
        raise ValidationError(f"Only PENDING memberships can be activated. Current status: {membership.status}")

    # Check member profile
    if membership.member.status in ("SUSPENDED", "ARCHIVED"):
        raise ValidationError("Cannot activate membership for a suspended or archived member profile.")

    if is_complimentary:
        if not is_owner(actor) and not actor.is_superuser:
            raise PermissionDenied("Only Gym Owners or Superusers can authorize complimentary activation.")
        membership.payment_clearance_status = Membership.PaymentClearance.WAIVED_COMPLIMENTARY
    else:
        membership.payment_clearance_status = Membership.PaymentClearance.CLEARED

    membership.status = Membership.Status.ACTIVE
    membership.activation_status = Membership.ActivationStatus.ACTIVATED
    membership.activated_at = timezone.now()
    membership.updated_by = actor
    membership.save(update_fields=[
        "status",
        "activation_status",
        "payment_clearance_status",
        "activated_at",
        "updated_at",
        "updated_by",
    ])

    MembershipEvent.objects.create(
        membership=membership,
        event_type=MembershipEvent.EventType.ACTIVATED,
        actor=actor,
        metadata={
            "payment_reference": payment_reference or "N/A",
            "is_complimentary": is_complimentary,
            "bypass_reason": bypass_reason or reason or ("Complimentary grant" if is_complimentary else None),
            "activated_at": str(membership.activated_at),
        },
    )

    record_event(
        actor,
        "membership_activated",
        membership,
        {
            "membership_number": membership.membership_number,
            "payment_clearance": membership.payment_clearance_status,
        },
    )

    return membership


@transaction.atomic
def request_freeze(*, actor, membership, start_date=None, end_date=None, reason="", requested_start=None, requested_end=None):
    """
    Submit a freeze request for an active membership.
    Freeze requests require manager approval before taking effect.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    start_date = start_date or requested_start
    end_date = end_date or requested_end
    if not start_date or not end_date:
        raise ValidationError("Freeze start date and end date are required.")

    if membership.status != Membership.Status.ACTIVE:
        raise ValidationError(f"Only ACTIVE memberships can be frozen. Current status: {membership.status}")

    package = membership.package
    if not package.freeze_allowed:
        raise ValidationError("Freezing is not allowed for this membership package.")

    if end_date <= start_date:
        raise ValidationError("Freeze end date must be strictly after start date.")

    freeze_days = (end_date - start_date).days

    # Enforce package freeze limits
    if package.max_freeze_days and freeze_days > package.max_freeze_days:
        raise ValidationError(f"Requested freeze ({freeze_days} days) exceeds package limit of {package.max_freeze_days} days.")

    # Check previous approved freeze counts
    approved_count = MembershipFreeze.objects.filter(
        membership=membership,
        status=MembershipFreeze.FreezeStatus.APPROVED,
    ).count()
    if package.max_freeze_requests and approved_count >= package.max_freeze_requests:
        raise ValidationError(f"Maximum freeze requests ({package.max_freeze_requests}) already reached for this membership.")

    # Check overlapping pending or approved freezes
    overlapping = MembershipFreeze.objects.filter(
        membership=membership,
        status__in=[MembershipFreeze.FreezeStatus.REQUESTED, MembershipFreeze.FreezeStatus.APPROVED],
        requested_start_date__lt=end_date,
        requested_end_date__gt=start_date,
    ).exists()
    if overlapping:
        raise ValidationError("An active or pending freeze request already overlaps with these dates.")

    freeze = MembershipFreeze.objects.create(
        membership=membership,
        requested_start_date=start_date,
        requested_end_date=end_date,
        freeze_days=freeze_days,
        reason=reason.strip(),
        status=MembershipFreeze.FreezeStatus.REQUESTED,
        requested_by=actor,
    )

    record_event(
        actor,
        "membership_freeze_requested",
        membership,
        {
            "membership_number": membership.membership_number,
            "freeze_days": freeze_days,
            "start_date": str(start_date),
            "end_date": str(end_date),
        },
    )

    return freeze


@transaction.atomic
def approve_freeze(*, actor, freeze, approved_start_date=None, approved_end_date=None):
    """
    Approve a pending freeze request.
    Only authorized Manager or Gym Owner can approve.
    Extends membership expiry date exactly once and updates membership status.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor) and not has_role(actor, "Branch Manager"):
        raise PermissionDenied("Only a Gym Owner or Branch Manager can approve freeze requests.")

    membership = Membership.objects.select_for_update().get(pk=freeze.membership_id)

    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if freeze.status != MembershipFreeze.FreezeStatus.REQUESTED:
        raise ValidationError(f"Only REQUESTED freezes can be approved. Current status: {freeze.status}")

    start_d = approved_start_date or freeze.requested_start_date
    end_d = approved_end_date or freeze.requested_end_date
    if end_d <= start_d:
        raise ValidationError("Approved end date must be after approved start date.")

    freeze_days = (end_d - start_d).days

    # Lock & verify no other approved freeze overlaps
    conflict = MembershipFreeze.objects.filter(
        membership=membership,
        status=MembershipFreeze.FreezeStatus.APPROVED,
        approved_start_date__lt=end_d,
        approved_end_date__gt=start_d,
    ).exclude(pk=freeze.pk).exists()
    if conflict:
        raise ValidationError("Another approved freeze already conflicts with these approved dates.")

    # Extend membership exclusive end date by freeze_days exactly once
    old_end_date = membership.end_date
    new_end_date = old_end_date + timedelta(days=freeze_days)
    membership.end_date = new_end_date

    # If current date falls within freeze window, update membership status to FROZEN
    today = get_gym_today()
    if start_d <= today < end_d:
        membership.status = Membership.Status.FROZEN

    membership.updated_by = actor
    membership.save(update_fields=["end_date", "status", "updated_at", "updated_by"])

    # Update freeze record
    freeze.approved_start_date = start_d
    freeze.approved_end_date = end_d
    freeze.freeze_days = freeze_days
    freeze.status = MembershipFreeze.FreezeStatus.APPROVED
    freeze.approved_by = actor
    freeze.approved_at = timezone.now()
    freeze.save(update_fields=[
        "approved_start_date",
        "approved_end_date",
        "freeze_days",
        "status",
        "approved_by",
        "approved_at",
        "updated_at",
    ])

    # Record event
    MembershipEvent.objects.create(
        membership=membership,
        event_type=MembershipEvent.EventType.FROZEN,
        actor=actor,
        metadata={
            "freeze_id": str(freeze.pk),
            "approved_start_date": str(start_d),
            "approved_end_date": str(end_d),
            "freeze_days": freeze_days,
            "old_end_date": str(old_end_date),
            "new_end_date": str(new_end_date),
        },
    )

    record_event(
        actor,
        "membership_freeze_approved",
        membership,
        {
            "membership_number": membership.membership_number,
            "freeze_days": freeze_days,
            "new_end_date": str(new_end_date),
        },
    )

    return freeze


@transaction.atomic
def reject_freeze(*, actor, freeze, rejection_reason):
    """Reject a pending freeze request with an explicit reason."""
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor) and not has_role(actor, "Branch Manager"):
        raise PermissionDenied("Only a Gym Owner or Branch Manager can reject freeze requests.")

    membership = freeze.membership
    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if freeze.status != MembershipFreeze.FreezeStatus.REQUESTED:
        raise ValidationError(f"Only REQUESTED freezes can be rejected. Current status: {freeze.status}")

    rejection_reason = (rejection_reason or "").strip()
    if not rejection_reason:
        raise ValidationError({"rejection_reason": "Rejection reason is required."})

    freeze.status = MembershipFreeze.FreezeStatus.REJECTED
    freeze.approved_by = actor
    freeze.rejection_reason = rejection_reason
    freeze.save(update_fields=["status", "approved_by", "rejection_reason", "updated_at"])

    record_event(
        actor,
        "membership_freeze_rejected",
        membership,
        {
            "membership_number": membership.membership_number,
            "rejection_reason": rejection_reason[:100],
        },
    )

    return freeze


@transaction.atomic
def resume_freeze(*, actor, freeze):
    """Resume a frozen membership back to ACTIVE once the freeze period concludes."""
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    membership = Membership.objects.select_for_update().get(pk=freeze.membership_id)
    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if freeze.status != MembershipFreeze.FreezeStatus.APPROVED:
        raise ValidationError("Only APPROVED freezes can be resumed.")

    freeze.status = MembershipFreeze.FreezeStatus.COMPLETED
    freeze.save(update_fields=["status", "updated_at"])

    if membership.status == Membership.Status.FROZEN:
        membership.status = Membership.Status.ACTIVE
        membership.updated_by = actor
        membership.save(update_fields=["status", "updated_at", "updated_by"])

        MembershipEvent.objects.create(
            membership=membership,
            event_type=MembershipEvent.EventType.RESUMED,
            actor=actor,
            metadata={"freeze_id": str(freeze.pk)},
        )

    return freeze


@transaction.atomic
def request_renewal(
    *,
    actor,
    membership,
    idempotency_key,
    new_package=None,
    new_start_date=None,
    billing_reference="",
    is_complimentary=False,
):
    """
    Create a membership renewal request.
    If membership is currently valid (end_date > today), extends from current end_date.
    If expired (end_date <= today), calculates new period starting from new_start_date or today.
    Idempotent: prevents duplicate extensions for the same key.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    # Idempotency check: if renewal with this key exists, return it
    existing_renewal = MembershipRenewal.objects.filter(idempotency_key=idempotency_key).first()
    if existing_renewal:
        return existing_renewal

    package = new_package or membership.package
    if not package.is_active:
        raise ValidationError(f"Package '{package.name}' is inactive.")

    today = get_gym_today()
    if new_start_date:
        base_start = new_start_date
    elif membership.end_date > today and membership.status in (Membership.Status.ACTIVE, Membership.Status.FROZEN):
        # Early renewal: extend from current exclusive end date
        base_start = membership.end_date
    else:
        # Expired renewal: start from today
        base_start = today

    new_end = compute_membership_end_date(base_start, package.duration_unit, package.duration_value)

    renewal = MembershipRenewal.objects.create(
        membership=membership,
        previous_end_date=membership.end_date,
        new_end_date=new_end,
        duration_unit_snapshot=package.duration_unit,
        duration_value_snapshot=package.duration_value,
        price_snapshot=package.price,
        renewal_status=MembershipRenewal.RenewalStatus.PENDING_BILLING,
        billing_reference=billing_reference,
        idempotency_key=idempotency_key,
        requested_by=actor,
    )

    record_event(
        actor,
        "membership_renewal_requested",
        membership,
        {
            "membership_number": membership.membership_number,
            "idempotency_key": idempotency_key,
            "new_end_date": str(new_end),
        },
    )

    if is_complimentary:
        if not is_owner(actor) and not actor.is_superuser:
            raise PermissionDenied("Only Gym Owners or Superusers can authorize complimentary renewals.")
        return complete_renewal(actor=actor, renewal=renewal, is_complimentary=True)

    try:
        from apps.billing.integrations import MembershipBillingIntegration
        MembershipBillingIntegration.create_renewal_invoice(renewal=renewal, actor=actor)
    except Exception:
        pass

    return renewal


@transaction.atomic
def complete_renewal(*, actor, renewal, billing_reference=None, is_complimentary=False):
    """
    Complete an approved renewal following payment clearance.
    Updates membership end date atomically.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    membership = Membership.objects.select_for_update().get(pk=renewal.membership_id)
    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if renewal.renewal_status == MembershipRenewal.RenewalStatus.COMPLETED:
        # Already completed (idempotent)
        return renewal

    if renewal.renewal_status != MembershipRenewal.RenewalStatus.PENDING_BILLING:
        raise ValidationError(f"Renewal is in state {renewal.renewal_status} and cannot be completed.")

    old_end = membership.end_date
    membership.end_date = renewal.new_end_date

    # If membership was EXPIRED or PENDING, return to ACTIVE
    if membership.status in (Membership.Status.EXPIRED, Membership.Status.PENDING):
        membership.status = Membership.Status.ACTIVE

    if is_complimentary:
        membership.payment_clearance_status = Membership.PaymentClearance.WAIVED_COMPLIMENTARY
    else:
        membership.payment_clearance_status = Membership.PaymentClearance.CLEARED
    membership.updated_by = actor
    membership.save(update_fields=["end_date", "status", "payment_clearance_status", "updated_at", "updated_by"])

    renewal.renewal_status = MembershipRenewal.RenewalStatus.COMPLETED
    renewal.approved_by = actor
    renewal.completed_at = timezone.now()
    if billing_reference:
        renewal.billing_reference = billing_reference
    renewal.save(update_fields=["renewal_status", "approved_by", "completed_at", "billing_reference"])

    MembershipEvent.objects.create(
        membership=membership,
        event_type=MembershipEvent.EventType.RENEWED,
        actor=actor,
        metadata={
            "renewal_id": str(renewal.pk),
            "old_end_date": str(old_end),
            "new_end_date": str(renewal.new_end_date),
            "price": str(renewal.price_snapshot),
        },
    )

    record_event(
        actor,
        "membership_renewal_completed",
        membership,
        {
            "membership_number": membership.membership_number,
            "new_end_date": str(renewal.new_end_date),
        },
    )

    return renewal


@transaction.atomic
def cancel_membership(*, actor, membership, reason):
    """Cancel a membership with audit reason."""
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Authentication required.")

    if not is_owner(actor) and not has_role(actor, "Branch Manager"):
        raise PermissionDenied("Only a Gym Owner or Branch Manager can cancel memberships.")

    if not is_owner(actor) and not can_access_branch(actor, membership.home_branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if membership.status == Membership.Status.CANCELLED:
        return membership

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A cancellation reason is required."})

    membership.status = Membership.Status.CANCELLED
    membership.cancelled_at = timezone.now()
    membership.cancellation_reason = reason
    membership.updated_by = actor
    membership.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at", "updated_by"])

    MembershipEvent.objects.create(
        membership=membership,
        event_type=MembershipEvent.EventType.CANCELLED,
        actor=actor,
        metadata={"reason": reason},
    )

    record_event(
        actor,
        "membership_cancelled",
        membership,
        {"membership_number": membership.membership_number, "reason": reason[:100]},
    )

    return membership


@transaction.atomic
def sync_expired_memberships():
    """
    Periodic or on-demand service transitioning ACTIVE memberships
    whose exclusive end_date <= today to EXPIRED.
    """
    today = get_gym_today()
    to_expire = Membership.objects.filter(
        status=Membership.Status.ACTIVE,
        end_date__lte=today,
    )
    count = 0
    for m in to_expire:
        m.status = Membership.Status.EXPIRED
        m.save(update_fields=["status", "updated_at"])
        MembershipEvent.objects.create(
            membership=m,
            event_type=MembershipEvent.EventType.EXPIRED,
            metadata={"expired_on": str(today)},
        )
        count += 1
    return count
