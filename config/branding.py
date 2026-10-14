import os
import re
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse_lazy

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

def superuser(request):
    return request.user.is_active and request.user.is_superuser

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
        "COLORS": {"primary": primary_shades()},
        "DASHBOARD_CALLBACK": "apps.core.dashboard.dashboard",
        "SIDEBAR": {"show_all_applications": False, "navigation": [{"title": "Workspace", "items": [
            {"title": "Dashboard", "icon": "space_dashboard", "link": reverse_lazy("admin:index")},
            {"title": "Users", "icon": "group", "link": reverse_lazy("admin:accounts_user_changelist"), "permission": superuser},
            {"title": "Groups", "icon": "shield_person", "link": reverse_lazy("admin:auth_group_changelist"), "permission": superuser},
            {"title": "System Settings", "icon": "settings", "link": reverse_lazy("admin:core_gymprofile_changelist"), "permission": superuser},
            {"title": "Access Policies", "icon": "rule", "link": reverse_lazy("admin:core_gymsettings_changelist"), "permission": superuser},
        ]}]},
    }
