from datetime import date, datetime, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import seed_roles
from apps.attendance.credentials import issue_member_credential, lookup_credential
from apps.attendance.models import AccessAttempt, Attendance, MemberCredential
from apps.attendance.permissions import assert_attendance_method_enabled, get_attendance_method_settings
from apps.attendance.selectors import get_attendance_metrics
from apps.attendance.services import process_check_in, process_check_out
from apps.billing.models import Invoice, PaymentMethod
from apps.billing.services import record_payment
from apps.branches.models import Branch, UserBranchAccess
from apps.core.admin import GymSettingsAdminForm
from apps.core.models import GymProfile, GymSettings
from apps.members.models import Member
from apps.members.services import register_member
from apps.memberships.models import Membership, MembershipFreeze, MembershipPackage, PackageBranch
from apps.memberships.services import create_membership

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_roles_and_settings(db):
    seed_roles()
    PaymentMethod.objects.update_or_create(
        code="CASH",
        defaults={
            "name": "Cash",
            "method_type": PaymentMethod.MethodType.CASH,
            "is_active": True,
            "requires_reference": False,
            "requires_verification": False,
        },
    )
    profile = GymProfile.objects.first()
    if not profile:
        profile = GymProfile.objects.create(name="AGMS Master Gym")
    settings_obj, _ = GymSettings.objects.get_or_create(
        profile=profile,
        defaults={
            "attendance_qr_enabled": True,
            "attendance_manual_enabled": True,
            "allow_access_with_balance": False,
        },
    )
    settings_obj.attendance_qr_enabled = True
    settings_obj.attendance_manual_enabled = True
    settings_obj.save()
    return settings_obj


@pytest.fixture
def branch_a(db):
    return Branch.objects.create(name="Bole Central", code="BC", address="Bole Road, Addis Ababa", city="Addis Ababa", is_active=True)


@pytest.fixture
def branch_b(db):
    return Branch.objects.create(name="CMC Prime", code="CP", address="CMC Road, Addis Ababa", city="Addis Ababa", is_active=True)


@pytest.fixture
def owner(db, password):
    user = User.objects.create_user("owner@gym.test", password, is_staff=True)
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Gym Owner"))
    return user


@pytest.fixture
def receptionist(db, password, branch_a):
    user = User.objects.create_user("receptionist@gym.test", password, is_staff=True)
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=user, branch=branch_a, is_active=True, is_primary=True)
    return user


@pytest.fixture
def package_standard(branch_a):
    pkg = MembershipPackage.objects.create(
        code="PKG-STD",
        name="Standard Monthly",
        price=Decimal("1500.00"),
        duration_value=1,
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        cross_branch_enabled=False,
    )
    PackageBranch.objects.create(package=pkg, branch=branch_a, is_enabled=True)
    return pkg


@pytest.fixture
def member_a(receptionist, branch_a):
    return register_member(
        actor=receptionist,
        home_branch=branch_a,
        first_name="Abebe",
        last_name="Bikila",
        phone="0911223344",
        date_of_birth=date(1990, 5, 1),
        gender=Member.Gender.MALE,
    )


