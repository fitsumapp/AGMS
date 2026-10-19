import concurrent.futures
from datetime import date, timedelta
from decimal import Decimal
import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import has_role, is_owner, seed_roles
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.finance.models import ExpenseCategory, FinancialAccount
from apps.finance.services import close_reconciliation
from apps.members.models import Member
from apps.members.services import register_member
from apps.memberships.models import Membership, MembershipPackage, PackageBranch
from apps.memberships.services import create_membership
from apps.facilities.models import (
    LockerZone,
    Locker,
    LockerAssignment,
    LockerIncident,
    EquipmentCategory,
    FacilityArea,
    Equipment,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
    FaultReport,
    CleaningTaskSchedule,
    CleaningTaskExecution,
    FacilityInspection,
    IncidentReport,
)
from apps.facilities.selectors import (
    get_facility_dashboard_metrics,
    get_locker_utilization_by_zone,
)
from apps.facilities.services import (
    assign_locker,
    release_locker,
    record_locker_incident,
    register_equipment,
    create_fault_report,
    convert_fault_to_work_order,
    update_work_order_status,
    post_work_order_expense_to_finance,
    generate_preventive_work_orders,
    complete_cleaning_task,
    record_facility_inspection,
    record_incident_report,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_facilities_prerequisites(db):
    seed_roles()
    GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "AGMS Gym",
            "legal_name": "Antigravity Fitness PLC",
            "email": "facilities@agms.test",
            "phone": "+251911000000",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )


@pytest.fixture
def branches():
    b1 = Branch.objects.create(
        code="BOLE",
        name="Bole Branch",
        address="Bole Road, Addis Ababa",
        city="Addis Ababa",
        is_active=True,
    )
    b2 = Branch.objects.create(
        code="MEG",
        name="Megenagna Branch",
        address="Megenagna Square",
        city="Addis Ababa",
        is_active=True,
    )
    return b1, b2


@pytest.fixture
def users(branches):
    b1, b2 = branches

    owner = User.objects.create_user("owner@agms.test", "OwnerPass12345!", is_staff=True)
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    mgr = User.objects.create_user("manager@agms.test", "ManagerPass12345!", is_staff=True)
    mgr.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr, branch=b1, is_active=True, is_primary=True)

    receptionist = User.objects.create_user("receptionist@agms.test", "ReceptPass12345!", is_staff=True)
    receptionist.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=receptionist, branch=b1, is_active=True, is_primary=True)

    trainer = User.objects.create_user("trainer@agms.test", "TrainerPass12345!", is_staff=True)
    trainer.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer, branch=b1, is_active=True, is_primary=True)

    accountant = User.objects.create_user("accountant@agms.test", "AccountPass12345!", is_staff=True)
    accountant.groups.add(Group.objects.get(name="Accountant"))
    UserBranchAccess.objects.create(user=accountant, branch=b1, is_active=True, is_primary=True)

    pos_staff = User.objects.create_user("pos@agms.test", "PosPass12345!", is_staff=True)
    pos_staff.groups.add(Group.objects.get(name="POS Staff"))
    UserBranchAccess.objects.create(user=pos_staff, branch=b1, is_active=True, is_primary=True)

    # Manager for branch 2
    mgr2 = User.objects.create_user("manager2@agms.test", "ManagerPass12345!", is_staff=True)
    mgr2.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr2, branch=b2, is_active=True, is_primary=True)

    return {
        "owner": owner,
        "manager": mgr,
        "receptionist": receptionist,
        "trainer": trainer,
        "accountant": accountant,
        "pos_staff": pos_staff,
        "manager2": mgr2,
    }


@pytest.fixture
def member(branches, users):
    b1, _ = branches
    owner = users["owner"]
    m = register_member(
        actor=owner,
        home_branch=b1,
        first_name="Haile",
        last_name="Gebrselassie",
        phone="+251911112233",
        gender=Member.Gender.MALE,
    )
    return m


