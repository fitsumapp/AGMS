import os
import re
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse_lazy
from django.templatetags.static import static

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

def primary_shades():
    rgb = tuple(int(PALETTE["primary"][index:index + 2], 16) for index in (1, 3, 5))
    shades = {}
    for level, blend in {50: .95, 100: .90, 200: .75, 300: .55, 400: .35, 500: .15, 600: 0, 700: -.15, 800: -.30, 900: -.45, 950: -.60}.items():
        values = [round(channel + (255 - channel) * blend) if blend >= 0 else round(channel * (1 + blend)) for channel in rgb]
        shades[str(level)] = "#" + "".join(f"{channel:02x}" for channel in values)
    return shades

def unfold_config():
    return {
        "SITE_TITLE": "apps.core.dashboard.site_name",
        "SITE_HEADER": "apps.core.dashboard.site_name",
        "SITE_SUBHEADER": "Management workspace", "SITE_SYMBOL": "exercise",
        "SITE_LOGO": "apps.core.dashboard.site_logo",
        "SITE_URL": None, "SHOW_VIEW_ON_SITE": False,
        "COLORS": {"primary": primary_shades(), "sidebar": {"600": SIDEBAR}},
        "STYLES": [lambda request: static("css/erp-admin.css")],
        "GLOBAL_CALLBACK": "apps.core.dashboard.global_context",
        "DASHBOARD_CALLBACK": "apps.core.dashboard.dashboard",
        "SIDEBAR": {"show_all_applications": False, "navigation": [
            {"title": "Overview", "items": [
                {"title": "Dashboard", "icon": "space_dashboard", "link": reverse_lazy("admin:index")}]},
            {"title": "Gym Operations", "items": [
                {"title": "Branches", "icon": "location_city", "link": reverse_lazy("admin:branches_branch_changelist"), "permission": can_see_branches},
                {"title": "Operating Hours", "icon": "schedule", "link": reverse_lazy("admin:branches_branchsettings_changelist"), "permission": can_see_branch_settings}]},
            {"title": "Management", "items": [
                {"title": "Staff Accounts", "icon": "badge", "link": reverse_lazy("admin:accounts_staffaccount_changelist"), "permission": owner},
                {"title": "Branch Assignments", "icon": "assignment_ind", "link": reverse_lazy("admin:branches_userbranchaccess_changelist"), "permission": owner},
                {"title": "Gym Branding", "icon": "palette", "link": reverse_lazy("admin:core_gymbranding_changelist"), "permission": owner},
                {"title": "Security & Audit", "icon": "policy", "link": reverse_lazy("admin:branches_auditevent_changelist"), "permission": owner},
                {"title": "Technical Users", "icon": "manage_accounts", "link": reverse_lazy("admin:accounts_user_changelist"), "permission": superuser},
                {"title": "Technical Groups", "icon": "admin_panel_settings", "link": reverse_lazy("admin:auth_group_changelist"), "permission": superuser},
                {"title": "System Settings", "icon": "settings", "link": reverse_lazy("admin:core_gymprofile_changelist"), "permission": superuser},
                {"title": "Access Policies", "icon": "rule", "link": reverse_lazy("admin:core_gymsettings_changelist"), "permission": superuser},
            ]},
        ]},
    }
