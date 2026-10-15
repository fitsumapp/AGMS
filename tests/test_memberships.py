from datetime import date, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import StaffAccount, User
from apps.accounts.roles import seed_roles
from apps.branches.models import Branch, UserBranchAccess
from apps.members.models import Member
from apps.members.services import register_member, change_member_status
from apps.memberships.dates import (
    add_calendar_months,
    add_calendar_years,
    compute_membership_end_date,
    get_gym_today,
)
from apps.memberships.eligibility import (
    AccessResult,
    can_access_branch,
)
from apps.memberships.models import (
    Membership,
    MembershipAdjustment,
    MembershipEvent,
    MembershipFreeze,
    MembershipPackage,
    MembershipRenewal,
    PackageBranch,
)
from apps.memberships.selectors import get_membership_metrics
from apps.memberships.services import (
    activate_membership,
    approve_freeze,
    cancel_membership,
    create_membership,
    reject_freeze,
    request_freeze,
    request_renewal,
    sync_expired_memberships,
)

pytestmark = pytest.mark.django_db


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture(autouse=True)
def setup_roles():
    seed_roles()


@pytest.fixture
def gym_owner():
    user = User.objects.create_user(
        email="owner@gym.test",
        first_name="Gym",
        last_name="Owner",
        password="ValidPassword123!",
        is_staff=True,
    )
    user.groups.add(user.groups.model.objects.get(name="Gym Owner"))
    return user


@pytest.fixture
def branch_manager_a(branch_a):
    user = User.objects.create_user(
        email="manager.a@gym.test",
        first_name="Manager",
        last_name="Alpha",
        password="ValidPassword123!",
        is_staff=True,
    )
    user.groups.add(user.groups.model.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=user, branch=branch_a, is_active=True, is_primary=True)
    return user


@pytest.fixture
def receptionist_a(branch_a):
    user = User.objects.create_user(
        email="receptionist.a@gym.test",
        first_name="Receptionist",
        last_name="Alpha",
        password="ValidPassword123!",
        is_staff=True,
    )
    user.groups.add(user.groups.model.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=user, branch=branch_a, is_active=True, is_primary=True)
    return user


@pytest.fixture
def branch_a():
    return Branch.objects.create(code="ADD-A", name="Addis Central", address="Bole Road, Addis Ababa", city="Addis Ababa", is_active=True)


@pytest.fixture
def branch_b():
    return Branch.objects.create(code="BOLE-B", name="Bole Branch", address="Bole Medhanealem, Addis Ababa", city="Addis Ababa", is_active=True)


@pytest.fixture
def member_a(gym_owner, branch_a):
    return register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Abebe",
        last_name="Bikila",
        phone="0911111111",
    )


@pytest.fixture
def standard_monthly_package(branch_a):
    pkg = MembershipPackage.objects.create(
        code="STD-MONTHLY",
        name="Standard Monthly",
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        duration_value=1,
        price=Decimal("1500.00"),
        cross_branch_enabled=False,
        freeze_allowed=True,
        max_freeze_days=14,
        max_freeze_requests=2,
    )
    PackageBranch.objects.create(package=pkg, branch=branch_a, is_enabled=True)
    return pkg


@pytest.fixture
def multi_branch_package(branch_a, branch_b):
    pkg = MembershipPackage.objects.create(
        code="VIP-MULTI",
        name="VIP Multi-Branch",
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        duration_value=3,
        price=Decimal("4000.00"),
        cross_branch_enabled=True,
        freeze_allowed=True,
        max_freeze_days=30,
        max_freeze_requests=3,
    )
    PackageBranch.objects.create(package=pkg, branch=branch_a, is_enabled=True)
    PackageBranch.objects.create(package=pkg, branch=branch_b, is_enabled=True)
    return pkg


# ==============================================================================
# 1. Date Arithmetic & Calendar Semantics
# ==============================================================================

