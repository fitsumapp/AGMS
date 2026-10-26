import hashlib
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.roles import seed_roles
from apps.attendance.models import (
    AccessAttempt,
    AccessDevice,
    Attendance,
    MemberCredential,
)
from apps.attendance.services import process_check_in
from apps.billing.models import (
    Invoice,
    InvoiceItem,
    Payment,
    PaymentMethod,
    Receipt,
    Refund,
)
from apps.billing.services import (
    approve_refund,
    create_invoice,
    issue_invoice,
    record_payment,
    request_refund,
)
from apps.branches.models import Branch, BranchSettings, UserBranchAccess
from apps.branches.services import can_access_branch
from apps.classes.models import (
    ClassBooking,
    ClassSession,
    ClassType,
)
from apps.classes.services import (
    book_class_session,
    cancel_class_booking,
    mark_class_attendance,
)
from apps.facilities.models import (
    Equipment,
    EquipmentCategory,
    FacilityArea,
    Locker,
    LockerAssignment,
    LockerZone,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
)
from apps.facilities.services import (
    assign_locker,
    release_locker,
)
from apps.finance.models import (
    BranchFinanceSettings,
    Expense,
    ExpenseCategory,
    FinancialAccount,
)
from apps.finance.services import (
    approve_expense,
    create_expense,
    record_expense_payment,
)
from apps.inventory.models import (
    CashierShift,
    Product,
    ProductCategory,
    Sale,
    SaleItem,
    SalePayment,
    StockItem,
)
from apps.inventory.services import (
    close_cashier_shift,
    open_cashier_shift,
    process_pos_checkout,
)
from apps.members.models import Member, MemberEmergencyContact
from apps.members.services import register_member
from apps.memberships.models import (
    Membership,
    MembershipFreeze,
    MembershipPackage,
    PackageBranch,
)
from apps.memberships.services import (
    activate_membership,
    approve_freeze,
    create_membership,
    request_freeze,
)
from apps.notifications.services import (
    render_template_safely,
)
from apps.staff.models import (
    LeaveRequest,
    StaffProfile,
    StaffShift,
    TrainerProfile,
)
from apps.staff.services import (
    terminate_staff,
)

User = get_user_model()


@pytest.fixture(autouse=True)
def setup_roles_and_branches(db):
    seed_roles()


