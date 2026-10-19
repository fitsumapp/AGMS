from apps.accounts.roles import has_role, is_owner
from apps.branches.services import accessible_branches


def get_user_facility_branches(user):
    """Return queryset of branches the user is authorized to manage or view for facilities."""
    if is_owner(user):
        from apps.branches.models import Branch
        return Branch.objects.filter(is_active=True)
    return accessible_branches(user)


def can_manage_facility(user, branch=None) -> bool:
    """Superuser, Gym Owner, and Branch Manager have full facility and equipment management permissions."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    if has_role(user, "Branch Manager"):
        if branch is None:
            return True
        return get_user_facility_branches(user).filter(pk=branch.pk).exists()
    return False


def can_view_facility(user, branch=None) -> bool:
    """Check if user can view facility, lockers, or equipment."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    if has_role(user, "Branch Manager") or has_role(request_user_or_staff(user), "Receptionist") or has_role(user, "Accountant") or has_role(user, "Trainer"):
        if branch is None:
            return True
        return get_user_facility_branches(user).filter(pk=branch.pk).exists()
    return False


def can_assign_locker(user, branch=None) -> bool:
    """Gym Owner, Branch Manager, and Receptionist can issue and release lockers."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    if has_role(user, "Branch Manager") or has_role(user, "Receptionist"):
        if branch is None:
            return True
        return get_user_facility_branches(user).filter(pk=branch.pk).exists()
    return False


def can_report_fault(user) -> bool:
    """Trainers, staff, receptionists, and managers can report equipment faults."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    return is_owner(user) or has_role(user, "Branch Manager") or has_role(user, "Receptionist") or has_role(user, "Trainer")


def can_manage_maintenance(user, branch=None) -> bool:
    """Superuser, Gym Owner, and Branch Manager can approve/manage work orders."""
    return can_manage_facility(user, branch)


def request_user_or_staff(user):
    return user
