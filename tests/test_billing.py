from datetime import date, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.roles import seed_roles
from apps.billing.models import (
    FinancialAuditEvent,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentMethod,
    Receipt,
    Refund,
)
from apps.billing.receipts import generate_invoice_pdf, generate_receipt_pdf
from apps.billing.selectors import get_billing_metrics
from apps.billing.services import (
    approve_refund,
    create_invoice,
    issue_invoice,
    record_payment,
    reject_payment,
    request_refund,
    verify_payment,
    void_invoice,
)
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymSettings
from apps.members.models import Member
from apps.memberships.eligibility import can_access_branch
from apps.memberships.models import (
    Membership,
    MembershipPackage,
    MembershipRenewal,
    PackageBranch,
)
from apps.memberships.services import (
    activate_membership,
    create_membership,
    request_renewal,
)

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
    PaymentMethod.objects.update_or_create(
        code="TELEBIRR",
        defaults={
            "name": "Telebirr",
            "method_type": PaymentMethod.MethodType.TELEBIRR,
            "is_active": True,
            "requires_reference": True,
            "requires_verification": True,
        },
    )


from apps.members.services import register_member


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
def accountant(db, password, branch_a):
    user = User.objects.create_user("accountant@gym.test", password, is_staff=True)
    from django.contrib.auth.models import Group
    user.groups.add(Group.objects.get(name="Accountant"))
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
def member_a(db, owner, branch_a):
    return register_member(
        actor=owner,
        home_branch=branch_a,
        first_name="Abebe",
        last_name="Bikila",
        email="abebe@example.test",
        phone="0911000001",
    )


@pytest.fixture
def package_standard(db, branch_a):
    pkg = MembershipPackage.objects.create(
        name="Standard Monthly",
        code="PKG-STD-M",
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        duration_value=1,
        price=Decimal("1500.00"),
        cross_branch_enabled=True,
    )
    PackageBranch.objects.create(package=pkg, branch=branch_a, is_enabled=True)
    return pkg


def test_invoice_creation_and_numbering(root, member_a, branch_a):
    items = [
        {"description": "Gym Pass", "unit_price": Decimal("1000.00"), "quantity": 1},
        {"description": "Locker Fee", "unit_price": Decimal("200.00"), "quantity": 2},
    ]
    invoice = create_invoice(
        actor=root,
        member=member_a,
        branch=branch_a,
        items=items,
        discount_amount=Decimal("100.00"),
        tax_amount=Decimal("50.00"),
        issue_now=False,
    )
    assert invoice.invoice_number.startswith("INV-")
    assert invoice.subtotal == Decimal("1400.00")
    assert invoice.discount_amount == Decimal("100.00")
    assert invoice.tax_amount == Decimal("50.00")
    assert invoice.total_amount == Decimal("1350.00")
    assert invoice.balance_due == Decimal("1350.00")
    assert invoice.paid_amount == Decimal("0.00")
    assert invoice.status == Invoice.Status.DRAFT

    # Issue invoice
    issued = issue_invoice(root, invoice)
    assert issued.status == Invoice.Status.ISSUED
    assert issued.issued_by == root


def test_immutable_price_snapshots(root, member_a, branch_a, package_standard):
    membership = create_membership(
        actor=root,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=date.today(),
    )
    invoice = Invoice.objects.filter(membership=membership).first()
    assert invoice is not None
    assert invoice.total_amount == Decimal("1500.00")

    # Update package price afterwards
    package_standard.price = Decimal("2500.00")
    package_standard.save()

    # Verify line item pricing remains at snapshot 1500.00
    item = invoice.items.first()
    assert item.unit_price == Decimal("1500.00")
    assert item.line_total == Decimal("1500.00")
    invoice.refresh_from_db()
    assert invoice.total_amount == Decimal("1500.00")


def test_negative_financial_amounts_prevented(root, member_a, branch_a):
    with pytest.raises(ValidationError):
        create_invoice(
            actor=root,
            member=member_a,
            branch=branch_a,
            items=[{"description": "Bad item", "unit_price": Decimal("-50.00"), "quantity": 1}],
        )

    with pytest.raises(ValidationError):
        create_invoice(
            actor=root,
            member=member_a,
            branch=branch_a,
            items=[{"description": "Item", "unit_price": Decimal("100.00"), "quantity": 1}],
            discount_amount=Decimal("-10.00"),
        )