@pytest.fixture
def active_membership_a(receptionist, member_a, branch_a, package_standard):
    m = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=timezone.localdate(),
    )
    inv = Invoice.objects.get(membership=m)
    record_payment(
        actor=receptionist,
        invoice=inv,
        amount=inv.balance_due,
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    m.refresh_from_db()
    return m


# 1. Both methods enabled by default
def test_both_methods_enabled_by_default():
    config = get_attendance_method_settings()
    assert config["qr_enabled"] is True
    assert config["manual_enabled"] is True
    assert_attendance_method_enabled(Attendance.Method.QR)
    assert_attendance_method_enabled(Attendance.Method.MANUAL)


# 2. QR only mode
def test_qr_only_mode():
    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_qr_enabled = True
    settings_obj.attendance_manual_enabled = False
    settings_obj.save()

    config = get_attendance_method_settings()
    assert config["qr_enabled"] is True
    assert config["manual_enabled"] is False

    assert_attendance_method_enabled(Attendance.Method.QR)
    with pytest.raises(ValidationError, match="Manual .* attendance is currently disabled"):
        assert_attendance_method_enabled(Attendance.Method.MANUAL)


# 3. Manual only mode
def test_manual_only_mode():
    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_qr_enabled = False
    settings_obj.attendance_manual_enabled = True
    settings_obj.save()

    config = get_attendance_method_settings()
    assert config["qr_enabled"] is False
    assert config["manual_enabled"] is True

    assert_attendance_method_enabled(Attendance.Method.MANUAL)
    with pytest.raises(ValidationError, match="QR Code attendance is currently disabled"):
        assert_attendance_method_enabled(Attendance.Method.QR)


# 4. Both disabled rejected by model clean, form, and DB constraint
def test_both_methods_disabled_rejected():
    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_qr_enabled = False
    settings_obj.attendance_manual_enabled = False

    # Model clean check
    with pytest.raises(ValidationError, match="At least one attendance method"):
        settings_obj.clean()

    # Form clean check
    form = GymSettingsAdminForm(data={
        "profile": settings_obj.profile.pk,
        "cross_branch_access_mode": "PACKAGE_BASED",
        "early_renewal_mode": "EXTEND_EXISTING_END_DATE",
        "attendance_qr_enabled": False,
        "attendance_manual_enabled": False,
    }, instance=settings_obj)
    assert not form.is_valid()
    assert "Both attendance methods cannot be disabled" in str(form.errors)

    # DB constraint check
    with pytest.raises(IntegrityError):
        GymSettings.objects.filter(pk=settings_obj.pk).update(
            attendance_qr_enabled=False,
            attendance_manual_enabled=False,
        )


# 5. Settings permissions: Owner can change, receptionist can view only
def test_settings_permissions(client, owner, receptionist):
    settings_obj = GymSettings.objects.first()
    url = reverse("admin:core_gympolicy_change", args=[settings_obj.pk])

    client.force_login(owner)
    res_owner = client.get(url)
    assert res_owner.status_code == 200

    post_data = {
        "profile": settings_obj.profile.pk,
        "cross_branch_access_mode": "PACKAGE_BASED",
        "early_renewal_mode": "EXTEND_EXISTING_END_DATE",
        "attendance_qr_enabled": "on",
        "attendance_manual_enabled": "on",
    }
    res_post = client.post(url, post_data)
    assert res_post.status_code in (200, 302)

    # Receptionist can view but cannot change
    client.force_login(receptionist)
    res_rec = client.get(url)
    assert res_rec.status_code == 200
    res_rec_post = client.post(url, post_data)
    assert res_rec_post.status_code in (403, 302)


# 6. QR endpoint blocked when disabled
def test_qr_endpoint_blocked_when_disabled(receptionist, member_a, branch_a, active_membership_a):
    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_qr_enabled = False
    settings_obj.attendance_manual_enabled = True
    settings_obj.save()

    cred, raw_token = issue_member_credential(member_a)

    with pytest.raises(ValidationError, match="QR Code attendance is currently disabled"):
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            raw_token=raw_token,
            method=Attendance.Method.QR,
        )


# 7. Manual endpoint blocked when disabled
def test_manual_endpoint_blocked_when_disabled(receptionist, member_a, branch_a, active_membership_a):
    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_qr_enabled = True
    settings_obj.attendance_manual_enabled = False
    settings_obj.save()

    with pytest.raises(ValidationError, match="Manual .* attendance is currently disabled"):
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            member=member_a,
            method=Attendance.Method.MANUAL,
        )


