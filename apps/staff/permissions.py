from apps.accounts.roles import has_role, is_owner
from apps.branches.services import accessible_branches


def can_manage_staff(user):
    """Gym Owner and Superuser can fully manage all staff across the business."""
    return is_owner(user) or user.is_superuser


def can_manage_branch_staff(user, branch=None):
    """Gym Owner can manage any branch; Branch Manager can manage their assigned branch."""
    if is_owner(user) or user.is_superuser:
        return True
    if has_role(user, "Branch Manager"):
        if branch is None:
            return accessible_branches(user).exists()
        return accessible_branches(user).filter(pk=branch.pk).exists()
    return False


def can_view_staff_directory(user):
    """Staff with management, front desk, or accountant role can view the directory."""
    if is_owner(user) or user.is_superuser:
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Accountant")
    )


def can_view_trainer_directory(user):
    if is_owner(user) or user.is_superuser:
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Trainer")
    )


def can_manage_trainer_assignments(user, branch=None):
    return can_manage_branch_staff(user, branch)


def can_manage_training_sessions(user, trainer=None, branch=None):
    if is_owner(user) or user.is_superuser:
        return True
    if has_role(user, "Branch Manager"):
        if branch:
            return accessible_branches(user).filter(pk=branch.pk).exists()
        return True
    if has_role(user, "Trainer") and trainer:
        return trainer.staff.user_id == user.id
    if has_role(user, "Receptionist"):
        # Receptionists can book sessions at their branch
        return True
    return False


def can_view_schedules(user):
    if is_owner(user) or user.is_superuser:
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Trainer")
    )


def can_manage_schedules(user, branch=None):
    return can_manage_branch_staff(user, branch)


def can_approve_leave(user, branch=None):
    return can_manage_branch_staff(user, branch)


def can_view_performance_analytics(user):
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Trainer")