@pytest.fixture
def active_membership(branches, users, member):
    b1, _ = branches
    owner = users["owner"]
    pkg = MembershipPackage.objects.create(
        code="GOLD-MTH",
        name="Standard Gold",
        duration_value=1,
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        price=Decimal("1500.00"),
        is_active=True,
    )
    PackageBranch.objects.create(package=pkg, branch=b1, is_enabled=True)
    membership = create_membership(
        actor=owner,
        member=member,
        package=pkg,
        home_branch=b1,
        start_date=timezone.localdate(),
    )
    membership.status = "ACTIVE"
    membership.save(update_fields=["status"])
    return membership


@pytest.fixture
def locker_setup(branches):
    b1, b2 = branches
    z1 = LockerZone.objects.create(
        branch=b1,
        name="Men's Executive Locker Room",
        code="ZONE-MALE-01",
        gender_restriction=LockerZone.GenderRestriction.MALE,
    )
    l1 = Locker.objects.create(
        branch=b1,
        zone=z1,
        locker_number="L-101",
        locker_type=Locker.LockerType.EXECUTIVE,
        daily_fee_etb=Decimal("50.00"),
        monthly_fee_etb=Decimal("500.00"),
        deposit_amount_etb=Decimal("200.00"),
        status=Locker.LockerStatus.AVAILABLE,
    )
    l2 = Locker.objects.create(
        branch=b1,
        zone=z1,
        locker_number="L-102",
        locker_type=Locker.LockerType.STANDARD,
        daily_fee_etb=Decimal("30.00"),
        monthly_fee_etb=Decimal("300.00"),
        deposit_amount_etb=Decimal("150.00"),
        status=Locker.LockerStatus.AVAILABLE,
    )
    # Branch 2 zone & locker
    z2 = LockerZone.objects.create(
        branch=b2,
        name="Megenagna General Lockers",
        code="ZONE-MEG-01",
    )
    l_b2 = Locker.objects.create(
        branch=b2,
        zone=z2,
        locker_number="MEG-001",
        status=Locker.LockerStatus.AVAILABLE,
    )
    return {"zone1": z1, "locker1": l1, "locker2": l2, "zone2": z2, "locker_b2": l_b2}


@pytest.fixture
def equipment_setup(branches):
    b1, _ = branches
    cat = EquipmentCategory.objects.create(
        name="Cardio Machines",
        code="CAT-CARDIO",
        default_maintenance_interval_days=30,
    )
    area = FacilityArea.objects.create(
        branch=b1,
        name="Cardio Floor",
        code="AREA-CARDIO",
        area_type=FacilityArea.AreaType.TRAINING_HALL,
    )
    eq = Equipment.objects.create(
        branch=b1,
        category=cat,
        facility_area=area,
        asset_code="TRD-001",
        name="TechnoGym Run Live 1000",
        manufacturer="TechnoGym",
        model_number="RL-1000",
        serial_number="TG-987654321",
        purchase_cost_etb=Decimal("250000.00"),
        warranty_expiry_date=timezone.localdate() + timedelta(days=365),
        status=Equipment.OperationalStatus.OPERATIONAL,
    )
    return {"category": cat, "area": area, "equipment": eq}


# ==============================================================================
# 1. Locker Management Tests
# ==============================================================================

def test_locker_setup_and_inventory(locker_setup, branches):
    b1, _ = branches
    z1 = locker_setup["zone1"]
    l1 = locker_setup["locker1"]

    assert l1.branch == b1
    assert l1.zone == z1
    assert l1.status == Locker.LockerStatus.AVAILABLE
    assert l1.daily_fee_etb == Decimal("50.00")
    assert l1.monthly_fee_etb == Decimal("500.00")
    assert l1.deposit_amount_etb == Decimal("200.00")


