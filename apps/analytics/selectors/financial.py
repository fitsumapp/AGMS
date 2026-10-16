from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.analytics.dates import DateFilterRange, get_gym_timezone, get_today_in_gym_tz
from apps.billing.models import Invoice, Payment, PaymentMethod, Refund
from apps.branches.models import Branch


def get_financial_analytics(
    branch_qs,
    date_range: DateFilterRange,
) -> Dict:
    """
    Compute comprehensive financial analytics across verified transactions,
    invoices, collections, and refunds strictly adhering to cash-basis principles.
    
    Guarantees:
    - Only VERIFIED payments are counted as collections.
    - Refunds are explicitly deducted for net cash calculation.
    - No double-counting of invoices or payments.
    - No fabricated revenue or profit figures.
    """
    today = get_today_in_gym_tz()
    tz = get_gym_timezone()

    invoices = Invoice.objects.filter(branch__in=branch_qs)
    payments = Payment.objects.filter(branch__in=branch_qs)
    refunds = Refund.objects.filter(invoice__branch__in=branch_qs)

    # 1. Total Verified Collections in Date Range
    range_verified_payments = payments.filter(
        status=Payment.Status.VERIFIED,
        paid_at__gte=date_range.start_datetime,
        paid_at__lte=date_range.end_datetime,
    )
    total_verified_collections = (
        range_verified_payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )
    verified_payment_count = range_verified_payments.count()

    # 2. Today's and Monthly Verified Collections
    today_start = timezone.datetime.combine(today, timezone.datetime.min.time(), tzinfo=tz)
    today_end = timezone.datetime.combine(today, timezone.datetime.max.time(), tzinfo=tz)
    month_start_date = today.replace(day=1)
    month_start_dt = timezone.datetime.combine(month_start_date, timezone.datetime.min.time(), tzinfo=tz)

    today_verified_collections = (
        payments.filter(
            status=Payment.Status.VERIFIED,
            paid_at__gte=today_start,
            paid_at__lte=today_end,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )

    monthly_verified_collections = (
        payments.filter(
            status=Payment.Status.VERIFIED,
            paid_at__gte=month_start_dt,
            paid_at__lte=today_end,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )

    # 3. Completed Refunds in Date Range
    range_completed_refunds = refunds.filter(
        status=Refund.Status.COMPLETED,
        completed_at__gte=date_range.start_datetime,
        completed_at__lte=date_range.end_datetime,
    )
    total_refunds = (
        range_completed_refunds.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )
    refund_count = range_completed_refunds.count()

    # 4. Net Cash Collections = Verified Collections - Completed Refunds
    net_cash_collections = total_verified_collections - total_refunds

    # 5. Invoices Issued in Date Range (excluding VOID)
    range_invoices = invoices.filter(
        issue_date__gte=date_range.start_date,
        issue_date__lte=date_range.end_date,
    ).exclude(status=Invoice.Status.VOID)

    total_invoiced_amount = (
        range_invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    )
    total_invoices_count = range_invoices.count()

    # 6. Outstanding Balances (Facility-wide or branch-scoped, current as of today)
    unpaid_invoices = invoices.filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID],
    )
    total_outstanding_balances = (
        unpaid_invoices.aggregate(total=Sum("balance_due"))["total"] or Decimal("0.00")
    )
    unpaid_invoice_count = unpaid_invoices.count()

    # 7. Pending Payments (unverified cash/transfers awaiting approval)
    pending_payments_qs = payments.filter(status=Payment.Status.PENDING)
    pending_payments_count = pending_payments_qs.count()
    pending_payments_amount = (
        pending_payments_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )

    # 8. Collections by Payment Method
    method_data = []
    method_aggregates = (
        range_verified_payments.values("payment_method__name", "payment_method__code")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    for m in method_aggregates:
        amount = m["total"] or Decimal("0.00")
        pct = (
            round(float(amount / total_verified_collections * 100), 1)
            if total_verified_collections > Decimal("0.00")
            else 0.0
        )
        method_data.append({
            "name": m["payment_method__name"] or "Unknown",
            "code": m["payment_method__code"] or "UNKNOWN",
            "amount": amount,
            "count": m["count"],
            "percentage": pct,
        })

    # 9. Collections by Branch
    branch_collections = []
    branch_aggregates = (
        range_verified_payments.values("branch__id", "branch__name", "branch__code")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    for b in branch_aggregates:
        amount = b["total"] or Decimal("0.00")
        pct = (
            round(float(amount / total_verified_collections * 100), 1)
            if total_verified_collections > Decimal("0.00")
            else 0.0
        )
        branch_collections.append({
            "id": str(b["branch__id"]),
            "name": b["branch__name"],
            "code": b["branch__code"],
            "amount": amount,
            "count": b["count"],
            "percentage": pct,
        })

    # 10. Daily Collection Trend (Timeline across filtered date range)
    # Populate every day in date range with 0 if no collections
    daily_map = defaultdict(lambda: Decimal("0.00"))
    for p in range_verified_payments.values_list("paid_at", "amount"):
        dt, amt = p
        if dt:
            d = dt.astimezone(tz).date()
            daily_map[d] += (amt or Decimal("0.00"))

    # Cap chart points to at most 31 points (if range is long, group by week/month, else daily)
    daily_trend = []
    cur_d = date_range.start_date
    while cur_d <= date_range.end_date:
        daily_trend.append({
            "date": cur_d.strftime("%Y-%m-%d"),
            "display": cur_d.strftime("%b %d"),
            "amount": daily_map[cur_d],
        })
        cur_d += timedelta(days=1)

    # 11. Monthly Collection Trend (Last 6 months)
    monthly_trend = []
    for i in range(5, -1, -1):
        # Calculate month i months ago
        target_year = today.year
        target_month = today.month - i
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        m_start = date(target_year, target_month, 1)
        if target_month == 12:
            m_end = date(target_year, 12, 31)
        else:
            m_end = date(target_year, target_month + 1, 1) - timedelta(days=1)
        
        m_start_dt = timezone.datetime.combine(m_start, timezone.datetime.min.time(), tzinfo=tz)
        m_end_dt = timezone.datetime.combine(m_end, timezone.datetime.max.time(), tzinfo=tz)

        m_amt = (
            payments.filter(
                status=Payment.Status.VERIFIED,
                paid_at__gte=m_start_dt,
                paid_at__lte=m_end_dt,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        )
        monthly_trend.append({
            "month": m_start.strftime("%b %Y"),
            "short": m_start.strftime("%b"),
            "amount": m_amt,
        })

    # 12. Outstanding Balances Aging Categories
    # Categories: 0-30 days, 31-60 days, 61-90 days, 90+ days past due date
    aging_buckets = {
        "current_0_30": Decimal("0.00"),
        "overdue_31_60": Decimal("0.00"),
        "overdue_61_90": Decimal("0.00"),
        "overdue_90_plus": Decimal("0.00"),
    }
    aging_counts = {
        "current_0_30": 0,
        "overdue_31_60": 0,
        "overdue_61_90": 0,
        "overdue_90_plus": 0,
    }

    for inv in unpaid_invoices.values("due_date", "balance_due"):
        due = inv["due_date"]
        bal = inv["balance_due"] or Decimal("0.00")
        if not due or due >= today - timedelta(days=30):
            aging_buckets["current_0_30"] += bal
            aging_counts["current_0_30"] += 1
        elif due >= today - timedelta(days=60):
            aging_buckets["overdue_31_60"] += bal
            aging_counts["overdue_31_60"] += 1
        elif due >= today - timedelta(days=90):
            aging_buckets["overdue_61_90"] += bal
            aging_counts["overdue_61_90"] += 1
        else:
            aging_buckets["overdue_90_plus"] += bal
            aging_counts["overdue_90_plus"] += 1

    aging_summary = [
        {"bucket": "0–30 Days (Current)", "code": "0_30", "amount": aging_buckets["current_0_30"], "count": aging_counts["current_0_30"]},
        {"bucket": "31–60 Days", "code": "31_60", "amount": aging_buckets["overdue_31_60"], "count": aging_counts["overdue_31_60"]},
        {"bucket": "61–90 Days", "code": "61_90", "amount": aging_buckets["overdue_61_90"], "count": aging_counts["overdue_61_90"]},
        {"bucket": "90+ Days Overdue", "code": "90_plus", "amount": aging_buckets["overdue_90_plus"], "count": aging_counts["overdue_90_plus"]},
    ]

    return {
        "total_verified_collections": total_verified_collections,
        "total_collected": total_verified_collections,
        "verified_payment_count": verified_payment_count,
        "today_verified_collections": today_verified_collections,
        "today_collections": today_verified_collections,
        "monthly_verified_collections": monthly_verified_collections,
        "month_collections": monthly_verified_collections,
        "total_refunds": total_refunds,
        "refund_count": refund_count,
        "net_cash_collections": net_cash_collections,
        "net_collections": net_cash_collections,
        "total_invoiced_amount": total_invoiced_amount,
        "total_invoiced": total_invoiced_amount,
        "total_invoices_count": total_invoices_count,
        "total_outstanding_balances": total_outstanding_balances,
        "outstanding_balance": total_outstanding_balances,
        "unpaid_invoice_count": unpaid_invoice_count,
        "overdue_invoice_count": unpaid_invoice_count,
        "pending_payments_count": pending_payments_count,
        "pending_payments_amount": pending_payments_amount,
        "collections_by_payment_method": method_data,
        "collections_by_branch": branch_collections,
        "daily_collection_trend": daily_trend,
        "monthly_collection_trend": monthly_trend,
        "aging_summary": aging_summary,
    }