def test_cash_payment_auto_verified(receptionist, member_a, branch_a):
    invoice = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Day Pass", "unit_price": Decimal("500.00"), "quantity": 1}],
        issue_now=True,
    )
    cash_method = PaymentMethod.objects.get(code="CASH")
    payment = record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("500.00"),
        payment_method=cash_method,
    )
    # Cash payment is automatically verified because requires_verification=False
    assert payment.status == Payment.Status.VERIFIED
    assert payment.payment_number.startswith("PAY-")
    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("500.00")
    assert invoice.balance_due == Decimal("0.00")
    assert invoice.status == Invoice.Status.PAID

    # Receipt auto-generated
    receipt = Receipt.objects.filter(payment=payment).first()
    assert receipt is not None
    assert receipt.receipt_number.startswith("REC-")


def test_bank_transfer_requires_verification(receptionist, accountant, member_a, branch_a):
    invoice = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Session", "unit_price": Decimal("2000.00"), "quantity": 1}],
        issue_now=True,
    )
    bank_method = PaymentMethod.objects.get(code="BANK_TRANSFER")
    payment = record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("2000.00"),
        payment_method=bank_method,
        transaction_reference="CBE-REF-998877",
    )
    # Bank transfer starts in PENDING
    assert payment.status == Payment.Status.PENDING
    invoice.refresh_from_db()
    # Unverified payment does NOT reduce balance due
    assert invoice.paid_amount == Decimal("0.00")
    assert invoice.balance_due == Decimal("2000.00")
    assert invoice.status == Invoice.Status.ISSUED

    # Accountant verifies payment
    verified = verify_payment(accountant, payment)
    assert verified.status == Payment.Status.VERIFIED
    assert verified.verified_by == accountant
    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("2000.00")
    assert invoice.balance_due == Decimal("0.00")
    assert invoice.status == Invoice.Status.PAID


def test_duplicate_verified_transaction_ref_prevented(receptionist, accountant, member_a, branch_a):
    bank_method = PaymentMethod.objects.get(code="BANK_TRANSFER")
    inv1 = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "P1", "unit_price": Decimal("500.00"), "quantity": 1}],
        issue_now=True,
    )
    pay1 = record_payment(
        actor=receptionist,
        invoice=inv1,
        amount=Decimal("500.00"),
        payment_method=bank_method,
        transaction_reference="TXN-DUP-001",
    )
    verify_payment(accountant, pay1)

    inv2 = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "P2", "unit_price": Decimal("500.00"), "quantity": 1}],
        issue_now=True,
    )
    # Recording a second payment with identical transaction reference on same method must fail immediately
    with pytest.raises(ValidationError) as exc:
        record_payment(
            actor=receptionist,
            invoice=inv2,
            amount=Decimal("500.00"),
            payment_method=bank_method,
            transaction_reference="TXN-DUP-001",
        )
    assert "has already been verified" in str(exc.value)


def test_partial_payment_and_overpayment_rejection(receptionist, member_a, branch_a):
    invoice = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Plan", "unit_price": Decimal("1000.00"), "quantity": 1}],
        issue_now=True,
    )
    cash_method = PaymentMethod.objects.get(code="CASH")

    # Partial payment 400 ETB
    pay1 = record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("400.00"),
        payment_method=cash_method,
    )
    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("400.00")
    assert invoice.balance_due == Decimal("600.00")
    assert invoice.status == Invoice.Status.PARTIALLY_PAID

    # Overpayment attempt (700 ETB > 600 balance due) must be rejected
    with pytest.raises(ValidationError) as exc:
        record_payment(
            actor=receptionist,
            invoice=invoice,
            amount=Decimal("700.00"),
            payment_method=cash_method,
        )
    assert "Overpayment rejected" in str(exc.value)


def test_payment_rejection(receptionist, accountant, member_a, branch_a):
    invoice = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Item", "unit_price": Decimal("800.00"), "quantity": 1}],
        issue_now=True,
    )
    telebirr = PaymentMethod.objects.get(code="TELEBIRR")
    pay = record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("800.00"),
        payment_method=telebirr,
        transaction_reference="TB-FAKE-123",
    )
    assert pay.status == Payment.Status.PENDING

    # Accountant rejects the fraudulent payment
    rejected = reject_payment(accountant, pay, reason="Telebirr SMS reference not found in merchant portal.")
    assert rejected.status == Payment.Status.REJECTED
    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("0.00")
    assert invoice.status == Invoice.Status.ISSUED