@pytest.fixture
def test_setup(db):
    """Create isolated test gym setup with 2 branches and staff."""
    branch_a = Branch.objects.create(
        code=f"BR-A-{uuid.uuid4().hex[:6].upper()}",
        name="Branch Alpha",
        address="Bole Alpha 123",
        city="Addis Ababa",
        is_active=True,
    )
    branch_b = Branch.objects.create(
        code=f"BR-B-{uuid.uuid4().hex[:6].upper()}",
        name="Branch Beta",
        address="CMC Beta 456",
        city="Addis Ababa",
        is_active=True,
    )

    owner = User.objects.create_user(
        f"owner_{uuid.uuid4().hex[:6]}@example.test",
        "Pass123!",
        is_staff=True,
    )
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    mgr_a = User.objects.create_user(
        f"mgr_a_{uuid.uuid4().hex[:6]}@example.test",
        "Pass123!",
        is_staff=True,
    )
    mgr_a.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_a, branch=branch_a, is_primary=True, is_active=True)
    branch_a.manager = mgr_a
    branch_a.save()

    mgr_b = User.objects.create_user(
        f"mgr_b_{uuid.uuid4().hex[:6]}@example.test",
        "Pass123!",
        is_staff=True,
    )
    mgr_b.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_b, branch=branch_b, is_primary=True, is_active=True)
    branch_b.manager = mgr_b
    branch_b.save()

    rec_a = User.objects.create_user(
        f"rec_a_{uuid.uuid4().hex[:6]}@example.test",
        "Pass123!",
        is_staff=True,
    )
    rec_a.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=rec_a, branch=branch_a, is_primary=True, is_active=True)

    acct = User.objects.create_user(
        f"acct_{uuid.uuid4().hex[:6]}@example.test",
        "Pass123!",
        is_staff=True,
    )
    acct.groups.add(Group.objects.get(name="Accountant"))
    UserBranchAccess.objects.create(user=acct, branch=branch_a, is_primary=True, is_active=True)

    pos_staff = User.objects.create_user(
        f"pos_{uuid.uuid4().hex[:6]}@example.test",
        "Pass123!",
        is_staff=True,
    )
    pos_staff.groups.add(Group.objects.get(name="POS Staff"))
    UserBranchAccess.objects.create(user=pos_staff, branch=branch_a, is_primary=True, is_active=True)

    trainer_u = User.objects.create_user(
        f"trainer_{uuid.uuid4().hex[:6]}@example.test",
        "Pass123!",
        is_staff=True,
    )
    trainer_u.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer_u, branch=branch_a, is_primary=True, is_active=True)
    trainer_sp = StaffProfile.objects.create(
        user=trainer_u,
        employee_number=f"EMP-{uuid.uuid4().hex[:4].upper()}",
        primary_branch=branch_a,
        job_title="Performance Coach",
        department=StaffProfile.Department.FITNESS,
        employment_type=StaffProfile.EmploymentType.FULL_TIME,
        employment_status="ACTIVE",
        hire_date=date(2024, 1, 1),
    )
    trainer_tp = TrainerProfile.objects.create(
        staff=trainer_sp,
        specializations="HIIT & Strength",
        experience_years=5,
        status="ACTIVE",
    )

    cash_method, _ = PaymentMethod.objects.get_or_create(
        code="TEST-CASH",
        defaults={"name": "Cash", "method_type": PaymentMethod.MethodType.CASH, "is_active": True},
    )

    pkg_single = MembershipPackage.objects.create(
        code=f"PKG-STD-{uuid.uuid4().hex[:4].upper()}",
        name="Standard 1M",
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        duration_value=1,
        price=Decimal("1500.00"),
        cross_branch_enabled=False,
        freeze_allowed=True,
        max_freeze_days=30,
        max_freeze_requests=1,
        is_active=True,
    )
    PackageBranch.objects.create(package=pkg_single, branch=branch_a, is_enabled=True)

    pkg_multi = MembershipPackage.objects.create(
        code=f"PKG-VIP-{uuid.uuid4().hex[:4].upper()}",
        name="VIP Multi-Branch 1Y",
        duration_unit=MembershipPackage.DurationUnit.YEAR,
        duration_value=1,
        price=Decimal("15000.00"),
        cross_branch_enabled=True,
        freeze_allowed=True,
        max_freeze_days=60,
        max_freeze_requests=2,
        is_active=True,
    )
    PackageBranch.objects.create(package=pkg_multi, branch=branch_a, is_enabled=True)
    PackageBranch.objects.create(package=pkg_multi, branch=branch_b, is_enabled=True)

    return {
        "branch_a": branch_a,
        "branch_b": branch_b,
        "owner": owner,
        "mgr_a": mgr_a,
        "mgr_b": mgr_b,
        "rec_a": rec_a,
        "acct": acct,
        "pos_staff": pos_staff,
        "trainer_u": trainer_u,
        "trainer_sp": trainer_sp,
        "trainer_tp": trainer_tp,
        "cash_method": cash_method,
        "pkg_single": pkg_single,
        "pkg_multi": pkg_multi,
    }


# ==============================================================================
# WORKFLOW 01 & 04: Member Lifecycle, Billing & Payment
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_01_complete_member_lifecycle(test_setup):
    """
    Test complete lifecycle:
    Registration -> Membership Purchase -> Invoice Creation -> Payment -> Activation.
    """
    setup = test_setup
    rec = setup["rec_a"]
    acct = setup["acct"]
    branch_a = setup["branch_a"]
    pkg = setup["pkg_single"]
    cash = setup["cash_method"]

    # 1. Register Member
    phone = f"+251911{uuid.uuid4().int % 900000 + 100000:06d}"
    member = register_member(
        actor=rec,
        home_branch=branch_a,
        first_name="Abdi",
        last_name="Bekele",
        phone=phone,
        email="abdi.test@example.test",
        gender="MALE",
        date_of_birth=date(1995, 5, 20),
    )
    assert member.pk is not None
    assert member.home_branch == branch_a
    assert member.status == Member.MemberStatus.ACTIVE

    # 2. Purchase Membership
    membership = create_membership(
        actor=rec,
        member=member,
        package=pkg,
        home_branch=branch_a,
    )
    assert membership.status == Membership.Status.PENDING
    assert membership.payment_clearance_status == Membership.PaymentClearance.PENDING
    assert membership.activation_status == Membership.ActivationStatus.NOT_ACTIVATED

    # 3. Create Invoice
    invoice = create_invoice(
        actor=rec,
        member=member,
        branch=branch_a,
        items=[{
            "description": f"Package {pkg.name}",
            "quantity": 1,
            "unit_price": pkg.price,
            "item_type": "PACKAGE",
        }],
        membership=membership,
    )
    assert invoice.status == Invoice.Status.ISSUED
    assert invoice.total_amount == Decimal("1500.00")
    assert invoice.balance_due == Decimal("1500.00")

    # 4. Record Payment
    payment = record_payment(
        actor=acct,
        invoice=invoice,
        amount=Decimal("1500.00"),
        payment_method=cash,
        transaction_reference=f"TXN-{uuid.uuid4().hex[:8]}",
    )
    assert payment.status == Payment.Status.VERIFIED
    invoice.refresh_from_db()
    assert invoice.status == Invoice.Status.PAID
    assert invoice.balance_due == Decimal("0.00")

    # 5. Activate Membership
    activate_membership(actor=rec, membership=membership, payment_reference=payment.transaction_reference)
    membership.refresh_from_db()
    assert membership.status == Membership.Status.ACTIVE
    assert membership.payment_clearance_status == Membership.PaymentClearance.CLEARED
    assert membership.activation_status == Membership.ActivationStatus.ACTIVATED


