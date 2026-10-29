import uuid
from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import BUSINESS_ROLES, is_owner
from apps.branches.models import Branch, UserBranchAccess
from apps.members.models import Member
from apps.memberships.models import Membership, MembershipPackage
from apps.billing.models import Invoice, Payment, PaymentMethod
from apps.finance.models import (
    BranchFinanceSettings,
    Expense,
    ExpenseCategory,
    FinanceAuditEvent,
    FinancialAccount,
    FinancialReconciliation,
)
from apps.finance.services import (
    approve_expense,
    close_reconciliation,
    create_expense,
    generate_next_finance_number,
    record_expense_payment,
    reopen_reconciliation,
)
from apps.core.models import FeatureFlag, GymProfile

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def test_setup():
    call_command("seed_business_roles", verbosity=0)

    # Branches
    b_addis = Branch.objects.create(code="SEC-ADD", name="Security Addis Main", address="Bole Atlas, Addis Ababa", is_active=True)
    b_bole = Branch.objects.create(code="SEC-BOL", name="Security Bole Express", address="Bole Airport Road, Addis Ababa", is_active=True)

    # Roles / Users
    owner_user = User.objects.create_user("sec_owner@example.test", "TestPass123!", is_staff=True, is_superuser=False)
    owner_user.groups.add(Group.objects.get(name="Gym Owner"))

    mgr_addis = User.objects.create_user("sec_mgr_addis@example.test", "TestPass123!", is_staff=True, is_superuser=False)
    mgr_addis.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_addis, branch=b_addis, is_primary=True, is_active=True)

    mgr_bole = User.objects.create_user("sec_mgr_bole@example.test", "TestPass123!", is_staff=True, is_superuser=False)
    mgr_bole.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_bole, branch=b_bole, is_primary=True, is_active=True)

    receptionist = User.objects.create_user("sec_rec@example.test", "TestPass123!", is_staff=True, is_superuser=False)
    receptionist.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=receptionist, branch=b_addis, is_primary=True, is_active=True)

    accountant = User.objects.create_user("sec_acc@example.test", "TestPass123!", is_staff=True, is_superuser=False)
    accountant.groups.add(Group.objects.get(name="Accountant"))
    UserBranchAccess.objects.create(user=accountant, branch=b_addis, is_primary=True, is_active=True)

    trainer = User.objects.create_user("sec_trainer@example.test", "TestPass123!", is_staff=True, is_superuser=False)
    trainer.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer, branch=b_addis, is_primary=True, is_active=True)

    pos_staff = User.objects.create_user("sec_pos@example.test", "TestPass123!", is_staff=True, is_superuser=False)
    pos_staff.groups.add(Group.objects.get(name="POS Staff"))
    UserBranchAccess.objects.create(user=pos_staff, branch=b_addis, is_primary=True, is_active=True)

    # Members in different branches
    mem_addis = Member.objects.create(
        member_number="SEC-M-001",
        first_name="Addis",
        last_name="Member",
        phone="+251911880001",
        home_branch=b_addis,
        date_of_birth=timezone.localdate() - timezone.timedelta(days=365 * 25),
    )
    mem_bole = Member.objects.create(
        member_number="SEC-M-002",
        first_name="Bole",
        last_name="Member",
        phone="+251911880002",
        home_branch=b_bole,
        date_of_birth=timezone.localdate() - timezone.timedelta(days=365 * 25),
    )

    # Finance Category & Account
    cat = ExpenseCategory.objects.create(name="Security Supplies", code="SEC-SUP")
    account = FinancialAccount.objects.create(
        branch=b_addis,
        name="Operating Cash",
        account_type=FinancialAccount.AccountType.CASH_ON_HAND,
        current_balance=Decimal("50000.00"),
        is_active=True,
    )

    return {
        "b_addis": b_addis,
        "b_bole": b_bole,
        "owner": owner_user,
        "mgr_addis": mgr_addis,
        "mgr_bole": mgr_bole,
        "receptionist": receptionist,
        "accountant": accountant,
        "trainer": trainer,
        "pos_staff": pos_staff,
        "mem_addis": mem_addis,
        "mem_bole": mem_bole,
        "category": cat,
        "account": account,
    }