def test_invoice_voiding(receptionist, member_a, branch_a):
    invoice = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Accidental Invoice", "unit_price": Decimal("300.00"), "quantity": 1}],
        issue_now=True,
    )
    voided = void_invoice(receptionist, invoice, reason="Customer cancelled before paying.")
    assert voided.status == Invoice.Status.VOID
    assert voided.balance_due == Decimal("0.00")

    # Cannot void an invoice that has verified payments
    invoice2 = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Paid item", "unit_price": Decimal("300.00"), "quantity": 1}],
        issue_now=True,
    )
    record_payment(
        actor=receptionist,
        invoice=invoice2,
        amount=Decimal("300.00"),
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    with pytest.raises(ValidationError) as exc:
        void_invoice(receptionist, invoice2, reason="Try to void paid invoice")
    assert "Cannot void an invoice with payments" in str(exc.value)


def test_refund_workflow_and_separation_of_duties(receptionist, accountant, owner, member_a, branch_a):
    invoice = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Full Package", "unit_price": Decimal("2000.00"), "quantity": 1}],
        issue_now=True,
    )
    cash_method = PaymentMethod.objects.get(code="CASH")
    payment = record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("2000.00"),
        payment_method=cash_method,
    )

    # Receptionist requests refund of 1000 ETB
    refund = request_refund(
        actor=receptionist,
        payment=payment,
        amount=Decimal("1000.00"),
        reason="Medical relocation",
    )
    assert refund.status == Refund.Status.REQUESTED
    assert refund.refund_number.startswith("REF-")

    # Separation of duties: receptionist cannot approve their own refund
    with pytest.raises(PermissionDenied):
        approve_refund(actor=receptionist, refund=refund, approve=True)

    # Owner approves the refund
    completed_refund = approve_refund(actor=owner, refund=refund, approve=True)
    assert completed_refund.status == Refund.Status.COMPLETED
    assert completed_refund.approved_by == owner

    # Invoice balance updates: balance due increases by 1000 ETB, status becomes PARTIALLY_REFUNDED
    invoice.refresh_from_db()
    assert invoice.paid_amount == Decimal("1000.00")
    assert invoice.balance_due == Decimal("1000.00")
    assert invoice.status == Invoice.Status.PARTIALLY_REFUNDED

    # Double refund beyond payment limit is rejected
    with pytest.raises(ValidationError) as exc:
        request_refund(
            actor=receptionist,
            payment=payment,
            amount=Decimal("1500.00"),  # Only 1000 remaining
            reason="Exceeding limit",
        )
    assert "cannot exceed payment amount" in str(exc.value)


def test_membership_activation_upon_payment_clearance(receptionist, member_a, branch_a, package_standard):
    from apps.core.models import GymProfile
    profile, _ = GymProfile.objects.get_or_create(singleton=1, defaults={"name": "PowerZone Gym"})
    settings, _ = GymSettings.objects.get_or_create(profile=profile, defaults={"allow_access_with_balance": False})
    settings.allow_access_with_balance = False
    settings.save()

    # Create membership (pending clearance)
    membership = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=date.today(),
    )
    assert membership.status == Membership.Status.PENDING
    invoice = Invoice.objects.get(membership=membership)
    assert invoice.balance_due == Decimal("1500.00")

    # Access is denied because membership is pending
    result = can_access_branch(member_a, branch_a)
    assert result.allowed is False
    assert "pending billing clearance" in result.message

    # Partial payment 500 ETB (with allow_access_with_balance=False)
    record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("500.00"),
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    membership.refresh_from_db()
    assert membership.status == Membership.Status.PENDING
    result = can_access_branch(member_a, branch_a)
    assert result.allowed is False

    # Full payment of remaining 1000 ETB
    record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("1000.00"),
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    membership.refresh_from_db()
    assert membership.status == Membership.Status.ACTIVE

    result = can_access_branch(member_a, branch_a)
    assert result.allowed is True