# ==============================================================================
# WORKFLOW 02: Membership Policies (Freeze, Cross-Branch, Limits)
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_02_membership_policies_and_freezes(test_setup):
    """Verify freeze request and manager approval."""
    setup = test_setup
    rec = setup["rec_a"]
    mgr_a = setup["mgr_a"]
    branch_a = setup["branch_a"]
    pkg = setup["pkg_single"]

    member = register_member(
        actor=rec,
        home_branch=branch_a,
        first_name="Chala",
        last_name="Tadesse",
        phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}",
    )
    membership = create_membership(actor=rec, member=member, package=pkg, home_branch=branch_a)
    membership.payment_clearance_status = Membership.PaymentClearance.CLEARED
    membership.status = Membership.Status.ACTIVE
    membership.activation_status = Membership.ActivationStatus.ACTIVATED
    membership.save()

    # Request Freeze
    today = timezone.localdate()
    freeze = request_freeze(
        actor=rec,
        membership=membership,
        start_date=today + timedelta(days=2),
        end_date=today + timedelta(days=12),
        reason="Medical advice",
    )
    assert freeze.status == "REQUESTED"
    assert freeze.freeze_days == 10

    # Approve Freeze
    approve_freeze(actor=mgr_a, freeze=freeze)
    freeze.refresh_from_db()
    assert freeze.status == "APPROVED"
    assert freeze.approved_by == mgr_a


# ==============================================================================
# WORKFLOW 03: Attendance & Access Control
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_03_attendance_qr_and_branch_restriction(test_setup):
    """Verify QR check-in, duplicate prevention, and cross-branch access rules."""
    setup = test_setup
    rec = setup["rec_a"]
    branch_a = setup["branch_a"]
    branch_b = setup["branch_b"]
    pkg_single = setup["pkg_single"]
    pkg_multi = setup["pkg_multi"]

    # Member 1: Single-branch package at Branch A
    m1 = register_member(actor=rec, home_branch=branch_a, first_name="Desta", last_name="Hailu", phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}")
    ms1 = create_membership(actor=rec, member=m1, package=pkg_single, home_branch=branch_a)
    ms1.payment_clearance_status = Membership.PaymentClearance.CLEARED
    ms1.status = Membership.Status.ACTIVE
    ms1.activation_status = Membership.ActivationStatus.ACTIVATED
    ms1.save()

    token_str = f"TOKEN-{uuid.uuid4().hex}"
    MemberCredential.objects.create(
        member=m1,
        credential_type=MemberCredential.CredentialType.QR,
        token_hash=hashlib.sha256(token_str.encode()).hexdigest(),
        status=MemberCredential.Status.ACTIVE,
    )

    # Valid check-in at home branch
    att, result = process_check_in(branch=branch_a, actor=rec, raw_token=token_str)
    assert att is not None
    assert att.member == m1
    assert att.status == Attendance.Status.PRESENT

    # Denied check-in at Branch B (Single-branch restriction)
    with pytest.raises(ValidationError):
        process_check_in(branch=branch_b, actor=setup["mgr_b"], raw_token=token_str)

    # Member 2: VIP multi-branch package
    m2 = register_member(actor=rec, home_branch=branch_a, first_name="Elsa", last_name="Kassa", phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}")
    ms2 = create_membership(actor=rec, member=m2, package=pkg_multi, home_branch=branch_a)
    ms2.payment_clearance_status = Membership.PaymentClearance.CLEARED
    ms2.status = Membership.Status.ACTIVE
    ms2.activation_status = Membership.ActivationStatus.ACTIVATED
    ms2.save()

    token_str_2 = f"TOKEN-VIP-{uuid.uuid4().hex}"
    MemberCredential.objects.create(
        member=m2,
        credential_type=MemberCredential.CredentialType.QR,
        token_hash=hashlib.sha256(token_str_2.encode()).hexdigest(),
        status=MemberCredential.Status.ACTIVE,
    )

    # Allowed at Branch B because VIP package has cross_branch_enabled=True
    att2, result2 = process_check_in(branch=branch_b, actor=setup["mgr_b"], raw_token=token_str_2)
    assert att2 is not None
    assert att2.member == m2
    assert att2.branch == branch_b