def test_calendar_date_arithmetic_months_and_years():
    # Regular month addition
    d1 = date(2026, 1, 15)
    assert add_calendar_months(d1, 1) == date(2026, 2, 15)
    assert add_calendar_months(d1, 6) == date(2026, 7, 15)

    # Month-end clamping (January 31 to February in non-leap year 2025)
    assert add_calendar_months(date(2025, 1, 31), 1) == date(2025, 2, 28)

    # Leap year month-end clamping (January 31 to February in leap year 2028)
    assert add_calendar_months(date(2028, 1, 31), 1) == date(2028, 2, 29)

    # Leap day February 29 + 1 year = February 28
    assert add_calendar_years(date(2028, 2, 29), 1) == date(2029, 2, 28)
    assert add_calendar_years(date(2028, 2, 29), 4) == date(2032, 2, 29)


def test_membership_end_date_calculation():
    # Prompt example: Monthly starting Oct 14, 2026 -> exclusive end Nov 14, 2026
    start = date(2026, 10, 14)
    end = compute_membership_end_date(start, MembershipPackage.DurationUnit.MONTH, 1)
    assert end == date(2026, 11, 14)

    # Days
    assert compute_membership_end_date(start, MembershipPackage.DurationUnit.DAY, 7) == date(2026, 10, 21)

    # Weeks
    assert compute_membership_end_date(start, MembershipPackage.DurationUnit.WEEK, 2) == date(2026, 10, 28)

    # Year
    assert compute_membership_end_date(start, MembershipPackage.DurationUnit.YEAR, 1) == date(2027, 10, 14)


# ==============================================================================
# 2. Package Management & Validation
# ==============================================================================

def test_package_creation_and_negative_price_rejected():
    pkg = MembershipPackage.objects.create(
        code="TEST-PKG",
        name="Test Package",
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        duration_value=1,
        price=Decimal("1000.00"),
    )
    assert pkg.price == Decimal("1000.00")
    assert pkg.currency == "ETB"

    # Reject negative price at validation / DB constraint level
    with pytest.raises((ValidationError, IntegrityError)):
        with transaction.atomic():
            MembershipPackage.objects.create(
                code="NEG-PRICE",
                name="Negative Price",
                duration_unit=MembershipPackage.DurationUnit.MONTH,
                duration_value=1,
                price=Decimal("-10.00"),
            )

    # Reject zero or negative duration at validation / DB constraint level
    with pytest.raises((ValidationError, IntegrityError)):
        with transaction.atomic():
            MembershipPackage.objects.create(
                code="ZERO-DUR",
                name="Zero Duration",
                duration_unit=MembershipPackage.DurationUnit.MONTH,
                duration_value=0,
                price=Decimal("100.00"),
            )


def test_package_deactivation_does_not_affect_existing_memberships(gym_owner, member_a, standard_monthly_package, branch_a):
    membership = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=membership, is_complimentary=True, bypass_reason="Owner authorized")

    # Deactivate package
    standard_monthly_package.is_active = False
    standard_monthly_package.save()

    membership.refresh_from_db()
    assert membership.status == Membership.Status.ACTIVE
    assert membership.price_snapshot == Decimal("1500.00")


# ==============================================================================
# 3. Membership Assignment & Pending Activation
# ==============================================================================

def test_create_membership_starts_in_pending_state(gym_owner, member_a, standard_monthly_package, branch_a):
    membership = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )

    assert membership.status == Membership.Status.PENDING
    assert membership.activation_status == Membership.ActivationStatus.NOT_ACTIVATED
    assert membership.payment_clearance_status == Membership.PaymentClearance.PENDING
    assert membership.membership_number.startswith("MSHIP-")
    assert membership.price_snapshot == Decimal("1500.00")
    assert membership.end_date == date(2026, 11, 14)

    # Audit event created
    assert MembershipEvent.objects.filter(membership=membership, event_type=MembershipEvent.EventType.CREATED).exists()


def test_create_membership_rejects_ineligible_branch(gym_owner, member_a, standard_monthly_package, branch_b):
    with pytest.raises(ValidationError, match="not authorized for branch"):
        create_membership(
            actor=gym_owner,
            member=member_a,
            package=standard_monthly_package,
            home_branch=branch_b,
            start_date=date(2026, 10, 14),
        )


def test_receptionist_cannot_activate_without_billing_or_owner_approval(receptionist_a, member_a, standard_monthly_package, branch_a):
    membership = create_membership(
        actor=receptionist_a,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )

    # Receptionist trying complimentary bypass without owner role must fail
    with pytest.raises(PermissionDenied, match="Only Gym Owners or Superusers"):
        activate_membership(
            actor=receptionist_a,
            membership=membership,
            is_complimentary=True,
            bypass_reason="Attempted free pass",
        )


