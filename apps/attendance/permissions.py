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


def get_attendance_method_settings() -> dict:
    """
    Retrieve the effective gym-wide attendance method configuration.
    Returns {"qr_enabled": bool, "manual_enabled": bool}.
    """
    from apps.core.models import GymSettings
    settings_obj = GymSettings.objects.first()
    if not settings_obj:
        return {"qr_enabled": True, "manual_enabled": True}
    return {
        "qr_enabled": bool(settings_obj.attendance_qr_enabled),
        "manual_enabled": bool(settings_obj.attendance_manual_enabled),
    }


def assert_attendance_method_enabled(method: str):
    """
    Enforce server-side that the requested attendance method is currently enabled.
    Raises ValidationError if disabled.
    """
    from django.core.exceptions import ValidationError
    from .models import Attendance

    config = get_attendance_method_settings()
    method_str = (method or "").upper()

    if method_str in (Attendance.Method.QR, Attendance.CheckoutMethod.QR, "QR", "QR_CODE"):
        if not config["qr_enabled"]:
            raise ValidationError("QR Code attendance is currently disabled by gym policy.")
    elif method_str in (Attendance.Method.MANUAL, Attendance.CheckoutMethod.MANUAL, "MANUAL", "NAME_PHONE"):
        if not config["manual_enabled"]:
            raise ValidationError("Manual (Name/Phone) attendance is currently disabled by gym policy.")
    elif method_str == Attendance.Method.OVERRIDE:
        if not config["qr_enabled"] and not config["manual_enabled"]:
            raise ValidationError("Attendance recording is currently disabled by gym policy.")