# ==============================================================================
# WORKFLOW 05: Staff & Termination Access Revocation
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_05_staff_termination_and_revocation(test_setup):
    """Verify staff termination revokes branch access and inactivates user."""
    setup = test_setup
    owner = setup["owner"]
    trainer_sp = setup["trainer_sp"]
    trainer_u = setup["trainer_u"]
    branch_a = setup["branch_a"]

    terminate_staff(
        actor=owner,
        staff_profile=trainer_sp,
        termination_date=timezone.localdate(),
        reason="Voluntary Resignation",
    )
    trainer_sp.refresh_from_db()
    trainer_u.refresh_from_db()

    assert trainer_sp.employment_status == "TERMINATED"
    assert not trainer_u.is_active
    assert not UserBranchAccess.objects.filter(user=trainer_u, branch=branch_a, is_active=True).exists()


# ==============================================================================
# WORKFLOW 06: Group Classes & Bookings
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_06_group_classes_booking_and_attendance(test_setup):
    """Verify class creation, member booking, and attendance marking."""
    setup = test_setup
    rec = setup["rec_a"]
    owner = setup["owner"]
    trainer_tp = setup["trainer_tp"]
    branch_a = setup["branch_a"]
    pkg = setup["pkg_multi"]

    ctype = ClassType.objects.create(
        name=f"Zumba Blast {uuid.uuid4().hex[:4]}",
        slug=f"zumba-{uuid.uuid4().hex[:4]}",
        category=ClassType.Category.DANCE,
        default_duration_minutes=60,
        default_capacity=2,
        created_by=owner,
    )

    s_date = timezone.localdate() + timedelta(days=2)
    session = ClassSession.objects.create(
        class_type=ctype,
        branch=branch_a,
        trainer=trainer_tp,
        session_date=s_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        capacity=2,
        status="SCHEDULED",
    )

    m1 = register_member(actor=rec, home_branch=branch_a, first_name="Fikru", last_name="Lemma", phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}")
    ms1 = create_membership(actor=rec, member=m1, package=pkg, home_branch=branch_a)
    ms1.status = Membership.Status.ACTIVE
    ms1.payment_clearance_status = Membership.PaymentClearance.CLEARED
    ms1.save()

    # Book member
    booking = book_class_session(actor=rec, session=session, member=m1)
    assert booking.status == "BOOKED"

    # Mark attendance
    mark_class_attendance(actor=trainer_tp.staff.user, booking=booking, status="ATTENDED")
    booking.refresh_from_db()
    assert booking.status == "ATTENDED"


