from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils import timezone

from .models import Membership, PackageBranch, MembershipFreeze


@dataclass(frozen=True)
class AccessResult:
    allowed: bool
    reason_code: str
    message: str
    membership: Optional[Membership] = None

    # Denial Reason Codes
    CODE_ALLOWED = "ALLOWED"
    CODE_MEMBER_SUSPENDED = "MEMBER_SUSPENDED"
    CODE_MEMBER_ARCHIVED = "MEMBER_ARCHIVED"
    CODE_MEMBER_INACTIVE = "MEMBER_INACTIVE"
    CODE_NO_ACTIVE_MEMBERSHIP = "NO_ACTIVE_MEMBERSHIP"
    CODE_MEMBERSHIP_PENDING_PAYMENT = "MEMBERSHIP_PENDING_PAYMENT"
    CODE_MEMBERSHIP_EXPIRED = "MEMBERSHIP_EXPIRED"
    CODE_MEMBERSHIP_FROZEN = "MEMBERSHIP_FROZEN"
    CODE_MEMBERSHIP_NOT_YET_STARTED = "MEMBERSHIP_NOT_YET_STARTED"
    CODE_CROSS_BRANCH_UNAUTHORIZED = "CROSS_BRANCH_UNAUTHORIZED"
    CODE_BRANCH_NOT_ELIGIBLE = "BRANCH_NOT_ELIGIBLE"
    CODE_VISIT_LIMIT_EXCEEDED = "VISIT_LIMIT_EXCEEDED"


def can_access_branch(member, branch, at_datetime=None) -> AccessResult:
    """
    Core branch access eligibility engine.
    Evaluates profile status, active memberships, payment clearance,
    approved freeze windows, and cross-branch permissions.
    """
    if at_datetime is None:
        at_datetime = timezone.now()

    tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Africa/Addis_Ababa")

    if isinstance(at_datetime, datetime):
        current_date = at_datetime.astimezone(tz).date()
    else:
        current_date = at_datetime

    # 1. Member Profile Status Checks
    if not member:
        return AccessResult(False, AccessResult.CODE_NO_ACTIVE_MEMBERSHIP, "Member record not provided.")

    if member.status == "SUSPENDED":
        return AccessResult(False, AccessResult.CODE_MEMBER_SUSPENDED, f"Member profile is suspended: {member.member_number}.")

    if member.status == "ARCHIVED":
        return AccessResult(False, AccessResult.CODE_MEMBER_ARCHIVED, f"Member profile is archived: {member.member_number}.")

    if member.status == "INACTIVE":
        return AccessResult(False, AccessResult.CODE_MEMBER_INACTIVE, f"Member profile is inactive: {member.member_number}.")

    # 2. Query memberships for this member that could be valid
    # Start date inclusive, end date exclusive
    memberships = (
        Membership.objects.filter(
            member=member,
            status__in=[Membership.Status.ACTIVE, Membership.Status.FROZEN, Membership.Status.PENDING],
        )
        .select_related("package", "home_branch")
        .order_by("-start_date")
    )

    if not memberships.exists():
        # Check if there are expired ones to provide accurate message
        has_expired = Membership.objects.filter(member=member, status=Membership.Status.EXPIRED).exists()
        if has_expired:
            return AccessResult(False, AccessResult.CODE_MEMBERSHIP_EXPIRED, "Membership has expired. Renewal required.")
        return AccessResult(False, AccessResult.CODE_NO_ACTIVE_MEMBERSHIP, "No active membership found for this member.")

    # Find candidate membership covering current_date
    eligible_candidate = None
    for m in memberships:
        if m.start_date <= current_date < m.end_date:
            eligible_candidate = m
            break
        elif current_date < m.start_date and m.status == Membership.Status.ACTIVE:
            return AccessResult(
                False,
                AccessResult.CODE_MEMBERSHIP_NOT_YET_STARTED,
                f"Membership {m.membership_number} starts on {m.start_date}.",
                membership=m,
            )

    if not eligible_candidate:
        return AccessResult(False, AccessResult.CODE_MEMBERSHIP_EXPIRED, "No membership currently covers today's date.")

    m = eligible_candidate

    # 3. Payment Clearance Check
    if m.payment_clearance_status == Membership.PaymentClearance.PENDING or m.status == Membership.Status.PENDING:
        return AccessResult(
            False,
            AccessResult.CODE_MEMBERSHIP_PENDING_PAYMENT,
            f"Membership {m.membership_number} is pending billing clearance.",
            membership=m,
        )

    # 4. Freeze Status & Active Freeze Window Checks
    if m.status == Membership.Status.FROZEN:
        return AccessResult(
            False,
            AccessResult.CODE_MEMBERSHIP_FROZEN,
            f"Membership {m.membership_number} is currently frozen.",
            membership=m,
        )

    is_frozen_now = MembershipFreeze.objects.filter(
        membership=m,
        status=MembershipFreeze.FreezeStatus.APPROVED,
        approved_start_date__lte=current_date,
        approved_end_date__gt=current_date,
    ).exists()

    if is_frozen_now:
        return AccessResult(
            False,
            AccessResult.CODE_MEMBERSHIP_FROZEN,
            f"Membership {m.membership_number} is within an approved freeze window.",
            membership=m,
        )

    # 5. Branch Eligibility & Cross-Branch Access Policy
    is_home_branch = (m.home_branch_id == branch.pk)
    if is_home_branch:
        return AccessResult(True, AccessResult.CODE_ALLOWED, "Access granted for home branch.", membership=m)

    # Cross-branch evaluation
    if not m.cross_branch_snapshot:
        return AccessResult(
            False,
            AccessResult.CODE_CROSS_BRANCH_UNAUTHORIZED,
            f"Package '{m.package.name}' does not permit cross-branch access.",
            membership=m,
        )

    # Check if target branch is explicitly enabled in package branch mapping
    branch_authorized = PackageBranch.objects.filter(
        package=m.package,
        branch=branch,
        is_enabled=True,
    ).exists()

    if not branch_authorized:
        return AccessResult(
            False,
            AccessResult.CODE_BRANCH_NOT_ELIGIBLE,
            f"Branch '{branch.name}' is not authorized for package '{m.package.name}'.",
            membership=m,
        )

    return AccessResult(
        True,
        AccessResult.CODE_ALLOWED,
        f"Cross-branch access authorized at {branch.name}.",
        membership=m,
    )
