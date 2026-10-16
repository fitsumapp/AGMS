from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from django.contrib.auth.models import Group
from apps.accounts.models import User
from apps.accounts.roles import has_role, is_owner, seed_roles
from apps.analytics.branching import resolve_analytics_branch_scope
from apps.analytics.charts import (
    render_svg_bar_chart,
    render_svg_donut_chart,
    render_svg_horizontal_bar,
)
from apps.analytics.dates import (
    DateFilterRange,
    get_gym_timezone,
    get_today_in_gym_tz,
    resolve_date_filter,
)
from apps.analytics.models import ReportExportLog
from apps.analytics.permissions import (
    can_access_report_center,
    can_export_reports,
    can_view_attendance_analytics,
    can_view_financial_analytics,
    can_view_membership_analytics,
)
from apps.analytics.reports import REPORT_REGISTRY, get_available_reports_for_user
from apps.analytics.reports.exporters import sanitize_csv_cell
from apps.analytics.selectors import (
    get_attendance_analytics,
    get_executive_dashboard_data,
    get_financial_analytics,
    get_membership_analytics,
)
from apps.attendance.models import AccessAttempt, Attendance, MemberCredential
from apps.attendance.services import process_check_in, process_check_out
from apps.billing.models import Invoice, Payment, PaymentMethod, Refund
from apps.billing.services import approve_refund, create_invoice, record_payment, request_refund
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.members.models import Member
from apps.members.services import register_member
from apps.memberships.models import Membership, MembershipPackage, MembershipRenewal, PackageBranch
from apps.memberships.services import activate_membership, create_membership

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_analytics_prerequisites(db):
    seed_roles()
    GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "Bole Alpha Gym",
            "default_currency": "ETB",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )
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
def branches(db):
    b1 = Branch.objects.create(name="Bole Branch", code="BOLE", address="Bole Road, Addis Ababa", is_active=True)
    b2 = Branch.objects.create(name="CMC Branch", code="CMC", address="CMC Road, Addis Ababa", is_active=True)
    return b1, b2


@pytest.fixture
def owner_user(db, branches):
    b1, _ = branches
    user = User.objects.create_user(
        email="owner@gym.et",
        phone="+251911000001",
        first_name="Gym",
        last_name="Owner",
        is_staff=True,
    )
    user.groups.add(Group.objects.get(name="Gym Owner"))
    UserBranchAccess.objects.create(user=user, branch=b1, is_primary=True)
    return user


@pytest.fixture
def manager_user(db, branches):
    b1, _ = branches
    user = User.objects.create_user(
        email="manager@gym.et",
        phone="+251911000002",
        first_name="Branch",
        last_name="Manager",
        is_staff=True,
    )
    user.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=user, branch=b1, is_primary=True)
    return user


@pytest.fixture
def receptionist_user(db, branches):
    b1, _ = branches
    user = User.objects.create_user(
        email="receptionist@gym.et",
        phone="+251911000003",
        first_name="Front",
        last_name="Desk",
        is_staff=True,
    )
    user.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=user, branch=b1, is_primary=True)
    return user


# =========================================================================
# 1. DATE BOUNDARIES & TIMEZONE TESTS
# =========================================================================

def test_date_filter_boundary_resolution():
    """Verify that all date presets resolve to valid timezone-aware boundaries."""
    today = get_today_in_gym_tz()

    presets = ["today", "yesterday", "last_7_days", "last_30_days", "this_month", "previous_month"]
    for p in presets:
        res = resolve_date_filter(preset=p)
        assert res.start_date <= res.end_date
        assert res.start_datetime.tzinfo is not None
        assert res.end_datetime.tzinfo is not None
        assert res.start_datetime < res.end_datetime

    # Custom range
    custom_start = today - timedelta(days=15)
    custom_end = today
    custom_res = resolve_date_filter(preset="custom", custom_start=custom_start, custom_end=custom_end)
    assert custom_res.preset == "custom"
    assert custom_res.start_date == custom_start
    assert custom_res.end_date == custom_end
    assert custom_res.start_datetime.hour == 0 and custom_res.start_datetime.minute == 0
    assert custom_res.end_datetime.hour == 23 and custom_res.end_datetime.minute == 59