def test_authorized_complimentary_activation(gym_owner, member_a, standard_monthly_package, branch_a):
    membership = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )

    activate_membership(
        actor=gym_owner,
        membership=membership,
        is_complimentary=True,
        bypass_reason="Executive VIP grant",
    )

    membership.refresh_from_db()
    assert membership.status == Membership.Status.ACTIVE
    assert membership.payment_clearance_status == Membership.PaymentClearance.WAIVED_COMPLIMENTARY
    assert membership.activated_at is not None

    # Audit event created
    assert MembershipEvent.objects.filter(membership=membership, event_type=MembershipEvent.EventType.ACTIVATED).exists()


def test_prevent_conflicting_overlapping_active_memberships(gym_owner, member_a, standard_monthly_package, branch_a):
    m1 = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m1, is_complimentary=True, bypass_reason="First membership")

    # Trying to assign another active membership overlapping the same period
    with pytest.raises(ValidationError, match="already has an active"):
        create_membership(
            actor=gym_owner,
            member=member_a,
            package=standard_monthly_package,
            home_branch=branch_a,
            start_date=date(2026, 10, 20),
        )


# ==============================================================================
# 4. Renewal Workflow & Idempotency
# ==============================================================================

def test_early_renewal_extends_from_existing_end_date(gym_owner, member_a, standard_monthly_package, branch_a):
    # Starts Oct 14, 2026 -> Exclusive end Nov 14, 2026
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active base")

    assert m.end_date == date(2026, 11, 14)

    # Early renewal on Oct 25 (while still active) extends from Nov 14 -> Dec 14
    renewal = request_renewal(
        actor=gym_owner,
        membership=m,
        idempotency_key="RENEWAL-OCT-2026-001",
        is_complimentary=True,
    )

    assert renewal.renewal_status == MembershipRenewal.RenewalStatus.COMPLETED
    assert renewal.previous_end_date == date(2026, 11, 14)
    assert renewal.new_end_date == date(2026, 12, 14)

    m.refresh_from_db()
    assert m.end_date == date(2026, 12, 14)


def test_renewal_idempotency_prevents_duplicate_extension(gym_owner, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Base")

    # First request
    r1 = request_renewal(
        actor=gym_owner,
        membership=m,
        idempotency_key="IDEMPOTENT-KEY-ABC",
        is_complimentary=True,
    )
    m.refresh_from_db()
    first_end_date = m.end_date
    assert first_end_date == date(2026, 12, 14)

    # Repeat request with same idempotency key
    r2 = request_renewal(
        actor=gym_owner,
        membership=m,
        idempotency_key="IDEMPOTENT-KEY-ABC",
        is_complimentary=True,
    )

    m.refresh_from_db()
    assert r1.id == r2.id
    # End date must NOT have extended twice!
    assert m.end_date == first_end_date


def test_expired_renewal_starts_from_approved_start_date(gym_owner, member_a, standard_monthly_package, branch_a):
    # Expired membership: Aug 1 to Sep 1
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 8, 1),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Past")
    m.status = Membership.Status.EXPIRED
    m.save()

    # Renewing on Oct 14
    renewal = request_renewal(
        actor=gym_owner,
        membership=m,
        new_start_date=date(2026, 10, 14),
        idempotency_key="EXPIRED-RENEW-001",
        is_complimentary=True,
    )

    m.refresh_from_db()
    assert m.status == Membership.Status.ACTIVE
    assert m.end_date == date(2026, 11, 14)


# ==============================================================================
# 5. Freeze Request & Approval Workflow
# ==============================================================================

