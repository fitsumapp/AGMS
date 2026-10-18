import concurrent.futures
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import has_role, is_owner, seed_roles
from apps.analytics.dates import DateFilterRange
from apps.billing.models import Invoice, Payment, PaymentMethod, Refund
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.finance.models import (
    AccountAdjustment,
    AccountTransfer,
    BranchFinanceSettings,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    FinanceAuditEvent,
    FinancialAccount,
    FinancialReconciliation,
    Supplier,
)
from apps.finance.permissions import (
    can_approve_expense,
    can_create_expense,
    can_record_expense_payment,
    get_user_finance_branches,
)
from apps.finance.reports import (
    extract_account_movements,
    extract_approval_audit_report,
    extract_branch_financial_summary,
    extract_cash_flow_statement,
    extract_daily_reconciliation,
    extract_expense_category_summary,
    extract_expense_register,
    extract_income_expense_summary,
    extract_outstanding_expenses,
    extract_supplier_payments,
)
from apps.finance.selectors import (
    get_cash_flow_summary,
    get_expenses_by_branch,
    get_expenses_by_category,
    get_finance_overview_metrics,
    get_top_suppliers_breakdown,
)
from apps.finance.services import (
    approve_expense,
    close_reconciliation,
    conduct_reconciliation,
    create_expense,
    perform_account_adjustment,
    record_expense_payment,
    reject_expense,
    reopen_reconciliation,
    submit_expense,
    transfer_funds,
    void_expense,
    void_expense_payment,
    get_or_create_branch_finance_settings,
    post_verified_billing_batch_to_account,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_finance_prerequisites(db):
    seed_roles()
    GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "AGMS Gym",
            "legal_name": "Antigravity Fitness PLC",
            "email": "support@agms.test",
            "phone": "+251911000000",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )


@pytest.fixture
def test_branches():
    b1 = Branch.objects.create(code="BOLE", name="Bole Branch", address="Bole Road", is_active=True)
    b2 = Branch.objects.create(code="MEG", name="Megenagna Branch", address="Megenagna Square", is_active=True)
    return b1, b2


@pytest.fixture
def test_users(test_branches):
    b1, b2 = test_branches
    from django.contrib.auth.models import Group

    owner = User.objects.create_user("owner@agms.test", "OwnerPass12345!", is_staff=True)
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    mgr_bole = User.objects.create_user("mgr_bole@agms.test", "ManagerPass123!", is_staff=True)
    mgr_bole.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_bole, branch=b1, is_active=True, is_primary=True)

    mgr_meg = User.objects.create_user("mgr_meg@agms.test", "ManagerPass123!", is_staff=True)
    mgr_meg.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_meg, branch=b2, is_active=True, is_primary=True)

    accountant = User.objects.create_user("accountant@agms.test", "AccountPass123!", is_staff=True)
    accountant.groups.add(Group.objects.get(name="Accountant"))
    UserBranchAccess.objects.create(user=accountant, branch=b1, is_active=True, is_primary=True)
    UserBranchAccess.objects.create(user=accountant, branch=b2, is_active=True, is_primary=False)

    recept = User.objects.create_user("recept@agms.test", "ReceptPass123!", is_staff=True)
    recept.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=recept, branch=b1, is_active=True, is_primary=True)

    trainer = User.objects.create_user("trainer@agms.test", "TrainerPass123!", is_staff=True)
    trainer.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer, branch=b1, is_active=True, is_primary=True)

    return {
        "owner": owner,
        "mgr_bole": mgr_bole,
        "mgr_meg": mgr_meg,
        "accountant": accountant,
        "receptionist": recept,
        "trainer": trainer,
    }


@pytest.fixture
def test_finance_accounts(test_branches):
    b1, b2 = test_branches
    today = timezone.localdate()
    # Bole Cash on hand
    cash_bole = FinancialAccount.objects.create(
        name="Main Cash Drawer - Bole",
        account_type=FinancialAccount.AccountType.CASH_ON_HAND,
        branch=b1,
        opening_balance=Decimal("5000.00"),
        opening_date=today,
        current_balance=Decimal("5000.00"),
        is_active=True,
    )
    # Bole Bank
    bank_bole = FinancialAccount.objects.create(
        name="CBE Operating - Bole",
        account_type=FinancialAccount.AccountType.BANK_ACCOUNT,
        branch=b1,
        bank_name="Commercial Bank of Ethiopia",
        account_number="1000123456789",
        opening_balance=Decimal("50000.00"),
        opening_date=today,
        current_balance=Decimal("50000.00"),
        is_active=True,
    )
    # Megenagna Cash on hand
    cash_meg = FinancialAccount.objects.create(
        name="Main Cash Drawer - Megenagna",
        account_type=FinancialAccount.AccountType.CASH_ON_HAND,
        branch=b2,
        opening_balance=Decimal("3000.00"),
        opening_date=today,
        current_balance=Decimal("3000.00"),
        is_active=True,
    )
    return {
        "cash_bole": cash_bole,
        "bank_bole": bank_bole,
        "cash_meg": cash_meg,
    }


@pytest.fixture
def test_category():
    cat, _ = ExpenseCategory.objects.get_or_create(
        code="UTILITIES",
        defaults={"name": "Utilities", "is_active": True, "order": 2},
    )
    return cat


# 1. Expense Categories: Creation, edit, enable/disable, historical linkage
def test_expense_categories_lifecycle_and_historical_integrity(test_branches, test_users):
    b1, _ = test_branches
    owner = test_users["owner"]

    cat = ExpenseCategory.objects.create(
        code="WIFI_TEMP",
        name="Temporary WiFi",
        description="Hotspot and backup 4G",
        is_active=True,
        order=50,
    )
    assert cat.is_active is True

    # Create expense under category
    exp = create_expense(
        actor=owner,
        branch=b1,
        category=cat,
        description="Router backup bundle",
        amount=Decimal("1200.00"),
    )
    assert exp.category == cat

    # Disable category
    cat.is_active = False
    cat.save()

    # Existing expense retains original category
    exp.refresh_from_db()
    assert exp.category.code == "WIFI_TEMP"

    # Creating a new expense under disabled category is rejected
    with pytest.raises(ValidationError) as exc:
        create_expense(
            actor=owner,
            branch=b1,
            category=cat,
            description="Attempt under disabled category",
            amount=Decimal("500.00"),
        )
    assert "Selected expense category is currently inactive" in str(exc.value)