# ==============================================================================
# WORKFLOW 07: POS & Retail Inventory
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_07_pos_shift_and_inventory_sale(test_setup):
    """Verify opening shift, selling product via checkout, and stock deduction."""
    setup = test_setup
    pos_staff = setup["pos_staff"]
    branch_a = setup["branch_a"]
    cash_method = setup["cash_method"]

    cat = ProductCategory.objects.create(
        code=f"CAT-{uuid.uuid4().hex[:4]}",
        name="Nutrition",
    )
    product = Product.objects.create(
        product_number=f"PRD-{uuid.uuid4().hex[:4]}",
        sku=f"SKU-{uuid.uuid4().hex[:4]}",
        name="Energy Bar Pro",
        category=cat,
        selling_price=Decimal("120.00"),
        cost_price=Decimal("70.00"),
        unit_of_measurement="PCS",
        is_active=True,
    )
    stock = StockItem.objects.create(
        product=product,
        branch=branch_a,
        quantity_on_hand=50,
        reorder_level=10,
    )

    # 1. Open Shift
    shift = open_cashier_shift(
        actor=pos_staff,
        branch=branch_a,
        opening_float=Decimal("500.00"),
    )
    assert shift.status == "OPEN"

    # 2. Process Checkout
    sale = process_pos_checkout(
        actor=pos_staff,
        branch=branch_a,
        shift=shift,
        items_data=[{
            "product": product,
            "quantity": 2,
            "unit_price": Decimal("120.00"),
        }],
        payments_data=[{
            "payment_method": cash_method,
            "amount": Decimal("240.00"),
            "tendered_amount": Decimal("250.00"),
        }],
    )
    assert sale.status == "COMPLETED"
    assert sale.total_amount == Decimal("240.00")

    # Verify inventory deduction
    stock.refresh_from_db()
    assert stock.quantity_on_hand == 48


# ==============================================================================
# WORKFLOW 08: Finance & Expenses
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_08_expense_request_and_approval(test_setup):
    """Verify expense submission, manager approval within limit."""
    setup = test_setup
    rec = setup["rec_a"]
    mgr_a = setup["mgr_a"]
    branch_a = setup["branch_a"]

    BranchFinanceSettings.objects.create(
        branch=branch_a,
        manager_approval_limit=Decimal("10000.00"),
        receptionist_can_create_expenses=True,
    )

    cat = ExpenseCategory.objects.create(
        code=f"EXP-CAT-{uuid.uuid4().hex[:4]}",
        name="Gym Floor Sanitation",
        is_active=True,
    )

    expense = create_expense(
        actor=rec,
        branch=branch_a,
        category=cat,
        amount=Decimal("3500.00"),
        description="Sanitizer wipes bulk purchase",
        expense_date=timezone.localdate(),
        submit_for_approval=True,
    )
    assert expense.approval_status == "SUBMITTED"

    # Approve Expense by Branch Manager
    approve_expense(actor=mgr_a, expense=expense)
    expense.refresh_from_db()
    assert expense.approval_status == "APPROVED"
    assert expense.approved_by == mgr_a


# ==============================================================================
# WORKFLOW 09: Facilities & Locker Allocation
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_09_locker_allocation_and_release(test_setup):
    """Verify locker allocation, deposit collection, and release."""
    setup = test_setup
    rec = setup["rec_a"]
    branch_a = setup["branch_a"]
    pkg = setup["pkg_single"]

    zone = LockerZone.objects.create(
        branch=branch_a,
        code=f"ZONE-{uuid.uuid4().hex[:4]}",
        name="Main Changing Room",
        gender_restriction=LockerZone.GenderRestriction.ALL,
    )
    locker = Locker.objects.create(
        branch=branch_a,
        zone=zone,
        locker_number=f"LCK-{uuid.uuid4().hex[:4]}",
        locker_type=Locker.LockerType.STANDARD,
        status=Locker.LockerStatus.AVAILABLE,
        deposit_amount_etb=Decimal("200.00"),
    )

    m = register_member(actor=rec, home_branch=branch_a, first_name="Girma", last_name="Alemu", phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}")
    ms = create_membership(actor=rec, member=m, package=pkg, home_branch=branch_a)
    ms.status = Membership.Status.ACTIVE
    ms.save()

    # Allocate Locker
    assignment = assign_locker(
        actor=rec,
        locker=locker,
        member=m,
        assignment_type=LockerAssignment.AssignmentType.MEMBERSHIP_LINKED,
        deposit_amount_etb=Decimal("200.00"),
        start_date=timezone.localdate(),
        end_date=timezone.localdate() + timedelta(days=30),
    )
    assert assignment.status == "ACTIVE"
    locker.refresh_from_db()
    assert locker.status == Locker.LockerStatus.OCCUPIED

    # Release Locker
    release_locker(
        actor=rec,
        assignment=assignment,
        refund_deposit=True,
    )
    assignment.refresh_from_db()
    assert assignment.status == "COMPLETED"
    locker.refresh_from_db()
    assert locker.status == Locker.LockerStatus.AVAILABLE


