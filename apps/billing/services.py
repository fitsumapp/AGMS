from decimal import Decimal
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.roles import is_owner
from apps.branches.audit import record_event
from apps.branches.services import can_access_branch
from apps.core.models import GymProfile, GymSettings
from .models import (
    FinancialAuditEvent,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentAdjustment,
    PaymentMethod,
    Receipt,
    Refund,
)
from .permissions import (
    can_approve_refunds,
    can_manage_invoices,
    can_record_payments,
    can_request_refunds,
    can_verify_payments,
)
from .sequence import (
    generate_next_invoice_number,
    generate_next_payment_number,
    generate_next_receipt_number,
    generate_next_refund_number,
)


@transaction.atomic
def create_invoice(
    *,
    actor,
    member,
    branch,
    items,
    due_date=None,
    discount_amount=Decimal("0.00"),
    tax_amount=Decimal("0.00"),
    notes="",
    membership=None,
    renewal=None,
    issue_now=True,
) -> Invoice:
    """
    Create a new invoice with itemized charges.
    Ensures branch access, strictly non-negative totals, and immutable pricing.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Staff authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if not can_manage_invoices(actor):
        raise PermissionDenied("Permission denied to create invoices.")

    if not items:
        raise ValidationError("Invoice must contain at least one line item.")

    discount = Decimal(str(discount_amount or "0.00"))
    tax = Decimal(str(tax_amount or "0.00"))

    if discount < Decimal("0.00"):
        raise ValidationError("Discount amount cannot be negative.")
    if tax < Decimal("0.00"):
        raise ValidationError("Tax amount cannot be negative.")

    subtotal = Decimal("0.00")
    for item in items:
        qty = int(item.get("quantity", 1))
        if qty <= 0:
            raise ValidationError("Item quantity must be strictly greater than zero.")
        price = Decimal(str(item.get("unit_price", "0.00")))
        if price < Decimal("0.00"):
            raise ValidationError("Item unit price cannot be negative.")
        item_discount = Decimal(str(item.get("discount_amount", "0.00")))
        if item_discount < Decimal("0.00"):
            raise ValidationError("Item discount cannot be negative.")
        item_tax = Decimal(str(item.get("tax_amount", "0.00")))
        if item_tax < Decimal("0.00"):
            raise ValidationError("Item tax cannot be negative.")
        line_total = (Decimal(qty) * price) - item_discount + item_tax
        if line_total < Decimal("0.00"):
            raise ValidationError("Line item total cannot be negative.")
        subtotal += line_total

    if discount > subtotal:
        raise ValidationError("Total invoice discount cannot exceed subtotal.")

    total_amount = max(Decimal("0.00"), subtotal - discount + tax)
    balance_due = total_amount

    today = timezone.localdate()
    inv_due = due_date or today

    invoice_number = generate_next_invoice_number()

    status = Invoice.Status.ISSUED if issue_now else Invoice.Status.DRAFT

    invoice = Invoice.objects.create(
        invoice_number=invoice_number,
        member=member,
        membership=membership,
        renewal=renewal,
        branch=branch,
        issue_date=today,
        due_date=inv_due,
        subtotal=subtotal,
        discount_amount=discount,
        tax_amount=tax,
        total_amount=total_amount,
        paid_amount=Decimal("0.00"),
        balance_due=balance_due,
        status=status,
        notes=notes,
        created_by=actor,
        issued_by=actor if issue_now else None,
    )

    for item in items:
        qty = item.get("quantity", 1)
        price = Decimal(str(item.get("unit_price", "0.00")))
        item_discount = Decimal(str(item.get("discount_amount", "0.00")))
        item_tax = Decimal(str(item.get("tax_amount", "0.00")))
        line_total = max(Decimal("0.00"), (qty * price) - item_discount + item_tax)

        InvoiceItem.objects.create(
            invoice=invoice,
            description=item.get("description", "Service Item"),
            item_type=item.get("item_type", InvoiceItem.ItemType.PACKAGE),
            quantity=qty,
            unit_price=price,
            discount_amount=item_discount,
            tax_amount=item_tax,
            line_total=line_total,
            related_membership=item.get("related_membership") or membership,
            related_renewal=item.get("related_renewal") or renewal,
        )

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.INVOICE_CREATED,
        invoice=invoice,
        actor=actor,
        branch=branch,
        metadata={
            "invoice_number": invoice.invoice_number,
            "total_amount": str(invoice.total_amount),
            "member_id": str(member.pk),
            "items_count": len(items),
        },
    )

    record_event(
        actor,
        "invoice_created",
        invoice,
        {
            "invoice_number": invoice.invoice_number,
            "total_amount": str(invoice.total_amount),
            "branch_code": branch.code,
        },
    )

    return invoice


@transaction.atomic
def issue_invoice(actor, invoice) -> Invoice:
    """Transition draft invoice to ISSUED."""
    if not is_owner(actor) and not can_access_branch(actor, invoice.branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError(f"Only DRAFT invoices can be issued. Current status: {invoice.status}")

    invoice.status = Invoice.Status.ISSUED
    invoice.issued_by = actor
    invoice.save(update_fields=["status", "issued_by", "updated_at"])

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.INVOICE_ISSUED,
        invoice=invoice,
        actor=actor,
        branch=invoice.branch,
        metadata={"invoice_number": invoice.invoice_number},
    )
    return invoice


@transaction.atomic
def record_payment(
    actor,
    invoice,
    amount,
    payment_method,
    transaction_reference="",
    notes="",
    paid_at=None,
    idempotency_key=None,
) -> Payment:
    """
    Record a customer payment against an issued invoice.
    Enforces row locking, non-overpayment, and idempotency protection.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Staff authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, invoice.branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if not can_record_payments(actor):
        raise PermissionDenied("Permission denied to record payments.")

    # Idempotency check: if payment with this key exists, return it
    key = idempotency_key or f"PAY-{uuid.uuid4()}"
    existing = Payment.objects.filter(idempotency_key=key).first()
    if existing:
        return existing

    pay_amount = Decimal(str(amount))
    if pay_amount <= Decimal("0.00"):
        raise ValidationError("Payment amount must be greater than zero.")

    # Lock invoice row
    inv = Invoice.objects.select_for_update().get(pk=invoice.pk)

    if inv.status in (Invoice.Status.VOID, Invoice.Status.REFUNDED):
        raise ValidationError(f"Cannot accept payments for {inv.get_status_display()} invoices.")

    if pay_amount > inv.balance_due:
        raise ValidationError(f"Overpayment rejected. Maximum accepted amount is {inv.balance_due} ETB.")

    ref = (transaction_reference or "").strip()
    if payment_method.requires_reference and not ref:
        raise ValidationError(f"Transaction reference is required for {payment_method.name}.")

    if ref:
        # Check duplicate verified references for same provider
        duplicate = Payment.objects.filter(
            payment_method=payment_method,
            transaction_reference=ref,
            status=Payment.Status.VERIFIED,
        ).exists()
        if duplicate:
            raise ValidationError(f"Transaction reference '{ref}' has already been verified for {payment_method.name}.")

    pay_number = generate_next_payment_number()

    # Cash method does not require separate electronic verification
    auto_verify = not payment_method.requires_verification

    init_status = Payment.Status.VERIFIED if auto_verify else Payment.Status.PENDING

    payment = Payment.objects.create(
        payment_number=pay_number,
        invoice=inv,
        branch=inv.branch,
        amount=pay_amount,
        payment_method=payment_method,
        transaction_reference=ref,
        status=init_status,
        received_by=actor,
        verified_by=actor if auto_verify else None,
        paid_at=paid_at or timezone.now(),
        verified_at=timezone.now() if auto_verify else None,
        notes=notes,
        idempotency_key=key,
    )

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.PAYMENT_RECORDED,
        invoice=inv,
        payment=payment,
        actor=actor,
        branch=inv.branch,
        metadata={
            "payment_number": payment.payment_number,
            "amount": str(payment.amount),
            "method": payment_method.name,
            "status": payment.status,
            "transaction_reference": ref,
        },
    )

    record_event(
        actor,
        "payment_recorded",
        payment,
        {
            "payment_number": payment.payment_number,
            "invoice_number": inv.invoice_number,
            "amount": str(payment.amount),
        },
    )

    if auto_verify:
        _apply_verified_payment_to_invoice(actor=actor, payment=payment, invoice=inv)

    return payment


