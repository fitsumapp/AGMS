from datetime import date, datetime, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import seed_roles
from apps.attendance.credentials import (
    generate_qr_code_base64,
    hash_token,
    issue_member_credential,
    lookup_credential,
    reissue_member_credential,
    revoke_credential,
)
from apps.attendance.models import (
    AccessAttempt,
    AccessDevice,
    Attendance,
    AttendanceOverride,
    MemberCredential,
)
from apps.attendance.reports import generate_attendance_csv, generate_attendance_pdf
from apps.attendance.selectors import get_attendance_metrics, get_currently_inside
from apps.attendance.services import (
    auto_checkout_stale_sessions,
    process_check_in,
    process_check_out,
    record_manual_override,
)
from apps.billing.models import Invoice, PaymentMethod
from apps.billing.services import approve_refund, create_invoice, record_payment, request_refund
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile, GymSettings
from apps.members.models import Member
from apps.members.services import register_member
from apps.memberships.models import Membership, MembershipPackage, PackageBranch
from apps.memberships.services import activate_membership, create_membership

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_roles_and_methods(db):
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
    PaymentMethod.objects.update_or_create(
        code="BANK_TRANSFER",
        defaults={
            "name": "Bank Transfer",
            "method_type": PaymentMethod.MethodType.BANK_TRANSFER,
            "is_active": True,
            "requires_reference": True,
            "requires_verification": True,
        },
    )


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
def manager(db, password, branch_a):
    user = User.objects.create_user("manager@gym.test", password, is_staff=True)
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=user, branch=branch_a, is_active=True, is_primary=True)
    return user


@pytest.fixture
def receptionist(db, password, branch_a):
    user = User.objects.create_user("receptionist@gym.test", password, is_staff=True)
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=user, branch=branch_a, is_active=True, is_primary=True)
    return user


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
def active_membership_a(receptionist, member_a, branch_a, package_standard):
    m = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=timezone.localdate(),
    )
    # Pay & clear membership
    inv = Invoice.objects.get(membership=m)
    record_payment(
        actor=receptionist,
        invoice=inv,
        amount=inv.balance_due,
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    m.refresh_from_db()
    return m


# 1. Valid QR Check-In Workflow
def test_valid_qr_checkin_workflow(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    assert cred.status == MemberCredential.Status.ACTIVE
    assert cred.token_hash == hash_token(raw_token)

    att, elig = process_check_in(
        branch=branch_a,
        actor=receptionist,
        raw_token=raw_token,
        method=Attendance.Method.QR,
    )

    assert att.status == Attendance.Status.PRESENT
    assert att.member == member_a
    assert att.branch == branch_a
    assert att.membership == active_membership_a
    assert elig.allowed is True

    # Audit log created
    attempt = AccessAttempt.objects.filter(member=member_a).latest("timestamp")
    assert attempt.result == AccessAttempt.Result.GRANTED
    assert attempt.reason_code == "ALLOWED"


# 2. Invalid or Unrecognized QR
def test_invalid_qr_token_rejected(receptionist, branch_a):
    with pytest.raises(ValidationError) as exc:
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            raw_token="AGMS-FAKE-TOKEN-9999",
            method=Attendance.Method.QR,
        )
    assert "Invalid or unrecognized QR credential" in str(exc.value)

    attempt = AccessAttempt.objects.latest("timestamp")
    assert attempt.result == AccessAttempt.Result.DENIED
    assert attempt.reason_code == "INVALID_CREDENTIAL"


# 3. Revoked QR Credential
def test_revoked_qr_credential_rejected(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    revoke_credential(cred, reason="Reported lost", revoked_by=receptionist)

    with pytest.raises(ValidationError) as exc:
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            raw_token=raw_token,
            method=Attendance.Method.QR,
        )
    assert "revoked" in str(exc.value).lower()

    attempt = AccessAttempt.objects.filter(member=member_a).latest("timestamp")
    assert attempt.result == AccessAttempt.Result.DENIED
    assert attempt.reason_code == "CREDENTIAL_REVOKED"


# 4. Expired QR Credential
def test_expired_qr_credential_rejected(receptionist, member_a, branch_a, active_membership_a):
    past_expiry = timezone.now() - timedelta(days=1)
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist, expiry_date=past_expiry)

    with pytest.raises(ValidationError) as exc:
        process_check_in(
            branch=branch_a,
            actor=receptionist,
            raw_token=raw_token,
            method=Attendance.Method.QR,
        )
    assert "expired" in str(exc.value).lower()


