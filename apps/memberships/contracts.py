"""
Billing Integration Contract - AGMS Phase 04 / Phase 05 Interface

This module establishes the explicit, immutable boundary between Phase 04 (Memberships)
and Phase 05 (Billing & Invoicing).

Key Security Guarantees:
1. No browser form, receptionist, or ordinary staff endpoint can mark a membership as paid.
2. Memberships initialized in standard paid workflows remain PENDING_BILLING until
   the server-side billing service confirms payment clearance.
3. Billing clearance callbacks are idempotent (protected via idempotency keys).
4. Complimentary memberships are handled via a dedicated, audited approval workflow
   accessible exclusively to Gym Owners and Superusers.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Dict, Any
from django.utils import timezone


@dataclass(frozen=True)
class BillingInvoiceRequest:
    """
    Contract payload passed to Phase 05 Billing Service to request invoice generation
    upon membership creation or renewal.
    """
    membership_id: str
    membership_number: str
    member_id: str
    home_branch_id: str
    amount: Decimal
    currency: str
    description: str
    idempotency_key: str
    created_at: str = field(default_factory=lambda: timezone.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentClearanceResult:
    """
    Response returned by Phase 05 Billing Service when a payment event is processed.
    """
    success: bool
    invoice_number: Optional[str]
    payment_reference: Optional[str]
    amount_cleared: Decimal
    status: str  # 'CLEARED', 'PARTIAL_PENDING', 'FAILED', 'REFUNDED'
    message: str
    cleared_at: Optional[str] = None


class BillingContractService:
    """
    Stub interface defining the server-to-server contract for Phase 05 Billing.
    Phase 04 calls these hooks without fabricating fake transaction records.
    """

    @staticmethod
    def prepare_invoice_request(membership, idempotency_key: str) -> BillingInvoiceRequest:
        """Construct the immutable invoice request payload for a membership."""
        return BillingInvoiceRequest(
            membership_id=str(membership.pk),
            membership_number=membership.membership_number,
            member_id=str(membership.member.pk),
            home_branch_id=str(membership.home_branch.pk),
            amount=membership.price_snapshot,
            currency="ETB",
            description=f"Membership Package: {membership.package.name} ({membership.membership_number})",
            idempotency_key=idempotency_key,
            metadata={
                "duration_unit": membership.duration_unit_snapshot,
                "duration_value": membership.duration_value_snapshot,
                "start_date": str(membership.start_date),
                "end_date": str(membership.end_date),
            },
        )