# 8. Forged POST attempts fail closed
def test_forged_post_attempts_fail_closed(client, receptionist, member_a, branch_a):
    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_manual_enabled = False
    settings_obj.save()

    client.force_login(receptionist)
    url = reverse("admin:attendance_manual_checkin")

    with pytest.raises(ValidationError, match="Manual .* attendance is currently disabled"):
        client.post(url, {
            "member_id": str(member_a.pk),
            "branch_id": str(branch_a.pk),
            "confirm_identity": "1",
            "action": "checkin",
        })
    assert not Attendance.objects.filter(member=member_a, check_in_method=Attendance.Method.MANUAL).exists()


# 9. Search by full name
def test_search_by_full_name(client, receptionist, member_a):
    client.force_login(receptionist)
    url = f"{reverse('admin:attendance_manual_checkin')}?q=Abebe+Bikila"
    res = client.get(url)
    assert res.status_code == 200
    assert member_a.full_name in res.content.decode()


# 10. Search by partial name
def test_search_by_partial_name(client, receptionist, member_a):
    client.force_login(receptionist)
    url = f"{reverse('admin:attendance_manual_checkin')}?q=Biki"
    res = client.get(url)
    assert res.status_code == 200
    assert member_a.member_number in res.content.decode()


# 11. Search by Ethiopian phone format
def test_search_by_ethiopian_phone(client, receptionist, member_a):
    client.force_login(receptionist)
    # Local format
    url_local = f"{reverse('admin:attendance_manual_checkin')}?q=0911223344"
    res_local = client.get(url_local)
    assert res_local.status_code == 200
    assert member_a.full_name in res_local.content.decode()

    # International format
    url_intl = f"{reverse('admin:attendance_manual_checkin')}?q=%2B251911223344"
    res_intl = client.get(url_intl)
    assert res_intl.status_code == 200
    assert member_a.full_name in res_intl.content.decode()


# 12. Search by member number
def test_search_by_member_number(client, receptionist, member_a):
    client.force_login(receptionist)
    url = f"{reverse('admin:attendance_manual_checkin')}?q={member_a.member_number}"
    res = client.get(url)
    assert res.status_code == 200
    assert member_a.full_name in res.content.decode()


# 13. Duplicate name results
def test_duplicate_name_results(client, receptionist, branch_a):
    member_1 = register_member(actor=receptionist, home_branch=branch_a, first_name="Dawit", last_name="Kebede", phone="0911111111", date_of_birth=date(1992, 1, 1), gender=Member.Gender.MALE)
    member_2 = register_member(actor=receptionist, home_branch=branch_a, first_name="Dawit", last_name="Kebede", phone="0922222222", date_of_birth=date(1995, 2, 2), gender=Member.Gender.MALE)

    client.force_login(receptionist)
    url = f"{reverse('admin:attendance_manual_checkin')}?q=Dawit+Kebede"
    res = client.get(url)
    assert res.status_code == 200
    content = res.content.decode()
    assert member_1.member_number in content
    assert member_2.member_number in content


# 14. Member identity confirmation required
def test_identity_confirmation_required(client, receptionist, member_a, branch_a, active_membership_a):
    client.force_login(receptionist)
    url = reverse("admin:attendance_manual_checkin")

    res = client.post(url, {
        "member_id": str(member_a.pk),
        "branch_id": str(branch_a.pk),
        "action": "checkin",
    })
    assert res.status_code == 200
    assert "Identity confirmation required" in res.content.decode()
    assert not Attendance.objects.filter(member=member_a).exists()


# 15. Manual check-in
def test_manual_checkin_success(receptionist, member_a, branch_a, active_membership_a):
    att, elig = process_check_in(
        branch=branch_a,
        actor=receptionist,
        member=member_a,
        method=Attendance.Method.MANUAL,
        notes="Card forgotten in locker",
    )
    assert att.status == Attendance.Status.PRESENT
    assert att.check_in_method == Attendance.Method.MANUAL
    assert att.notes == "Card forgotten in locker"
    assert att.recorded_by == receptionist

    attempt = AccessAttempt.objects.filter(member=member_a).first()
    assert attempt.result == AccessAttempt.Result.GRANTED
    assert attempt.check_in_method == Attendance.Method.MANUAL


