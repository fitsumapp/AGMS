from apps.accounts.roles import has_role, is_owner
from apps.branches.services import can_access_branch


def can_checkin_members(user, branch=None) -> bool:
    """Check if user is authorized to perform check-ins."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    if has_role(user, "Branch Manager") or has_role(user, "Receptionist"):
        if branch:
            return can_access_branch(user, branch)
        return True
    return False


def can_override_checkin(user, branch=None) -> bool:
    """Only Branch Managers and Gym Owners can authorize access overrides."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    if has_role(user, "Branch Manager"):
        if branch:
            return can_access_branch(user, branch)
        return True
    return False


def can_manage_access_devices(user, branch=None) -> bool:
    """Only Branch Managers and Gym Owners can configure gate/kiosk devices."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    if has_role(user, "Branch Manager"):
        if branch:
            return can_access_branch(user, branch)
        return True
    return False


def can_view_attendance_reports(user, branch=None) -> bool:
    """Check if user can view attendance analytics and export reports."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user):
        return True
    if has_role(user, "Branch Manager") or has_role(user, "Accountant"):
        if branch:
            return can_access_branch(user, branch)
        return True
    return False