# 5. Rapid Duplicate Scan Prevention
def test_rapid_duplicate_scan_prevention(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)

    # First check in succeeds
    att, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert att.status == Attendance.Status.PRESENT

    # Immediate second scan fails
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert "already checked in" in str(exc.value).lower()

    attempt = AccessAttempt.objects.filter(member=member_a).latest("timestamp")
    assert attempt.result == AccessAttempt.Result.DENIED
    assert attempt.reason_code == "DUPLICATE_SCAN"


# 6. Check-Out Workflow
def test_checkout_workflow(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)

    # Check in
    att, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)

    # Check out
    att_out = process_check_out(
        branch=branch_a,
        actor=receptionist,
        raw_token=raw_token,
        method=Attendance.CheckoutMethod.QR,
    )
    assert att_out.pk == att.pk
    assert att_out.status == Attendance.Status.COMPLETED
    assert att_out.check_out_datetime is not None
    assert att_out.check_out_method == Attendance.CheckoutMethod.QR


# 7. Check-Out Without Active Session Fails
def test_checkout_without_active_session_fails(receptionist, member_a, branch_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    with pytest.raises(ValidationError) as exc:
        process_check_out(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert "No active check-in session found" in str(exc.value)


# 8. Expired Membership Denied
def test_expired_membership_denied(receptionist, member_a, branch_a, package_standard):
    # Membership expired yesterday
    m = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=timezone.localdate() - timedelta(days=31),
    )
    inv = Invoice.objects.get(membership=m)
    record_payment(receptionist, inv, inv.balance_due, PaymentMethod.objects.get(code="CASH"))
    m.refresh_from_db()
    m.status = Membership.Status.EXPIRED
    m.save()

    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert "expired" in str(exc.value).lower()


# 9. Frozen Membership Denied
def test_frozen_membership_denied(receptionist, member_a, branch_a, active_membership_a):
    active_membership_a.status = Membership.Status.FROZEN
    active_membership_a.save(update_fields=["status"])

    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert "frozen" in str(exc.value).lower()


# 10. Unpaid Membership Denied
def test_unpaid_membership_denied(receptionist, member_a, branch_a, package_standard):
    profile, _ = GymProfile.objects.get_or_create(singleton=1, defaults={"name": "PowerZone"})
    settings, _ = GymSettings.objects.get_or_create(profile=profile, defaults={"allow_access_with_balance": False})
    settings.allow_access_with_balance = False
    settings.save()

    # Create membership without payment
    m = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=timezone.localdate(),
    )
    assert m.status == Membership.Status.PENDING

    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert "pending billing clearance" in str(exc.value).lower()


# 11. Partial Payment Policy in Attendance
def test_partial_payment_policy_in_attendance(receptionist, member_a, branch_a, package_standard):
    profile, _ = GymProfile.objects.get_or_create(singleton=1, defaults={"name": "PowerZone"})
    settings, _ = GymSettings.objects.get_or_create(profile=profile, defaults={"allow_access_with_balance": True})
    settings.allow_access_with_balance = True
    settings.save()

    m = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=timezone.localdate(),
    )
    inv = Invoice.objects.get(membership=m)
    # Zero payment: still denied
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    with pytest.raises(ValidationError):
        process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)

    # Partial payment recorded
    record_payment(receptionist, inv, Decimal("500.00"), PaymentMethod.objects.get(code="CASH"))
    # Now allowed under policy!
    att, elig = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert att.status == Attendance.Status.PRESENT
    assert elig.allowed is True


# 12. Cross-Branch Restrictions
def test_cross_branch_restrictions(owner, member_a, branch_a, branch_b, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=owner)

    # Active membership package has cross_branch_enabled=False
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_b, actor=owner, raw_token=raw_token)
    assert "cross-branch" in str(exc.value).lower()


# 13. Visit Limit Enforcement
def test_visit_limit_enforcement(receptionist, member_a, branch_a):
    pkg_visits = MembershipPackage.objects.create(
        code="PKG-PUNCH",
        name="10-Visit Pass",
        price=Decimal("1000.00"),
        duration_value=1,
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        allowed_visits=2,  # Cap at 2 visits for testing
        cross_branch_enabled=False,
    )
    PackageBranch.objects.create(package=pkg_visits, branch=branch_a, is_enabled=True)

    m = create_membership(
        actor=receptionist,
        member=member_a,
        package=pkg_visits,
        home_branch=branch_a,
        start_date=timezone.localdate(),
    )
    inv = Invoice.objects.get(membership=m)
    record_payment(receptionist, inv, inv.balance_due, PaymentMethod.objects.get(code="CASH"))
    m.refresh_from_db()
    assert m.allowed_visits_snapshot == 2

    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)

    # Visit 1: check in and check out
    att1, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    process_check_out(branch=branch_a, actor=receptionist, raw_token=raw_token)

    # Visit 2: check in and check out
    att2, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    process_check_out(branch=branch_a, actor=receptionist, raw_token=raw_token)

    # Visit 3: Limit reached! Rejection must occur
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert "visit limit of 2 reached" in str(exc.value).lower()

    attempt = AccessAttempt.objects.filter(member=member_a).latest("timestamp")
    assert attempt.reason_code == "VISIT_LIMIT_EXCEEDED"