def test_allow_access_with_balance_policy(receptionist, member_a, branch_a, package_standard):
    from apps.core.models import GymProfile
    profile, _ = GymProfile.objects.get_or_create(singleton=1, defaults={"name": "PowerZone Gym"})
    settings, _ = GymSettings.objects.get_or_create(profile=profile, defaults={"allow_access_with_balance": True})
    settings.allow_access_with_balance = True
    settings.save()

    membership = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=date.today(),
    )
    invoice = Invoice.objects.get(membership=membership)

    # Zero payment: still denied even when allow_access_with_balance=True (requires at least one verified payment)
    result = can_access_branch(member_a, branch_a)
    assert result.allowed is False

    # Record partial payment
    record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("500.00"),
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    # Now allowed under policy!
    result = can_access_branch(member_a, branch_a)
    assert result.allowed is True


def test_renewal_payment_clearance(receptionist, member_a, branch_a, package_standard):
    # Active membership
    membership = create_membership(
        actor=receptionist,
        member=member_a,
        package=package_standard,
        home_branch=branch_a,
        start_date=date.today(),
    )
    activate_membership(actor=receptionist, membership=membership, reason="Initial setup")

    # Early renewal
    renewal = request_renewal(
        actor=receptionist,
        membership=membership,
        idempotency_key="REN-TEST-001",
    )
    assert renewal.renewal_status == MembershipRenewal.RenewalStatus.PENDING_BILLING

    inv = Invoice.objects.get(renewal=renewal)
    assert inv.balance_due == Decimal("1500.00")

    # Pay renewal invoice
    record_payment(
        actor=receptionist,
        invoice=inv,
        amount=Decimal("1500.00"),
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    renewal.refresh_from_db()
    assert renewal.renewal_status == MembershipRenewal.RenewalStatus.COMPLETED
    assert renewal.completed_at is not None


def test_pdf_generation_validity(receptionist, member_a, branch_a):
    invoice = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Quarterly Pass", "unit_price": Decimal("4500.00"), "quantity": 1}],
        issue_now=True,
    )
    pay = record_payment(
        actor=receptionist,
        invoice=invoice,
        amount=Decimal("4500.00"),
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )
    receipt = Receipt.objects.get(payment=pay)

    inv_pdf = generate_invoice_pdf(invoice)
    assert inv_pdf.startswith(b"%PDF-")
    assert len(inv_pdf) > 500

    rec_pdf = generate_receipt_pdf(receipt)
    assert rec_pdf.startswith(b"%PDF-")
    assert len(rec_pdf) > 500


def test_dashboard_billing_metrics(receptionist, accountant, member_a, branch_a):
    # Create invoice and verified payment
    inv1 = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Item 1", "unit_price": Decimal("3000.00"), "quantity": 1}],
        issue_now=True,
    )
    record_payment(
        actor=receptionist,
        invoice=inv1,
        amount=Decimal("2000.00"),
        payment_method=PaymentMethod.objects.get(code="CASH"),
    )

    metrics = get_billing_metrics(user=accountant, active_branch=branch_a)
    assert metrics["today_collections"] == Decimal("2000.00")
    assert metrics["monthly_collections"] == Decimal("2000.00")
    assert metrics["outstanding_balances"] == Decimal("1000.00")
    assert metrics["pending_payments_count"] == 0
    assert metrics["refund_totals_month"] == Decimal("0.00")
    assert metrics["net_collections_month"] == Decimal("2000.00")
    assert len(metrics["collections_by_method"]) >= 1


def test_role_authorization_for_financial_actions(receptionist, accountant, owner, member_a, branch_a):
    inv = create_invoice(
        actor=receptionist,
        member=member_a,
        branch=branch_a,
        items=[{"description": "Item", "unit_price": Decimal("1000.00"), "quantity": 1}],
        issue_now=True,
    )
    pay = record_payment(
        actor=receptionist,
        invoice=inv,
        amount=Decimal("1000.00"),
        payment_method=PaymentMethod.objects.get(code="BANK_TRANSFER"),
        transaction_reference="CBE-ROLE-TEST",
    )

    # Receptionist cannot verify payments
    with pytest.raises(PermissionDenied):
        verify_payment(actor=receptionist, payment=pay)

    # Accountant can verify payments
    verified = verify_payment(actor=accountant, payment=pay)
    assert verified.status == Payment.Status.VERIFIED

    # Receptionist cannot approve refunds
    ref = request_refund(receptionist, verified, Decimal("500.00"), "Customer requested")
    with pytest.raises(PermissionDenied):
        approve_refund(actor=receptionist, refund=ref, approve=True)
