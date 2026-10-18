from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.analytics.dates import DateFilterRange, get_gym_timezone
from apps.billing.models import Invoice, Payment, Refund
from .models import (
    AccountAdjustment,
    AccountTransfer,
    Expense,
    ExpenseCategory,
    ExpensePayment,
    FinancialAccount,
    FinancialReconciliation,
    Supplier,
)
from .validators import sanitize_csv_cell


def extract_expense_register(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Detailed log of all operational expenses."""
    tz = get_gym_timezone()
    expenses = (
        Expense.objects.filter(
            branch__in=branch_qs,
            expense_date__gte=date_range.start_date,
            expense_date__lte=date_range.end_date,
        )
        .select_related("branch", "category", "supplier", "created_by", "approved_by")
        .order_by("-expense_date", "-created_at")
    )
    rows = []
    for exp in expenses:
        rows.append({
            "date": exp.expense_date.strftime("%Y-%m-%d"),
            "reference": sanitize_csv_cell(exp.reference),
            "branch": sanitize_csv_cell(exp.branch.name),
            "category": sanitize_csv_cell(exp.category.name),
            "supplier": sanitize_csv_cell(exp.supplier.name if exp.supplier else "N/A"),
            "description": sanitize_csv_cell(exp.description[:60]),
            "amount": float(exp.amount),
            "tax": float(exp.tax_amount),
            "total_amount": float(exp.total_amount),
            "paid_amount": float(exp.paid_amount),
            "balance_due": float(exp.balance_due),
            "approval_status": exp.get_approval_status_display(),
            "payment_status": exp.get_payment_status_display(),
        })
    return rows


def extract_expense_category_summary(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Category breakdown of approved expenses."""
    qs = (
        Expense.objects.filter(
            branch__in=branch_qs,
            approval_status=Expense.ApprovalStatus.APPROVED,
            expense_date__gte=date_range.start_date,
            expense_date__lte=date_range.end_date,
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
    grand_total = sum(row["total_amount"] for row in qs) or Decimal("0.00")
    rows = []
    for r in qs:
        pct = (r["total_amount"] / grand_total * 100) if grand_total > 0 else 0
        rows.append({
            "category": sanitize_csv_cell(r["category__name"]),
            "code": sanitize_csv_cell(r["category__code"]),
            "expense_count": r["count"],
            "total_amount": float(r["total_amount"]),
            "paid_amount": float(r["paid_amount"]),
            "balance_due": float(r["balance_due"]),
            "share_pct": f"{pct:.1f}%",
        })
    return rows


def extract_supplier_payments(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Supplier volume and payment history."""
    suppliers = (
        Supplier.objects.filter(
            Q(branch__in=branch_qs) | Q(branch__isnull=True),
            expenses__approval_status=Expense.ApprovalStatus.APPROVED,
            expenses__expense_date__gte=date_range.start_date,
            expenses__expense_date__lte=date_range.end_date,
        )
        .annotate(
            total_billed=Sum("expenses__total_amount"),
            total_paid=Sum("expenses__paid_amount"),
            balance_due=Sum("expenses__balance_due"),
            expense_count=Count("expenses__id"),
        )
        .order_by("-total_billed")
        .distinct()
    )
    rows = []
    for s in suppliers:
        rows.append({
            "supplier_name": sanitize_csv_cell(s.name),
            "supplier_type": s.get_supplier_type_display(),
            "tin_vat": sanitize_csv_cell(s.masked_tax_id),
            "expenses_count": s.expense_count,
            "total_billed": float(s.total_billed or 0),
            "total_paid": float(s.total_paid or 0),
            "balance_due": float(s.balance_due or 0),
        })
    return rows


def extract_outstanding_expenses(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Aging and list of unpaid/partially paid approved expenses."""
    today = timezone.localdate()
    qs = (
        Expense.objects.filter(
            branch__in=branch_qs,
            approval_status=Expense.ApprovalStatus.APPROVED,
            balance_due__gt=Decimal("0.00"),
        )
        .select_related("branch", "category", "supplier")
        .order_by("payment_due_date", "-balance_due")
    )
    rows = []
    for exp in qs:
        is_overdue = exp.payment_due_date and exp.payment_due_date < today
        rows.append({
            "reference": sanitize_csv_cell(exp.reference),
            "branch": sanitize_csv_cell(exp.branch.name),
            "category": sanitize_csv_cell(exp.category.name),
            "supplier": sanitize_csv_cell(exp.supplier.name if exp.supplier else "N/A"),
            "expense_date": exp.expense_date.strftime("%Y-%m-%d"),
            "due_date": exp.payment_due_date.strftime("%Y-%m-%d") if exp.payment_due_date else "Not Set",
            "is_overdue": "OVERDUE" if is_overdue else "Current",
            "total_amount": float(exp.total_amount),
            "paid_amount": float(exp.paid_amount),
            "balance_due": float(exp.balance_due),
        })
    return rows


def extract_cash_flow_statement(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Operational Cash Flow Statement (Cash Basis)."""
    # 1. Verified Customer Collections
    collections = Payment.objects.filter(
        branch__in=branch_qs,
        status=Payment.Status.VERIFIED,
        paid_at__date__gte=date_range.start_date,
        paid_at__date__lte=date_range.end_date,
    ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    # 2. Completed Refunds
    refunds = Refund.objects.filter(
        payment__branch__in=branch_qs,
        status=Refund.Status.COMPLETED,
        completed_at__date__gte=date_range.start_date,
        completed_at__date__lte=date_range.end_date,
    ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    # 3. Paid Expenses
    paid_expenses = ExpensePayment.objects.filter(
        expense__branch__in=branch_qs,
        status=ExpensePayment.Status.VERIFIED,
        payment_date__gte=date_range.start_date,
        payment_date__lte=date_range.end_date,
    ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    # 4. Invoiced Accrual Revenue (for comparison)
    invoiced_rev = Invoice.objects.filter(
        branch__in=branch_qs,
        issue_date__gte=date_range.start_date,
        issue_date__lte=date_range.end_date,
    ).exclude(status=Invoice.Status.VOID).aggregate(tot=Sum("total_amount"))["tot"] or Decimal("0.00")

    # 5. Outstanding Expense Liabilities
    unpaid_liabilities = Expense.objects.filter(
        branch__in=branch_qs,
        approval_status=Expense.ApprovalStatus.APPROVED,
        balance_due__gt=Decimal("0.00"),
    ).aggregate(tot=Sum("balance_due"))["tot"] or Decimal("0.00")

    # 6. Internal Transfers Volume
    transfers = AccountTransfer.objects.filter(
        source_account__branch__in=branch_qs,
        status=AccountTransfer.Status.COMPLETED,
        transfer_date__gte=date_range.start_date,
        transfer_date__lte=date_range.end_date,
    ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    net_cash = collections - refunds - paid_expenses

    return [
        {"component": "Cash Inflows: Customer Collections (Verified)", "amount": float(collections), "flow_type": "Operating Inflow", "notes": "Actual cash collected from member subscriptions"},
        {"component": "Cash Outflows: Member Refunds (Completed)", "amount": float(-refunds), "flow_type": "Operating Outflow", "notes": "Authorized membership refunds disbursed"},
        {"component": "Net Customer Receipts", "amount": float(collections - refunds), "flow_type": "Operating Net", "notes": "Net cash received from customers"},
        {"component": "Cash Outflows: Operational Expenses Paid", "amount": float(-paid_expenses), "flow_type": "Operating Outflow", "notes": "Rent, utilities, salaries, maintenance disbursed"},
        {"component": "NET OPERATING CASH FLOW", "amount": float(net_cash), "flow_type": "NET CASH", "notes": "Actual surplus / deficit during this period"},
        {"component": "Accrual Comparison: Invoiced Revenue", "amount": float(invoiced_rev), "flow_type": "Non-Cash Accrual", "notes": "Total invoices billed during period"},
        {"component": "Liability Snapshot: Outstanding Expense Payables", "amount": float(unpaid_liabilities), "flow_type": "Liability", "notes": "Approved expenses awaiting disbursement"},
        {"component": "Internal Funds Transferred (Neutral)", "amount": float(transfers), "flow_type": "Internal Movement", "notes": "Transfers between accounts (Excluded from profit/cashflow)"},
    ]


def extract_account_movements(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Cash & Bank account transaction ledger."""
    # Combine Expense Payments, Transfers, Adjustments
    rows = []

    # Expense disbursements
    exp_pays = ExpensePayment.objects.filter(
        financial_account__branch__in=branch_qs,
        status=ExpensePayment.Status.VERIFIED,
        payment_date__gte=date_range.start_date,
        payment_date__lte=date_range.end_date,
    ).select_related("financial_account", "expense")
    for ep in exp_pays:
        rows.append({
            "date": ep.payment_date.strftime("%Y-%m-%d"),
            "account": sanitize_csv_cell(ep.financial_account.name),
            "account_type": ep.financial_account.get_account_type_display(),
            "movement_type": "Expense Payment",
            "reference": sanitize_csv_cell(ep.payment_number),
            "details": sanitize_csv_cell(f"Paid for {ep.expense.reference} ({ep.expense.category.name})"),
            "inflow": 0.0,
            "outflow": float(ep.amount),
        })

    # Transfers In / Out
    trfs = AccountTransfer.objects.filter(
        Q(source_account__branch__in=branch_qs) | Q(destination_account__branch__in=branch_qs),
        status=AccountTransfer.Status.COMPLETED,
        transfer_date__gte=date_range.start_date,
        transfer_date__lte=date_range.end_date,
    ).select_related("source_account", "destination_account")
    for t in trfs:
        if t.source_account.branch in branch_qs:
            rows.append({
                "date": t.transfer_date.strftime("%Y-%m-%d"),
                "account": sanitize_csv_cell(t.source_account.name),
                "account_type": t.source_account.get_account_type_display(),
                "movement_type": "Transfer Out",
                "reference": sanitize_csv_cell(t.transfer_number),
                "details": sanitize_csv_cell(f"Transfer to {t.destination_account.name}"),
                "inflow": 0.0,
                "outflow": float(t.amount),
            })
        if t.destination_account.branch in branch_qs:
            rows.append({
                "date": t.transfer_date.strftime("%Y-%m-%d"),
                "account": sanitize_csv_cell(t.destination_account.name),
                "account_type": t.destination_account.get_account_type_display(),
                "movement_type": "Transfer In",
                "reference": sanitize_csv_cell(t.transfer_number),
                "details": sanitize_csv_cell(f"Transfer from {t.source_account.name}"),
                "inflow": float(t.amount),
                "outflow": 0.0,
            })

    # Adjustments
    adjs = AccountAdjustment.objects.filter(
        account__branch__in=branch_qs,
        adjustment_date__gte=date_range.start_date,
        adjustment_date__lte=date_range.end_date,
    ).select_related("account")
    for a in adjs:
        is_cred = a.adjustment_type == AccountAdjustment.AdjustmentType.CREDIT
        rows.append({
            "date": a.adjustment_date.strftime("%Y-%m-%d"),
            "account": sanitize_csv_cell(a.account.name),
            "account_type": a.account.get_account_type_display(),
            "movement_type": f"Adjustment ({a.get_adjustment_type_display()})",
            "reference": sanitize_csv_cell(a.adjustment_number),
            "details": sanitize_csv_cell(a.reason[:60]),
            "inflow": float(a.amount) if is_cred else 0.0,
            "outflow": 0.0 if is_cred else float(a.amount),
        })

    rows.sort(key=lambda x: x["date"], reverse=True)
    return rows


def extract_daily_reconciliation(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Cash count and bank statement reconciliation reports."""
    recons = (
        FinancialReconciliation.objects.filter(
            branch__in=branch_qs,
            reconciliation_date__gte=date_range.start_date,
            reconciliation_date__lte=date_range.end_date,
        )
        .select_related("branch", "account", "reconciled_by", "approved_by")
        .order_by("-reconciliation_date", "-created_at")
    )
    rows = []
    for r in recons:
        rows.append({
            "date": r.reconciliation_date.strftime("%Y-%m-%d"),
            "reconciliation_number": sanitize_csv_cell(r.reconciliation_number),
            "branch": sanitize_csv_cell(r.branch.name),
            "account": sanitize_csv_cell(r.account.name),
            "opening_balance": float(r.opening_balance),
            "inflows": float(r.total_inflows),
            "outflows": float(r.total_outflows),
            "expected_balance": float(r.expected_balance),
            "actual_counted": float(r.actual_counted_balance),
            "variance": float(r.variance),
            "status": r.get_status_display(),
            "reconciled_by": sanitize_csv_cell(r.reconciled_by.email if r.reconciled_by else "System"),
            "variance_reason": sanitize_csv_cell(r.variance_reason[:80] if r.variance_reason else "None"),
        })
    return rows


def extract_branch_financial_summary(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Comparative financial performance across branches."""
    rows = []
    for branch in branch_qs:
        # Collections
        colls = Payment.objects.filter(
            branch=branch,
            status=Payment.Status.VERIFIED,
            paid_at__date__gte=date_range.start_date,
            paid_at__date__lte=date_range.end_date,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

        # Refunds
        refs = Refund.objects.filter(
            payment__branch=branch,
            status=Refund.Status.COMPLETED,
            completed_at__date__gte=date_range.start_date,
            completed_at__date__lte=date_range.end_date,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

        # Expenses
        exps = ExpensePayment.objects.filter(
            expense__branch=branch,
            status=ExpensePayment.Status.VERIFIED,
            payment_date__gte=date_range.start_date,
            payment_date__lte=date_range.end_date,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

        # Liabilities
        liab = Expense.objects.filter(
            branch=branch,
            approval_status=Expense.ApprovalStatus.APPROVED,
        ).aggregate(tot=Sum("balance_due"))["tot"] or Decimal("0.00")

        net = colls - refs - exps
        rows.append({
            "branch_name": sanitize_csv_cell(branch.name),
            "branch_code": sanitize_csv_cell(branch.code),
            "verified_collections": float(colls),
            "completed_refunds": float(refs),
            "paid_expenses": float(exps),
            "net_cash_flow": float(net),
            "outstanding_liabilities": float(liab),
        })
    return rows


def extract_income_expense_summary(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """
    Operational Income & Expense Summary (Cash-basis actuals).
    Note: Clearly labeled as cash-basis operational collections and disbursements,
    not an audited accrual Profit & Loss statement.
    """
    today = timezone.localdate()
    s_date = date_range.start_date
    e_date = date_range.end_date

    # Break down month-by-month or single period
    colls = Payment.objects.filter(
        branch__in=branch_qs,
        status=Payment.Status.VERIFIED,
        paid_at__date__gte=s_date,
        paid_at__date__lte=e_date,
    ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    refs = Refund.objects.filter(
        payment__branch__in=branch_qs,
        status=Refund.Status.COMPLETED,
        completed_at__date__gte=s_date,
        completed_at__date__lte=e_date,
    ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    net_colls = colls - refs

    exps = ExpensePayment.objects.filter(
        expense__branch__in=branch_qs,
        status=ExpensePayment.Status.VERIFIED,
        payment_date__gte=s_date,
        payment_date__lte=e_date,
    ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

    net_operating_cash = net_colls - exps

    approved_unpaid = Expense.objects.filter(
        branch__in=branch_qs,
        approval_status=Expense.ApprovalStatus.APPROVED,
        expense_date__gte=s_date,
        expense_date__lte=e_date,
    ).aggregate(tot=Sum("balance_due"))["tot"] or Decimal("0.00")

    return [{
        "period": f"{s_date.strftime('%Y-%m-%d')} to {e_date.strftime('%Y-%m-%d')}",
        "gross_collections": float(colls),
        "customer_refunds": float(refs),
        "net_collections": float(net_colls),
        "operating_expenses_paid": float(exps),
        "net_operating_cash": float(net_operating_cash),
        "unpaid_expense_liabilities": float(approved_unpaid),
        "accounting_note": "Operational Cash Basis (Not GAAP/IFRS P&L)",
    }]


def extract_approval_audit_report(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Audit log of expense submissions, approvals, rejections, and voids."""
    expenses = (
        Expense.objects.filter(
            branch__in=branch_qs,
            expense_date__gte=date_range.start_date,
            expense_date__lte=date_range.end_date,
        )
        .exclude(approval_status=Expense.ApprovalStatus.DRAFT)
        .select_related("branch", "category", "created_by", "approved_by", "rejected_by", "voided_by")
        .order_by("-updated_at")
    )
    rows = []
    for exp in expenses:
        actor = exp.approved_by or exp.rejected_by or exp.voided_by or exp.created_by
        decision_dt = exp.approved_at or exp.rejected_at or exp.voided_at or exp.submitted_at
        
        turnaround = "N/A"
        if exp.submitted_at and decision_dt and decision_dt > exp.submitted_at:
            delta = decision_dt - exp.submitted_at
            hours = delta.total_seconds() / 3600.0
            turnaround = f"{hours:.1f} hrs"

        reason = exp.rejection_reason or exp.void_reason or "Approved" if exp.approval_status == Expense.ApprovalStatus.APPROVED else "Pending Review"

        rows.append({
            "reference": sanitize_csv_cell(exp.reference),
            "branch": sanitize_csv_cell(exp.branch.name),
            "category": sanitize_csv_cell(exp.category.name),
            "amount": float(exp.total_amount),
            "submitted_by": sanitize_csv_cell(exp.created_by.email if exp.created_by else "System"),
            "decision_by": sanitize_csv_cell(actor.email if actor else "Pending"),
            "status": exp.get_approval_status_display(),
            "turnaround_time": turnaround,
            "notes": sanitize_csv_cell(reason[:80]),
        })
    return rows