# 2. Supplier Management: Creation, TIN/VAT, Branch scoping
def test_supplier_creation_and_scoping(test_branches, test_users, test_category):
    b1, b2 = test_branches
    owner = test_users["owner"]
    mgr_meg = test_users["mgr_meg"]

    # Gym-wide supplier
    s_gym = Supplier.objects.create(
        name="Ethiopian Electric Utility",
        supplier_type=Supplier.SupplierType.UTILITY,
        tax_identification_number="TIN-123456789",
        branch=None,
        is_active=True,
    )
    assert s_gym.masked_tax_id.endswith("789")

    # Branch-specific supplier for Bole
    s_bole = Supplier.objects.create(
        name="Bole Janitorial Services",
        supplier_type=Supplier.SupplierType.SERVICE_PROVIDER,
        branch=b1,
        is_active=True,
    )

    # Creating expense in Bole with Bole supplier -> Success
    exp_bole = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Cleaning contract",
        amount=Decimal("3500.00"),
        supplier=s_bole,
    )
    assert exp_bole.supplier == s_bole

    # Attempting to assign Bole-only supplier to Megenagna branch -> Rejection
    with pytest.raises(ValidationError) as exc:
        create_expense(
            actor=mgr_meg,
            branch=b2,
            category=test_category,
            description="Cleaning Megenagna",
            amount=Decimal("3000.00"),
            supplier=s_bole,
        )
    assert "restricted to a different branch" in str(exc.value)


# 3. Expense Draft and Submission Workflow
def test_expense_draft_and_submission(test_branches, test_users, test_category):
    b1, _ = test_branches
    mgr_bole = test_users["mgr_bole"]

    # Create as DRAFT
    exp = create_expense(
        actor=mgr_bole,
        branch=b1,
        category=test_category,
        description="Office water refill",
        amount=Decimal("800.00"),
        tax_amount=Decimal("120.00"),
        submit_for_approval=False,
    )
    assert exp.approval_status == Expense.ApprovalStatus.DRAFT
    assert exp.total_amount == Decimal("920.00")
    assert exp.balance_due == Decimal("920.00")

    # Submit for approval
    submitted_exp = submit_expense(actor=mgr_bole, expense=exp)
    assert submitted_exp.approval_status == Expense.ApprovalStatus.SUBMITTED
    assert submitted_exp.submitted_at is not None


# 4. Approval and Rejection Controls
def test_expense_approval_and_rejection(test_branches, test_users, test_category):
    b1, _ = test_branches
    owner = test_users["owner"]
    mgr_bole = test_users["mgr_bole"]

    # Expense 1: Approved by Owner
    exp1 = create_expense(
        actor=mgr_bole,
        branch=b1,
        category=test_category,
        description="Facility plumbing",
        amount=Decimal("4000.00"),
        submit_for_approval=True,
    )
    app1 = approve_expense(actor=owner, expense=exp1)
    assert app1.approval_status == Expense.ApprovalStatus.APPROVED
    assert app1.approved_by == owner
    assert app1.approved_at is not None

    # Expense 2: Rejected by Owner
    exp2 = create_expense(
        actor=mgr_bole,
        branch=b1,
        category=test_category,
        description="Unbudgeted sound system",
        amount=Decimal("25000.00"),
        submit_for_approval=True,
    )
    # Rejection without reason fails
    with pytest.raises(ValidationError):
        reject_expense(actor=owner, expense=exp2, reason="")

    rej2 = reject_expense(actor=owner, expense=exp2, reason="Over annual entertainment budget")
    assert rej2.approval_status == Expense.ApprovalStatus.REJECTED
    assert rej2.rejection_reason == "Over annual entertainment budget"
    assert rej2.rejected_by == owner


# 5. Branch Manager Approval Limits
def test_branch_manager_approval_limit_enforcement(test_branches, test_users, test_category):
    b1, _ = test_branches
    owner = test_users["owner"]
    mgr_bole = test_users["mgr_bole"]
    accountant = test_users["accountant"]

    # Set Bole limit to 8,000 ETB
    settings = get_or_create_branch_finance_settings(b1)
    settings.manager_approval_limit = Decimal("8000.00")
    settings.allow_self_approval = False
    settings.save()

    # Expense created by accountant: 5,000 ETB (within limit) -> Manager can approve
    exp_within = create_expense(
        actor=accountant,
        branch=b1,
        category=test_category,
        description="Filter replacement",
        amount=Decimal("5000.00"),
        submit_for_approval=True,
    )
    app_res = approve_expense(actor=mgr_bole, expense=exp_within)
    assert app_res.approval_status == Expense.ApprovalStatus.APPROVED

    # Expense created by accountant: 12,000 ETB (exceeds limit) -> Manager approval is rejected
    exp_over = create_expense(
        actor=accountant,
        branch=b1,
        category=test_category,
        description="AC Unit motor",
        amount=Decimal("12000.00"),
        submit_for_approval=True,
    )
    with pytest.raises(ValidationError) as exc:
        approve_expense(actor=mgr_bole, expense=exp_over)
    assert "exceeds your Branch Manager approval limit" in str(exc.value)

    # Gym Owner can approve regardless of limit
    owner_app = approve_expense(actor=owner, expense=exp_over)
    assert owner_app.approval_status == Expense.ApprovalStatus.APPROVED


# 6. Self-Approval Restriction
def test_self_approval_restriction(test_branches, test_users, test_category):
    b1, _ = test_branches
    mgr_bole = test_users["mgr_bole"]

    settings = get_or_create_branch_finance_settings(b1)
    settings.allow_self_approval = False
    settings.save()

    # Manager creates an expense
    exp = create_expense(
        actor=mgr_bole,
        branch=b1,
        category=test_category,
        description="Office printing ink",
        amount=Decimal("1500.00"),
        submit_for_approval=True,
    )

    # Attempting to self-approve is rejected by policy
    with pytest.raises(ValidationError) as exc:
        approve_expense(actor=mgr_bole, expense=exp)
    assert "cannot approve your own expense submission" in str(exc.value)