# ==============================================================================
# WORKFLOW 10: Notification Template Rendering
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_10_notifications_rendering():
    """Verify template variable rendering with dictionary context."""
    template_str = "Hello {{ member_name }}, your membership at {{ gym_name }} is active until {{ end_date }}."
    context = {
        "member_name": "Haile Gebrselassie",
        "gym_name": "Antigravity Gym",
        "end_date": "2026-12-31",
    }
    rendered = render_template_safely(template_str, context)
    assert "Hello Haile Gebrselassie" in rendered
    assert "Antigravity Gym" in rendered
    assert "2026-12-31" in rendered


# ==============================================================================
# WORKFLOW 12: Multi-Branch Isolation & Security Boundaries
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_workflow_12_cross_branch_access_denial(test_setup):
    """Verify that staff assigned to Branch A cannot transact in Branch B."""
    setup = test_setup
    rec_a = setup["rec_a"]
    mgr_b = setup["mgr_b"]
    branch_b = setup["branch_b"]

    assert not can_access_branch(rec_a, branch_b)

    with pytest.raises(PermissionDenied):
        register_member(
            actor=rec_a,
            home_branch=branch_b,
            first_name="Illegal",
            last_name="Member",
            phone="+251911999888",
        )


# ==============================================================================
# PHASE D: Edge Cases, Concurrency, and Transactions
# ==============================================================================

@pytest.mark.django_db(transaction=True)
def test_edge_case_overpayment_rejected(test_setup):
    """Verify that an invoice strictly rejects payments greater than the balance due."""
    setup = test_setup
    rec = setup["rec_a"]
    acct = setup["acct"]
    branch_a = setup["branch_a"]
    cash = setup["cash_method"]

    m = register_member(actor=rec, home_branch=branch_a, first_name="Kassaye", last_name="Mulu", phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}")
    inv = create_invoice(
        actor=rec,
        member=m,
        branch=branch_a,
        items=[{"description": "Fee", "quantity": 1, "unit_price": Decimal("500.00"), "item_type": "OTHER"}],
    )

    with pytest.raises(ValidationError) as exc:
        record_payment(
            actor=acct,
            invoice=inv,
            amount=Decimal("600.00"),  # > 500 balance
            payment_method=cash,
        )
    assert "Overpayment rejected" in str(exc.value)


@pytest.mark.django_db(transaction=True)
def test_edge_case_negative_amount_rejected(test_setup):
    """Verify that negative amounts in invoices or payments are rejected."""
    setup = test_setup
    rec = setup["rec_a"]
    acct = setup["acct"]
    branch_a = setup["branch_a"]
    cash = setup["cash_method"]

    m = register_member(actor=rec, home_branch=branch_a, first_name="Negash", last_name="Dadi", phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}")
    with pytest.raises(ValidationError):
        create_invoice(
            actor=rec,
            member=m,
            branch=branch_a,
            items=[{"description": "Fee", "quantity": 1, "unit_price": Decimal("-100.00"), "item_type": "OTHER"}],
        )

    inv = create_invoice(
        actor=rec,
        member=m,
        branch=branch_a,
        items=[{"description": "Fee", "quantity": 1, "unit_price": Decimal("200.00"), "item_type": "OTHER"}],
    )
    with pytest.raises(ValidationError):
        record_payment(actor=acct, invoice=inv, amount=Decimal("-50.00"), payment_method=cash)


@pytest.mark.django_db(transaction=True)
def test_edge_case_duplicate_payment_idempotency(test_setup):
    """Verify that submitting identical payment with same idempotency key does not double-charge."""
    setup = test_setup
    rec = setup["rec_a"]
    acct = setup["acct"]
    branch_a = setup["branch_a"]
    cash = setup["cash_method"]

    m = register_member(actor=rec, home_branch=branch_a, first_name="Tarekegn", last_name="Baye", phone=f"+251911{uuid.uuid4().int % 900000 + 100000:06d}")
    inv = create_invoice(
        actor=rec,
        member=m,
        branch=branch_a,
        items=[{"description": "Pass", "quantity": 1, "unit_price": Decimal("400.00"), "item_type": "OTHER"}],
    )

    key = f"IDEMP-KEY-{uuid.uuid4().hex}"
    p1 = record_payment(actor=acct, invoice=inv, amount=Decimal("200.00"), payment_method=cash, idempotency_key=key)
    p2 = record_payment(actor=acct, invoice=inv, amount=Decimal("200.00"), payment_method=cash, idempotency_key=key)

    assert p1.pk == p2.pk
    inv.refresh_from_db()
    assert inv.paid_amount == Decimal("200.00")