def test_freeze_request_workflow_and_manager_approval(gym_owner, branch_manager_a, receptionist_a, member_a, standard_monthly_package, branch_a):
    # Setup active membership: Oct 14 to Nov 14 (31 days)
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")
    original_end_date = m.end_date

    # Receptionist can request freeze (7 days: Oct 20 to Oct 27)
    freeze = request_freeze(
        actor=receptionist_a,
        membership=m,
        requested_start=date(2026, 10, 20),
        requested_end=date(2026, 10, 27),
        reason="Medical leave",
    )
    assert freeze.status == MembershipFreeze.Status.REQUESTED
    m.refresh_from_db()
    # End date unchanged before approval
    assert m.end_date == original_end_date

    # Receptionist cannot approve freeze
    with pytest.raises(PermissionDenied, match="Only a Gym Owner or Branch Manager"):
        approve_freeze(actor=receptionist_a, freeze=freeze)

    # Manager approves freeze
    approve_freeze(actor=branch_manager_a, freeze=freeze)

    freeze.refresh_from_db()
    assert freeze.status == MembershipFreeze.Status.APPROVED
    assert freeze.approved_by == branch_manager_a
    assert freeze.approved_start_date == date(2026, 10, 20)
    assert freeze.approved_end_date == date(2026, 10, 27)

    # Expiry extended by exactly 7 days
    m.refresh_from_db()
    assert m.end_date == original_end_date + timedelta(days=7)

    # Approving again cannot double-extend
    with pytest.raises(ValidationError, match="Only REQUESTED freezes can be approved"):
        approve_freeze(actor=branch_manager_a, freeze=freeze)

    m.refresh_from_db()
    assert m.end_date == original_end_date + timedelta(days=7)