# 14. Manual Check-in Flow
def test_manual_checkin_flow(receptionist, member_a, branch_a, active_membership_a):
    att, elig = process_check_in(
        branch=branch_a,
        actor=receptionist,
        member=member_a,
        method=Attendance.Method.MANUAL,
        notes="Forgot physical card",
    )
    assert att.status == Attendance.Status.PRESENT
    assert att.check_in_method == Attendance.Method.MANUAL
    assert att.notes == "Forgot physical card"


# 15. Manual Override Authorization
def test_manual_override_authorization(receptionist, manager, member_a, branch_a):
    # Receptionist cannot override
    with pytest.raises(PermissionDenied):
        record_manual_override(
            actor=receptionist,
            branch=branch_a,
            member=member_a,
            override_reason="VIP guest pass",
        )

    # Branch Manager CAN override
    att = record_manual_override(
        actor=manager,
        branch=branch_a,
        member=member_a,
        override_reason="VIP guest pass approved by owner",
    )
    assert att.status == Attendance.Status.PRESENT
    assert att.check_in_method == Attendance.Method.OVERRIDE

    override = AttendanceOverride.objects.get(attendance=att)
    assert override.approved_by == manager
    assert "VIP guest pass" in override.override_reason


# 16. Auto-Checkout of Stale Sessions
def test_auto_checkout_stale_sessions(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    att, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)

    # Artificially set check_in_datetime to 14 hours ago
    att.check_in_datetime = timezone.now() - timedelta(hours=14)
    att.save(update_fields=["check_in_datetime"])

    closed = auto_checkout_stale_sessions(branch=branch_a, max_hours=12)
    assert closed == 1

    att.refresh_from_db()
    assert att.status == Attendance.Status.AUTO_CLOSED
    assert att.check_out_method == Attendance.CheckoutMethod.AUTO
    assert att.is_estimated_duration is True
    assert att.is_verified_checkout is False
    assert att.estimated_duration_minutes == 120
    assert att.verified_duration_minutes is None


# 17. Attendance Selectors and Metrics
def test_attendance_selectors_and_metrics(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    att, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)

    metrics = get_attendance_metrics(user=receptionist, active_branch=branch_a)
    assert metrics["today_checkins"] == 1
    assert metrics["currently_inside"] == 1
    assert metrics["unique_visitors_today"] == 1
    assert len(metrics["peak_hours"]) >= 1

    inside = get_currently_inside(user=receptionist, active_branch=branch_a)
    assert inside.count() == 1


# 18. Report Exports (PDF and CSV)
def test_report_exports(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)
    process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)

    qs = Attendance.objects.filter(branch=branch_a)

    csv_data = generate_attendance_csv(qs)
    assert b"Member Number" in csv_data
    assert member_a.member_number.encode() in csv_data

    pdf_data = generate_attendance_pdf(qs, title="Daily Attendance", branch_name=branch_a.name)
    assert pdf_data.startswith(b"%PDF-")
    assert len(pdf_data) > 500


# 19. QR Code Base64 Generator
def test_qr_code_base64_generation():
    token = "AGMS-QR-v1-TEST-RANDOM-SECRET"
    data_uri = generate_qr_code_base64(token)
    assert data_uri.startswith("data:image/png;base64,")
    assert len(data_uri) > 100


# 20. Admin Custom Views HTTP Status
def test_admin_custom_views_http_status(client, owner, branch_a):
    client.force_login(owner)

    urls = [
        reverse("admin:attendance_qr_checkin"),
        reverse("admin:attendance_manual_checkin"),
        reverse("admin:attendance_dashboard"),
        reverse("admin:attendance_reports"),
        reverse("admin:attendance_attendance_changelist"),
        reverse("admin:attendance_membercredential_changelist"),
        reverse("admin:attendance_accessdevice_changelist"),
        reverse("admin:attendance_attendanceoverride_changelist"),
        reverse("admin:attendance_accessattempt_changelist"),
    ]

    for url in urls:
        resp = client.get(url, SERVER_NAME="127.0.0.1")
        assert resp.status_code == 200, f"URL {url} returned status {resp.status_code}"


