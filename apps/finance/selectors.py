from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.billing.models import Invoice, Payment, Refund
from .models import (
    AccountTransfer,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    FinancialAccount,
    FinancialReconciliation,
    Supplier,
)


def get_finance_overview_metrics(
    branch_qs,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Compute operational finance metrics adhering to cash-basis principles:
    - Verified Collections (Phase 05 Billing)
    - Completed Customer Refunds (Phase 05 Billing)
    - Paid Operational Expenses (Phase 10 Expense Payments)
    - Net Operating Cash Movement
    - Outstanding Expense Balance (Liabilities)
    - Total Cash on Hand & Total Bank Balances
    """
    today = timezone.localdate()
    s_date = start_date or today.replace(day=1)
    e_date = end_date or today

    # 1. Phase 05 Customer Collections & Refunds
    verified_payments_qs = Payment.objects.filter(
        branch__in=branch_qs,
        status=Payment.Status.VERIFIED,
        paid_at__date__gte=s_date,
        paid_at__date__lte=e_date,
    )
    total_collections = verified_payments_qs.aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")
    collections_count = verified_payments_qs.count()

    completed_refunds_qs = Refund.objects.filter(
        payment__branch__in=branch_qs,
        status=Refund.Status.COMPLETED,
        completed_at__date__gte=s_date,
        completed_at__date__lte=e_date,
    )
    total_refunds = completed_refunds_qs.aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    # 2. Phase 10 Expenses
    approved_expenses_qs = Expense.objects.filter(
        branch__in=branch_qs,
        approval_status=Expense.ApprovalStatus.APPROVED,
        expense_date__gte=s_date,
        expense_date__lte=e_date,
    )
    total_approved_expenses = approved_expenses_qs.aggregate(tot=Sum("total_amount"))["tot"] or Decimal("0.00")

    verified_expense_payments_qs = ExpensePayment.objects.filter(
        expense__branch__in=branch_qs,
        status=ExpensePayment.Status.VERIFIED,
        payment_date__gte=s_date,
        payment_date__lte=e_date,
    )
    total_paid_expenses = verified_expense_payments_qs.aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    # 3. Outstanding Liabilities (Unpaid balance on all active approved expenses)
    outstanding_expenses = Expense.objects.filter(
        branch__in=branch_qs,
        approval_status=Expense.ApprovalStatus.APPROVED,
    ).aggregate(tot=Sum("balance_due"))["tot"] or Decimal("0.00")

    # 4. Net Cash Movement (Collections - Refunds - Paid Expenses)
    net_cash_movement = total_collections - total_refunds - total_paid_expenses

    # 5. Accounts Balances
    accounts = FinancialAccount.objects.filter(branch__in=branch_qs, is_active=True)
    cash_balance = accounts.filter(
        account_type__in=[FinancialAccount.AccountType.CASH_ON_HAND, FinancialAccount.AccountType.PETTY_CASH]
    ).aggregate(tot=Sum("current_balance"))["tot"] or Decimal("0.00")

    bank_balance = accounts.filter(
        account_type__in=[FinancialAccount.AccountType.BANK_ACCOUNT, FinancialAccount.AccountType.DIGITAL_WALLET]
    ).aggregate(tot=Sum("current_balance"))["tot"] or Decimal("0.00")

    # 6. Operational Status Counts
    pending_approvals = Expense.objects.filter(
        branch__in=branch_qs,
        approval_status=Expense.ApprovalStatus.SUBMITTED,
    ).count()

    open_reconciliations = FinancialReconciliation.objects.filter(
        branch__in=branch_qs,
        status__in=[FinancialReconciliation.Status.DRAFT, FinancialReconciliation.Status.SUBMITTED],
    ).count()

    return {
        "start_date": s_date,
        "end_date": e_date,
        "total_collections": total_collections,
        "collections_count": collections_count,
        "total_refunds": total_refunds,
        "total_approved_expenses": total_approved_expenses,
        "total_paid_expenses": total_paid_expenses,
        "outstanding_expenses": outstanding_expenses,
        "net_cash_movement": net_cash_movement,
        "cash_balance": cash_balance,
        "bank_balance": bank_balance,
        "total_liquidity": cash_balance + bank_balance,
        "pending_approvals_count": pending_approvals,
        "open_reconciliations_count": open_reconciliations,
    }


def get_expenses_by_category(
    branch_qs,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Aggregate approved expenses grouped by category."""
    today = timezone.localdate()
    s_date = start_date or today.replace(day=1)
    e_date = end_date or today

    qs = (
        Expense.objects.filter(
            branch__in=branch_qs,
            approval_status=Expense.ApprovalStatus.APPROVED,
            expense_date__gte=s_date,
            expense_date__lte=e_date,
        )
        .values("category__name", "category__code")
        .annotate(
            total_amount=Sum("total_amount"),
            paid_amount=Sum("paid_amount"),
            balance_due=Sum("balance_due"),
            count=Count("id"),
        )
        .order_by("-total_amount")
    )
    total_all = sum(row["total_amount"] for row in qs) or Decimal("0.00")

    result = []
    for row in qs:
        pct = (row["total_amount"] / total_all * Decimal("100.0")) if total_all > Decimal("0.00") else Decimal("0.0")
        result.append({
            "category_name": row["category__name"],
            "category_code": row["category__code"],
            "total_amount": row["total_amount"],
            "paid_amount": row["paid_amount"],
            "balance_due": row["balance_due"],
            "count": row["count"],
            "percentage": round(float(pct), 1),
        })
    return result


def get_expenses_by_branch(
    branch_qs,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Aggregate approved expenses grouped by branch."""
    today = timezone.localdate()
    s_date = start_date or today.replace(day=1)
    e_date = end_date or today

    qs = (
        Expense.objects.filter(
            branch__in=branch_qs,
            approval_status=Expense.ApprovalStatus.APPROVED,
            expense_date__gte=s_date,
            expense_date__lte=e_date,
        )
        .values("branch__name", "branch__code")
        .annotate(
            total_amount=Sum("total_amount"),
            paid_amount=Sum("paid_amount"),
            balance_due=Sum("balance_due"),
            count=Count("id"),
        )
        .order_by("-total_amount")
    )
    return list(qs)


def get_cash_flow_summary(
    branch_qs,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Detailed cash flow statement distinguishing revenue, collections, refunds, expenses, and transfers."""
    today = timezone.localdate()
    s_date = start_date or today.replace(day=1)
    e_date = end_date or today

    # Invoiced Accrual Revenue
    invoiced_revenue = (
        Invoice.objects.filter(
            branch__in=branch_qs,
            issue_date__gte=s_date,
            issue_date__lte=e_date,
        )
        .exclude(status=Invoice.Status.VOID)
        .aggregate(tot=Sum("total_amount"))["tot"] or Decimal("0.00")
    )

    # Cash Inflows: Verified Collections
    verified_collections = (
        Payment.objects.filter(
            branch__in=branch_qs,
            status=Payment.Status.VERIFIED,
            paid_at__date__gte=s_date,
            paid_at__date__lte=e_date,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")
    )

    # Cash Outflows: Refunds
    completed_refunds = (
        Refund.objects.filter(
            payment__branch__in=branch_qs,
            status=Refund.Status.COMPLETED,
            completed_at__date__gte=s_date,
            completed_at__date__lte=e_date,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")
    )

    # Cash Outflows: Paid Expenses
    paid_expenses = (
        ExpensePayment.objects.filter(
            expense__branch__in=branch_qs,
            status=ExpensePayment.Status.VERIFIED,
            payment_date__gte=s_date,
            payment_date__lte=e_date,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")
    )

    # Internal Transfers (Non-operational, strictly balance movement)
    internal_transfers = (
        AccountTransfer.objects.filter(
            source_account__branch__in=branch_qs,
            status=AccountTransfer.Status.COMPLETED,
            transfer_date__gte=s_date,
            transfer_date__lte=e_date,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")
    )

    net_operating_cash_flow = verified_collections - completed_refunds - paid_expenses

    return {
        "period_start": s_date,
        "period_end": e_date,
        "invoiced_revenue": invoiced_revenue,
        "verified_collections": verified_collections,
        "completed_refunds": completed_refunds,
        "paid_expenses": paid_expenses,
        "net_operating_cash_flow": net_operating_cash_flow,
        "internal_transfers_volume": internal_transfers,
    }


def get_top_suppliers_breakdown(
    branch_qs,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Top suppliers by billed expense volume."""
    today = timezone.localdate()
    s_date = start_date or today.replace(day=1)
    e_date = end_date or today

    qs = (
        Expense.objects.filter(
            branch__in=branch_qs,
            supplier__isnull=False,
            approval_status=Expense.ApprovalStatus.APPROVED,
            expense_date__gte=s_date,
            expense_date__lte=e_date,
        )
        .values("supplier__name", "supplier__supplier_type")
        .annotate(
            total_billed=Sum("total_amount"),
            total_paid=Sum("paid_amount"),
            outstanding_balance=Sum("balance_due"),
            expense_count=Count("id"),
        )
        .order_by("-total_billed")[:limit]
    )
    return list(qs)