# =========================================================================
# 2. BRANCH SCOPING & SECURITY TESTS
# =========================================================================

def test_branch_scoping_owner_vs_manager(owner_user, manager_user, branches):
    """Owner can query all branches or a specific branch; Manager is restricted."""
    b1, b2 = branches

    # Owner requesting 'all'
    qs_owner_all, sel_b, lbl = resolve_analytics_branch_scope(owner_user, requested_branch_id="all")
    assert sel_b is None
    assert qs_owner_all.count() >= 2
    assert "All Branches" in lbl

    # Owner requesting b2
    qs_owner_b2, sel_b, lbl = resolve_analytics_branch_scope(owner_user, requested_branch_id=str(b2.pk))
    assert sel_b == b2
    assert qs_owner_b2.count() == 1

    # Manager (only assigned to b1) requesting b2 (unauthorized) -> raises PermissionDenied
    with pytest.raises(PermissionDenied):
        resolve_analytics_branch_scope(manager_user, requested_branch_id=str(b2.pk))


def test_user_without_branch_access_denied(db):
    """User with no assigned branch cannot view analytics."""
    user = User.objects.create_user(
        email="nobranch@gym.et",
        phone="+251911999999",
        first_name="Orphan",
        last_name="Staff",
        is_staff=True,
    )
    user.groups.add(Group.objects.get(name="Receptionist"))

    with pytest.raises(PermissionDenied):
        resolve_analytics_branch_scope(user)


# =========================================================================
# 3. FINANCIAL ANALYTICS & RECONCILIATION TESTS
# =========================================================================

def test_financial_verified_vs_pending_and_refunds(owner_user, branches):
    """
    Ensure verified collections include only completed payments.
    Pending payments must NOT be counted.
    Refunds must be explicitly deducted in net cash collections.
    """
    b1, _ = branches
    member = register_member(
        actor=owner_user,
        home_branch=b1,
        first_name="Abebe",
        last_name="Kebede",
        phone="0911111111",
        gender=Member.Gender.MALE,
    )
    cash = PaymentMethod.objects.get(code="CASH")
    bank = PaymentMethod.objects.get(code="BANK_TRANSFER")

    # Invoice 1: 3,000 ETB, paid in cash (instant verified)
    inv1 = create_invoice(
        actor=owner_user,
        member=member,
        branch=b1,
        items=[{"description": "Monthly Pass", "unit_price": Decimal("3000.00"), "quantity": 1}],
        issue_now=True,
    )
    record_payment(actor=owner_user, invoice=inv1, amount=Decimal("3000.00"), payment_method=cash)

    # Invoice 2: 2,000 ETB, paid via Bank Transfer (pending verification)
    inv2 = create_invoice(
        actor=owner_user,
        member=member,
        branch=b1,
        items=[{"description": "PT Session", "unit_price": Decimal("2000.00"), "quantity": 1}],
        issue_now=True,
    )
    record_payment(
        actor=owner_user,
        invoice=inv2,
        amount=Decimal("2000.00"),
        payment_method=bank,
        transaction_reference="REF-9999",
    )

    # Invoice 3: 1,500 ETB, verified and then partially refunded 500 ETB
    inv3 = create_invoice(
        actor=owner_user,
        member=member,
        branch=b1,
        items=[{"description": "Pass Refundable", "unit_price": Decimal("1500.00"), "quantity": 1}],
        issue_now=True,
    )
    p3 = record_payment(actor=owner_user, invoice=inv3, amount=Decimal("1500.00"), payment_method=cash)
    ref = request_refund(actor=owner_user, payment=p3, amount=Decimal("500.00"), reason="Customer relocated")
    approve_refund(actor=owner_user, refund=ref, approve=True)

    date_range = resolve_date_filter(preset="this_month")
    fin = get_financial_analytics(Branch.objects.filter(pk=b1.pk), date_range)

    # Invoiced total = 3000 + 2000 + 1500 = 6500
    assert fin["total_invoiced"] == Decimal("6500.00")

    # Gross Verified Collections = inv1 (3000) + inv3 (1500) = 4500 (inv2 pending is EXCLUDED)
    assert fin["total_collected"] == Decimal("4500.00")

    # Total Refunds = 500
    assert fin["total_refunds"] == Decimal("500.00")

    # Net Cash Collections = 4500 - 500 = 4000
    assert fin["net_collections"] == Decimal("4000.00")


