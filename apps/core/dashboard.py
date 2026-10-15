import logging
from django.conf import settings
from django.db import connection, DatabaseError
from django.urls import reverse
from config.branding import PALETTE
from .models import GymProfile
from .utils import local_date
from apps.branches.services import accessible_branches, active_branch
from apps.accounts.roles import is_owner

logger = logging.getLogger("gym.status")


def global_context(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return {}
    branches = list(accessible_branches(request.user))
    selected = active_branch(request)
    return {"branch_options": branches, "selected_branch": selected,
        "all_branches_allowed": is_owner(request.user),
        "branch_context_label": selected.name if selected else "All Branches" if is_owner(request.user) else "No branch assigned"}

def profile_for_request(request):
    if not hasattr(request, "_gym_profile"):
        try:
            request._gym_profile = GymProfile.objects.first()
        except DatabaseError:
            logger.error("gym_profile_unavailable: check database connectivity and migrations")
            request._gym_profile = None
    return request._gym_profile

def site_name(request):
    profile = profile_for_request(request)
    if profile and profile.name:
        return profile.name
    return getattr(settings, "GYM_DISPLAY_NAME", "") or "Gym ERP Administration"

def site_logo(request):
    profile = profile_for_request(request)
    return reverse("gym-logo") if request.user.is_authenticated and request.user.is_staff and profile and profile.logo else None

def database_available():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        logger.error("database_unavailable: verify service, environment and connectivity")
        return False

def dashboard(request, context):
    profile = profile_for_request(request)
    zone = profile.default_timezone if profile else settings.GYM_DEFAULT_TIMEZONE
    branches = context.get("branch_options", [])
    selected_b = context.get("selected_branch")

    try:
        from apps.members.selectors import get_member_metrics
        metrics = get_member_metrics(request.user, active_branch=selected_b)
        active_profiles_val = metrics["active_profiles"]
        total_val = metrics["total_members"]
        new_month_val = metrics["new_this_month"]
        member_status_label = f"{total_val} total registered"
        member_change_label = f"+{new_month_val} this month" if new_month_val else "No new this month"
    except Exception:
        active_profiles_val = 0
        member_status_label = "0 total registered"
        member_change_label = None

    try:
        from apps.memberships.selectors import get_membership_metrics
        m_metrics = get_membership_metrics(request.user, active_branch=selected_b)
    except Exception:
        m_metrics = {
            "active_memberships": 0,
            "pending_memberships": 0,
            "frozen_memberships": 0,
            "expired_memberships": 0,
            "expiring_7_days": 0,
            "memberships_by_package": [],
            "memberships_by_branch": [],
        }

    kpi_cards = [
        {"title": "Active Members Profiles", "icon": "groups", "value": active_profiles_val, "status": member_status_label, "change": member_change_label, "permission": True},
        {"title": "Today's Check-ins", "icon": "how_to_reg", "value": None, "status": "Awaiting attendance module", "change": None, "permission": True},
        {"title": "Monthly Revenue", "icon": "payments", "value": None, "status": "Awaiting billing module", "change": None, "permission": True},
        {"title": "Active Memberships", "icon": "card_membership", "value": m_metrics["active_memberships"], "status": f"{m_metrics['pending_memberships']} pending clearance", "change": f"{m_metrics['expiring_7_days']} expiring in 7d", "permission": True},
        {"title": "Expired Memberships", "icon": "event_busy", "value": m_metrics["expired_memberships"], "status": f"{m_metrics['frozen_memberships']} frozen", "change": None, "permission": True},
    ]

    analytics_sections = [
        ("Revenue Overview", "Revenue series will appear when billing is active."),
        ("Today's Attendance", "Check-ins will appear when attendance is active."),
        ("Upcoming Classes", "Class schedules will appear in a future phase."),
        ("Membership Expiry Alerts", f"{m_metrics['expiring_7_days']} subscriptions expiring within 7 days." if m_metrics['expiring_7_days'] else "No subscriptions expiring within 7 days."),
    ]

    context.update(gym_name=site_name(request), gym_timezone=zone, current_date=local_date(zone),
        application_version=settings.APP_VERSION, database_online=database_available(), brand=PALETTE,
        membership_metrics=m_metrics,
        kpi_cards=kpi_cards,
        analytics_sections=analytics_sections,
        branch_rows=branches[:5], branch_count=len(branches))
    return context