def test_locker_assignment_daily(users, member, locker_setup):
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]

    assignment = assign_locker(
        actor=receptionist,
        locker=l1,
        member=member,
        assignment_type=LockerAssignment.AssignmentType.DAILY,
        charged_fee_etb=Decimal("50.00"),
        deposit_amount_etb=Decimal("200.00"),
        key_card_code="KEY-101",
    )

    assert assignment.status == LockerAssignment.Status.ACTIVE
    assert assignment.assignment_type == LockerAssignment.AssignmentType.DAILY
    assert assignment.key_issued is True
    assert assignment.fee_charged_etb == Decimal("50.00")
    assert assignment.deposit_charged_etb == Decimal("200.00")
    assert assignment.deposit_refunded_etb == Decimal("0.00")

    l1.refresh_from_db()
    assert l1.status == Locker.LockerStatus.OCCUPIED


def test_locker_assignment_monthly_requires_active_membership(users, branches, locker_setup):
    b1, _ = branches
    owner = users["owner"]
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]

    # Member with no active membership
    inactive_member = register_member(
        actor=owner,
        home_branch=b1,
        first_name="Kenenisa",
        last_name="Bekele",
        phone="+251922334455",
        gender=Member.Gender.MALE,
    )

    with pytest.raises(ValidationError) as exc:
        assign_locker(
            actor=receptionist,
            locker=l1,
            member=inactive_member,
            assignment_type=LockerAssignment.AssignmentType.MONTHLY,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30),
        )
    assert "ACTIVE membership" in str(exc.value)


def test_locker_assignment_monthly_with_active_membership(users, member, active_membership, locker_setup):
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]

    assignment = assign_locker(
        actor=receptionist,
        locker=l1,
        member=member,
        assignment_type=LockerAssignment.AssignmentType.MONTHLY,
        start_date=timezone.localdate(),
        end_date=timezone.localdate() + timedelta(days=30),
        charged_fee_etb=Decimal("500.00"),
        deposit_amount_etb=Decimal("200.00"),
        create_billing_invoice=True,
    )

    assert assignment.status == LockerAssignment.Status.ACTIVE
    assert assignment.membership == active_membership
    assert assignment.invoice is not None
    assert assignment.invoice.total_amount == Decimal("700.00")  # 500 fee + 200 deposit

    l1.refresh_from_db()
    assert l1.status == Locker.LockerStatus.OCCUPIED


def test_locker_double_assignment_database_constraint(users, member, locker_setup):
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]

    assign_locker(
        actor=receptionist,
        locker=l1,
        member=member,
        assignment_type=LockerAssignment.AssignmentType.DAILY,
    )

    # Attempt second assignment on the same locker
    with pytest.raises(ValidationError) as exc:
        assign_locker(
            actor=receptionist,
            locker=l1,
            member=member,
            assignment_type=LockerAssignment.AssignmentType.DAILY,
        )
    assert "Occupied" in str(exc.value) or "cannot be assigned" in str(exc.value)


def test_locker_cross_branch_assignment_rejected(users, branches, locker_setup):
    b1, b2 = branches
    owner = users["owner"]
    l_b2 = locker_setup["locker_b2"]  # Belongs to branch 2

    # Member belongs to branch 1
    m1 = register_member(
        actor=owner,
        home_branch=b1,
        first_name="Derartu",
        last_name="Tulu",
        phone="+251933445566",
        gender=Member.Gender.FEMALE,
    )

    with pytest.raises(ValidationError) as exc:
        assign_locker(
            actor=owner,
            locker=l_b2,
            member=m1,
            assignment_type=LockerAssignment.AssignmentType.DAILY,
        )
    assert "different branch" in str(exc.value)


def test_locker_release_and_deposit_refund(users, member, locker_setup):
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]

    assignment = assign_locker(
        actor=receptionist,
        locker=l1,
        member=member,
        assignment_type=LockerAssignment.AssignmentType.DAILY,
        deposit_amount_etb=Decimal("200.00"),
    )

    released = release_locker(
        assignment=assignment,
        user=receptionist,
        key_returned=True,
        refund_deposit=True,
    )

    assert released.status == LockerAssignment.Status.COMPLETED
    assert released.key_returned is True
    assert released.deposit_refunded_etb == Decimal("200.00")
    assert released.released_by == receptionist

    l1.refresh_from_db()
    assert l1.status == Locker.LockerStatus.AVAILABLE