@transaction.atomic
def verify_payment(actor, payment, notes="") -> Payment:
    """
    Formally verify a pending payment by an authorized manager or accountant.
    Updates invoice balance, issues official receipt, and checks membership clearance.
    """
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Staff authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, payment.branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if not can_verify_payments(actor):
        raise PermissionDenied("Only Branch Managers, Accountants, or Gym Owners can verify payments.")

    pay = Payment.objects.select_for_update().get(pk=payment.pk)
    if pay.status == Payment.Status.VERIFIED:
        # Idempotent: already verified
        return pay

    if pay.status != Payment.Status.PENDING:
        raise ValidationError(f"Only PENDING payments can be verified. Current status: {pay.status}")

    if pay.transaction_reference:
        duplicate = Payment.objects.filter(
            payment_method=pay.payment_method,
            transaction_reference=pay.transaction_reference,
            status=Payment.Status.VERIFIED,
        ).exclude(pk=pay.pk).exists()
        if duplicate:
            raise ValidationError(f"Transaction reference '{pay.transaction_reference}' is already verified.")

    inv = Invoice.objects.select_for_update().get(pk=pay.invoice_id)
    if pay.amount > inv.balance_due:
        raise ValidationError(f"Payment amount ({pay.amount} ETB) exceeds current invoice balance ({inv.balance_due} ETB).")

    pay.status = Payment.Status.VERIFIED
    pay.verified_by = actor
    pay.verified_at = timezone.now()
    if notes:
        pay.notes = f"{pay.notes}\n[Verification Note] {notes}".strip()
    pay.save(update_fields=["status", "verified_by", "verified_at", "notes", "updated_at"])

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.PAYMENT_VERIFIED,
        invoice=inv,
        payment=pay,
        actor=actor,
        branch=pay.branch,
        metadata={"payment_number": pay.payment_number, "verified_at": str(pay.verified_at)},
    )

    _apply_verified_payment_to_invoice(actor=actor, payment=pay, invoice=inv)
    return pay