# 7. Partial and Full Expense Payments
def test_partial_and_full_expense_payments(test_branches, test_users, test_category, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    initial_cash = cash_bole.current_balance

    exp = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Annual generator service",
        amount=Decimal("2000.00"),
    )
    approve_expense(actor=owner, expense=exp)

    # Installment 1: Partial payment of 800 ETB
    pay1 = record_expense_payment(
        actor=accountant,
        expense=exp,
        financial_account=cash_bole,
        amount=Decimal("800.00"),
        notes="Installment 1",
    )
    exp.refresh_from_db()
    cash_bole.refresh_from_db()
    assert pay1.status == ExpensePayment.Status.VERIFIED
    assert exp.paid_amount == Decimal("800.00")
    assert exp.balance_due == Decimal("1200.00")
    assert exp.payment_status == Expense.PaymentStatus.PARTIALLY_PAID
    assert cash_bole.current_balance == initial_cash - Decimal("800.00")

    # Installment 2: Overpayment attempt (1500 ETB > 1200 ETB remaining) -> Rejected
    with pytest.raises(ValidationError) as exc:
        record_expense_payment(
            actor=accountant,
            expense=exp,
            financial_account=cash_bole,
            amount=Decimal("1500.00"),
        )
    assert "exceeds outstanding balance due" in str(exc.value)

    # Installment 2: Exact final payment of 1200 ETB
    pay2 = record_expense_payment(
        actor=accountant,
        expense=exp,
        financial_account=cash_bole,
        amount=Decimal("1200.00"),
        notes="Installment 2 final",
    )
    exp.refresh_from_db()
    cash_bole.refresh_from_db()
    assert exp.paid_amount == Decimal("2000.00")
    assert exp.balance_due == Decimal("0.00")
    assert exp.payment_status == Expense.PaymentStatus.PAID
    assert cash_bole.current_balance == initial_cash - Decimal("2000.00")


# 8. Duplicate Payment Prevention via Idempotency Key
def test_idempotent_duplicate_payment_prevention(test_branches, test_users, test_category, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]

    exp = create_expense(actor=owner, branch=b1, category=test_category, description="Supplies", amount=Decimal("1000.00"))
    approve_expense(actor=owner, expense=exp)

    key = "idem-unique-key-1234"
    record_expense_payment(actor=accountant, expense=exp, financial_account=cash_bole, amount=Decimal("300.00"), idempotency_key=key)

    # Second request with the same idempotency key is rejected
    with pytest.raises(ValidationError) as exc:
        record_expense_payment(actor=accountant, expense=exp, financial_account=cash_bole, amount=Decimal("300.00"), idempotency_key=key)
    assert "duplicate idempotency key" in str(exc.value)