def test_aging_buckets_calculation(owner_user, branches):
    """Test aging summary buckets for unpaid invoices."""
    b1, _ = branches
    member = register_member(
        actor=owner_user,
        home_branch=b1,
        first_name="Sara",
        last_name="Tesfaye",
        phone="0911222222",
        gender=Member.Gender.FEMALE,
    )

    today = timezone.localdate()

    # Invoice due 10 days ago (Bucket: 0-30 days)
    inv1 = create_invoice(
        actor=owner_user,
        member=member,
        branch=b1,
        items=[{"description": "Overdue 10d", "unit_price": Decimal("1000.00"), "quantity": 1}],
        issue_now=True,
    )
    Invoice.objects.filter(pk=inv1.pk).update(due_date=today - timedelta(days=10))

    # Invoice due 45 days ago (Bucket: 31-60 days)
    inv2 = create_invoice(
        actor=owner_user,
        member=member,
        branch=b1,
        items=[{"description": "Overdue 45d", "unit_price": Decimal("2000.00"), "quantity": 1}],
        issue_now=True,
    )
    Invoice.objects.filter(pk=inv2.pk).update(due_date=today - timedelta(days=45))

    # Invoice due 75 days ago (Bucket: 61-90 days)
    inv3 = create_invoice(
        actor=owner_user,
        member=member,
        branch=b1,
        items=[{"description": "Overdue 75d", "unit_price": Decimal("3000.00"), "quantity": 1}],
        issue_now=True,
    )
    Invoice.objects.filter(pk=inv3.pk).update(due_date=today - timedelta(days=75))

    # Invoice due 120 days ago (Bucket: 90+ days)
    inv4 = create_invoice(
        actor=owner_user,
        member=member,
        branch=b1,
        items=[{"description": "Overdue 120d", "unit_price": Decimal("4000.00"), "quantity": 1}],
        issue_now=True,
    )
    Invoice.objects.filter(pk=inv4.pk).update(due_date=today - timedelta(days=120))

    date_range = resolve_date_filter(preset="this_month")
    fin = get_financial_analytics(Branch.objects.filter(pk=b1.pk), date_range)

    aging = {b["code"]: b["amount"] for b in fin["aging_summary"]}
    assert aging["0_30"] == Decimal("1000.00")
    assert aging["31_60"] == Decimal("2000.00")
    assert aging["61_90"] == Decimal("3000.00")
    assert aging["90_plus"] == Decimal("4000.00")


# =========================================================================
# 4. MEMBERSHIP ANALYTICS TESTS
# =========================================================================

def test_membership_distinct_persons_vs_subscriptions(owner_user, branches):
    """Confirm distinct unique members vs subscription count."""
    b1, _ = branches
    member = register_member(
        actor=owner_user,
        home_branch=b1,
        first_name="Dawit",
        last_name="Bekele",
        phone="0911333333",
        gender=Member.Gender.MALE,
    )
    pkg = MembershipPackage.objects.create(
        name="Gold 1 Month",
        code="PKG-GOLD-1M",
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        duration_value=1,
        price=Decimal("1500.00"),
        cross_branch_enabled=True,
    )
    PackageBranch.objects.create(package=pkg, branch=b1)

    today = timezone.localdate()

    # Membership 1 (Expired subscription for this member)
    m1 = Membership.objects.create(
        member=member,
        package=pkg,
        home_branch=b1,
        status=Membership.Status.EXPIRED,
        start_date=today - timedelta(days=60),
        end_date=today - timedelta(days=30),
        price_snapshot=Decimal("1500.00"),
        duration_unit_snapshot="MONTH",
        duration_value_snapshot=1,
    )

    # Membership 2 (Active renewal for same member)
    m2 = Membership.objects.create(
        member=member,
        package=pkg,
        home_branch=b1,
        status=Membership.Status.ACTIVE,
        start_date=today - timedelta(days=10),
        end_date=today + timedelta(days=20),
        price_snapshot=Decimal("1500.00"),
        duration_unit_snapshot="MONTH",
        duration_value_snapshot=1,
    )
    MembershipRenewal.objects.create(
        membership=m2,
        previous_end_date=today - timedelta(days=30),
        new_end_date=today + timedelta(days=20),
        duration_unit_snapshot="MONTH",
        duration_value_snapshot=1,
        price_snapshot=Decimal("1500.00"),
        renewal_status=MembershipRenewal.RenewalStatus.COMPLETED,
        idempotency_key="REN-1001",
    )

    date_range = resolve_date_filter(preset="this_month")
    mem_stats = get_membership_analytics(Branch.objects.filter(pk=b1.pk), date_range)

    assert mem_stats["total_registered_members"] == 1
    assert mem_stats["active_memberships"] == 1
    assert mem_stats["unique_active_members"] == 1
    assert mem_stats["renewals_period"] == 1