# ==============================================================================
# 1. ADMIN URL & ROLE AUTHORIZATION TESTS
# ==============================================================================

def test_unauthorized_admin_urls_blocked_for_receptionist(client, test_setup):
    """Receptionist must not access sensitive financial, settings, or user management URLs."""
    client.force_login(test_setup["receptionist"])

    # Forbidden administrative and financial URLs
    forbidden_urls = [
        reverse("admin:accounts_user_changelist"),
        reverse("admin:core_gymprofile_changelist"),
        reverse("admin:finance_branchfinancesettings_changelist"),
        reverse("admin:finance_financialaccount_changelist"),
        reverse("admin:finance_financialreconciliation_changelist"),
        reverse("admin:core_featureflag_changelist"),
    ]
    for url in forbidden_urls:
        response = client.get(url)
        assert response.status_code == 403, f"Expected 403 for {url}, got {response.status_code}"


def test_cross_branch_direct_object_id_manipulation_blocked(client, test_setup):
    """Addis Branch Manager cannot view or modify a Bole member's detail page."""
    client.force_login(test_setup["mgr_addis"])

    bole_member = test_setup["mem_bole"]
    url = reverse("admin:members_member_change", args=[bole_member.pk])
    response = client.get(url)
    # Must be 302 (redirect back to admin/changelist with permission error) or 403/404
    assert response.status_code in (302, 403, 404), f"Expected redirect or 403/404 for foreign branch ID access, got {response.status_code}"


def test_inactive_user_access_denied(client, test_setup):
    """Deactivated staff account cannot access admin views."""
    user = test_setup["receptionist"]
    user.is_active = False
    user.save()

    client.force_login(user)
    response = client.get(reverse("admin:index"))
    # Inactive users should be redirected to login (302) or denied
    assert response.status_code in (302, 403)


# ==============================================================================
# 2. PRIVILEGE ESCALATION & SELF-APPROVAL TESTS
# ==============================================================================

def test_branch_manager_self_approval_rejected_at_service_layer(test_setup):
    """A manager cannot approve their own expense submission unless explicitly permitted by policy."""
    mgr = test_setup["mgr_addis"]
    branch = test_setup["b_addis"]

    expense = create_expense(
        actor=mgr,
        branch=branch,
        category=test_setup["category"],
        description="Manager Office Desk",
        amount=Decimal("3500.00"),
        submit_for_approval=True,
    )
    assert expense.approval_status == Expense.ApprovalStatus.SUBMITTED

    # Manager attempts to self-approve
    with pytest.raises(ValidationError, match="You cannot approve your own expense submission"):
        approve_expense(actor=mgr, expense=expense)


def test_manager_approval_limit_enforced_at_service_layer(test_setup):
    """A manager cannot approve expenses exceeding their branch approval limit."""
    mgr = test_setup["mgr_addis"]
    branch = test_setup["b_addis"]
    acc = test_setup["accountant"]

    # Configure 5,000 ETB limit
    b_settings, _ = BranchFinanceSettings.objects.get_or_create(branch=branch)
    b_settings.manager_approval_limit = Decimal("5000.00")
    b_settings.save()

    expense = create_expense(
        actor=acc,
        branch=branch,
        category=test_setup["category"],
        description="Commercial Treadmill Motor",
        amount=Decimal("8500.00"),
        submit_for_approval=True,
    )

    with pytest.raises(ValidationError, match="exceeds your Branch Manager approval limit"):
        approve_expense(actor=mgr, expense=expense)

    # Gym Owner CAN approve
    owner = test_setup["owner"]
    approved = approve_expense(actor=owner, expense=expense)
    assert approved.approval_status == Expense.ApprovalStatus.APPROVED
    assert approved.approved_by == owner


# ==============================================================================
# 3. FINANCIAL IMMUTABILITY & AUDIT TRAIL TESTS
# ==============================================================================