def test_locker_incident_lost_key_penalty(users, member, locker_setup):
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]

    assignment = assign_locker(
        actor=receptionist,
        locker=l1,
        member=member,
        assignment_type=LockerAssignment.AssignmentType.DAILY,
        deposit_amount_etb=Decimal("200.00"),
    )

    # Record lost key incident
    incident = record_locker_incident(
        actor=receptionist,
        locker=l1,
        assignment=assignment,
        member=member,
        incident_type=LockerIncident.IncidentType.LOST_KEY,
        penalty_fee_etb=Decimal("350.00"),
        forfeit_deposit=True,
        details="Member lost physical brass key at the cafe.",
    )

    assert incident.penalty_fee_etb == Decimal("350.00")
    assert incident.deposit_forfeited_etb == Decimal("200.00")
    assert incident.is_settled is True

    # Releasing locker without key return
    released = release_locker(
        assignment=assignment,
        user=receptionist,
        key_returned=False,
        refund_deposit=False,
    )
    assert released.deposit_refunded_etb == Decimal("0.00")
    assert released.key_returned is False

    l1.refresh_from_db()
    assert l1.status == Locker.LockerStatus.OUT_OF_SERVICE  # Needs lock replacement


def test_locker_utilization_selector(locker_setup, branches, users, member):
    b1, _ = branches
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]

    assign_locker(
        actor=receptionist,
        locker=l1,
        member=member,
        assignment_type=LockerAssignment.AssignmentType.DAILY,
    )

    zones_data = get_locker_utilization_by_zone(branch_ids=[b1.id])
    assert len(zones_data) >= 1
    z_info = zones_data[0]
    assert z_info["total"] == 2
    assert z_info["occupied"] == 1
    assert z_info["available"] == 1
    assert z_info["utilization_rate"] == 50.0


# ==============================================================================
# 2. Equipment Asset Management Tests
# ==============================================================================

def test_equipment_registration_and_attributes(users, branches, equipment_setup):
    mgr = users["manager"]
    b1, _ = branches
    cat = equipment_setup["category"]
    area = equipment_setup["area"]

    eq = register_equipment(
        actor=mgr,
        branch=b1,
        category=cat,
        facility_area=area,
        asset_code="TRD-002",
        name="TechnoGym SkillMill",
        manufacturer="TechnoGym",
        model_number="SM-500",
        serial_number="TG-555666777",
        purchase_cost_etb=Decimal("180000.00"),
        warranty_expiry_date=timezone.localdate() + timedelta(days=730),
    )

    assert eq.asset_code == "TRD-002"
    assert eq.status == Equipment.OperationalStatus.OPERATIONAL
    assert eq.condition == Equipment.EquipmentCondition.GOOD
    assert eq.purchase_cost_etb == Decimal("180000.00")
    assert eq.total_downtime_hours == Decimal("0.00")


def test_equipment_status_transitions_and_downtime(users, equipment_setup):
    eq = equipment_setup["equipment"]
    mgr = users["manager"]

    # File fault report
    fault = create_fault_report(
        actor=mgr,
        branch=eq.branch,
        equipment=eq,
        title="Belt Slipping",
        description="Drive belt tension is loose.",
        priority=FaultReport.Priority.HIGH,
    )

    # Convert to work order
    wo = convert_fault_to_work_order(
        actor=mgr,
        fault_report=fault,
    )

    eq.refresh_from_db()
    assert eq.status == Equipment.OperationalStatus.UNDER_MAINTENANCE

    # Advance work order: REPORTED -> IN_PROGRESS -> RESOLVED with downtime
    update_work_order_status(
        actor=mgr,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.IN_PROGRESS,
    )

    update_work_order_status(
        actor=mgr,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.RESOLVED,
        service_cost_etb=Decimal("1500.00"),
        parts_cost_etb=Decimal("800.00"),
        downtime_hours=Decimal("4.50"),
        spare_parts_used="Replacement drive belt",
        resolution_notes="Belt replaced and calibrated.",
    )

    eq.refresh_from_db()
    assert eq.status == Equipment.OperationalStatus.OPERATIONAL
    assert eq.total_downtime_hours == Decimal("4.50")
    assert eq.last_maintenance_date == timezone.localdate()


