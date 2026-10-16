from apps.accounts.roles import has_role, is_owner


def can_view_executive_dashboard(user) -> bool:
    if not user.is_authenticated or not user.is_staff or not user.is_active:
        return False
    return is_owner(user) or user.is_superuser or has_role(user, "Branch Manager") or has_role(user, "Accountant")


def can_view_financial_analytics(user) -> bool:
    """
    Financial confidentiality rule:
    Receptionists, Trainers, and POS staff MUST NOT access gym-wide financial analytics.
    """
    if not user.is_authenticated or not user.is_staff or not user.is_active:
        return False
    if is_owner(user) or user.is_superuser or has_role(user, "Accountant"):
        return True
    if has_role(user, "Branch Manager"):
        return True
    return False


def can_view_membership_analytics(user) -> bool:
    if not user.is_authenticated or not user.is_staff or not user.is_active:
        return False
    return (
        is_owner(user)
        or user.is_superuser
        or has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Accountant")
    )


def can_view_attendance_analytics(user) -> bool:
    if not user.is_authenticated or not user.is_staff or not user.is_active:
        return False
    return (
        is_owner(user)
        or user.is_superuser
        or has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Accountant")
    )


def can_access_report_center(user) -> bool:
    if not user.is_authenticated or not user.is_staff or not user.is_active:
        return False
    return (
        is_owner(user)
        or user.is_superuser
        or has_role(user, "Branch Manager")
        or has_role(user, "Accountant")
        or has_role(user, "Receptionist")
    )


def can_export_reports(user) -> bool:
    if not user.is_authenticated or not user.is_staff or not user.is_active:
        return False
    # Receptionists can export only member/attendance reports, not financial
    return is_owner(user) or user.is_superuser or has_role(user, "Branch Manager") or has_role(user, "Accountant") or has_role(user, "Receptionist")