# =========================================================================
# 5. ATTENDANCE ANALYTICS & DURATION INTEGRITY TESTS
# =========================================================================

def test_attendance_auto_closed_duration_exclusion(owner_user, branches):
    """
    Auto-closed sessions must be tracked separately and excluded from verified
    workout duration calculations.
    """
    b1, _ = branches
    member = register_member(
        actor=owner_user,
        home_branch=b1,
        first_name="Almaz",
        last_name="Ayana",
        phone="0911444444",
        gender=Member.Gender.FEMALE,
    )

    date_range = resolve_date_filter(preset="today")
    t1 = date_range.start_datetime + timedelta(hours=8)
    t2 = date_range.start_datetime + timedelta(hours=10)

    # Session 1: Verified Checkout (60 min duration)
    Attendance.objects.create(
        member=member,
        branch=b1,
        check_in_datetime=t1,
        check_out_datetime=t1 + timedelta(minutes=60),
        check_in_method=Attendance.Method.QR,
        status=Attendance.Status.COMPLETED,
        is_estimated_duration=False,
    )

    # Session 2: Auto-Closed (Estimated duration, must be EXCLUDED from verified average)
    Attendance.objects.create(
        member=member,
        branch=b1,
        check_in_datetime=t2,
        check_out_datetime=t2 + timedelta(minutes=120),
        check_in_method=Attendance.Method.MANUAL,
        status=Attendance.Status.AUTO_CLOSED,
        is_estimated_duration=True,
    )
    att = get_attendance_analytics(Branch.objects.filter(pk=b1.pk), date_range)

    assert att["total_checkins"] == 2
    assert att["qr_checkins"] == 1
    assert att["manual_checkins"] == 1
    assert att["auto_closed_sessions_count"] == 1
    # Average verified duration must equal 60 mins (the single verified session), NOT averaged with the auto-closed session
    assert att["avg_verified_duration_minutes"] == 60


# =========================================================================
# 6. ROLE PERMISSIONS & SENSITIVE REPORTS CONFIDENTIALITY
# =========================================================================

def test_receptionist_denied_financial_analytics_and_exports(client, receptionist_user, owner_user):
    """Receptionist cannot view financial analytics or export financial reports."""
    # 1. Financial Analytics View -> 403
    client.force_login(receptionist_user)
    resp = client.get(reverse("analytics-financial"))
    assert resp.status_code == 403

    # 2. Executive Dashboard View -> 200 (financial KPI cards hidden)
    resp_dash = client.get(reverse("analytics-dashboard"))
    assert resp_dash.status_code == 200
    assert resp_dash.context["can_view_financial"] is False

    # 3. Financial Report Export -> 403
    resp_exp = client.get(
        reverse("analytics-reports") + "?report=payment_collections&export=csv"
    )
    assert resp_exp.status_code == 403

    # 4. Attendance Analytics View -> 200 (Receptionist is authorized)
    resp_att = client.get(reverse("analytics-attendance"))
    assert resp_att.status_code == 200