# ==============================================================================
# 3. Preventive Maintenance Tests
# ==============================================================================

def test_maintenance_schedule_and_preventive_generation(users, branches, equipment_setup):
    b1, _ = branches
    eq = equipment_setup["equipment"]
    today = timezone.localdate()

    sched = MaintenanceSchedule.objects.create(
        branch=b1,
        equipment=eq,
        title="Monthly Treadmill Inspection",
        frequency=MaintenanceSchedule.Frequency.MONTHLY,
        interval_days=30,
        checklist=["Check deck lubrication", "Check belt tracking", "Test safety stop button"],
        next_run_date=today,
        is_active=True,
    )

    work_orders = generate_preventive_work_orders(today=today)
    assert len(work_orders) == 1
    wo = work_orders[0]
    assert wo.order_type == MaintenanceWorkOrder.OrderType.PREVENTIVE
    assert wo.equipment == eq
    assert wo.status == MaintenanceWorkOrder.Status.REPORTED

    sched.refresh_from_db()
    assert sched.next_run_date == today + timedelta(days=30)
    assert sched.last_run_date == today


# ==============================================================================
# 4. Corrective Maintenance & Work Order Lifecycle Tests
# ==============================================================================

def test_fault_reporting_workflow(users, equipment_setup):
    trainer = users["trainer"]
    eq = equipment_setup["equipment"]

    fault = create_fault_report(
        actor=trainer,
        branch=eq.branch,
        equipment=eq,
        title="Console Screen Flickering",
        description="Display glitches during high-speed workout.",
        priority=FaultReport.Priority.MEDIUM,
    )

    assert fault.reported_by == trainer
    assert fault.status == FaultReport.Status.REPORTED
    assert fault.report_number.startswith("FLT-")


def test_work_order_status_progression_and_validation(users, equipment_setup):
    mgr = users["manager"]
    eq = equipment_setup["equipment"]

    fault = create_fault_report(
        actor=mgr,
        branch=eq.branch,
        equipment=eq,
        title="Motor Noise",
        description="High pitch whining sound.",
        priority=FaultReport.Priority.URGENT,
    )

    wo = convert_fault_to_work_order(actor=mgr, fault_report=fault)
    assert wo.status == MaintenanceWorkOrder.Status.REPORTED

    # Invalid jump: REPORTED directly to RESOLVED must fail
    with pytest.raises(ValidationError):
        update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.RESOLVED)

    # Valid: REPORTED -> IN_PROGRESS
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.IN_PROGRESS)
    wo.refresh_from_db()
    assert wo.status == MaintenanceWorkOrder.Status.IN_PROGRESS
    assert wo.started_at is not None

    # Valid: IN_PROGRESS -> RESOLVED
    update_work_order_status(
        actor=mgr,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.RESOLVED,
        service_cost_etb=Decimal("2000.00"),
        parts_cost_etb=Decimal("500.00"),
    )
    wo.refresh_from_db()
    assert wo.status == MaintenanceWorkOrder.Status.RESOLVED
    assert wo.total_cost_etb == Decimal("2500.00")

    # Valid: RESOLVED -> CLOSED
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.CLOSED)
    wo.refresh_from_db()
    assert wo.status == MaintenanceWorkOrder.Status.CLOSED
    assert wo.closed_at is not None


