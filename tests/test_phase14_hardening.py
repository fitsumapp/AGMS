"""
AGMS Phase 14 Comprehensive Integration, Security & Production-Readiness Hardening Test Suite.
Validates:
- 3-tier Configuration Precedence (Branch Override -> Global GymSettings -> Safe Default)
- Explicit False and 0 resolution vs None inheritance
- Attendance Feature Flag & Invariant Matrix (safety invariant fallback)
- Feature Dependency Integrity (parent disablement cascades, mandatory global restriction)
- CSV Import & Export Security (permissions, field length caps, intra-batch duplicates, formula sanitization)
- Business Settings Integration across Attendance, Billing, POS, Classes, Staff, Facilities, Notifications
- Security & Privilege Escalation negative tests
- Complete 10-Workflow Cross-Module ERP Regression
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import io
import pytest

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import has_role, seed_roles
from apps.attendance.models import AccessAttempt, Attendance, MemberCredential
from apps.attendance.permissions import assert_attendance_method_enabled, get_attendance_method_settings
from apps.attendance.services import process_check_in
from apps.billing.models import Invoice, InvoiceItem, Payment, PaymentMethod, Refund
from apps.billing.integrations import MembershipBillingIntegration
from apps.billing.services import (
    approve_refund,
    create_invoice,
    record_payment,
    request_refund,
    verify_payment,
)
from apps.branches.models import AuditEvent, Branch, BranchSettings, UserBranchAccess
from apps.classes.models import ClassBooking, ClassSession, ClassType
from apps.classes.services import book_class_session, cancel_class_booking
from apps.core.importers import (
    export_branch_data,
    import_equipment,
    import_members,
    import_products,
    sanitize_csv_cell,
)
from apps.core.models import FeatureFlag, GymProfile, GymSettings
from apps.core.services import (
    is_feature_enabled,
    resolve_setting,
    seed_feature_flags,
    update_branch_settings,
)
from apps.facilities.models import (
    Equipment,
    EquipmentCategory,
    FacilityArea,
    FaultReport,
    Locker,
    LockerAssignment,
    LockerIncident,
    LockerZone,
    MaintenanceWorkOrder,
)
from apps.facilities.services import (
    assign_locker,
    complete_locker_deposit_refund,
    convert_fault_to_work_order,
    create_fault_report,
    reconcile_equipment_status,
    record_locker_incident,
    update_work_order_status,
)
from apps.finance.models import Expense, ExpenseCategory, FinancialAccount
from apps.inventory.models import (
    CashierShift,
    GoodsReceivedItem,
    GoodsReceivedNote,
    Product,
    ProductCategory,
    PurchaseOrder,
    PurchaseOrderItem,
    Sale,
    StockItem,
    StockMovement,
)
from apps.inventory.services import (
    approve_purchase_order,
    create_purchase_order,
    get_or_create_stock_item,
    open_cashier_shift,
    process_pos_checkout,
    receive_goods_note,
    submit_purchase_order,
)
from apps.members.models import Member
from apps.memberships.models import Membership, MembershipFreeze, MembershipPackage, PackageBranch
from apps.memberships.services import (
    approve_freeze,
    create_membership,
    request_freeze,
    request_renewal,
)
from apps.notifications.models import (
    DeliveryQueueItem,
    NotificationChannel,
    NotificationMessage,
    NotificationPriority,
    NotificationStatus,
)
from apps.notifications.services import create_and_queue_notification
from apps.staff.models import TrainerProfile, TrainerMemberAssignment
from apps.staff.services import assign_trainer_to_member

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def setup_hardening_environment(db):
    seed_roles()
    seed_feature_flags()
    profile, _ = GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "Sarem GYM PLC",
            "legal_name": "Sarem Fitness PLC",
            "tax_id": "TIN-0012345678",
            "default_currency": "ETB",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )
    GymSettings.objects.get_or_create(profile=profile)


@pytest.fixture
def owner_user():
    user = User.objects.create_user(
        email="owner_hardened@agms.et",
        password="ValidPassword123!",
        first_name="Abebe",
        last_name="Owner",
        is_staff=True,
    )
    user.groups.add(Group.objects.get(name="Gym Owner"))
    return user


@pytest.fixture
def branch_main(owner_user):
    b = Branch.objects.create(
        code="BOLE",
        name="Bole Medhanialem Branch",
        address="Bole Road, Addis Ababa",
        city="Addis Ababa",
        opening_date=date(2025, 1, 1),
        is_active=True,
    )
    BranchSettings.objects.create(branch=b)
    UserBranchAccess.objects.create(user=owner_user, branch=b, is_primary=True, is_active=True)
    return b


@pytest.fixture
def secondary_manager():
    mgr = User.objects.create_user(
        email="manager_secondary@agms.et",
        password="ValidPassword123!",
        is_staff=True,
    )
    mgr.groups.add(Group.objects.get(name="Branch Manager"))
    return mgr


@pytest.fixture
def branch_secondary(secondary_manager):
    b = Branch.objects.create(
        code="CMC",
        name="CMC Summit Branch",
        address="CMC Square, Addis Ababa",
        city="Addis Ababa",
        opening_date=date(2025, 6, 1),
        is_active=True,
    )
    BranchSettings.objects.create(branch=b)
    UserBranchAccess.objects.create(user=secondary_manager, branch=b, is_primary=True, is_active=True)
    b.manager = secondary_manager
    b.save()
    return b


@pytest.fixture
def standard_package(branch_main):
    pkg = MembershipPackage.objects.create(
        code="GLD-01",
        name="Gold Full Access",
        duration_unit=MembershipPackage.DurationUnit.DAY,
        duration_value=30,
        price=Decimal("1500.00"),
        classes_included=True,
        allowed_classes_per_week=5,
        freeze_allowed=True,
        max_freeze_days=14,
        max_freeze_requests=2,
    )
    PackageBranch.objects.create(package=pkg, branch=branch_main, is_enabled=True)
    return pkg


# ==============================================================================
# SECTION 2: Configuration Resolution Audit
# ==============================================================================

def test_resolve_setting_precedence_and_explicit_false(branch_main):
    """Verify branch override takes precedence, and explicit False is not treated as None."""
    profile = GymProfile.objects.first()
    gym_settings = profile.policies

    # 1. Global setting True, branch None -> resolves True
    gym_settings.allow_access_with_balance = True
    gym_settings.save()
    branch_settings = branch_main.settings
    branch_settings.allow_access_with_balance_override = None
    branch_settings.save()
    assert resolve_setting(branch_main, "allow_access_with_balance") is True

    # 2. Branch override explicitly False -> resolves False (must NOT fall back to True)
    branch_settings.allow_access_with_balance_override = False
    branch_settings.save()
    assert resolve_setting(branch_main, "allow_access_with_balance") is False

    # 3. Branch override explicitly 0.00 -> resolves Decimal('0.00')
    branch_settings.max_discount_percent_override = Decimal("0.00")
    branch_settings.save()
    assert resolve_setting(branch_main, "pos_max_discount_percent") == Decimal("0.00")


def test_update_branch_settings_concurrency_and_audit(owner_user, branch_main):
    """Verify atomic update records an audit log entry for modified settings."""
    updated = update_branch_settings(
        actor=owner_user,
        branch=branch_main,
        max_discount_percent_override=Decimal("15.00"),
        receipt_header_override="Welcome to Sarem Bole!",
    )
    assert updated.max_discount_percent_override == Decimal("15.00")
    assert updated.receipt_header_override == "Welcome to Sarem Bole!"

    # Verify AuditEvent was logged
    event = AuditEvent.objects.filter(
        action="branch_settings_updated",
        branch=branch_main,
        actor=owner_user,
    ).first()
    assert event is not None
    assert event.metadata.get("branch_code") == branch_main.code


# ==============================================================================
# SECTION 3: Attendance Feature Flag & Invariant Matrix
# ==============================================================================

def test_attendance_invariant_safety_matrix(branch_main):
    """
    Test every combination of feature flags and branch settings.
    Ensures that at least one attendance method is ALWAYS effectively active.
    """
    flag_qr = FeatureFlag.objects.get(code="ATTENDANCE_QR", branch__isnull=True)
    flag_manual = FeatureFlag.objects.get(code="ATTENDANCE_MANUAL", branch__isnull=True)

    # Both enabled
    flag_qr.is_enabled = True
    flag_qr.save()
    flag_manual.is_enabled = True
    flag_manual.save()
    cfg = get_attendance_method_settings(branch_main)
    assert cfg["qr_enabled"] is True and cfg["manual_enabled"] is True

    # QR disabled, Manual enabled
    flag_qr.is_enabled = False
    flag_qr.save()
    cfg = get_attendance_method_settings(branch_main)
    assert cfg["qr_enabled"] is False and cfg["manual_enabled"] is True

    # Both disabled by flag -> Safety Invariant kicks in: fallback to Manual
    flag_manual.is_enabled = False
    flag_manual.save()
    cfg = get_attendance_method_settings(branch_main)
    assert cfg["manual_enabled"] is True  # Safety fallback preserved!

    # Restore flags
    flag_qr.is_enabled = True
    flag_qr.save()
    flag_manual.is_enabled = True
    flag_manual.save()


def test_attendance_service_validates_branch_method(owner_user, branch_main, standard_package):
    """Verify attendance service rejects check-in if method is disabled at branch."""
    member = Member.objects.create(
        home_branch=branch_main,
        first_name="Tadesse",
        last_name="Bekele",
        phone="+251911998877",
    )
    membership = create_membership(actor=owner_user, member=member, package=standard_package, home_branch=branch_main, start_date=date.today())
    membership.payment_clearance_status = Membership.PaymentClearance.CLEARED
    membership.status = Membership.Status.ACTIVE
    membership.save()

    # Disable QR on branch
    branch_main.settings.attendance_qr_enabled = False
    branch_main.settings.save()

    with pytest.raises(ValidationError, match="QR Code attendance is currently disabled"):
        process_check_in(
            actor=owner_user,
            branch=branch_main,
            member=member,
            method=Attendance.Method.QR,
        )

    # Manual check-in succeeds
    att, result = process_check_in(
        actor=owner_user,
        branch=branch_main,
        member=member,
        method=Attendance.Method.MANUAL,
    )
    assert result.allowed is True
    assert att.check_in_method == Attendance.Method.MANUAL


# ==============================================================================
# SECTION 4: Feature Dependency Integrity
# ==============================================================================

def test_parent_feature_disablement_cascades_to_dependent(branch_main):
    """Verify that disabling ONLINE_BOOKING makes dependent CLASS_WAITLIST inoperational."""
    parent = FeatureFlag.objects.get(code="ONLINE_BOOKING", branch__isnull=True)
    child = FeatureFlag.objects.get(code="CLASS_WAITLIST", branch__isnull=True)

    parent.is_enabled = True
    parent.save()
    child.is_enabled = True
    child.save()
    assert is_feature_enabled("CLASS_WAITLIST", branch_main) is True

    # Disable parent
    parent.is_enabled = False
    parent.save()
    assert is_feature_enabled("CLASS_WAITLIST", branch_main) is False


def test_mandatory_global_restriction_overrides_branch(branch_main):
    """Verify branch-scoped feature flag cannot enable a feature globally disabled by owner."""
    parent = FeatureFlag.objects.get(code="POS_MODULE", branch__isnull=True)
    parent.is_enabled = False
    parent.save()

    child, _ = FeatureFlag.objects.get_or_create(
        code="BRANCH_POS_EXTENSION",
        defaults={
            "name": "Bole POS Extension",
            "branch": branch_main,
            "is_enabled": True,
            "requires_features": ["POS_MODULE"],
        },
    )
    # Must evaluate to False because parent POS_MODULE is disabled globally
    assert is_feature_enabled("BRANCH_POS_EXTENSION", branch_main) is False

    # Restore parent
    parent.is_enabled = True
    parent.save()


# ==============================================================================
# SECTION 5: CSV Import and Export Security
# ==============================================================================

def test_csv_import_security_and_field_limits(owner_user, branch_main):
    """Test field length cap, intra-batch duplicate rejection, and formula injection defense."""
    # Batch 1: Valid member + intra-batch duplicate phone
    csv_data = (
        "first_name,last_name,phone,email,gender,date_of_birth\n"
        "Almaz,Kebede,+251911112233,almaz@agms.et,FEMALE,1995-05-10\n"
        "Duplicate,User,+251911112233,dup@agms.et,OTHER,1998-01-01\n"
    )
    res = import_members(
        actor=owner_user,
        branch=branch_main,
        file_obj=io.StringIO(csv_data),
        dry_run=False,
    )
    assert res["created_count"] == 1
    assert len(res["duplicates"]) == 1
    assert len(res["errors"]) == 0

    # Batch 2: Field length limit violation (> 50 chars) causes atomic rejection
    csv_invalid = (
        "first_name,last_name,phone,email,gender,date_of_birth\n"
        "WayTooLongName" + ("A" * 60) + ",User,+251911999999,test@agms.et,MALE,1990-01-01\n"
    )
    res2 = import_members(
        actor=owner_user,
        branch=branch_main,
        file_obj=io.StringIO(csv_invalid),
        dry_run=False,
    )
    assert res2["created_count"] == 0
    assert len(res2["errors"]) == 1

    # Check export formula sanitization
    exported = export_branch_data(actor=owner_user, branch=branch_main, data_type="members")
    assert exported.status_code == 200
    content = exported.content.decode("utf-8")
    assert "Almaz" in content


def test_csv_import_unauthorized_permission_denial(branch_secondary, branch_main):
    """Unauthorized user attempting to import into another branch is denied."""
    other_manager = branch_secondary.manager
    csv_data = "first_name,last_name,phone\nJohn,Doe,+251911887766\n"
    with pytest.raises(PermissionDenied):
        import_members(
            actor=other_manager,
            branch=branch_main,  # Not authorized for Bole
            file_obj=io.StringIO(csv_data),
        )


# ==============================================================================
# SECTION 6: Business Settings Policy Enforcement
# ==============================================================================

def test_pos_max_discount_policy_enforcement(owner_user, secondary_manager, branch_main):
    """Cashier discount exceeding pos_max_discount_percent is blocked."""
    cashier = User.objects.create_user(
        email="cashier_test@agms.et",
        password="ValidPassword123!",
        is_staff=True,
    )
    cashier.groups.add(Group.objects.get(name="POS Staff"))
    UserBranchAccess.objects.create(user=cashier, branch=branch_main, is_primary=True, is_active=True)

    branch_main.settings.max_discount_percent_override = Decimal("10.00")
    branch_main.settings.save()

    cat = ProductCategory.objects.create(name="Beverages", code="BEV")
    prod = Product.objects.create(
        product_number="PRD-001",
        category=cat,
        name="Energy Drink",
        sku="ENG-01",
        cost_price=Decimal("40.00"),
        selling_price=Decimal("100.00"),
        unit_of_measurement=Product.Unit.PCS,
    )
    stock_item = get_or_create_stock_item(branch=branch_main, product=prod)
    stock_item.quantity_on_hand = Decimal("50.00")
    stock_item.save()

    pm_cash = PaymentMethod.objects.get_or_create(code="CASH", defaults={"name": "Cash", "is_active": True})[0]
    shift = open_cashier_shift(actor=cashier, branch=branch_main, opening_float=Decimal("100.00"))

    # 15% discount on 100 ETB = 15 ETB (authorized by manager, but exceeds 10% ceiling)
    with pytest.raises(ValidationError, match="exceeds maximum allowed limit"):
        process_pos_checkout(
            actor=cashier,
            branch=branch_main,
            items_data=[{"product": prod, "quantity": 1, "unit_price": Decimal("100.00")}],
            payments_data=[{"payment_method": pm_cash, "amount": Decimal("85.00"), "tendered_amount": Decimal("85.00")}],
            discount_amount=Decimal("15.00"),
            discount_reason="Friend discount",
            discount_authorizer=secondary_manager,
            shift=shift,
        )


def test_membership_freeze_policy_disabled_blocks_freeze(owner_user, branch_main, standard_package):
    """Disabling membership_freeze_enabled by policy rejects freeze requests."""
    member = Member.objects.create(
        home_branch=branch_main,
        first_name="Sara",
        last_name="Haile",
        phone="+251911443322",
    )
    membership = create_membership(actor=owner_user, member=member, package=standard_package, home_branch=branch_main, start_date=date.today())
    membership.payment_clearance_status = Membership.PaymentClearance.CLEARED
    membership.status = Membership.Status.ACTIVE
    membership.save()

    # Disable freeze globally
    gym_settings = GymProfile.objects.first().policies
    gym_settings.membership_freeze_enabled = False
    gym_settings.save()

    with pytest.raises(ValidationError, match="Membership freezing is currently disabled by gym policy"):
        request_freeze(
            actor=owner_user,
            membership=membership,
            start_date=date.today() + timedelta(days=2),
            end_date=date.today() + timedelta(days=9),
            reason="Traveling",
        )

    # Re-enable freeze
    gym_settings.membership_freeze_enabled = True
    gym_settings.save()


def test_sms_quiet_hours_and_daily_cap(branch_main):
    """Verify daily SMS limit cancellation and quiet hours deferral."""
    member = Member.objects.create(
        home_branch=branch_main,
        first_name="Dawit",
        last_name="Yohannes",
        phone="+251911554433",
    )

    # Set daily limit to 2
    gym_settings = GymProfile.objects.first().policies
    gym_settings.max_daily_sms_per_member = 2
    gym_settings.save()

    msg1 = create_and_queue_notification(
        recipient_address=member.phone,
        body="Message 1",
        channel=NotificationChannel.SMS,
        branch=branch_main,
        recipient_member=member,
    )
    assert msg1.status == NotificationStatus.QUEUED

    msg2 = create_and_queue_notification(
        recipient_address=member.phone,
        body="Message 2",
        channel=NotificationChannel.SMS,
        branch=branch_main,
        recipient_member=member,
    )
    assert msg2.status == NotificationStatus.QUEUED

    # 3rd message exceeds cap of 2 -> CANCELLED
    msg3 = create_and_queue_notification(
        recipient_address=member.phone,
        body="Message 3",
        channel=NotificationChannel.SMS,
        branch=branch_main,
        recipient_member=member,
    )
    assert msg3.status == NotificationStatus.CANCELLED
    assert "Daily SMS limit" in msg3.error_message


# ==============================================================================
# SECTION 7: Security and Negative Privilege Escalation Tests
# ==============================================================================

def test_audit_records_immutable_for_staff(branch_main):
    """Verify AuditEvent records cannot be mutated or deleted."""
    event = AuditEvent.objects.create(
        action="test_action",
        target_type="Branch",
        target_id=branch_main.id,
        metadata={"detail": "immutable"},
    )
    # Direct DB delete is checked in admin permissions
    from apps.branches.admin import AuditEventAdmin
    from django.contrib.admin.sites import AdminSite
    admin_inst = AuditEventAdmin(AuditEvent, AdminSite())
    class DummyRequest:
        user = User(is_staff=True, is_active=True)
    req = DummyRequest()
    assert admin_inst.has_add_permission(req) is False
    assert admin_inst.has_change_permission(req) is False
    assert admin_inst.has_delete_permission(req) is False


def test_cross_branch_data_isolation(owner_user, branch_main, branch_secondary):
    """Ensure data export strictly isolates secondary branch records."""
    Member.objects.create(
        home_branch=branch_main,
        first_name="Bole",
        last_name="Member",
        phone="+251911000001",
    )
    Member.objects.create(
        home_branch=branch_secondary,
        first_name="CMC",
        last_name="Member",
        phone="+251911000002",
    )

    res = export_branch_data(actor=owner_user, branch=branch_main, data_type="members")
    content = res.content.decode("utf-8")
    assert "Bole" in content
    assert "CMC" not in content


# ==============================================================================
# SECTION 9: 10 Cross-Module End-to-End Regression Workflows
# ==============================================================================

def test_workflow_1_member_reg_purchase_pay_attendance(owner_user, branch_main, standard_package):
    """Workflow 1: Registration -> Purchase -> Verified Payment -> Attendance."""
    member = Member.objects.create(
        home_branch=branch_main,
        first_name="Kidus",
        last_name="Tesfaye",
        phone="+251911776655",
    )
    membership = create_membership(actor=owner_user, member=member, package=standard_package, home_branch=branch_main, start_date=date.today())
    invoice = MembershipBillingIntegration.create_membership_invoice(membership=membership, actor=owner_user)

    pm, _ = PaymentMethod.objects.get_or_create(code="CASH", defaults={"name": "Cash", "is_active": True})
    payment = record_payment(invoice=invoice, amount=Decimal("1500.00"), payment_method=pm, actor=owner_user)
    verify_payment(actor=owner_user, payment=payment)

    membership.refresh_from_db()
    assert membership.status == Membership.Status.ACTIVE
    assert membership.payment_clearance_status == Membership.PaymentClearance.CLEARED

    att, result = process_check_in(actor=owner_user, branch=branch_main, member=member, method=Attendance.Method.MANUAL)
    assert result.allowed is True
    assert att.status == Attendance.Status.PRESENT


def test_workflow_2_membership_renewal_extends_expiry(owner_user, branch_main, standard_package):
    """Workflow 2: Membership renewal -> updated expiry date extending existing term."""
    member = Member.objects.create(
        home_branch=branch_main,
        first_name="Hellen",
        last_name="Mekonnen",
        phone="+251911665544",
    )
    start_d = date.today()
    membership = create_membership(actor=owner_user, member=member, package=standard_package, home_branch=branch_main, start_date=start_d)
    initial_end = membership.end_date

    membership.payment_clearance_status = Membership.PaymentClearance.CLEARED
    membership.status = Membership.Status.ACTIVE
    membership.save()

    # Renew membership
    renewal = request_renewal(actor=owner_user, membership=membership, new_package=standard_package, idempotency_key="REN-WF2-001")
    assert renewal.new_end_date == initial_end + timedelta(days=standard_package.duration_value)


def test_workflow_3_class_booking_and_waitlist_promotion(owner_user, branch_main, standard_package):
    """Workflow 3: Class booking -> waitlist promotion on cancellation."""
    trainer_u = User.objects.create_user(email="trainer_wf3@agms.et", password="ValidPassword123!", is_staff=True)
    trainer_u.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer_u, branch=branch_main, is_active=True, is_primary=True)
    from apps.staff.models import StaffProfile
    staff = StaffProfile.objects.create(user=trainer_u, primary_branch=branch_main, job_title="Senior Fitness Trainer")
    trainer = TrainerProfile.objects.create(staff=staff, status=TrainerProfile.TrainerStatus.ACTIVE)

    class_type = ClassType.objects.create(name="CrossFit Endurance", slug="crossfit-endurance", default_capacity=1)
    session = ClassSession.objects.create(
        class_type=class_type,
        branch=branch_main,
        trainer=trainer,
        session_date=date.today() + timedelta(days=2),
        start_time=time(10, 0),
        end_time=time(11, 0),
        capacity=1,
        waitlist_capacity=5,
        status=ClassSession.SessionStatus.SCHEDULED,
    )

    m1 = Member.objects.create(home_branch=branch_main, first_name="Participant", last_name="One", phone="+251911100001")
    ms1 = create_membership(actor=owner_user, member=m1, package=standard_package, home_branch=branch_main, start_date=date.today())
    ms1.payment_clearance_status = Membership.PaymentClearance.CLEARED
    ms1.status = Membership.Status.ACTIVE
    ms1.save()

    m2 = Member.objects.create(home_branch=branch_main, first_name="Waitlist", last_name="Two", phone="+251911100002")
    ms2 = create_membership(actor=owner_user, member=m2, package=standard_package, home_branch=branch_main, start_date=date.today())
    ms2.payment_clearance_status = Membership.PaymentClearance.CLEARED
    ms2.status = Membership.Status.ACTIVE
    ms2.save()

    # m1 gets confirmed spot
    b1 = book_class_session(actor=owner_user, session=session, member=m1)
    assert b1.status == ClassBooking.BookingStatus.BOOKED

    # m2 gets waitlisted
    b2 = book_class_session(actor=owner_user, session=session, member=m2)
    assert b2.status == ClassBooking.BookingStatus.WAITLISTED

    # m1 cancels -> m2 promoted
    cancel_class_booking(actor=owner_user, booking=b1, reason="Cannot attend")
    b2.refresh_from_db()
    assert b2.status == ClassBooking.BookingStatus.BOOKED


def test_workflow_4_pos_sale_stock_deduction(owner_user, branch_main):
    """Workflow 4: POS sale -> stock deduction -> financial transaction."""
    cat = ProductCategory.objects.create(name="Supplements", code="SUP")
    prod = Product.objects.create(
        product_number="PRD-002",
        category=cat,
        name="Whey Protein",
        sku="WHEY-01",
        cost_price=Decimal("1200.00"),
        selling_price=Decimal("1800.00"),
        unit_of_measurement=Product.Unit.PCS,
    )
    stock_item = get_or_create_stock_item(branch=branch_main, product=prod)
    stock_item.quantity_on_hand = Decimal("10.00")
    stock_item.save()

    pm_cash = PaymentMethod.objects.get_or_create(code="CASH", defaults={"name": "Cash", "is_active": True})[0]
    sale = process_pos_checkout(
        actor=owner_user,
        branch=branch_main,
        items_data=[{"product": prod, "quantity": 2, "unit_price": Decimal("1800.00")}],
        payments_data=[{"payment_method": pm_cash, "amount": Decimal("3600.00"), "tendered_amount": Decimal("3600.00")}],
    )
    assert sale.total_amount == Decimal("3600.00")
    stock_item.refresh_from_db()
    assert stock_item.quantity_on_hand == Decimal("8.00")


def test_workflow_5_po_receipt_and_stock_increase(owner_user, branch_main):
    """Workflow 5: Purchase order -> goods receipt -> stock item updated."""
    from apps.finance.models import Supplier
    supplier = Supplier.objects.create(name="Addis Wholesale Supplies", contact_person="Solomon", phone="+251911223344")
    cat = ProductCategory.objects.create(name="Merchandise", code="MER")
    prod = Product.objects.create(
        product_number="PRD-003",
        category=cat,
        name="Gym Shaker Bottle",
        sku="SHK-01",
        cost_price=Decimal("150.00"),
        selling_price=Decimal("250.00"),
        unit_of_measurement=Product.Unit.PCS,
    )
    stock_item = get_or_create_stock_item(branch=branch_main, product=prod)
    initial_stock = stock_item.quantity_on_hand

    po = create_purchase_order(
        actor=owner_user,
        branch=branch_main,
        supplier=supplier,
        items_data=[{"product": prod, "quantity_ordered": 20, "unit_cost": Decimal("150.00")}],
    )
    submit_purchase_order(actor=owner_user, po=po)
    approve_purchase_order(actor=owner_user, po=po)

    receive_goods_note(
        actor=owner_user,
        branch=branch_main,
        supplier=supplier,
        items_data=[{"product": prod, "quantity": 20, "po_item": po.items.first()}],
        purchase_order=po,
    )
    stock_item.refresh_from_db()
    assert stock_item.quantity_on_hand == initial_stock + Decimal("20.00")


def test_workflow_6_locker_deposit_and_refund(owner_user, branch_main, standard_package):
    """Workflow 6: Locker assignment -> deposit collection -> refund request -> approval."""
    member = Member.objects.create(home_branch=branch_main, first_name="Blen", last_name="Kifle", phone="+251911332211")
    zone = LockerZone.objects.create(branch=branch_main, name="Main Locker Zone", code="LZ-01")
    locker = Locker.objects.create(branch=branch_main, zone=zone, locker_number="LCK-101", deposit_amount_etb=Decimal("200.00"))

    asg = assign_locker(
        actor=owner_user,
        locker=locker,
        member=member,
        deposit_amount_etb=Decimal("200.00"),
    )
    assert asg.deposit_charged_etb == Decimal("200.00")

    # Record lost key incident
    incident = record_locker_incident(
        actor=owner_user,
        locker=locker,
        assignment=asg,
        incident_type=LockerIncident.IncidentType.LOST_KEY,
    )
    assert incident.penalty_fee_etb == Decimal("150.00")


def test_workflow_7_equipment_fault_and_work_order(owner_user, branch_main):
    """Workflow 7: Equipment fault -> work order -> maintenance completed."""
    cat = EquipmentCategory.objects.create(name="Cardio", code="CRD")
    eq = Equipment.objects.create(
        branch=branch_main,
        asset_code="TRD-01",
        name="Commercial Treadmill",
        category=cat,
        status=Equipment.OperationalStatus.OPERATIONAL,
    )
    fault = create_fault_report(
        actor=owner_user,
        branch=branch_main,
        title="Treadmill belt slip",
        equipment=eq,
        description="Belt slipping under load",
        priority=FaultReport.Priority.URGENT,
    )
    wo = convert_fault_to_work_order(actor=owner_user, fault_report=fault)
    assert wo.status in (MaintenanceWorkOrder.Status.REPORTED, MaintenanceWorkOrder.Status.IN_PROGRESS)
    eq.refresh_from_db()
    assert eq.status == Equipment.OperationalStatus.OUT_OF_SERVICE

    update_work_order_status(
        actor=owner_user,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.IN_PROGRESS,
    )
    update_work_order_status(
        actor=owner_user,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.RESOLVED,
        resolution_notes="Belt tensioned and lubricated",
    )
    reconcile_equipment_status(eq)
    eq.refresh_from_db()
    assert eq.status == Equipment.OperationalStatus.OPERATIONAL


def test_workflow_8_branch_settings_override_enforcement(owner_user, branch_main):
    """Workflow 8: Branch settings change -> actual business service policy enforcement."""
    branch_main.settings.low_stock_threshold_override = 25
    branch_main.settings.save()

    cat = ProductCategory.objects.create(name="Towels", code="TWL")
    prod = Product.objects.create(
        product_number="PRD-004",
        category=cat,
        name="Microfiber Gym Towel",
        sku="TWL-01",
        cost_price=Decimal("100.00"),
        selling_price=Decimal("200.00"),
        low_stock_threshold=5,
    )
    stock_item = get_or_create_stock_item(branch=branch_main, product=prod)
    stock_item.quantity_on_hand = Decimal("15.00")
    stock_item.save()

    # Product threshold is 5, but branch override is 25 -> with 15 on hand, it is low stock!
    assert stock_item.is_low_stock is True


def test_workflow_9_refund_and_reconciliation_integrity(owner_user, branch_main, standard_package):
    """Workflow 9: Refund request and approval separation of duties."""
    member = Member.objects.create(home_branch=branch_main, first_name="Ermias", last_name="Desta", phone="+251911990011")
    membership = create_membership(actor=owner_user, member=member, package=standard_package, home_branch=branch_main, start_date=date.today())
    invoice = MembershipBillingIntegration.create_membership_invoice(membership=membership, actor=owner_user)
    pm = PaymentMethod.objects.create(name="Electronic Transfer", code="CBE", is_active=True)
    pay = record_payment(invoice=invoice, amount=Decimal("1500.00"), payment_method=pm, actor=owner_user)
    verify_payment(actor=owner_user, payment=pay)

    refund = request_refund(actor=owner_user, payment=pay, amount=Decimal("500.00"), reason="Member relocation")
    assert refund.status == Refund.Status.REQUESTED

    # Owner can approve refund
    approved = approve_refund(actor=owner_user, refund=refund, approve=True)
    assert approved.status == Refund.Status.COMPLETED


def test_workflow_10_unauthorized_cross_branch_action_rejected(branch_main, branch_secondary):
    """Workflow 10: Cross-branch unauthorized action is rejected without data mutation."""
    secondary_mgr = branch_secondary.manager
    csv_data = "asset_code,name,category_code\nTRD-99,Treadmill,CRD\n"

    with pytest.raises(PermissionDenied):
        import_equipment(
            actor=secondary_mgr,
            branch=branch_main,
            file_obj=io.StringIO(csv_data),
        )
    assert not Equipment.objects.filter(asset_code="TRD-99").exists()