# 9. Voiding Payment & Reversal
def test_void_expense_payment_reversal(test_branches, test_users, test_category, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    initial_cash = cash_bole.current_balance

    exp = create_expense(actor=owner, branch=b1, category=test_category, description="Lighting", amount=Decimal("1000.00"))
    approve_expense(actor=owner, expense=exp)

    pay = record_expense_payment(actor=accountant, expense=exp, financial_account=cash_bole, amount=Decimal("1000.00"))
    exp.refresh_from_db()
    assert exp.payment_status == Expense.PaymentStatus.PAID

    # Void the payment
    void_pay = void_expense_payment(actor=owner, payment=pay, reason="Wrong bank voucher issued")
    assert void_pay.status == ExpensePayment.Status.VOIDED
    assert void_pay.void_reason == "Wrong bank voucher issued"

    # Balances restored
    cash_bole.refresh_from_db()
    exp.refresh_from_db()
    assert cash_bole.current_balance == initial_cash
    assert exp.paid_amount == Decimal("0.00")
    assert exp.balance_due == Decimal("1000.00")
    assert exp.payment_status == Expense.PaymentStatus.UNPAID


# 10. Internal Account Transfers (Neutral to Revenue & Expenses)
def test_internal_transfers_and_revenue_neutrality(test_branches, test_users, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    cash_bole = test_finance_accounts["cash_bole"]
    bank_bole = test_finance_accounts["bank_bole"]

    initial_cash = cash_bole.current_balance
    initial_bank = bank_bole.current_balance

    # Transfer 2,000 ETB from Cash drawer to CBE Bank Account
    trf = transfer_funds(
        actor=owner,
        source_account=cash_bole,
        destination_account=bank_bole,
        amount=Decimal("2000.00"),
        reference="DEPOSIT-001",
        notes="End of day cash deposit",
    )
    assert trf.status == AccountTransfer.Status.COMPLETED

    cash_bole.refresh_from_db()
    bank_bole.refresh_from_db()
    assert cash_bole.current_balance == initial_cash - Decimal("2000.00")
    assert bank_bole.current_balance == initial_bank + Decimal("2000.00")

    # Verify Cash Flow Selector: Transfer does not inflate revenue or expenses!
    today = timezone.localdate()
    cf = get_cash_flow_summary([b1], today, today)
    assert cf["verified_collections"] == Decimal("0.00")
    assert cf["paid_expenses"] == Decimal("0.00")
    assert cf["internal_transfers_volume"] == Decimal("2000.00")


# 11. Account Adjustments (Debit and Credit)
def test_account_adjustments(test_branches, test_users, test_finance_accounts):
    owner = test_users["owner"]
    cash_bole = test_finance_accounts["cash_bole"]
    initial_cash = cash_bole.current_balance

    # Credit adjustment: +500 ETB
    adj_credit = perform_account_adjustment(
        actor=owner,
        account=cash_bole,
        adjustment_type=AccountAdjustment.AdjustmentType.CREDIT,
        amount=Decimal("500.00"),
        reason="Found unrecorded cash float",
    )
    cash_bole.refresh_from_db()
    assert cash_bole.current_balance == initial_cash + Decimal("500.00")

    # Debit adjustment: -200 ETB
    adj_debit = perform_account_adjustment(
        actor=owner,
        account=cash_bole,
        adjustment_type=AccountAdjustment.AdjustmentType.DEBIT,
        amount=Decimal("200.00"),
        reason="Bank fee deduction adjustment",
    )
    cash_bole.refresh_from_db()
    assert cash_bole.current_balance == initial_cash + Decimal("300.00")


# 12. Daily Reconciliation & Closed Period Protection
def test_reconciliation_workflow_and_closed_period_protection(test_branches, test_users, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    today = timezone.localdate()

    # Expected balance is 5,000 ETB. Counted cash is 4,950 ETB (50 ETB shortage)
    recon = conduct_reconciliation(
        actor=accountant,
        branch=b1,
        account=cash_bole,
        actual_counted_balance=Decimal("4950.00"),
        reconciliation_date=today,
        variance_reason="Missing receipt for taxi fare",
        submit_for_closure=True,
    )
    assert recon.variance == Decimal("-50.00")
    assert recon.status == FinancialReconciliation.Status.SUBMITTED

    # Close reconciliation
    closed_recon = close_reconciliation(actor=owner, reconciliation=recon)
    assert closed_recon.status == FinancialReconciliation.Status.APPROVED_CLOSED
    assert closed_recon.closed_at is not None

    # Unauthorized reopening attempt by non-owner fails
    with pytest.raises(ValidationError) as exc:
        reopen_reconciliation(actor=accountant, reconciliation=closed_recon, reason="Trying to reopen")
    assert "Only a Gym Owner can reopen" in str(exc.value)

    # Owner reopens reconciliation
    reopened = reopen_reconciliation(actor=owner, reconciliation=closed_recon, reason="Audit review")
    assert reopened.status == FinancialReconciliation.Status.DRAFT


# 13. Cash Flow Reporting & Refund Treatment
def test_cash_flow_reporting_and_refund_treatment(test_branches, test_users, test_category, test_finance_accounts):
    from apps.members.services import register_member
    from apps.memberships.models import Membership, MembershipPackage

    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    today = timezone.localdate()

    # 1. Member payment of 3,000 ETB verified
    member = register_member(actor=owner, home_branch=b1, first_name="Cashflow", last_name="Member", phone="+251911990011")
    pm, _ = PaymentMethod.objects.get_or_create(code="CASH", defaults={"name": "Cash", "is_active": True})
    inv = Invoice.objects.create(
        invoice_number="INV-CF-001",
        member=member,
        branch=b1,
        subtotal=Decimal("3000.00"),
        total_amount=Decimal("3000.00"),
        paid_amount=Decimal("3000.00"),
        balance_due=Decimal("0.00"),
        issue_date=today,
        due_date=today,
        status=Invoice.Status.PAID,
    )
    pay = Payment.objects.create(
        payment_number="PAY-CF-001",
        invoice=inv,
        branch=b1,
        payment_method=pm,
        amount=Decimal("3000.00"),
        status=Payment.Status.VERIFIED,
        received_by=owner,
        paid_at=timezone.now(),
        idempotency_key="IDEM-CF-001",
    )

    # 2. Member refund of 500 ETB completed
    ref = Refund.objects.create(
        refund_number="REF-CF-001",
        payment=pay,
        invoice=inv,
        amount=Decimal("500.00"),
        reason="Partial downgrade refund",
        status=Refund.Status.COMPLETED,
        completed_at=timezone.now(),
    )

    # 3. Operational expense paid: 1,000 ETB
    exp = create_expense(actor=owner, branch=b1, category=test_category, description="Supplies", amount=Decimal("1000.00"))
    approve_expense(actor=owner, expense=exp)
    record_expense_payment(actor=accountant, expense=exp, financial_account=cash_bole, amount=Decimal("1000.00"))

    # Net Cash Movement = Collections (3000) - Refunds (500) - Expenses (1000) = 1500 ETB
    metrics = get_finance_overview_metrics([b1], today, today)
    assert metrics["total_collections"] == Decimal("3000.00")
    assert metrics["total_refunds"] == Decimal("500.00")
    assert metrics["total_paid_expenses"] == Decimal("1000.00")
    assert metrics["net_cash_movement"] == Decimal("1500.00")


# 14. Branch Isolation & Role Permissions
def test_branch_isolation_and_role_access(test_branches, test_users, test_category):
    b1, b2 = test_branches
    mgr_bole = test_users["mgr_bole"]
    mgr_meg = test_users["mgr_meg"]
    receptionist = test_users["receptionist"]
    trainer = test_users["trainer"]

    # Manager of Megenagna cannot approve Bole expense
    exp_bole = create_expense(actor=mgr_bole, branch=b1, category=test_category, description="Bole paint", amount=Decimal("2000.00"), submit_for_approval=True)
    with pytest.raises(ValidationError) as exc:
        approve_expense(actor=mgr_meg, expense=exp_bole)
    assert "not have authorization" in str(exc.value)

    # Receptionist cannot create expense unless enabled by branch setting
    b_settings = get_or_create_branch_finance_settings(b1)
    b_settings.receptionist_can_create_expenses = False
    b_settings.save()

    with pytest.raises(ValidationError) as exc:
        create_expense(actor=receptionist, branch=b1, category=test_category, description="Desk pens", amount=Decimal("100.00"))
    assert "do not have permission" in str(exc.value)

    # Enable receptionist creation
    b_settings.receptionist_can_create_expenses = True
    b_settings.save()
    exp_recept = create_expense(actor=receptionist, branch=b1, category=test_category, description="Desk pens", amount=Decimal("100.00"))
    assert exp_recept.approval_status == Expense.ApprovalStatus.DRAFT

    # Trainer cannot access or create expenses
    with pytest.raises(ValidationError) as exc:
        create_expense(actor=trainer, branch=b1, category=test_category, description="Gym mats", amount=Decimal("500.00"))
    assert "do not have permission" in str(exc.value)


# 15. Financial Reports Extraction (10 Reports)
def test_all_ten_financial_reports_extract_successfully(test_branches, test_users, test_category, test_finance_accounts):
    from apps.analytics.dates import resolve_date_filter
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    today = timezone.localdate()
    dr = resolve_date_filter(preset="today")

    # Seed an expense and payment
    exp = create_expense(actor=owner, branch=b1, category=test_category, description="Janitorial supplies", amount=Decimal("1000.00"))
    approve_expense(actor=owner, expense=exp)
    record_expense_payment(actor=accountant, expense=exp, financial_account=cash_bole, amount=Decimal("600.00"))

    # Test all 10 reports extract without error
    r1 = extract_expense_register([b1], dr)
    assert len(r1) >= 1
    assert r1[0]["total_amount"] == 1000.0

    r2 = extract_expense_category_summary([b1], dr)
    assert len(r2) >= 1

    r3 = extract_supplier_payments([b1], dr)
    assert isinstance(r3, list)

    r4 = extract_outstanding_expenses([b1], dr)
    assert len(r4) >= 1
    assert r4[0]["balance_due"] == 400.0

    r5 = extract_cash_flow_statement([b1], dr)
    assert len(r5) == 8

    r6 = extract_account_movements([b1], dr)
    assert len(r6) >= 1

    r7 = extract_daily_reconciliation([b1], dr)
    assert isinstance(r7, list)

    r8 = extract_branch_financial_summary([b1], dr)
    assert len(r8) == 1

    r9 = extract_income_expense_summary([b1], dr)
    assert len(r9) == 1
    assert r9[0]["operating_expenses_paid"] == 600.0

    r10 = extract_approval_audit_report([b1], dr)
    assert len(r10) >= 1


# 16. Concurrent Expense Payments with PostgreSQL Row-Level Lock
@pytest.mark.django_db(transaction=True)
def test_concurrent_expense_payments_serialization(test_branches, test_users, test_category, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    cash_bole = test_finance_accounts["cash_bole"]

    # Expense with total 1000 ETB
    exp = create_expense(actor=owner, branch=b1, category=test_category, description="Concurrent repair", amount=Decimal("1000.00"))
    approve_expense(actor=owner, expense=exp)

    results = []

    def make_payment(worker_id):
        from django.db import connection
        try:
            # Re-read expense and attempt to pay 600 ETB
            # If 2 threads try to pay 600 ETB against a 1000 ETB balance, exactly 1 must succeed and the other must fail with exceeds balance!
            e = Expense.objects.get(pk=exp.pk)
            acc = FinancialAccount.objects.get(pk=cash_bole.pk)
            pay = record_expense_payment(
                actor=owner,
                expense=e,
                financial_account=acc,
                amount=Decimal("600.00"),
                idempotency_key=f"worker-{worker_id}",
            )
            return "SUCCESS"
        except ValidationError as ex:
            return f"VALIDATION_ERROR: {ex}"
        except Exception as ex:
            return f"ERROR: {type(ex).__name__}"
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(make_payment, 1)
        f2 = executor.submit(make_payment, 2)
        r1 = f1.result()
        r2 = f2.result()

    outcomes = [r1, r2]
    success_count = outcomes.count("SUCCESS")
    assert success_count == 1, f"Expected exactly 1 payment success, got {outcomes}"
    failed_outcome = [o for o in outcomes if o != "SUCCESS"][0]
    assert "exceeds outstanding balance due" in failed_outcome

    exp.refresh_from_db()
    cash_bole.refresh_from_db()
    assert exp.paid_amount == Decimal("600.00")
    assert exp.balance_due == Decimal("400.00")
    assert exp.payment_status == Expense.PaymentStatus.PARTIALLY_PAID


# 17. Private Attachment Security & Access Control
def test_private_receipt_and_payment_attachment_security(test_branches, test_users, test_category, test_finance_accounts):
    from django.core.files.uploadedfile import SimpleUploadedFile
    client = Client()
    b1, b2 = test_branches
    owner = test_users["owner"]
    mgr_bole = test_users["mgr_bole"]
    mgr_meg = test_users["mgr_meg"]
    trainer = test_users["trainer"]
    cash_bole = test_finance_accounts["cash_bole"]

    receipt_file = SimpleUploadedFile("invoice_jan.pdf", b"%PDF-1.4 test receipt content", content_type="application/pdf")
    exp = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Private equipment repair invoice",
        amount=Decimal("1500.00"),
        receipt_attachment=receipt_file,
    )
    approve_expense(actor=owner, expense=exp)

    voucher_file = SimpleUploadedFile("voucher_jan.png", b"\x89PNG\r\n\x1a\n test voucher content", content_type="image/png")
    pay = record_expense_payment(
        actor=owner,
        expense=exp,
        financial_account=cash_bole,
        amount=Decimal("1500.00"),
        attachment=voucher_file,
    )

    receipt_url = reverse("expense-receipt-download", kwargs={"expense_id": exp.id})
    voucher_url = reverse("payment-attachment-download", kwargs={"payment_id": pay.id})

    # Unauthenticated -> Redirect to login
    r_unauth = client.get(receipt_url)
    assert r_unauth.status_code == 302

    # Trainer -> Forbidden (HTTP 403)
    client.force_login(trainer)
    r_trainer = client.get(receipt_url)
    assert r_trainer.status_code == 403

    # Manager of Megenagna (cross-branch) -> Forbidden (HTTP 403)
    client.force_login(mgr_meg)
    r_cross = client.get(receipt_url)
    assert r_cross.status_code == 403

    # Manager of Bole -> Success (HTTP 200)
    client.force_login(mgr_bole)
    r_bole = client.get(receipt_url)
    assert r_bole.status_code == 200

    r_voucher = client.get(voucher_url)
    assert r_voucher.status_code == 200


# 18. Financial Overview Dashboard View & Access Control
def test_financial_overview_dashboard_view_and_unauthorized_access(test_branches, test_users):
    client = Client()
    b1, _ = test_branches
    owner = test_users["owner"]
    mgr_bole = test_users["mgr_bole"]
    accountant = test_users["accountant"]
    trainer = test_users["trainer"]
    overview_url = reverse("finance-overview")

    # Trainer -> 403 Forbidden
    client.force_login(trainer)
    r_trainer = client.get(overview_url)
    assert r_trainer.status_code == 403

    # Branch Manager -> 200 OK
    client.force_login(mgr_bole)
    r_mgr = client.get(overview_url)
    assert r_mgr.status_code == 200
    assert "Financial Overview" in r_mgr.content.decode()

    # Accountant -> 200 OK
    client.force_login(accountant)
    r_acc = client.get(overview_url)
    assert r_acc.status_code == 200

    # Owner with branch filter
    client.force_login(owner)
    r_owner = client.get(f"{overview_url}?branch={b1.id}&period=this_month")
    assert r_owner.status_code == 200


# 19. Timezone & Local Date Boundaries
def test_date_and_timezone_boundaries(test_branches, test_users, test_category):
    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    # Test expense dates strictly adhere to localdate
    exp = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Daily water",
        amount=Decimal("200.00"),
    )
    assert exp.expense_date == today

    # Filter metrics strictly by local date boundary
    metrics_today = get_finance_overview_metrics([b1], today, today)
    assert metrics_today["start_date"] == today
    assert metrics_today["end_date"] == today


# 20. Negative Account Balance Policy Enforcement
def test_negative_balance_enforcement_and_branch_policy(test_branches, test_users, test_category, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]

    # Current balance is 5,000 ETB
    # Create expense for 6,000 ETB
    exp = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Bulk equipment spares",
        amount=Decimal("6000.00"),
    )
    approve_expense(actor=owner, expense=exp)

    # By default, negative balance is prohibited -> Rejection
    b_settings = get_or_create_branch_finance_settings(b1)
    b_settings.allow_negative_account_balance = False
    b_settings.save()
    cash_bole.allow_negative = False
    cash_bole.save()

    with pytest.raises(ValidationError) as exc:
        record_expense_payment(
            actor=accountant,
            expense=exp,
            financial_account=cash_bole,
            amount=Decimal("5500.00"),
        )
    assert "Insufficient funds in account" in str(exc.value)

    # Enable negative account balance in branch settings -> Now permitted
    b_settings.allow_negative_account_balance = True
    b_settings.save()

    pay = record_expense_payment(
        actor=accountant,
        expense=exp,
        financial_account=cash_bole,
        amount=Decimal("5500.00"),
    )
    cash_bole.refresh_from_db()
    assert pay.status == ExpensePayment.Status.VERIFIED
    assert cash_bole.current_balance == Decimal("-500.00")


# 21. Billing Source Records vs Finance Reconciliation & Settlement Posting
def test_billing_source_to_finance_reconciliation_and_batch_settlement(test_branches, test_users, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    today = timezone.localdate()

    from apps.billing.models import Invoice, Payment, PaymentMethod, Refund
    from apps.members.models import Member

    # Setup billing payment methods
    pm_cash, _ = PaymentMethod.objects.get_or_create(
        code="CASH_TEST",
        defaults={"name": "Cash Test", "method_type": "CASH", "is_active": True},
    )
    pm_bank, _ = PaymentMethod.objects.get_or_create(
        code="BANK_TEST",
        defaults={"name": "Bank Transfer Test", "method_type": "BANK_TRANSFER", "is_active": True},
    )

    member = Member.objects.create(
        member_number="MBR-9999",
        first_name="Abebe",
        last_name="Bikila",
        phone="+251911999999",
        home_branch=b1,
    )
    inv = Invoice.objects.create(
        invoice_number="INV-9999",
        member=member,
        branch=b1,
        issue_date=today,
        due_date=today,
        subtotal=Decimal("4000.00"),
        total_amount=Decimal("4000.00"),
        balance_due=Decimal("0.00"),
        paid_amount=Decimal("4000.00"),
        status=Invoice.Status.PAID,
    )

    # 1. Authoritative Phase 05 Customer Payment (Cash: 3,000 ETB)
    pay_cash = Payment.objects.create(
        payment_number="PAY-CASH-001",
        invoice=inv,
        branch=b1,
        amount=Decimal("3000.00"),
        payment_method=pm_cash,
        status=Payment.Status.VERIFIED,
        paid_at=timezone.now(),
        idempotency_key="idemp-pay-cash-001",
    )

    # 2. Authoritative Phase 05 Customer Refund (Cash: 500 ETB)
    refund = Refund.objects.create(
        refund_number="REF-001",
        payment=pay_cash,
        invoice=inv,
        amount=Decimal("500.00"),
        reason="Overcharge adjustment",
        status=Refund.Status.COMPLETED,
        completed_at=timezone.now(),
    )

    # Reconcile Bole Cash Account for today:
    # Opening balance: 5,000 ETB
    # Expected: 5000 + 3000 (cash payment) - 500 (refund) = 7500 ETB
    recon = conduct_reconciliation(
        actor=accountant,
        branch=b1,
        account=cash_bole,
        actual_counted_balance=Decimal("7500.00"),
        reconciliation_date=today,
        period_start=today,
        period_end=today,
        submit_for_closure=True,
    )
    assert recon.total_inflows == Decimal("3000.00")
    assert recon.total_outflows == Decimal("500.00")
    assert recon.expected_balance == Decimal("7500.00")
    assert recon.variance == Decimal("0.00")

    # Now post authoritative billing batch settlement into cash_bole:
    # Net collections = 3,000 - 500 = 2,500 ETB
    init_balance = cash_bole.current_balance
    res = post_verified_billing_batch_to_account(
        actor=accountant,
        branch=b1,
        account=cash_bole,
        start_date=today,
        end_date=today,
        payment_method_type="CASH",
    )
    cash_bole.refresh_from_db()
    assert res["net_collections"] == Decimal("2500.00")
    assert cash_bole.current_balance == init_balance + Decimal("2500.00")

    # Duplicate settlement posting -> Rejected (idempotent)
    with pytest.raises(ValidationError) as exc:
        post_verified_billing_batch_to_account(
            actor=accountant,
            branch=b1,
            account=cash_bole,
            start_date=today,
            end_date=today,
            payment_method_type="CASH",
        )
    assert "already been settled" in str(exc.value)


# 22. Expense Payment Void Modes & Duplicate Prevention
def test_expense_payment_void_modes_and_duplicate_prevention(test_branches, test_users, test_category, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]

    exp1 = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Printer toner supply",
        amount=Decimal("800.00"),
    )
    approve_expense(actor=owner, expense=exp1)

    pay1 = record_expense_payment(
        actor=accountant,
        expense=exp1,
        financial_account=cash_bole,
        amount=Decimal("800.00"),
    )
    cash_bole.refresh_from_db()
    balance_after_pay = cash_bole.current_balance

    # Mode A: Physical return of funds (restore_account_balance=True)
    v_pay1 = void_expense_payment(
        actor=owner,
        payment=pay1,
        reason="Vendor refunded defective toner",
        restore_account_balance=True,
    )
    cash_bole.refresh_from_db()
    exp1.refresh_from_db()
    assert v_pay1.status == ExpensePayment.Status.VOIDED
    assert exp1.payment_status == Expense.PaymentStatus.UNPAID
    assert cash_bole.current_balance == balance_after_pay + Decimal("800.00")

    # Duplicate void call -> Rejected
    with pytest.raises(ValidationError) as exc:
        void_expense_payment(
            actor=owner,
            payment=pay1,
            reason="Repeated void attempt",
        )
    assert "Only verified payments can be voided" in str(exc.value)

    # Mode B: Administrative cancellation without false cash restoration (restore_account_balance=False)
    exp2 = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Erroneous duplicate entry",
        amount=Decimal("300.00"),
    )
    approve_expense(actor=owner, expense=exp2)
    pay2 = record_expense_payment(
        actor=accountant,
        expense=exp2,
        financial_account=cash_bole,
        amount=Decimal("300.00"),
    )
    cash_bole.refresh_from_db()
    bal_before_admin_void = cash_bole.current_balance

    v_pay2 = void_expense_payment(
        actor=owner,
        payment=pay2,
        reason="Data-entry error, money never physically left bank",
        restore_account_balance=False,
    )
    cash_bole.refresh_from_db()
    exp2.refresh_from_db()
    assert v_pay2.status == ExpensePayment.Status.VOIDED
    assert exp2.payment_status == Expense.PaymentStatus.UNPAID
    # Account balance must NOT have increased
    assert cash_bole.current_balance == bal_before_admin_void