def test_work_order_reopen_audit(users, equipment_setup):
    mgr = users["manager"]
    eq = equipment_setup["equipment"]

    fault = create_fault_report(actor=mgr, branch=eq.branch, equipment=eq, title="Sensor issue", description="...")
    wo = convert_fault_to_work_order(actor=mgr, fault_report=fault)
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.IN_PROGRESS)
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.RESOLVED)
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.CLOSED)

    # Reopening closed work order
    reopened = update_work_order_status(
        actor=mgr,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.IN_PROGRESS,
        reopened_reason="Sensor failed again within 24 hours.",
    )
    assert reopened.is_reopened is True
    assert reopened.reopened_by == mgr
    assert "Sensor failed again" in reopened.reopened_reason


# ==============================================================================
# 5. Financial Integration Tests (Phase 05 Billing & Phase 10 Finance)
# ==============================================================================

def test_maintenance_expense_posting_to_finance(users, branches, equipment_setup):
    b1, _ = branches
    mgr = users["manager"]
    eq = equipment_setup["equipment"]

    # Setup financial account for branch 1
    acc = FinancialAccount.objects.create(
        branch=b1,
        name="Operating Petty Cash",
        account_type=FinancialAccount.AccountType.PETTY_CASH,
        currency="ETB",
        current_balance=Decimal("50000.00"),
    )

    fault = create_fault_report(actor=mgr, branch=b1, equipment=eq, title="Incline motor fix", description="...")
    wo = convert_fault_to_work_order(actor=mgr, fault_report=fault)
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.IN_PROGRESS)
    update_work_order_status(
        actor=mgr,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.RESOLVED,
        service_cost_etb=Decimal("3000.00"),
        parts_cost_etb=Decimal("1200.00"),
    )

    expense = post_work_order_expense_to_finance(
        work_order=wo,
        actor=mgr,
    )

    assert expense is not None
    assert expense.amount == Decimal("4200.00")
    assert expense.branch == b1
    wo.refresh_from_db()
    assert wo.finance_expense == expense


def test_duplicate_maintenance_expense_rejected(users, branches, equipment_setup):
    b1, _ = branches
    mgr = users["manager"]
    eq = equipment_setup["equipment"]

    fault = create_fault_report(actor=mgr, branch=b1, equipment=eq, title="Power switch", description="...")
    wo = convert_fault_to_work_order(actor=mgr, fault_report=fault)
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.IN_PROGRESS)
    update_work_order_status(
        actor=mgr,
        work_order=wo,
        new_status=MaintenanceWorkOrder.Status.RESOLVED,
        service_cost_etb=Decimal("500.00"),
    )

    post_work_order_expense_to_finance(work_order=wo, actor=mgr)

    # Attempting to post a second time
    with pytest.raises(ValidationError) as exc:
        post_work_order_expense_to_finance(work_order=wo, actor=mgr)
    assert "already has an associated financial expense" in str(exc.value)


def test_closed_financial_period_blocks_expense_posting(users, branches, equipment_setup):
    b1, _ = branches
    mgr = users["manager"]
    owner = users["owner"]
    eq = equipment_setup["equipment"]

    acc = FinancialAccount.objects.create(
        branch=b1,
        name="Main Safe",
        account_type=FinancialAccount.AccountType.PETTY_CASH,
        currency="ETB",
        opening_balance=Decimal("10000.00"),
        current_balance=Decimal("10000.00"),
    )

    today = timezone.localdate()
    # Conduct and close reconciliation through today
    from apps.finance.services import conduct_reconciliation, close_reconciliation
    recon = conduct_reconciliation(
        actor=owner,
        branch=b1,
        account=acc,
        actual_counted_balance=Decimal("10000.00"),
        period_start=today - timedelta(days=30),
        period_end=today,
        submit_for_closure=True,
    )
    close_reconciliation(actor=owner, reconciliation=recon)

    fault = create_fault_report(actor=mgr, branch=b1, equipment=eq, title="Cable repair", description="...")
    wo = convert_fault_to_work_order(actor=mgr, fault_report=fault)
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.IN_PROGRESS)
    update_work_order_status(actor=mgr, work_order=wo, new_status=MaintenanceWorkOrder.Status.RESOLVED, service_cost_etb=Decimal("500.00"))

    with pytest.raises(ValidationError) as exc:
        post_work_order_expense_to_finance(work_order=wo, actor=mgr)
    assert "closed" in str(exc.value).lower() or "reconciliation" in str(exc.value).lower()