# 16. Manual check-out
def test_manual_checkout_success(receptionist, member_a, branch_a, active_membership_a):
    att, _ = process_check_in(
        branch=branch_a,
        actor=receptionist,
        member=member_a,
        method=Attendance.Method.MANUAL,
    )
    assert att.status == Attendance.Status.PRESENT

    checkout_att = process_check_out(
        branch=branch_a,
        actor=receptionist,
        member=member_a,
        method=Attendance.CheckoutMethod.MANUAL,
        notes="Member requested manual check-out",
    )
    assert checkout_att.status == Attendance.Status.COMPLETED
    assert checkout_att.check_out_method == Attendance.CheckoutMethod.MANUAL
    assert checkout_att.check_out_datetime is not None


# 17. QR check-in regression
def test_qr_checkin_regression(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member_a)
    att, elig = process_check_in(
        branch=branch_a,
        actor=receptionist,
        raw_token=raw_token,
        method=Attendance.Method.QR,
    )
    assert att.status == Attendance.Status.PRESENT
    assert att.check_in_method == Attendance.Method.QR


# 18. QR check-out regression
def test_qr_checkout_regression(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member_a)
    att, _ = process_check_in(
        branch=branch_a,
        actor=receptionist,
        raw_token=raw_token,
        method=Attendance.Method.QR,
    )
    checkout_att = process_check_out(
        branch=branch_a,
        actor=receptionist,
        raw_token=raw_token,
        method=Attendance.CheckoutMethod.QR,
    )
    assert checkout_att.status == Attendance.Status.COMPLETED
    assert checkout_att.check_out_method == Attendance.CheckoutMethod.QR


# 19. Expired membership denial via manual check-in
def test_expired_membership_denial(receptionist, member_a, branch_a, active_membership_a):
    active_membership_a.start_date = timezone.localdate() - timedelta(days=30)
    active_membership_a.end_date = timezone.localdate() - timedelta(days=1)
    active_membership_a.status = Membership.Status.EXPIRED
    active_membership_a.save()

    with pytest.raises(ValidationError, match="expired|Renewal required"):
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            member=member_a,
            method=Attendance.Method.MANUAL,
        )


# 20. Frozen membership denial via manual check-in
def test_frozen_membership_denial(receptionist, member_a, branch_a, active_membership_a):
    active_membership_a.status = Membership.Status.FROZEN
    active_membership_a.save()

    with pytest.raises(ValidationError, match="currently frozen"):
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            member=member_a,
            method=Attendance.Method.MANUAL,
        )


# 21. Outstanding balance policy
def test_outstanding_balance_policy(receptionist, member_a, branch_a, active_membership_a):
    settings_obj = GymSettings.objects.first()
    settings_obj.allow_access_with_balance = False
    settings_obj.save()

    # Unpaid invoice with explicit date objects
    Invoice.objects.create(
        member=member_a,
        branch=branch_a,
        invoice_number="INV-UNPAID-001",
        membership=active_membership_a,
        issue_date=timezone.localdate(),
        due_date=timezone.localdate() + timedelta(days=5),
        subtotal=Decimal("500.00"),
        total_amount=Decimal("500.00"),
        balance_due=Decimal("500.00"),
        status=Invoice.Status.ISSUED,
    )
    active_membership_a.payment_clearance_status = Membership.PaymentClearance.PENDING
    active_membership_a.save()

    with pytest.raises(ValidationError, match="clearance|balance"):
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            member=member_a,
            method=Attendance.Method.MANUAL,
        )

    # When cleared, succeeds
    active_membership_a.payment_clearance_status = Membership.PaymentClearance.CLEARED
    active_membership_a.save()

    att, elig = process_check_in(
        branch=branch_a,
        actor=receptionist,
        member=member_a,
        method=Attendance.Method.MANUAL,
    )
    assert att.status == Attendance.Status.PRESENT


