import os
import re
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse_lazy
from django.templatetags.static import static
from django.utils.safestring import mark_safe

def color(name, default):
    value = os.getenv(name, default)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ImproperlyConfigured(f"{name} must be a six-digit hex color.")
    return value

PALETTE = {"primary": color("GYM_COLOR_PRIMARY", "#164E63"),
    "secondary": color("GYM_COLOR_SECONDARY", "#0F766E"),
    "background": color("GYM_COLOR_BACKGROUND", "#F8FAFC"),
    "text": color("GYM_COLOR_TEXT", "#0F172A"),
    "accent": color("GYM_COLOR_ACCENT", "#D97706")}
SIDEBAR = color("GYM_COLOR_SIDEBAR", "#103B4C")

def superuser(request):
    return request.user.is_active and request.user.is_superuser

def owner(request):
    from apps.accounts.roles import is_owner
    return is_owner(request.user)

def can_see_branches(request):
    from apps.branches.services import accessible_branches
    return accessible_branches(request.user).exists() or owner(request)

def can_see_branch_settings(request):
    from apps.accounts.roles import has_role
    return owner(request) or has_role(request.user, "Branch Manager")

def can_see_members(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    if has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
        return False
    return has_role(request.user, "Branch Manager") or has_role(request.user, "Receptionist") or has_role(request.user, "Accountant")

def can_see_memberships(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    if has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
        return False
    return has_role(request.user, "Branch Manager") or has_role(request.user, "Receptionist") or has_role(request.user, "Accountant")

def can_see_billing(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    if has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
        return False
    return (has_role(request.user, "Branch Manager") or
            has_role(request.user, "Receptionist") or
            has_role(request.user, "Accountant"))

def can_see_refunds(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    return has_role(request.user, "Branch Manager") or has_role(request.user, "Accountant")

def can_see_financial_audit(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    return has_role(request.user, "Accountant")

def can_see_attendance(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    return (has_role(request.user, "Branch Manager") or
            has_role(request.user, "Receptionist") or
            has_role(request.user, "Trainer"))

def can_see_access_devices(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    return has_role(request.user, "Branch Manager")

def can_see_overrides(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request):
        return True
    from apps.accounts.roles import has_role
    return has_role(request.user, "Branch Manager")

def can_see_qr_checkin(request):
    if not can_see_attendance(request):
        return False
    from apps.attendance.permissions import get_attendance_method_settings
    return get_attendance_method_settings()["qr_enabled"]

def can_see_manual_checkin(request):
    if not can_see_attendance(request):
        return False
    from apps.attendance.permissions import get_attendance_method_settings
    return get_attendance_method_settings()["manual_enabled"]

def can_see_gym_policies(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    if owner(request) or superuser(request):
        return True
    from apps.accounts.roles import has_role
    return has_role(request.user, "Branch Manager") or has_role(request.user, "Receptionist")

def can_see_analytics(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.analytics.permissions import can_view_executive_dashboard
    return can_view_executive_dashboard(request.user)

def can_see_financial_analytics(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.analytics.permissions import can_view_financial_analytics as cvfa
    return cvfa(request.user)

def can_see_membership_analytics(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.analytics.permissions import can_view_membership_analytics as cvma
    return cvma(request.user)

def can_see_attendance_analytics(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.analytics.permissions import can_view_attendance_analytics as cvaa
    return cvaa(request.user)

def can_see_reports(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.analytics.permissions import can_access_report_center
    return can_access_report_center(request.user)

def can_see_staff(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_staff_directory
    return can_view_staff_directory(request.user)

def can_see_trainers(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_trainer_directory
    return can_view_trainer_directory(request.user)

def can_see_trainer_assignments(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_trainer_directory
    return can_view_trainer_directory(request.user)

def can_see_training_sessions(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_schedules
    return can_view_schedules(request.user)

def can_see_staff_schedules(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_schedules
    return can_view_schedules(request.user)

def can_see_leave_requests(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_staff_directory
    return can_view_staff_directory(request.user)

def can_see_trainer_performance(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_performance_analytics
    return can_view_performance_analytics(request.user)

def can_see_staff_reports(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.staff.permissions import can_view_staff_directory
    return can_view_staff_directory(request.user)

def can_see_classes(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.classes.permissions import can_manage_classes, can_book_classes, can_mark_class_attendance
    return can_manage_classes(request.user) or can_book_classes(request.user) or can_mark_class_attendance(request.user)

def can_see_class_reports(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        return False
    from apps.classes.permissions import can_view_class_reports
    return can_view_class_reports(request.user)

def primary_shades():
    rgb = tuple(int(PALETTE["primary"][index:index + 2], 16) for index in (1, 3, 5))
    shades = {}
    for level, blend in {50: .95, 100: .90, 200: .75, 300: .55, 400: .35, 500: .15, 600: 0, 700: -.15, 800: -.30, 900: -.45, 950: -.60}.items():
        values = [round(channel + (255 - channel) * blend) if blend >= 0 else round(channel * (1 + blend)) for channel in rgb]
        shades[str(level)] = "#" + "".join(f"{channel:02x}" for channel in values)
    return shades

def unfold_config():
    from django.utils.text import format_lazy
    membership_changelist = reverse_lazy("admin:memberships_membership_changelist")
    invoice_changelist = reverse_lazy("admin:billing_invoice_changelist")
    payment_changelist = reverse_lazy("admin:billing_payment_changelist")
    attendance_changelist = reverse_lazy("admin:attendance_attendance_changelist")
    accessattempt_changelist = reverse_lazy("admin:attendance_accessattempt_changelist")

    return {
        "SITE_TITLE": "apps.core.dashboard.site_name",
        "SITE_HEADER": "apps.core.dashboard.site_name",
        "SITE_SUBHEADER": "Management Portal", "SITE_SYMBOL": "exercise",
        "SITE_LOGO": "apps.core.dashboard.site_logo",
        "SITE_URL": None, "SHOW_VIEW_ON_SITE": False,
        "COLORS": {"primary": primary_shades(), "sidebar": {"600": SIDEBAR}},
        "STYLES": [lambda request: static("css/erp-admin.css")],
        "GLOBAL_CALLBACK": "apps.core.dashboard.global_context",
        "DASHBOARD_CALLBACK": "apps.core.dashboard.dashboard",
        "SIDEBAR": {"show_all_applications": False, "navigation": [
            {"title": "Overview", "items": [
                {"title": "Dashboard", "icon": "space_dashboard", "link": reverse_lazy("admin:index")}]},
            {"title": "Analytics & Reports", "items": [
                {"title": "Executive Dashboard", "icon": "monitoring", "link": reverse_lazy("analytics-dashboard"), "permission": can_see_analytics},
                {"title": "Financial Analytics", "icon": "query_stats", "link": reverse_lazy("analytics-financial"), "permission": can_see_financial_analytics},
                {"title": "Membership Analytics", "icon": "pie_chart", "link": reverse_lazy("analytics-memberships"), "permission": can_see_membership_analytics},
                {"title": "Attendance Analytics", "icon": "bar_chart", "link": reverse_lazy("analytics-attendance"), "permission": can_see_attendance_analytics},
                {"title": "Report Center", "icon": "table_chart", "link": reverse_lazy("analytics-reports"), "permission": can_see_reports},
                {"title": "Saved Presets", "icon": "bookmark", "link": reverse_lazy("admin:analytics_reportpreset_changelist"), "permission": can_see_reports},
                {"title": "Export Audit Logs", "icon": "history_toggle_off", "link": reverse_lazy("admin:analytics_reportexportlog_changelist"), "permission": owner},
            ]},
            {"title": "Gym Operations", "items": [
                {"title": "Members", "icon": "group", "link": reverse_lazy("admin:members_member_changelist"), "permission": can_see_members},
                {"title": "Branches", "icon": "location_city", "link": reverse_lazy("admin:branches_branch_changelist"), "permission": can_see_branches},
                {"title": "Operating Hours", "icon": "schedule", "link": reverse_lazy("admin:branches_branchsettings_changelist"), "permission": can_see_branch_settings}]},
            {"title": "Attendance & Access", "items": [
                {"title": "Attendance Dashboard", "icon": "analytics", "link": reverse_lazy("admin:attendance_dashboard"), "permission": can_see_attendance},
                {"title": "QR Check-in", "icon": "qr_code_scanner", "link": reverse_lazy("admin:attendance_qr_checkin"), "permission": can_see_qr_checkin},
                {"title": "Manual Check-in", "icon": "how_to_reg", "link": reverse_lazy("admin:attendance_manual_checkin"), "permission": can_see_manual_checkin},
                {"title": "Today's Attendance", "icon": "today", "link": format_lazy("{}?today=1", attendance_changelist), "permission": can_see_attendance},
                {"title": "Currently Inside", "icon": "groups", "link": format_lazy("{}?status__exact=PRESENT", attendance_changelist), "permission": can_see_attendance},
                {"title": "Attendance History", "icon": "history", "link": attendance_changelist, "permission": can_see_attendance},
                {"title": "Denied Access Attempts", "icon": "block", "link": format_lazy("{}?result__exact=DENIED", accessattempt_changelist), "permission": can_see_attendance},
                {"title": "QR Credentials", "icon": "badge", "link": reverse_lazy("admin:attendance_membercredential_changelist"), "permission": can_see_attendance},
                {"title": "Access Devices", "icon": "meeting_room", "link": reverse_lazy("admin:attendance_accessdevice_changelist"), "permission": can_see_access_devices},
                {"title": "Manual Overrides", "icon": "admin_panel_settings", "link": reverse_lazy("admin:attendance_attendanceoverride_changelist"), "permission": can_see_overrides},
                {"title": "Attendance Reports", "icon": "summarize", "link": reverse_lazy("admin:attendance_reports"), "permission": can_see_attendance},
            ]},
            {"title": "Membership Management", "items": [
                {"title": "Membership Dashboard", "icon": "space_dashboard", "link": reverse_lazy("admin:index"), "permission": can_see_memberships},
                {"title": "Membership Packages", "icon": "card_membership", "link": reverse_lazy("admin:memberships_membershippackage_changelist"), "permission": can_see_memberships},
                {"title": "Active Memberships", "icon": "how_to_reg", "link": format_lazy("{}?status__exact=ACTIVE", membership_changelist), "permission": can_see_memberships},
                {"title": "Pending Memberships", "icon": "pending_actions", "link": format_lazy("{}?status__exact=PENDING", membership_changelist), "permission": can_see_memberships},
                {"title": "Renewals", "icon": "autorenew", "link": reverse_lazy("admin:memberships_membershiprenewal_changelist"), "permission": can_see_memberships},
                {"title": "Freeze Requests", "icon": "ac_unit", "link": reverse_lazy("admin:memberships_membershipfreeze_changelist"), "permission": can_see_memberships},
                {"title": "Expiring Soon", "icon": "alarm", "link": format_lazy("{}?quick_filter=expiring_7_days", membership_changelist), "permission": can_see_memberships},
                {"title": "Membership History", "icon": "history", "link": reverse_lazy("admin:memberships_membershipevent_changelist"), "permission": can_see_memberships},
            ]},
            {"title": "Billing & Payments", "items": [
                {"title": "Billing Overview", "icon": "analytics", "link": reverse_lazy("admin:index"), "permission": can_see_billing},
                {"title": "Invoices", "icon": "receipt_long", "link": invoice_changelist, "permission": can_see_billing},
                {"title": "Create Invoice", "icon": "note_add", "link": reverse_lazy("admin:billing_invoice_create_invoice"), "permission": can_see_billing},
                {"title": "Payments", "icon": "payments", "link": payment_changelist, "permission": can_see_billing},
                {"title": "Pending Verification", "icon": "pending", "link": format_lazy("{}?status__exact=PENDING", payment_changelist), "permission": can_see_billing},
                {"title": "Receipts", "icon": "receipt", "link": reverse_lazy("admin:billing_receipt_changelist"), "permission": can_see_billing},
                {"title": "Refund Requests", "icon": "currency_exchange", "link": reverse_lazy("admin:billing_refund_changelist"), "permission": can_see_refunds},
                {"title": "Outstanding Balances", "icon": "warning", "link": format_lazy("{}?status__in=ISSUED,PARTIALLY_PAID", invoice_changelist), "permission": can_see_billing},
                {"title": "Payment Methods", "icon": "credit_card", "link": reverse_lazy("admin:billing_paymentmethod_changelist"), "permission": can_see_billing},
                {"title": "Financial Audit", "icon": "policy", "link": reverse_lazy("admin:billing_financialauditevent_changelist"), "permission": can_see_financial_audit},
            ]},
            {"title": "Staff & Trainers", "items": [
                {"title": mark_safe("Staff D&#105;rectory"), "icon": "badge", "link": reverse_lazy("admin:staff_staffprofile_changelist"), "permission": can_see_staff},
                {"title": mark_safe("Trainer D&#105;rectory"), "icon": "fitness_center", "link": reverse_lazy("admin:staff_trainerprofile_changelist"), "permission": can_see_trainers},
                {"title": "Trainer Assignments", "icon": "assignment_ind", "link": reverse_lazy("admin:staff_trainermemberassignment_changelist"), "permission": can_see_trainer_assignments},
                {"title": "Training Sessions", "icon": "event_available", "link": reverse_lazy("admin:staff_trainingsession_changelist"), "permission": can_see_training_sessions},
                {"title": "Staff Schedules", "icon": "calendar_month", "link": reverse_lazy("admin:staff_staffshift_changelist"), "permission": can_see_staff_schedules},
                {"title": "Leave Requests", "icon": "time_to_leave", "link": reverse_lazy("admin:staff_leaverequest_changelist"), "permission": can_see_leave_requests},
                {"title": "Trainer Performance", "icon": "insights", "link": reverse_lazy("staff-performance"), "permission": can_see_trainer_performance},
                {"title": "Staff Reports", "icon": "summarize", "link": reverse_lazy("staff-reports"), "permission": can_see_staff_reports},
            ]},
            {"title": "Classes & Bookings", "items": [
                {"title": "Class Catalog", "icon": "category", "link": reverse_lazy("admin:classes_classtype_changelist"), "permission": can_see_classes},
                {"title": "Class Schedule", "icon": "calendar_month", "link": reverse_lazy("admin:classes_classscheduletemplate_changelist"), "permission": can_see_classes},
                {"title": "Class Sessions", "icon": "event_available", "link": reverse_lazy("admin:classes_classsession_changelist"), "permission": can_see_classes},
                {"title": "Member Bookings", "icon": "book_online", "link": reverse_lazy("admin:classes_classbooking_changelist"), "permission": can_see_classes},
                {"title": "Waitlist", "icon": "pending_actions", "link": format_lazy("{}?status__exact=WAITLISTED", reverse_lazy("admin:classes_classbooking_changelist")), "permission": can_see_classes},
                {"title": "Class Attendance", "icon": "fact_check", "link": format_lazy("{}?status__in=SCHEDULED,IN_PROGRESS", reverse_lazy("admin:classes_classsession_changelist")), "permission": can_see_classes},
                {"title": "Class Calendar", "icon": "calendar_view_week", "link": reverse_lazy("class-calendar"), "permission": can_see_classes},
                {"title": "Class Reports", "icon": "bar_chart", "link": reverse_lazy("class-reports"), "permission": can_see_class_reports},
            ]},
            {"title": "Management", "items": [
                {"title": "Staff Accounts", "icon": "badge", "link": reverse_lazy("admin:accounts_staffaccount_changelist"), "permission": owner},
                {"title": "Branch Assignments", "icon": "assignment_ind", "link": reverse_lazy("admin:branches_userbranchaccess_changelist"), "permission": owner},
                {"title": "Gym Branding", "icon": "palette", "link": reverse_lazy("admin:core_gymbranding_changelist"), "permission": owner},
                {"title": "Security & Audit", "icon": "policy", "link": reverse_lazy("admin:branches_auditevent_changelist"), "permission": owner},
                {"title": "Technical Users", "icon": "manage_accounts", "link": reverse_lazy("admin:accounts_user_changelist"), "permission": superuser},
                {"title": "Technical Groups", "icon": "admin_panel_settings", "link": reverse_lazy("admin:auth_group_changelist"), "permission": superuser},
                {"title": "System Settings", "icon": "settings", "link": reverse_lazy("admin:core_gymprofile_changelist"), "permission": superuser},
                {"title": "Technical Settings", "icon": "tune", "link": reverse_lazy("admin:core_gymsettings_changelist"), "permission": superuser},
                {"title": "Gym Policies", "icon": "rule", "link": reverse_lazy("admin:core_gympolicy_changelist"), "permission": can_see_gym_policies},
            ]},
        ]},
    }