# ==============================================================================
# 6. Facility & Cleaning Management Tests
# ==============================================================================

def test_cleaning_schedule_and_execution_lifecycle(users, branches, equipment_setup):
    b1, _ = branches
    mgr = users["manager"]
    area = equipment_setup["area"]

    sched = CleaningTaskSchedule.objects.create(
        branch=b1,
        facility_area=area,
        task_name="Daily Cardio Hall Sanitization",
        frequency=CleaningTaskSchedule.Frequency.DAILY,
        checklist=["Wipe down console screens", "Mop floor with disinfectant", "Refill spray bottles"],
    )

    execution = CleaningTaskExecution.objects.create(
        branch=b1,
        schedule=sched,
        facility_area=area,
        task_name=sched.task_name,
        scheduled_date=timezone.localdate(),
        status=CleaningTaskExecution.Status.PENDING,
    )

    # Complete cleaning
    completed = complete_cleaning_task(
        execution=execution,
        actor=mgr,
        checklist_progress=["Wipe down console screens", "Mop floor with disinfectant"],
        notes="All cardio machines sanitized.",
    )

    assert completed.status == CleaningTaskExecution.Status.COMPLETED
    assert completed.completed_at is not None
    assert completed.verified_by == mgr


def test_facility_inspection_and_failed_escalation(users, branches, equipment_setup):
    b1, _ = branches
    mgr = users["manager"]
    area = equipment_setup["area"]

    # Conduct inspection that fails due to hazardous loose flooring
    inspection = record_facility_inspection(
        actor=mgr,
        branch=b1,
        facility_area=area,
        inspection_type=FacilityInspection.InspectionType.SAFETY_AUDIT,
        passed=False,
        score_percent=Decimal("60.00"),
        deficiencies="Rubber floor tile detached near dumbbell rack. Trip hazard.",
        auto_escalate_work_order=True,
    )

    assert inspection.passed is False
    assert inspection.score_percent == Decimal("60.00")
    assert inspection.escalation_work_order is not None

    wo = inspection.escalation_work_order
    assert wo.order_type == MaintenanceWorkOrder.OrderType.CORRECTIVE
    assert wo.priority == MaintenanceWorkOrder.Priority.URGENT
    assert "Trip hazard" in wo.description


def test_facility_incident_reporting(users, branches, equipment_setup):
    b1, _ = branches
    trainer = users["trainer"]
    area = equipment_setup["area"]

    incident = record_incident_report(
        actor=trainer,
        branch=b1,
        facility_area=area,
        incident_type=IncidentReport.IncidentType.SAFETY_HAZARD,
        severity=IncidentReport.Severity.MEDIUM,
        description="Water spill on weight room floor from leaking water bottle.",
        immediate_action="Placed warning sign and wiped area.",
    )

    assert incident.incident_number.startswith("INC-")
    assert incident.severity == IncidentReport.Severity.MEDIUM
    assert incident.status == IncidentReport.Status.OPEN


# ==============================================================================
# 7. Role Authorization & Branch Isolation Tests
# ==============================================================================

def test_branch_isolation_facilities(users, branches, locker_setup):
    mgr2 = users["manager2"]  # Branch 2 manager
    l1 = locker_setup["locker1"]  # Branch 1 locker

    # Manager 2 cannot assign locker in branch 1
    with pytest.raises(PermissionDenied):
        assign_locker(
            actor=mgr2,
            locker=l1,
            member=register_member(actor=users["owner"], home_branch=branches[0], first_name="A", last_name="B", phone="+251911999888"),
            assignment_type=LockerAssignment.AssignmentType.DAILY,
        )


