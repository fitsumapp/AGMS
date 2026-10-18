from django.db.models import QuerySet
from apps.accounts.roles import has_role, is_owner
from apps.branches.services import accessible_branches


def can_operate_pos(user) -> bool:
    """Whether user can access POS checkout and ring sales."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return (
        has_role(user, "POS Staff")
        or has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
    )


def can_manage_inventory(user) -> bool:
    """Whether user can create/edit products, stock, transfers, and adjustments."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")


def can_authorize_discount(user) -> bool:
    """Whether user can approve manual discounts on POS checkout."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager")


def can_authorize_refund(user) -> bool:
    """Whether user can authorize customer sales returns & cash/electronic refunds."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")


def can_approve_purchase_order(user) -> bool:
    """Whether user can approve purchase orders."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager")


def can_reconcile_stock_count(user) -> bool:
    """Whether user can finalize and reconcile physical cycle counts."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")


def can_view_inventory_reports(user) -> bool:
    """Whether user can access sales and inventory reports."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Accountant")
    )


def can_settle_pos_to_finance(user) -> bool:
    """Whether user can formally post verified POS collections to FinancialAccount."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Accountant") or has_role(user, "Branch Manager")


def accessible_branches_for_inventory(user) -> QuerySet:
    """Returns branches accessible by this user."""
    return accessible_branches(user)
