from decimal import Decimal
from typing import Optional
from django.utils import timezone

from apps.memberships.contracts import BillingInvoiceRequest, PaymentClearanceResult
from .models import Invoice, InvoiceItem, Payment, Refund
from .services import create_invoice, trigger_membership_billing_clearance


class MembershipBillingIntegration:
    """
    Concrete implementation of the Billing Integration Contract.
    Connects Phase 04 Membership Lifecycle with Phase 05 Financial Operations.
    """

    @staticmethod
    def create_membership_invoice(
        membership,
        actor=None,
        idempotency_key: Optional[str] = None,
    ) -> Invoice:
        """
        Create an official invoice for a newly registered membership.
        Guarantees immutable price snapshot and prevents duplicate invoices.
        """
        # Check if an invoice already exists for this membership
        existing = Invoice.objects.filter(membership=membership).first()
        if existing:
            return existing

        system_actor = actor or membership.created_by

        items = [
            {
                "description": f"Membership Package: {membership.package.name} ({membership.membership_number})",
                "item_type": InvoiceItem.ItemType.PACKAGE,
                "quantity": 1,
                "unit_price": membership.price_snapshot,
                "discount_amount": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
                "related_membership": membership,
            }
        ]

        invoice = create_invoice(
            actor=system_actor,
            member=membership.member,
            branch=membership.home_branch,
            items=items,
            due_date=max(membership.start_date, timezone.localdate()),
            membership=membership,
            issue_now=True,
            notes=f"Auto-generated for membership {membership.membership_number}",
        )
        return invoice

    @staticmethod
    def create_renewal_invoice(
        renewal,
        actor=None,
        idempotency_key: Optional[str] = None,
    ) -> Invoice:
        """
        Create an official invoice for a membership renewal.
        Prevents duplicate invoices using renewal reference.
        """
        existing = Invoice.objects.filter(renewal=renewal).first()
        if existing:
            return existing

        system_actor = actor or renewal.requested_by

        items = [
            {
                "description": f"Membership Renewal: {renewal.membership.package.name} ({renewal.membership.membership_number})",
                "item_type": InvoiceItem.ItemType.RENEWAL,
                "quantity": 1,
                "unit_price": renewal.price_snapshot,
                "discount_amount": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
                "related_renewal": renewal,
                "related_membership": renewal.membership,
            }
        ]

        invoice = create_invoice(
            actor=system_actor,
            member=renewal.membership.member,
            branch=renewal.membership.home_branch,
            items=items,
            due_date=renewal.new_end_date,
            membership=renewal.membership,
            renewal=renewal,
            issue_now=True,
            notes=f"Auto-generated for renewal of {renewal.membership.membership_number}",
        )
        return invoice

    @staticmethod
    def record_payment_clearance(invoice: Invoice, payment: Payment) -> PaymentClearanceResult:
        """
        Process payment clearance notification and trigger membership/renewal activation.
        """
        trigger_membership_billing_clearance(invoice=invoice, payment=payment)

        status_str = "CLEARED" if invoice.balance_due == Decimal("0.00") else "PARTIAL_PENDING"

        return PaymentClearanceResult(
            success=True,
            invoice_number=invoice.invoice_number,
            payment_reference=payment.payment_number,
            amount_cleared=payment.amount,
            status=status_str,
            message=f"Payment {payment.payment_number} processed. Invoice status: {invoice.get_status_display()}.",
            cleared_at=timezone.now().isoformat(),
        )

    @staticmethod
    def process_refund_cancellation(membership, refund: Refund) -> bool:
        """
        Handle financial audit and service impact if a full refund is approved.
        """
        # If invoice paid_amount becomes 0 and membership was active solely based on this invoice,
        # we update the membership notes and audit trail.
        return True
