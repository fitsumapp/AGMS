from apps.accounts.roles import has_role, is_owner


def can_manage_classes(user):
    """
    Checks if user can configure class catalog, schedules, and sessions.
    Gym Owner, Superuser, and Branch Manager.
    """
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager")


def can_book_classes(user):
    """
    Checks if user can book or cancel classes for members.
    Gym Owner, Superuser, Branch Manager, Receptionist.
    """
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager") or has_role(user, "Receptionist")


def can_mark_class_attendance(user):
    """
    Checks if user can record class attendance.
    Gym Owner, Superuser, Branch Manager, Receptionist, Trainer.
    """
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return (
        has_role(user, "Branch Manager")
        or has_role(user, "Receptionist")
        or has_role(user, "Trainer")
    )


def can_view_class_reports(user):
    """
    Checks if user can view class analytics and operational reports.
    """
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if is_owner(user) or user.is_superuser:
        return True
    return has_role(user, "Branch Manager")
