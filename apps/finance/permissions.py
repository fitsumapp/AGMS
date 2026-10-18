from decimal import Decimal
from django.core.exceptions import PermissionDenied
from apps.accounts.roles import has_role, is_owner
from apps.branches.models import Branch, UserBranchAccess


def get_user_finance_branches(user):
    """Return QuerySet of branches user is authorized to view/manage in finance."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return Branch.objects.none()
    if user.is_superuser or is_owner(user):
        return Branch.objects.all()

    # Finance access is strictly restricted to Branch Manager, Accountant, Receptionist
    if not (has_role(user, "Branch Manager") or has_role(user, "Accountant") or has_role(user, "Receptionist")):
        return Branch.objects.none()
    
    # Branch Manager, Accountant, Receptionist get assigned active branches
    assigned_branch_ids = UserBranchAccess.objects.filter(
        user=user,
        is_active=True,
    ).values_list("branch_id", flat=True)
    return Branch.objects.filter(id__in=assigned_branch_ids, is_active=True)


def can_view_financial_overview(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")


def can_create_expense(user, branch) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    if has_role(user, "Branch Manager") or has_role(user, "Accountant"):
        return branch in get_user_finance_branches(user)
    if has_role(user, "Receptionist"):
        # Check branch finance settings
        settings = getattr(branch, "finance_settings", None)
        if settings and settings.receptionist_can_create_expenses:
            return branch in get_user_finance_branches(user)
    return False


def can_approve_expense(user, expense) -> bool:
    """Validate whether user can approve this specific expense record."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True

    # Branch Manager
    if has_role(user, "Branch Manager"):
        if expense.branch not in get_user_finance_branches(user):
            return False

        # Self-approval restriction
        settings = getattr(expense.branch, "finance_settings", None)
        allow_self = settings.allow_self_approval if settings else False
        if not allow_self and expense.created_by_id == user.id:
            return False

        # Approval limit enforcement
        limit = settings.manager_approval_limit if settings else Decimal("10000.00")
        if expense.total_amount > limit:
            return False

        return True

    return False


def can_record_expense_payment(user, expense) -> bool:
    """Validate whether user can record an expense payment."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    if has_role(user, "Accountant") or has_role(user, "Branch Manager"):
        return expense.branch in get_user_finance_branches(user)
    return False


def can_manage_accounts(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Accountant") or has_role(user, "Branch Manager")


def can_conduct_reconciliation(user, branch) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    if has_role(user, "Accountant") or has_role(user, "Branch Manager"):
        return branch in get_user_finance_branches(user)
    return False


def can_close_reconciliation(user, branch) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    if has_role(user, "Branch Manager"):
        return branch in get_user_finance_branches(user)
    return False


def can_view_finance_reports(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")
