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


def get_attendance_method_settings(branch=None) -> dict:
    """
    Retrieve effective attendance method configuration for a branch or globally.
    Integrates:
    - Feature Flags: ATTENDANCE_QR, ATTENDANCE_MANUAL
    - Settings Precedence: BranchSettings -> GymSettings -> True
    - Safety Invariant: At least one attendance method MUST remain effectively enabled.
    Returns {"qr_enabled": bool, "manual_enabled": bool}.
    """
    from apps.core.services import is_feature_enabled, resolve_setting

    qr_flag = is_feature_enabled("ATTENDANCE_QR", branch)
    manual_flag = is_feature_enabled("ATTENDANCE_MANUAL", branch)

    qr_setting = resolve_setting(branch, "attendance_qr_enabled", default=True)
    manual_setting = resolve_setting(branch, "attendance_manual_enabled", default=True)

    effective_qr = bool(qr_flag and qr_setting)
    effective_manual = bool(manual_flag and manual_setting)

    # Safety Invariant: At least one method must remain active.
    # If both would be disabled by configuration or feature flags, fallback to manual (Name/Phone).
    if not effective_qr and not effective_manual:
        effective_manual = True

    return {
        "qr_enabled": effective_qr,
        "manual_enabled": effective_manual,
    }


def assert_attendance_method_enabled(method: str, branch=None):
    """
    Enforce server-side that the requested attendance method is currently enabled.
    Raises ValidationError if disabled.
    """
    from django.core.exceptions import ValidationError
    from .models import Attendance

    config = get_attendance_method_settings(branch)
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

