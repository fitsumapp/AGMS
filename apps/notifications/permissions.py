from apps.accounts.roles import has_role, is_owner
from apps.branches.models import Branch, UserBranchAccess


def get_user_notification_branches(user):
    """Return branches the user is authorized to manage notifications for."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return Branch.objects.none()
    if user.is_superuser or is_owner(user):
        return Branch.objects.filter(is_active=True)

    branch_ids = UserBranchAccess.objects.filter(
        user=user, is_active=True
    ).values_list("branch_id", flat=True)
    return Branch.objects.filter(id__in=branch_ids, is_active=True)


def can_view_notification_dashboard(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Accountant")
    )


def can_send_sms(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Receptionist") or has_role(user, "Accountant")


def can_send_bulk_messages(user) -> bool:
    """Only Gym Owner and Branch Manager can authorize bulk campaigns."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Branch Manager")


def can_manage_templates(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Branch Manager")


def can_manage_automation_rules(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Branch Manager")


def can_manage_provider_config(user) -> bool:
    """Only Technical Super Admin and Gym Owner can manage external gateway credentials/providers."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    return user.is_superuser or is_owner(user)


def can_view_communication_reports(user) -> bool:
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or is_owner(user):
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Accountant")
