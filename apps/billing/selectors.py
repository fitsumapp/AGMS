from datetime import timedelta
from decimal import Decimal
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.accounts.roles import is_owner
from apps.branches.services import accessible_branches
from .models import Invoice, Payment, Refund


def get_billing_metrics(user, active_branch=None) -> dict:
    """
    Compute branch-scoped financial and collection metrics for the dashboard and analytics.
    Enforces role-based branch scoping:
    - Gym Owner: sees all branches or filtered by active_branch
    - Branch Manager / Staff: restricted to authorized branches
    """
    branches = accessible_branches(user)

    invoice_qs = Invoice.objects.filter(branch__in=branches)
    payment_qs = Payment.objects.filter(branch__in=branches)
    refund_qs = Refund.objects.filter(invoice__branch__in=branches)

    if active_branch:
        invoice_qs = invoice_qs.filter(branch=active_branch)
        payment_qs = payment_qs.filter(branch=active_branch)
        refund_qs = refund_qs.filter(invoice__branch=active_branch)

    today = timezone.localdate()
    month_start = today.replace(day=1)

    # 1. Collections (Cash/Verified payments only)
    today_verified = (
        payment_qs.filter(
            status=Payment.Status.VERIFIED,
            paid_at__date=today,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    monthly_verified = (
        payment_qs.filter(
            status=Payment.Status.VERIFIED,
            paid_at__date__gte=month_start,
            paid_at__date__lte=today,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    # 2. Invoiced vs Outstanding Balances
    outstanding_balances = (
        invoice_qs.filter(
            status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
        ).aggregate(total=Sum("balance_due"))["total"]
        or Decimal("0.00")
    )

    total_invoiced_month = (
        invoice_qs.filter(
            issue_date__gte=month_start,
            issue_date__lte=today,
        ).exclude(status=Invoice.Status.VOID).aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )

    # 3. Pending Payments
    pending_payments_count = payment_qs.filter(status=Payment.Status.PENDING).count()
    pending_payments_amount = (
        payment_qs.filter(status=Payment.Status.PENDING).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    # 4. Refund Totals (completed)
    completed_refunds_month = (
        refund_qs.filter(
            status=Refund.Status.COMPLETED,
            completed_at__date__gte=month_start,
            completed_at__date__lte=today,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    # Net Collections = Gross Verified Collections - Completed Refunds
    net_collections_month = max(Decimal("0.00"), monthly_verified - completed_refunds_month)

    # 5. Collections by Branch
    by_branch = list(
        payment_qs.filter(
            status=Payment.Status.VERIFIED,
            paid_at__date__gte=month_start,
        )
        .values("branch__name")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")[:5]
    )

    # 6. Collections by Payment Method
    by_method = list(
        payment_qs.filter(
            status=Payment.Status.VERIFIED,
            paid_at__date__gte=month_start,
        )
        .values("payment_method__name")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")[:5]
    )

    return {
        "today_collections": today_verified,
        "monthly_collections": monthly_verified,
        "net_collections_month": net_collections_month,
        "total_invoiced_month": total_invoiced_month,
        "outstanding_balances": outstanding_balances,
        "pending_payments_count": pending_payments_count,
        "pending_payments_amount": pending_payments_amount,
        "refund_totals_month": completed_refunds_month,
        "collections_by_branch": by_branch,
        "collections_by_method": by_method,
    }