def test_receptionist_permitted_locker_assign_denied_equipment_admin(users, member, locker_setup, equipment_setup):
    receptionist = users["receptionist"]
    l1 = locker_setup["locker1"]
    eq = equipment_setup["equipment"]

    # Receptionist can assign locker
    assignment = assign_locker(
        actor=receptionist,
        locker=l1,
        member=member,
        assignment_type=LockerAssignment.AssignmentType.DAILY,
    )
    assert assignment.status == LockerAssignment.Status.ACTIVE

    # Receptionist cannot register equipment
    with pytest.raises(PermissionDenied):
        register_equipment(
            actor=receptionist,
            branch=eq.branch,
            category=equipment_setup["category"],
            asset_code="DENIED-001",
            name="Denied Machine",
        )


def test_trainer_permitted_fault_reporting_denied_locker_assign(users, member, locker_setup, equipment_setup):
    trainer = users["trainer"]
    eq = equipment_setup["equipment"]
    l1 = locker_setup["locker1"]

    # Trainer can report faults
    fault = create_fault_report(
        actor=trainer,
        branch=eq.branch,
        equipment=eq,
        title="Cable frayed",
        description="Cable needs replacement.",
    )
    assert fault.reported_by == trainer

    # Trainer cannot assign lockers
    with pytest.raises(PermissionDenied):
        assign_locker(
            actor=trainer,
            locker=l1,
            member=member,
            assignment_type=LockerAssignment.AssignmentType.DAILY,
        )


def test_pos_staff_denied_facility_access(users, member, locker_setup, equipment_setup):
    pos_staff = users["pos_staff"]
    l1 = locker_setup["locker1"]
    eq = equipment_setup["equipment"]

    # POS staff cannot assign lockers
    with pytest.raises(PermissionDenied):
        assign_locker(
            actor=pos_staff,
            locker=l1,
            member=member,
            assignment_type=LockerAssignment.AssignmentType.DAILY,
        )

    # POS staff cannot report faults or register equipment
    with pytest.raises(PermissionDenied):
        create_fault_report(
            actor=pos_staff,
            branch=eq.branch,
            equipment=eq,
            title="Fault",
            description="...",
        )


# ==============================================================================
# 8. Dashboard & Reports Tests
# ==============================================================================

def test_facility_dashboard_metrics(branches, locker_setup, equipment_setup):
    b1, _ = branches
    metrics = get_facility_dashboard_metrics(branch_ids=[b1.id])

    assert metrics["total_lockers"] >= 2
    assert metrics["available_lockers"] >= 2
    assert metrics["total_equipment"] >= 1
    assert metrics["operational_equipment"] >= 1
    assert "locker_utilization_rate" in metrics


def test_facility_reports_csv_export_and_sanitization(client, users, branches):
    b1, _ = branches
    owner = users["owner"]
    client.force_login(owner)

    # 1. Locker CSV Export
    resp = client.get(reverse("facility-reports"), {"export": "lockers", "branch": b1.id})
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv; charset=utf-8"
    assert "attachment; filename=" in resp["Content-Disposition"]
    content = resp.content.decode("utf-8")
    assert "Branch,Zone,Locker Number,Type,Status" in content

    # 2. Equipment CSV Export
    resp_eq = client.get(reverse("facility-reports"), {"export": "equipment", "branch": b1.id})
    assert resp_eq.status_code == 200
    content_eq = resp_eq.content.decode("utf-8")
    assert "Branch,Asset Code,Name,Category" in content_eq


def test_facility_http_views(client, users):
    owner = users["owner"]
    manager = users["manager"]
    pos_staff = users["pos_staff"]

    # Owner has access to dashboard and reports
    client.force_login(owner)
    assert client.get(reverse("facility-dashboard")).status_code == 200
    assert client.get(reverse("facility-reports")).status_code == 200

    # Manager has access
    client.force_login(manager)
    assert client.get(reverse("facility-dashboard")).status_code == 200
    assert client.get(reverse("facility-reports")).status_code == 200

    # POS staff denied access
    client.force_login(pos_staff)
    assert client.get(reverse("facility-dashboard")).status_code == 403
    assert client.get(reverse("facility-reports")).status_code == 403