def _apply_verified_payment_to_invoice(*, actor, payment, invoice):
    """Update invoice paid_amount, balance_due, status, issue receipt and trigger clearance."""
    invoice.paid_amount += payment.amount
    invoice.balance_due = max(Decimal("0.00"), invoice.total_amount - invoice.paid_amount)

    if invoice.balance_due == Decimal("0.00"):
        invoice.status = Invoice.Status.PAID
    else:
        invoice.status = Invoice.Status.PARTIALLY_PAID

    invoice.save(update_fields=["paid_amount", "balance_due", "status", "updated_at"])

    # Issue receipt automatically for verified payment
    issue_receipt(actor=actor, payment=payment)

    # Check and trigger membership or renewal clearance
    trigger_membership_billing_clearance(invoice=invoice, payment=payment)


@transaction.atomic
def reject_payment(actor, payment, reason) -> Payment:
    """Reject an unverified payment."""
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Staff authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, payment.branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if not can_verify_payments(actor):
        raise PermissionDenied("Only Branch Managers, Accountants, or Gym Owners can reject payments.")

    if not reason.strip():
        raise ValidationError("A clear rejection reason is required.")

    pay = Payment.objects.select_for_update().get(pk=payment.pk)
    if pay.status != Payment.Status.PENDING:
        raise ValidationError(f"Only PENDING payments can be rejected. Current status: {pay.status}")

    pay.status = Payment.Status.REJECTED
    pay.notes = f"{pay.notes}\n[Rejected by {actor}]: {reason}".strip()
    pay.save(update_fields=["status", "notes", "updated_at"])

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.PAYMENT_REJECTED,
        invoice=pay.invoice,
        payment=pay,
        actor=actor,
        branch=pay.branch,
        metadata={"reason": reason},
    )
    return pay