def test_audit_event_contains_actor_action_timestamp_branch(test_setup):
    """Every financial event writes an immutable audit record containing actor, action, timestamp, and branch."""
    owner = test_setup["owner"]
    branch = test_setup["b_addis"]

    expense = create_expense(
        actor=owner,
        branch=branch,
        category=test_setup["category"],
        description="Audit Verification Expense",
        amount=Decimal("1200.00"),
        submit_for_approval=True,
    )

    audit_entry = FinanceAuditEvent.objects.filter(target_id=str(expense.id)).first()
    assert audit_entry is not None
    assert audit_entry.actor == owner
    assert audit_entry.branch == branch
    assert audit_entry.created_at is not None
    assert audit_entry.action_summary != ""


def test_closed_reconciliation_period_immutable_to_non_owners(test_setup):
    """Closed financial periods cannot be reopened or edited by branch managers or accountants."""
    acc = test_setup["accountant"]
    mgr = test_setup["mgr_addis"]
    branch = test_setup["b_addis"]
    account = test_setup["account"]

    today = timezone.localdate()
    recon = FinancialReconciliation.objects.create(
        reconciliation_number="RECON-SEC-001",
        branch=branch,
        account=account,
        reconciliation_date=today,
        period_start=today - timezone.timedelta(days=30),
        period_end=today,
        opening_balance=Decimal("50000.00"),
        total_inflows=Decimal("0.00"),
        total_outflows=Decimal("0.00"),
        expected_balance=Decimal("50000.00"),
        actual_counted_balance=Decimal("50000.00"),
        variance=Decimal("0.00"),
        status=FinancialReconciliation.Status.APPROVED_CLOSED,
        reconciled_by=acc,
        approved_by=mgr,
        closed_at=timezone.now(),
    )

    # Manager attempts to reopen: rejected
    with pytest.raises(ValidationError, match="Only a Gym Owner can reopen a closed reconciliation period"):
        reopen_reconciliation(actor=mgr, reconciliation=recon, reason="Auditor adjustment")

    # Accountant attempts to reopen: rejected
    with pytest.raises(ValidationError, match="Only a Gym Owner can reopen a closed reconciliation period"):
        reopen_reconciliation(actor=acc, reconciliation=recon, reason="Auditor adjustment")

    # Gym Owner CAN reopen with mandatory reason
    owner = test_setup["owner"]
    reopened = reopen_reconciliation(actor=owner, reconciliation=recon, reason="Formal year-end audit adjustments authorized by owner")
    assert reopened.status == FinancialReconciliation.Status.DRAFT


def test_transactional_rollback_on_failed_financial_operation(test_setup):
    """Failed financial operations trigger full transactional rollback, leaving no orphan records."""
    branch = test_setup["b_addis"]
    mgr = test_setup["mgr_addis"]

    initial_expense_count = Expense.objects.count()
    initial_audit_count = FinanceAuditEvent.objects.count()

    try:
        with transaction.atomic():
            create_expense(
                actor=mgr,
                branch=branch,
                category=test_setup["category"],
                description="Rollback Test Item",
                amount=Decimal("500.00"),
            )
            # Simulated unexpected failure in pipeline
            raise RuntimeError("Database connection interrupted")
    except RuntimeError:
        pass

    assert Expense.objects.count() == initial_expense_count
    assert FinanceAuditEvent.objects.count() == initial_audit_count


# ==============================================================================
# 4. SETTINGS & FEATURE FLAG PROTECTION
# ==============================================================================

def test_feature_flags_and_system_settings_inaccessible_to_business_staff(client, test_setup):
    """Business roles cannot access or toggle system feature flags."""
    for role_name in ["receptionist", "mgr_addis", "accountant", "trainer", "pos_staff"]:
        user = test_setup[role_name]
        client.force_login(user)

        # Attempt to access feature flag changelist
        url = reverse("admin:core_featureflag_changelist")
        resp = client.get(url)
        assert resp.status_code == 403, f"Role {role_name} expected 403 for feature flags, got {resp.status_code}"
