import os
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.roles import has_role, is_owner
from apps.branches.models import Branch
from .models import (
    BranchFinanceSettings,
    Expense,
    ExpensePayment,
    FinancialAccount,
    FinancialReconciliation,
    Supplier,
)
from .permissions import (
    can_conduct_reconciliation,
    can_view_financial_overview,
    get_user_finance_branches,
)
from .selectors import (
    get_expenses_by_branch,
    get_expenses_by_category,
    get_finance_overview_metrics,
    get_top_suppliers_breakdown,
)
from .services import (
    conduct_reconciliation,
    get_or_create_branch_finance_settings,
)


@login_required
def financial_overview_view(request):
    """Operational Financial Overview dashboard."""
    if not can_view_financial_overview(request.user):
        raise PermissionDenied("You do not have permission to view the financial overview.")

    user_branches = get_user_finance_branches(request.user)
    if not user_branches.exists():
        raise PermissionDenied("No authorized branches assigned.")

    # Branch filter
    branch_id = request.GET.get("branch")
    if branch_id:
        selected_branches = user_branches.filter(id=branch_id)
        if not selected_branches.exists():
            raise PermissionDenied("Access to selected branch is unauthorized.")
    else:
        selected_branches = user_branches

    # Date filter range
    period = request.GET.get("period", "this_month")
    today = timezone.localdate()

    if period == "today":
        start_date = today
        end_date = today
    elif period == "this_week":
        start_date = today - timezone.timedelta(days=today.weekday())
        end_date = today
    elif period == "last_month":
        first_of_this_month = today.replace(day=1)
        last_of_prev_month = first_of_this_month - timezone.timedelta(days=1)
        start_date = last_of_prev_month.replace(day=1)
        end_date = last_of_prev_month
    elif period == "this_year":
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif period == "custom":
        s_str = request.GET.get("start_date")
        e_str = request.GET.get("end_date")
        try:
            start_date = timezone.datetime.strptime(s_str, "%Y-%m-%d").date() if s_str else today.replace(day=1)
            end_date = timezone.datetime.strptime(e_str, "%Y-%m-%d").date() if e_str else today
        except ValueError:
            start_date = today.replace(day=1)
            end_date = today
    else:  # this_month (default)
        start_date = today.replace(day=1)
        end_date = today

    metrics = get_finance_overview_metrics(selected_branches, start_date, end_date)
    cat_breakdown = get_expenses_by_category(selected_branches, start_date, end_date)
    branch_comparison = get_expenses_by_branch(selected_branches, start_date, end_date)
    top_suppliers = get_top_suppliers_breakdown(selected_branches, start_date, end_date)

    # Active accounts breakdown
    accounts = FinancialAccount.objects.filter(branch__in=selected_branches, is_active=True).order_by("account_type", "name")

    context = {
        "title": "Financial Overview",
        "metrics": metrics,
        "cat_breakdown": cat_breakdown,
        "branch_comparison": branch_comparison,
        "top_suppliers": top_suppliers,
        "accounts": accounts,
        "user_branches": user_branches,
        "selected_branch_id": branch_id or "",
        "period": period,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
    }
    return render(request, "finance/financial_overview.html", context)


@login_required
def download_expense_receipt(request, expense_id):
    """Secure, authenticated download of private expense receipt attachments."""
    expense = get_object_or_404(Expense, id=expense_id)
    user_branches = get_user_finance_branches(request.user)

    if expense.branch not in user_branches and not is_owner(request.user) and not request.user.is_superuser:
        raise PermissionDenied("You do not have permission to view receipts for this branch.")

    if not (
        request.user.is_superuser
        or is_owner(request.user)
        or has_role(request.user, "Branch Manager")
        or has_role(request.user, "Accountant")
        or (has_role(request.user, "Receptionist") and expense.created_by_id == request.user.id)
    ):
        raise PermissionDenied("Your role is not authorized to download expense receipts.")

    if not expense.receipt_attachment:
        raise Http404("No receipt attachment found for this expense.")

    file_path = expense.receipt_attachment.path
    if not os.path.exists(file_path):
        raise Http404("Attachment file does not exist on disk.")

    response = FileResponse(open(file_path, "rb"))
    filename = os.path.basename(file_path)
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
def download_payment_attachment(request, payment_id):
    """Secure, authenticated download of private payment vouchers/attachments."""
    payment = get_object_or_404(ExpensePayment, id=payment_id)
    user_branches = get_user_finance_branches(request.user)

    if payment.expense.branch not in user_branches and not is_owner(request.user) and not request.user.is_superuser:
        raise PermissionDenied("You do not have permission to view attachments for this branch.")

    if not (
        request.user.is_superuser
        or is_owner(request.user)
        or has_role(request.user, "Branch Manager")
        or has_role(request.user, "Accountant")
    ):
        raise PermissionDenied("Your role is not authorized to download payment attachments.")

    if not payment.attachment:
        raise Http404("No payment attachment found for this payment.")

    file_path = payment.attachment.path
    if not os.path.exists(file_path):
        raise Http404("Attachment file does not exist on disk.")

    response = FileResponse(open(file_path, "rb"))
    filename = os.path.basename(file_path)
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response
