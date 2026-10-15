from apps.accounts.roles import has_role, is_owner


def can_manage_invoices(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Accountant")
    )


def can_record_payments(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Accountant")
    )


def can_verify_payments(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")


def can_request_refunds(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Accountant")
    )


def can_approve_refunds(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")


def can_view_financial_audit(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Accountant")