@transaction.atomic
def void_invoice(actor, invoice, reason) -> Invoice:
    """Void an invoice that has no verified payments."""
    if not is_owner(actor) and not can_access_branch(actor, invoice.branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if not can_manage_invoices(actor):
        raise PermissionDenied("Permission denied to void invoices.")

    if not reason.strip():
        raise ValidationError("Reason for voiding invoice is required.")

    inv = Invoice.objects.select_for_update().get(pk=invoice.pk)
    has_verified = Payment.objects.filter(invoice=inv, status=Payment.Status.VERIFIED).exists()
    if has_verified or inv.paid_amount > Decimal("0.00"):
        raise ValidationError("Cannot void an invoice with payments. Process an authorized refund instead.")

    inv.status = Invoice.Status.VOID
    inv.balance_due = Decimal("0.00")
    inv.notes = f"{inv.notes}\n[Voided by {actor}]: {reason}".strip()
    inv.save(update_fields=["status", "balance_due", "notes", "updated_at"])

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.INVOICE_VOIDED,
        invoice=inv,
        actor=actor,
        branch=inv.branch,
        metadata={"reason": reason},
    )
    return inv


@transaction.atomic
def request_refund(actor, payment, amount, reason) -> Refund:
    """Submit a refund request for an approved payment."""
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Staff authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, payment.branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if not can_request_refunds(actor):
        raise PermissionDenied("Permission denied to request refunds.")

    if not reason.strip():
        raise ValidationError("Refund reason is mandatory.")

    ref_amount = Decimal(str(amount))
    if ref_amount <= Decimal("0.00"):
        raise ValidationError("Refund amount must be strictly positive.")

    pay = Payment.objects.select_for_update().get(pk=payment.pk)
    if pay.status != Payment.Status.VERIFIED:
        raise ValidationError("Only VERIFIED payments can be refunded.")

    # Calculate already refunded or approved amounts
    prior_refunds = Refund.objects.filter(
        payment=pay,
        status__in=[Refund.Status.REQUESTED, Refund.Status.APPROVED, Refund.Status.COMPLETED],
    ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    if (prior_refunds + ref_amount) > pay.amount:
        available = max(Decimal("0.00"), pay.amount - prior_refunds)
        raise ValidationError(f"Total refunds cannot exceed payment amount. Remaining refundable: {available} ETB.")

    refund_num = generate_next_refund_number()

    refund = Refund.objects.create(
        refund_number=refund_num,
        payment=pay,
        invoice=pay.invoice,
        amount=ref_amount,
        reason=reason,
        status=Refund.Status.REQUESTED,
        requested_by=actor,
    )

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.REFUND_REQUESTED,
        invoice=pay.invoice,
        payment=pay,
        actor=actor,
        branch=pay.branch,
        metadata={"refund_number": refund.refund_number, "amount": str(refund.amount)},
    )
    return refund