def test_freeze_request_exceeding_package_limits_rejected(gym_owner, branch_manager_a, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=branch_manager_a,
        member=member_a,
        package=standard_monthly_package,  # max 14 days, max 2 requests
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")

    # Requesting 20 days when max allowed is 14 days
    with pytest.raises(ValidationError, match="exceeds package limit"):
        request_freeze(
            actor=branch_manager_a,
            membership=m,
            requested_start=date(2026, 10, 15),
            requested_end=date(2026, 11, 4),
            reason="Exceeding allowance",
        )


def test_prevent_overlapping_freezes(gym_owner, branch_manager_a, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=branch_manager_a,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")

    # First freeze: Oct 15 to Oct 20
    f1 = request_freeze(
        actor=branch_manager_a,
        membership=m,
        requested_start=date(2026, 10, 15),
        requested_end=date(2026, 10, 20),
        reason="First",
    )
    approve_freeze(actor=branch_manager_a, freeze=f1)

    # Overlapping second freeze: Oct 18 to Oct 24
    with pytest.raises(ValidationError, match="overlaps"):
        request_freeze(
            actor=branch_manager_a,
            membership=m,
            requested_start=date(2026, 10, 18),
            requested_end=date(2026, 10, 24),
            reason="Overlap",
        )


def test_rejected_freeze_does_not_extend_expiry(gym_owner, branch_manager_a, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=branch_manager_a,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")
    orig_end = m.end_date

    f = request_freeze(
        actor=branch_manager_a,
        membership=m,
        requested_start=date(2026, 10, 15),
        requested_end=date(2026, 10, 20),
        reason="Vacation",
    )
    reject_freeze(actor=branch_manager_a, freeze=f, rejection_reason="Insufficient notice")

    f.refresh_from_db()
    assert f.status == MembershipFreeze.Status.REJECTED

    m.refresh_from_db()
    assert m.end_date == orig_end


# ==============================================================================
# 6. Cross-Branch Eligibility Engine
# ==============================================================================

def test_cross_branch_denial_for_single_branch_package(gym_owner, member_a, standard_monthly_package, branch_a, branch_b):
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,  # cross_branch_enabled=False
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")

    now = timezone.now()

    # Allowed at home branch
    res_home = can_access_branch(member_a, branch_a, at_datetime=now)
    assert res_home.allowed is True

    # Denied at other branch
    res_other = can_access_branch(member_a, branch_b, at_datetime=now)
    assert res_other.allowed is False
    assert res_other.reason_code == AccessResult.CODE_CROSS_BRANCH_UNAUTHORIZED


def test_cross_branch_allowed_for_multi_branch_package(gym_owner, member_a, multi_branch_package, branch_a, branch_b):
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=multi_branch_package,  # cross_branch_enabled=True, both branches enrolled
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")

    now = timezone.now()

    # Allowed at home branch
    assert can_access_branch(member_a, branch_a, at_datetime=now).allowed is True

    # Allowed at other enrolled branch
    res_other = can_access_branch(member_a, branch_b, at_datetime=now)
    assert res_other.allowed is True


def test_access_denied_when_member_is_suspended(gym_owner, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")

    # Member profile is suspended
    change_member_status(actor=gym_owner, member=member_a, new_status=Member.MemberStatus.SUSPENDED, reason="Conduct")

    res = can_access_branch(member_a, branch_a)
    assert res.allowed is False
    assert res.reason_code == AccessResult.CODE_MEMBER_SUSPENDED


def test_access_denied_during_active_freeze_period(gym_owner, branch_manager_a, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")

    freeze = request_freeze(
        actor=branch_manager_a,
        membership=m,
        requested_start=date(2026, 10, 15),
        requested_end=date(2026, 10, 22),
        reason="Injury",
    )
    approve_freeze(actor=branch_manager_a, freeze=freeze)

    # During freeze window
    check_time = timezone.make_aware(timezone.datetime(2026, 10, 18, 10, 0, 0))
    res = can_access_branch(member_a, branch_a, at_datetime=check_time)
    assert res.allowed is False
    assert res.reason_code == AccessResult.CODE_MEMBERSHIP_FROZEN


# ==============================================================================
# 7. Cancellation & Expiration Sync
# ==============================================================================

def test_membership_cancellation(gym_owner, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 10, 14),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Active")

    cancel_membership(actor=gym_owner, membership=m, reason="Member relocation")

    m.refresh_from_db()
    assert m.status == Membership.Status.CANCELLED
    assert m.cancellation_reason == "Member relocation"
    assert m.cancelled_at is not None

    assert MembershipEvent.objects.filter(membership=m, event_type=MembershipEvent.EventType.CANCELLED).exists()


def test_sync_expired_memberships(gym_owner, member_a, standard_monthly_package, branch_a):
    m = create_membership(
        actor=gym_owner,
        member=member_a,
        package=standard_monthly_package,
        home_branch=branch_a,
        start_date=date(2026, 8, 1),
    )
    activate_membership(actor=gym_owner, membership=m, is_complimentary=True, bypass_reason="Past")

    # Currently active in DB, but end date was Sep 1, 2026
    assert m.status == Membership.Status.ACTIVE
    assert m.end_date < get_gym_today()

    count = sync_expired_memberships()
    assert count >= 1

    m.refresh_from_db()
    assert m.status == Membership.Status.EXPIRED


# ==============================================================================
# 8. Dashboard Selectors & Branch Scoping
# ==============================================================================

def test_dashboard_membership_metrics(gym_owner, branch_manager_a, receptionist_a, branch_a, branch_b, standard_monthly_package, multi_branch_package):
    m1 = register_member(actor=gym_owner, home_branch=branch_a, first_name="A1", last_name="M", phone="0911000010")
    m2 = register_member(actor=gym_owner, home_branch=branch_a, first_name="A2", last_name="M", phone="0911000020")
    m3 = register_member(actor=gym_owner, home_branch=branch_b, first_name="B1", last_name="M", phone="0911000030")

    # Branch A: 1 active, 1 pending
    sub1 = create_membership(actor=gym_owner, member=m1, package=standard_monthly_package, home_branch=branch_a, start_date=date(2026, 10, 14))
    activate_membership(actor=gym_owner, membership=sub1, is_complimentary=True, bypass_reason="Grant")

    create_membership(actor=gym_owner, member=m2, package=standard_monthly_package, home_branch=branch_a, start_date=date(2026, 10, 14))

    # Branch B: 1 active
    sub3 = create_membership(actor=gym_owner, member=m3, package=multi_branch_package, home_branch=branch_b, start_date=date(2026, 10, 14))
    activate_membership(actor=gym_owner, membership=sub3, is_complimentary=True, bypass_reason="Grant")

    # Owner metrics across all branches
    owner_m = get_membership_metrics(gym_owner)
    assert owner_m["active_memberships"] == 2
    assert owner_m["pending_memberships"] == 1

    # Branch Manager A metrics scoped to Branch A
    mgr_m = get_membership_metrics(branch_manager_a)
    assert mgr_m["active_memberships"] == 1
    assert mgr_m["pending_memberships"] == 1