def test_owner_authorized_full_reports(client, owner_user):
    """Owner has full access to executive dashboard, financial analytics, and exports."""
    client.force_login(owner_user)

    for url_name in ["analytics-dashboard", "analytics-financial", "analytics-memberships", "analytics-attendance", "analytics-reports"]:
        resp = client.get(reverse(url_name))
        assert resp.status_code == 200


# =========================================================================
# 7. CSV INJECTION DEFENSE & EXPORT GENERATION TESTS
# =========================================================================

def test_csv_formula_injection_defense():
    """Verify that formula injection characters (=, +, -, @) are sanitized with a prefix quote."""
    dangerous = ["=SUM(A1:A10)", "+cmd|' /C calc'!A0", "-5+5", "@evil_formula()", "Regular Text"]
    sanitized = [sanitize_csv_cell(x) for x in dangerous]

    assert sanitized[0] == "'=SUM(A1:A10)"
    assert sanitized[1] == "'+cmd|' /C calc'!A0"
    assert sanitized[2] == "'-5+5"
    assert sanitized[3] == "'@evil_formula()"
    assert sanitized[4] == "Regular Text"


def test_report_center_exports_xlsx_pdf_csv(client, owner_user, branches):
    """Test PDF, XLSX, and CSV generation for payment collections report."""
    b1, _ = branches
    client.force_login(owner_user)

    # 1. CSV Export
    resp_csv = client.get(
        reverse("analytics-reports") + f"?report=payment_collections&branch={b1.pk}&export=csv"
    )
    assert resp_csv.status_code == 200
    assert resp_csv["Content-Type"].startswith("text/csv")
    assert "attachment; filename=" in resp_csv["Content-Disposition"]

    # 2. XLSX Export
    resp_xlsx = client.get(
        reverse("analytics-reports") + f"?report=payment_collections&branch={b1.pk}&export=xlsx"
    )
    assert resp_xlsx.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in resp_xlsx["Content-Type"]
    assert resp_xlsx.content[:2] == b"PK"  # Valid ZIP/XLSX header

    # 3. PDF Export
    resp_pdf = client.get(
        reverse("analytics-reports") + f"?report=payment_collections&branch={b1.pk}&export=pdf"
    )
    assert resp_pdf.status_code == 200
    assert resp_pdf["Content-Type"] == "application/pdf"
    assert resp_pdf.content[:4] == b"%PDF"  # Valid PDF header

    # 4. Verify audit log creation
    assert ReportExportLog.objects.filter(actor=owner_user, report_type="payment_collections").count() == 3


# =========================================================================
# 8. EMPTY DATABASE STABILITY & SVG CHARTS TESTS
# =========================================================================

def test_empty_database_analytics_and_svg_rendering(branches):
    """Verify selectors and SVG chart generators handle empty datasets gracefully."""
    b1, _ = branches
    date_range = resolve_date_filter(preset="today")

    data = get_executive_dashboard_data(Branch.objects.filter(pk=b1.pk), date_range)
    assert data["financial"]["total_collected"] == Decimal("0.00")
    assert data["memberships"]["total_registered_members"] == 0
    assert data["attendance"]["total_checkins"] == 0

    # Render empty charts
    empty_bar = render_svg_bar_chart([], label_key="label", value_key="val")
    assert "erp-chart-empty" in empty_bar

    empty_donut = render_svg_donut_chart([], label_key="name", value_key="count")
    assert "erp-chart-empty" in empty_donut

    empty_h_bar = render_svg_horizontal_bar([], label_key="name", value_key="count")
    assert "erp-chart-empty" in empty_h_bar

    # Render populated charts
    sample_data = [{"label": "Day 1", "val": 100}, {"label": "Day 2", "val": 200}]
    populated_bar = render_svg_bar_chart(sample_data, label_key="label", value_key="val")
    assert "<svg" in populated_bar

    populated_donut = render_svg_donut_chart(sample_data, label_key="label", value_key="val")
    assert "<svg" in populated_donut

    populated_h_bar = render_svg_horizontal_bar(sample_data, label_key="label", value_key="val")
    assert "erp-horizontal-bars" in populated_h_bar