# 22. Cross-branch access enforcement
def test_cross_branch_access(receptionist, member_a, branch_a, branch_b, active_membership_a, password):
    rec_b = User.objects.create_user("rec_b@gym.test", password, is_staff=True)
    from django.contrib.auth.models import Group
    rec_b.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=rec_b, branch=branch_b, is_active=True, is_primary=True)

    with pytest.raises(ValidationError, match="does not permit cross-branch access|not authorized"):
        process_check_in(
            branch=branch_b,
            actor=rec_b,
            member=member_a,
            method=Attendance.Method.MANUAL,
        )


# 23. Visit limits enforcement
def test_visit_limits(receptionist, member_a, branch_a):
    pkg_visit = MembershipPackage.objects.create(
        code="PKG-VISIT",
        name="1-Day Pass",
        price=Decimal("200.00"),
        duration_value=1,
        duration_unit=MembershipPackage.DurationUnit.DAY,
        allowed_visits=1,
    )
    PackageBranch.objects.create(package=pkg_visit, branch=branch_a, is_enabled=True)

    m = create_membership(actor=receptionist, member=member_a, package=pkg_visit, home_branch=branch_a)
    inv = Invoice.objects.get(membership=m)
    record_payment(actor=receptionist, invoice=inv, amount=inv.balance_due, payment_method=PaymentMethod.objects.get(code="CASH"))
    m.refresh_from_db()

    # First check-in succeeds
    att1, _ = process_check_in(branch=branch_a, actor=receptionist, member=member_a, method=Attendance.Method.MANUAL)
    process_check_out(branch=branch_a, actor=receptionist, member=member_a, method=Attendance.CheckoutMethod.MANUAL)

    # Second check-in must be rejected
    with pytest.raises(ValidationError, match="Visit limit.*reached|visit limit"):
        process_check_in(branch=branch_a, actor=receptionist, member=member_a, method=Attendance.Method.MANUAL)


# 24. Duplicate attendance prevention
def test_duplicate_attendance_prevention(receptionist, member_a, branch_a, active_membership_a):
    process_check_in(branch=branch_a, actor=receptionist, member=member_a, method=Attendance.Method.MANUAL)

    with pytest.raises(ValidationError, match="already checked in"):
        process_check_in(branch=branch_a, actor=receptionist, member=member_a, method=Attendance.Method.MANUAL)


# 25. QR credentials retained when disabled
def test_qr_credentials_retained_when_disabled(receptionist, member_a):
    cred, raw_token = issue_member_credential(member_a)
    assert cred.status == MemberCredential.Status.ACTIVE

    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_qr_enabled = False
    settings_obj.save()

    cred.refresh_from_db()
    assert cred.status == MemberCredential.Status.ACTIVE


# 26. QR works after re-enabling
def test_qr_works_after_reenabling(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member_a)

    settings_obj = GymSettings.objects.first()
    settings_obj.attendance_qr_enabled = False
    settings_obj.save()

    settings_obj.attendance_qr_enabled = True
    settings_obj.save()

    att, elig = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token, method=Attendance.Method.QR)
    assert att.status == Attendance.Status.PRESENT


# 27. Attendance method reporting
def test_attendance_method_reporting(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member_a)

    # 1 QR checkin & checkout
    att_qr, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token, method=Attendance.Method.QR)
    process_check_out(branch=branch_a, actor=receptionist, raw_token=raw_token, method=Attendance.CheckoutMethod.QR)

    # 1 Manual checkin & checkout
    att_man, _ = process_check_in(branch=branch_a, actor=receptionist, member=member_a, method=Attendance.Method.MANUAL)
    process_check_out(branch=branch_a, actor=receptionist, member=member_a, method=Attendance.CheckoutMethod.MANUAL)

    metrics = get_attendance_metrics(receptionist, active_branch=branch_a)
    assert metrics["qr_checkins"] == 1
    assert metrics["manual_checkins"] == 1
    assert metrics["today_checkins"] == 2
    assert metrics["qr_checkouts"] == 1
    assert metrics["manual_checkouts"] == 1
    assert metrics["today_checkouts"] == 2