@transaction.atomic
def approve_refund(actor, refund, approve=True, admin_notes="") -> Refund:
    """Approve or reject a refund request and complete the refund adjustments."""
    if not actor.is_authenticated or not actor.is_staff:
        raise PermissionDenied("Staff authentication required.")

    if not is_owner(actor) and not can_access_branch(actor, refund.invoice.branch):
        raise PermissionDenied("Unauthorized for this branch.")

    if not can_approve_refunds(actor):
        raise PermissionDenied("Only Branch Managers, Accountants, or Gym Owners can approve refunds.")

    ref = Refund.objects.select_for_update().get(pk=refund.pk)
    if ref.status != Refund.Status.REQUESTED:
        raise ValidationError(f"Only REQUESTED refunds can be processed. Current status: {ref.status}")

    # Separation of duties: requester cannot approve high-risk refund unless Gym Owner
    if ref.requested_by_id == actor.pk and not is_owner(actor):
        raise PermissionDenied("Separation of duties: requester cannot approve their own refund.")

    if not approve:
        ref.status = Refund.Status.REJECTED
        ref.approved_by = actor
        ref.approved_at = timezone.now()
        ref.notes = f"{ref.notes or ''}\n[Rejected by {actor}]: {admin_notes}".strip()
        ref.save(update_fields=["status", "approved_by", "approved_at", "notes", "updated_at"])
        return ref

    pay = Payment.objects.select_for_update().get(pk=ref.payment_id)
    inv = Invoice.objects.select_for_update().get(pk=ref.invoice_id)

    ref.status = Refund.Status.COMPLETED
    ref.approved_by = actor
    ref.completed_by = actor
    ref.approved_at = timezone.now()
    ref.completed_at = timezone.now()
    ref.save(update_fields=["status", "approved_by", "completed_by", "approved_at", "completed_at", "updated_at"])

    # Update invoice paid and balance
    inv.paid_amount = max(Decimal("0.00"), inv.paid_amount - ref.amount)
    inv.balance_due = max(Decimal("0.00"), inv.total_amount - inv.paid_amount)

    if inv.paid_amount == Decimal("0.00"):
        inv.status = Invoice.Status.REFUNDED
    else:
        inv.status = Invoice.Status.PARTIALLY_REFUNDED
    inv.save(update_fields=["paid_amount", "balance_due", "status", "updated_at"])

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.REFUND_COMPLETED,
        invoice=inv,
        payment=pay,
        actor=actor,
        branch=inv.branch,
        metadata={"refund_number": ref.refund_number, "amount": str(ref.amount)},
    )

    record_event(
        actor,
        "refund_completed",
        ref,
        {
            "refund_number": ref.refund_number,
            "amount": str(ref.amount),
            "invoice_number": inv.invoice_number,
        },
    )
    return ref


@transaction.atomic
def issue_receipt(actor, payment) -> Receipt:
    """Generate or retrieve the official computerized receipt for a payment."""
    existing = getattr(payment, "receipt", None)
    if existing:
        return existing

    rec_number = generate_next_receipt_number()
    snapshot = {
        "receipt_number": rec_number,
        "payment_number": payment.payment_number,
        "amount": str(payment.amount),
        "method": payment.payment_method.name,
        "transaction_reference": payment.transaction_reference,
        "paid_at": payment.paid_at.isoformat(),
        "member_name": payment.invoice.member.full_name,
        "member_number": payment.invoice.member.member_number,
        "branch_name": payment.branch.name,
        "issued_by": actor.get_full_name() if actor else "System",
    }

    receipt = Receipt.objects.create(
        receipt_number=rec_number,
        payment=payment,
        issued_at=timezone.now(),
        issued_by=actor,
        receipt_snapshot=snapshot,
    )

    FinancialAuditEvent.objects.create(
        event_type=FinancialAuditEvent.EventType.RECEIPT_ISSUED,
        invoice=payment.invoice,
        payment=payment,
        actor=actor,
        branch=payment.branch,
        metadata={"receipt_number": rec_number},
    )
    return receipt


def trigger_membership_billing_clearance(*, invoice, payment):
    """
    Evaluate if payment satisfies membership clearance conditions and activate subscription.
    Handles both initial memberships and renewals.
    Respects gym-level allow_access_with_balance policy.
    """
    settings_obj = GymSettings.objects.first()
    allow_balance = settings_obj.allow_access_with_balance if settings_obj else False

    is_cleared = (invoice.balance_due == Decimal("0.00")) or (allow_balance and invoice.paid_amount > Decimal("0.00"))

    if not is_cleared:
        return

    actor = payment.verified_by or payment.received_by

    # 1. Handle Membership initial activation
    if invoice.membership:
        m = invoice.membership
        if m.status == m.Status.PENDING:
            from apps.memberships.services import activate_membership
            activate_membership(
                actor=actor,
                membership=m,
                payment_reference=payment.payment_number,
                is_complimentary=False,
            )

    # 2. Handle Renewal completion
    if invoice.renewal:
        r = invoice.renewal
        if r.renewal_status == r.RenewalStatus.PENDING_BILLING:
            from apps.memberships.services import complete_renewal
            complete_renewal(
                actor=actor,
                renewal=r,
                billing_reference=payment.payment_number,
                is_complimentary=False,
            )