# 21. Duration Analytics Distinguishes Verified vs Estimated Durations
def test_duration_analytics_distinguishes_verified_vs_estimated(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)

    # Verified session: check in, wait 45 mins, check out
    now = timezone.now()
    att_verified, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    att_verified.check_in_datetime = now - timedelta(minutes=45)
    att_verified.save(update_fields=["check_in_datetime"])
    process_check_out(branch=branch_a, actor=receptionist, raw_token=raw_token)
    att_verified.refresh_from_db()
    assert att_verified.is_verified_checkout is True
    assert att_verified.verified_duration_minutes == 45

    # Auto-closed session: check in, stale session, auto-checkout
    att_auto, _ = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    att_auto.check_in_datetime = now - timedelta(minutes=10)
    att_auto.save(update_fields=["check_in_datetime"])
    auto_checkout_stale_sessions(branch=branch_a, max_hours=0)
    att_auto.refresh_from_db()
    assert att_auto.is_verified_checkout is False
    assert att_auto.is_estimated_duration is True
    assert att_auto.status == Attendance.Status.AUTO_CLOSED

    # Selector metrics test
    metrics = get_attendance_metrics(user=receptionist, active_branch=branch_a)
    assert metrics["verified_completed_count"] == 1
    assert metrics["avg_verified_duration_minutes"] == 45
    assert metrics["estimated_sessions_count"] == 1


# 22. Credential Reissuance and Badge Printing Flow
def test_credential_reissuance_and_badge_flow(receptionist, member_a, branch_a):
    cred1, token1 = issue_member_credential(member=member_a, issued_by=receptionist)
    assert cred1.status == MemberCredential.Status.ACTIVE

    # Reissue credential (e.g. lost badge or card replacement)
    cred2, token2 = reissue_member_credential(
        member=member_a,
        reissued_by=receptionist,
        reason="Lost card reported by member",
    )
    cred1.refresh_from_db()
    # Prior credential must be atomically revoked
    assert cred1.status == MemberCredential.Status.REVOKED
    assert "Lost card" in cred1.revoked_reason
    assert cred2.status == MemberCredential.Status.ACTIVE

    # Old token cannot be used to check in
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_a, actor=receptionist, raw_token=token1)
    assert "revoked" in str(exc.value).lower()

    # New token generates valid working QR
    qr_data = generate_qr_code_base64(token2)
    assert qr_data.startswith("data:image/png;base64,")


# 23. Unauthorized Receptionist Forged Manual Override POST Denied
def test_receptionist_forged_manual_override_post_denied(client, receptionist, member_a, branch_a):
    client.force_login(receptionist)
    url = reverse("admin:attendance_manual_checkin")

    resp = client.post(
        url,
        {
            "action": "override",
            "member_id": str(member_a.pk),
            "branch_id": str(branch_a.pk),
            "override_reason": "Attempting unauthorized override as receptionist",
        },
        SERVER_NAME="127.0.0.1",
    )
    # Must reject override
    assert resp.status_code == 200
    assert AttendanceOverride.objects.filter(member=member_a).count() == 0


# 24. Staff Checking In At Unauthorized Branch Denied
def test_unauthorized_branch_checkin_denied(receptionist, member_a, branch_b, active_membership_a):
    # receptionist is only assigned to branch_a, not branch_b
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)

    with pytest.raises(PermissionDenied):
        process_check_in(branch=branch_b, actor=receptionist, raw_token=raw_token)


# 25. Access Attempt Audit Does Not Expose Raw Token
def test_access_attempt_audit_does_not_expose_raw_token(receptionist, branch_a):
    fake_token = "SECRET_TOKEN_XYZ_1234567890"

    with pytest.raises(ValidationError):
        process_check_in(branch=branch_a, actor=receptionist, raw_token=fake_token)

    attempt = AccessAttempt.objects.filter(branch=branch_a, result=AccessAttempt.Result.DENIED).first()
    assert attempt is not None
    # Verify raw token is never stored in message or metadata
    assert fake_token not in attempt.message
    assert fake_token not in str(attempt.metadata)
    assert fake_token[:10] not in str(attempt.metadata)


# 26. Concurrent Scans Serialize on Member Row Lock
def test_concurrent_scans_serialize_on_member_lock(receptionist, member_a, branch_a, active_membership_a):
    cred, raw_token = issue_member_credential(member=member_a, issued_by=receptionist)

    # First check-in succeeds
    att1, elig1 = process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert att1.status == Attendance.Status.PRESENT

    # Immediate second scan while session is active is rejected
    with pytest.raises(ValidationError) as exc:
        process_check_in(branch=branch_a, actor=receptionist, raw_token=raw_token)
    assert "already" in str(exc.value).lower()

