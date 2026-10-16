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

    try:
        from apps.billing.selectors import get_billing_metrics
        b_metrics = get_billing_metrics(request.user, active_branch=selected_b)
    except Exception:
        from decimal import Decimal
        b_metrics = {
            "today_collections": Decimal("0.00"),
            "monthly_collections": Decimal("0.00"),
            "net_collections_month": Decimal("0.00"),
            "total_invoiced_month": Decimal("0.00"),
            "outstanding_balances": Decimal("0.00"),
            "pending_payments_count": 0,
            "pending_payments_amount": Decimal("0.00"),
            "refund_totals_month": Decimal("0.00"),
            "collections_by_branch": [],
            "collections_by_method": [],
        }

    try:
        from apps.attendance.selectors import get_attendance_metrics
        att_metrics = get_attendance_metrics(request.user, active_branch=selected_b)
    except Exception:
        att_metrics = {
            "today_checkins": 0,
            "currently_inside": 0,
            "unique_visitors_today": 0,
            "denied_attempts_today": 0,
        }

    kpi_cards = [
        {"title": "Active Members Profiles", "icon": "groups", "value": active_profiles_val, "status": member_status_label, "change": member_change_label, "permission": True},
        {"title": "Today's Check-ins", "icon": "how_to_reg", "value": att_metrics["today_checkins"], "status": f"{att_metrics['currently_inside']} currently inside", "change": f"{att_metrics['unique_visitors_today']} unique today", "permission": True},
        {"title": "Monthly Revenue", "icon": "payments", "value": f"{b_metrics['monthly_collections']:,.2f} ETB", "status": f"Net: {b_metrics['net_collections_month']:,.2f} ETB", "change": f"Today: {b_metrics['today_collections']:,.2f} ETB", "permission": True},
        {"title": "Active Memberships", "icon": "card_membership", "value": m_metrics["active_memberships"], "status": f"{m_metrics['pending_memberships']} pending clearance", "change": f"{m_metrics['expiring_7_days']} expiring in 7d", "permission": True},
        {"title": "Expired Memberships", "icon": "event_busy", "value": m_metrics["expired_memberships"], "status": f"{m_metrics['frozen_memberships']} frozen", "change": None, "permission": True},
        {"title": "Outstanding Balances", "icon": "warning", "value": f"{b_metrics['outstanding_balances']:,.2f} ETB", "status": f"Pending: {b_metrics['pending_payments_count']} payments", "change": None, "permission": True},
    ]

    analytics_sections = [
        ("Revenue Overview", f"Verified Collections: {b_metrics['monthly_collections']:,.2f} ETB | Refunds: {b_metrics['refund_totals_month']:,.2f} ETB | Net: {b_metrics['net_collections_month']:,.2f} ETB"),
        ("Today's Attendance", f"{att_metrics['today_checkins']} entries today | {att_metrics['currently_inside']} currently inside | {att_metrics['denied_attempts_today']} denied attempts"),
        ("Upcoming Classes", "Class schedules will appear in a future phase."),
        ("Membership Expiry Alerts", f"{m_metrics['expiring_7_days']} subscriptions expiring within 7 days." if m_metrics['expiring_7_days'] else "No subscriptions expiring within 7 days."),
    ]

    context.update(gym_name=site_name(request), gym_timezone=zone, current_date=local_date(zone),
        application_version=settings.APP_VERSION, database_online=database_available(), brand=PALETTE,
        membership_metrics=m_metrics,
        billing_metrics=b_metrics,
        kpi_cards=kpi_cards,
        analytics_sections=analytics_sections,
        branch_rows=branches[:5], branch_count=len(branches))
    return context