# 23. Closed-Period Integrity & Backdating Protection
def test_closed_period_integrity_and_backdating_rejection(test_branches, test_users, test_category, test_finance_accounts):
    b1, _ = test_branches
    owner = test_users["owner"]
    mgr_bole = test_users["mgr_bole"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    past_date = timezone.localdate() - timezone.timedelta(days=10)

    # 1. Conduct and CLOSE a reconciliation period covering past_date
    recon = conduct_reconciliation(
        actor=accountant,
        branch=b1,
        account=cash_bole,
        actual_counted_balance=cash_bole.opening_balance,
        reconciliation_date=past_date,
        period_start=past_date,
        period_end=past_date,
        submit_for_closure=True,
    )
    recon = close_reconciliation(actor=mgr_bole, reconciliation=recon)
    assert recon.status == FinancialReconciliation.Status.APPROVED_CLOSED

    # 2. Attempting to backdate an expense payment into the closed period -> REJECTED
    exp = create_expense(
        actor=owner,
        branch=b1,
        category=test_category,
        description="Backdated janitorial supplies",
        amount=Decimal("500.00"),
    )
    approve_expense(actor=owner, expense=exp)

    with pytest.raises(ValidationError) as exc:
        record_expense_payment(
            actor=accountant,
            expense=exp,
            financial_account=cash_bole,
            amount=Decimal("500.00"),
            payment_date=past_date,
        )
    assert "is closed and locked" in str(exc.value)

    # 3. Attempting to backdate an account adjustment into the closed period -> REJECTED
    with pytest.raises(ValidationError) as exc:
        perform_account_adjustment(
            actor=accountant,
            account=cash_bole,
            adjustment_type=AccountAdjustment.AdjustmentType.CREDIT,
            amount=Decimal("100.00"),
            reason="Backdated correction attempt",
            adjustment_date=past_date,
        )
    assert "is closed and locked" in str(exc.value)

    # 4. Attempting to create an overlapping reconciliation for closed period -> REJECTED
    with pytest.raises(ValidationError) as exc:
        conduct_reconciliation(
            actor=accountant,
            branch=b1,
            account=cash_bole,
            actual_counted_balance=cash_bole.opening_balance,
            reconciliation_date=past_date,
            period_start=past_date,
            period_end=past_date,
        )
    assert "already exists" in str(exc.value)

    # 5. Direct modification of closed reconciliation fields -> REJECTED
    recon.actual_counted_balance = Decimal("99999.00")
    with pytest.raises(ValidationError) as exc:
        recon.clean()
    assert "Closed reconciliation periods are immutable" in str(exc.value)

    # 6. Deletion of closed reconciliation -> REJECTED
    with pytest.raises(ValidationError) as exc:
        recon.delete()
    assert "Closed reconciliation periods cannot be deleted" in str(exc.value)

    # 7. Reopening: Non-owner manager -> REJECTED
    with pytest.raises(ValidationError) as exc:
        reopen_reconciliation(actor=mgr_bole, reconciliation=recon, reason="Auditing")
    assert "Only a Gym Owner can reopen" in str(exc.value)

    # 8. Reopening: Owner with valid reason -> SUCCESS
    reopened = reopen_reconciliation(actor=owner, reconciliation=recon, reason="External audit adjustments required")
    assert reopened.status == FinancialReconciliation.Status.DRAFT


# 24. Targeted Audit: Settlement Double-Posting Prevention & Concurrency Safety
@pytest.mark.django_db(transaction=True)
def test_settlement_double_posting_prevention_and_concurrency(test_branches, test_users, test_finance_accounts):
    import threading
    b1, _ = test_branches
    owner = test_users["owner"]
    accountant = test_users["accountant"]
    cash_bole = test_finance_accounts["cash_bole"]
    today = timezone.localdate()
    day1 = today - timezone.timedelta(days=3)
    day2 = today - timezone.timedelta(days=2)
    day3 = today - timezone.timedelta(days=1)

    from apps.billing.models import Invoice, Payment, PaymentMethod, Refund
    from apps.members.models import Member
    from apps.finance.models import SettledBillingPayment, SettledBillingRefund, BillingSettlementBatch

    pm_cash, _ = PaymentMethod.objects.get_or_create(
        code="CASH_AUDIT",
        defaults={"name": "Cash Audit", "method_type": "CASH", "is_active": True},
    )

    member = Member.objects.create(
        member_number="MBR-AUDIT-01",
        first_name="Haile",
        last_name="Gebrselassie",
        phone="+251911888888",
        home_branch=b1,
    )
    inv = Invoice.objects.create(
        invoice_number="INV-AUDIT-01",
        member=member,
        branch=b1,
        issue_date=day1,
        due_date=today,
        subtotal=Decimal("10000.00"),
        total_amount=Decimal("10000.00"),
        balance_due=Decimal("0.00"),
        paid_amount=Decimal("10000.00"),
        status=Invoice.Status.PAID,
    )

    # Create 3 verified cash payments across days 1, 2, and 3
    p1 = Payment.objects.create(
        payment_number="PAY-AUDIT-01",
        invoice=inv,
        branch=b1,
        amount=Decimal("1000.00"),
        payment_method=pm_cash,
        status=Payment.Status.VERIFIED,
        paid_at=timezone.make_aware(timezone.datetime.combine(day1, timezone.datetime.min.time())),
        idempotency_key="idemp-audit-p1",
    )
    p2 = Payment.objects.create(
        payment_number="PAY-AUDIT-02",
        invoice=inv,
        branch=b1,
        amount=Decimal("1000.00"),
        payment_method=pm_cash,
        status=Payment.Status.VERIFIED,
        paid_at=timezone.make_aware(timezone.datetime.combine(day2, timezone.datetime.min.time())),
        idempotency_key="idemp-audit-p2",
    )
    p3 = Payment.objects.create(
        payment_number="PAY-AUDIT-03",
        invoice=inv,
        branch=b1,
        amount=Decimal("1000.00"),
        payment_method=pm_cash,
        status=Payment.Status.VERIFIED,
        paid_at=timezone.make_aware(timezone.datetime.combine(day3, timezone.datetime.min.time())),
        idempotency_key="idemp-audit-p3",
    )

    init_balance = cash_bole.current_balance

    # Case 1: Partially Overlapping Range 1 (Day 1 to Day 2)
    # Settles p1 and p2 (2,000 ETB)
    batch1 = post_verified_billing_batch_to_account(
        actor=accountant,
        branch=b1,
        account=cash_bole,
        start_date=day1,
        end_date=day2,
        payment_method_type="CASH",
    )
    cash_bole.refresh_from_db()
    assert batch1["payment_count"] == 2
    assert batch1["net_collections"] == Decimal("2000.00")
    assert cash_bole.current_balance == init_balance + Decimal("2000.00")
    assert SettledBillingPayment.objects.filter(payment=p1).exists()
    assert SettledBillingPayment.objects.filter(payment=p2).exists()
    assert not SettledBillingPayment.objects.filter(payment=p3).exists()

    # Case 2: Partially Overlapping Range 2 (Day 2 to Day 3)
    # Day 2 (p2) is already settled! Must ONLY settle p3 (1,000 ETB).
    # Must NOT double-settle p2!
    batch2 = post_verified_billing_batch_to_account(
        actor=accountant,
        branch=b1,
        account=cash_bole,
        start_date=day2,
        end_date=day3,
        payment_method_type="CASH",
    )
    cash_bole.refresh_from_db()
    assert batch2["payment_count"] == 1
    assert batch2["net_collections"] == Decimal("1000.00")
    # Total account increase must be exactly 3,000 ETB (not 4,000 ETB!)
    assert cash_bole.current_balance == init_balance + Decimal("3000.00")
    assert SettledBillingPayment.objects.filter(payment=p3).exists()

    # Case 3: Fully Overlapping Range with Different Boundaries (Day 1 to Day 3)
    # All payments (p1, p2, p3) are already settled!
    with pytest.raises(ValidationError) as exc:
        post_verified_billing_batch_to_account(
            actor=accountant,
            branch=b1,
            account=cash_bole,
            start_date=day1,
            end_date=day3,
            payment_method_type="CASH",
        )
    assert "All transactions in this date range have already been settled" in str(exc.value)

    # Case 4: Same Payment Method Across Multiple Financial Accounts
    # Attempting to settle the same Day 1 to Day 3 into a secondary account
    from apps.finance.models import FinancialAccount
    petty_cash = FinancialAccount.objects.create(
        name="Petty Cash Drawer - Bole",
        account_type=FinancialAccount.AccountType.PETTY_CASH,
        branch=b1,
        opening_balance=Decimal("500.00"),
        current_balance=Decimal("500.00"),
    )
    with pytest.raises(ValidationError) as exc:
        post_verified_billing_batch_to_account(
            actor=accountant,
            branch=b1,
            account=petty_cash,
            start_date=day1,
            end_date=day3,
            payment_method_type="CASH",
        )
    assert "All transactions in this date range have already been settled" in str(exc.value)
    petty_cash.refresh_from_db()
    assert petty_cash.current_balance == Decimal("500.00")

    # Case 5: Late-Verified Payment & Late-Completed Refund
    # A payment p4 and refund r1 on Day 1 were pending and now get verified/completed
    p4 = Payment.objects.create(
        payment_number="PAY-AUDIT-04",
        invoice=inv,
        branch=b1,
        amount=Decimal("1500.00"),
        payment_method=pm_cash,
        status=Payment.Status.VERIFIED,
        paid_at=timezone.make_aware(timezone.datetime.combine(day1, timezone.datetime.min.time())),
        idempotency_key="idemp-audit-p4",
    )
    r1 = Refund.objects.create(
        refund_number="REF-AUDIT-01",
        payment=p1,
        invoice=inv,
        amount=Decimal("200.00"),
        reason="Partial service adjustment",
        status=Refund.Status.COMPLETED,
        completed_at=timezone.make_aware(timezone.datetime.combine(day2, timezone.datetime.min.time())),
    )

    # Settlement for Day 1 to Day 3 now cleanly captures ONLY p4 and r1!
    batch3 = post_verified_billing_batch_to_account(
        actor=accountant,
        branch=b1,
        account=cash_bole,
        start_date=day1,
        end_date=day3,
        payment_method_type="CASH",
    )
    cash_bole.refresh_from_db()
    assert batch3["payment_count"] == 1  # only p4
    assert batch3["refund_count"] == 1   # only r1
    assert batch3["net_collections"] == Decimal("1300.00")  # 1500 - 200
    assert cash_bole.current_balance == init_balance + Decimal("3000.00") + Decimal("1300.00")
    assert SettledBillingPayment.objects.filter(payment=p4).exists()
    assert SettledBillingRefund.objects.filter(refund=r1).exists()

    # Case 6: Concurrent Settlement Serialization & Double-Posting Race Safety
    # Create new payment p5
    p5 = Payment.objects.create(
        payment_number="PAY-AUDIT-05",
        invoice=inv,
        branch=b1,
        amount=Decimal("700.00"),
        payment_method=pm_cash,
        status=Payment.Status.VERIFIED,
        paid_at=timezone.make_aware(timezone.datetime.combine(day3, timezone.datetime.min.time())),
        idempotency_key="idemp-audit-p5",
    )

    results = []
    errors = []

    def worker():
        from django.db import connection
        try:
            res = post_verified_billing_batch_to_account(
                actor=accountant,
                branch=b1,
                account=cash_bole,
                start_date=day3,
                end_date=day3,
                payment_method_type="CASH",
            )
            results.append(res)
        except Exception as e:
            errors.append(e)
        finally:
            connection.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one thread must succeed, and the other must be safely rejected
    assert len(results) == 1
    assert len(errors) == 1
    # Exactly one SettledBillingPayment row for p5 must exist in the database
    assert SettledBillingPayment.objects.filter(payment=p5).count() == 1


