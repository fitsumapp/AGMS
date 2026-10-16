from datetime import date
from decimal import Decimal
from typing import Optional
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect

from apps.accounts.roles import has_role, is_owner
from apps.branches.services import accessible_branches, active_branch
from apps.core.models import GymProfile
from .branching import resolve_analytics_branch_scope
from .charts import (
    render_svg_bar_chart,
    render_svg_donut_chart,
    render_svg_horizontal_bar,
)
from .dates import (
    DateFilterRange,
    get_gym_timezone,
    get_today_in_gym_tz,
    resolve_date_filter,
)
from .models import ReportExportLog, ReportPreset
from .permissions import (
    can_access_report_center,
    can_export_reports,
    can_view_attendance_analytics,
    can_view_executive_dashboard,
    can_view_financial_analytics,
    can_view_membership_analytics,
)
from .reports import (
    REPORT_REGISTRY,
    export_to_csv,
    export_to_pdf,
    export_to_xlsx,
    get_available_reports_for_user,
)
from .selectors import (
    get_attendance_analytics,
    get_executive_dashboard_data,
    get_financial_analytics,
    get_membership_analytics,
)


def _get_common_context(request):
    """Common metadata, gym profile, and active timezone."""
    profile = GymProfile.objects.first()
    gym_name = profile.name if profile and profile.name else getattr(settings, "GYM_DISPLAY_NAME", "AGMS Gym ERP")
    tz = get_gym_timezone()
    return {
        "gym_name": gym_name,
        "gym_timezone": str(tz),
        "current_date": get_today_in_gym_tz(),
        "application_version": getattr(settings, "APP_VERSION", "1.0.0"),
    }


def _parse_filter_parameters(request):
    """
    Extract and validate date preset, custom date bounds, and requested branch.
    """
    date_preset = request.GET.get("date_preset", "this_month").strip()
    custom_start_raw = request.GET.get("start_date", "").strip()
    custom_end_raw = request.GET.get("end_date", "").strip()
    branch_id_param = request.GET.get("branch", "").strip()

    custom_start: Optional[date] = None
    custom_end: Optional[date] = None

    if date_preset == "custom" and custom_start_raw and custom_end_raw:
        try:
            custom_start = timezone.datetime.strptime(custom_start_raw, "%Y-%m-%d").date()
            custom_end = timezone.datetime.strptime(custom_end_raw, "%Y-%m-%d").date()
        except ValueError:
            date_preset = "this_month"

    date_range = resolve_date_filter(
        preset=date_preset,
        custom_start=custom_start,
        custom_end=custom_end,
    )

    branch_qs, selected_branch, branch_label = resolve_analytics_branch_scope(
        request.user,
        requested_branch_id=branch_id_param,
    )

    return date_range, branch_qs, selected_branch, branch_label


# =========================================================================
# 1. EXECUTIVE DASHBOARD VIEW
# =========================================================================

def executive_dashboard_view(request):
    """
    Unified Executive Overview with cross-domain KPIs, interactive date range
    filtering, branch filtering, and 10 responsive SVG charts.
    """
    if not request.user.is_authenticated or not request.user.is_staff:
        raise PermissionDenied("Authentication required.")

    date_range, branch_qs, selected_branch, branch_label = _parse_filter_parameters(request)
    context = _get_common_context(request)

    # Permission check for financial section:
    # If user cannot view financial (e.g. receptionist), we mask financial totals
    can_fin = can_view_financial_analytics(request.user)

    data = get_executive_dashboard_data(branch_qs, date_range)
    fin = data["financial"]
    mem = data["memberships"]
    att = data["attendance"]

    # Filter KPI cards by permissions
    visible_cards = [
        c for c in data["kpi_cards"]
        if c["category"] != "financial" or can_fin
    ]

    # Render Charts
    # Chart 1: Daily verified collections
    chart_daily_collections = (
        render_svg_bar_chart(
            fin["daily_collection_trend"],
            label_key="display",
            value_key="amount",
            height=220,
            bar_color="#0F766E",
            is_currency=True,
            empty_message="No verified collections in selected period",
        )
        if can_fin
        else None
    )

    # Chart 2: Monthly collection trend
    chart_monthly_collections = (
        render_svg_bar_chart(
            fin["monthly_collection_trend"],
            label_key="short",
            value_key="amount",
            height=220,
            bar_color="#0EA5E9",
            is_currency=True,
            empty_message="No monthly collection history available",
        )
        if can_fin
        else None
    )

    # Chart 3: New member registrations
    chart_member_registrations = render_svg_bar_chart(
        mem["registration_trend"],
        label_key="display",
        value_key="count",
        height=220,
        bar_color="#22C55E",
        is_currency=False,
        empty_message="No new member registrations in selected period",
    )

    # Chart 4: Membership status distribution
    chart_status_distribution = render_svg_donut_chart(
        mem["status_distribution"],
        label_key="status",
        value_key="count",
        colors=["#0F766E", "#EF4444", "#0EA5E9", "#D97706"],
        empty_message="No active subscriptions to display",
    )

    # Chart 5: Package popularity
    chart_package_popularity = render_svg_horizontal_bar(
        mem["package_popularity"],
        label_key="name",
        value_key="count",
        bar_color="#0F766E",
        empty_message="No packages assigned to active members",
    )

    # Chart 6: Attendance by day
    chart_daily_attendance = render_svg_bar_chart(
        att["daily_attendance_trend"],
        label_key="display",
        value_key="total",
        height=220,
        bar_color="#8B5CF6",
        is_currency=False,
        empty_message="No attendance recorded in selected period",
    )

    # Chart 7: Peak attendance hours
    chart_peak_hours = render_svg_bar_chart(
        att["peak_hours_distribution"],
        label_key="hour",
        value_key="count",
        height=220,
        bar_color="#F59E0B",
        is_currency=False,
        empty_message="No hourly visit distribution available",
    )

    # Chart 8: Payment method distribution
    chart_payment_methods = (
        render_svg_donut_chart(
            fin["collections_by_payment_method"],
            label_key="name",
            value_key="amount",
            colors=["#0F766E", "#0EA5E9", "#22C55E", "#D97706", "#8B5CF6"],
            empty_message="No verified payments by tender type",
        )
        if can_fin
        else None
    )

    # Chart 9: Branch performance comparison
    chart_branch_collections = (
        render_svg_horizontal_bar(
            fin["collections_by_branch"],
            label_key="name",
            value_key="amount",
            bar_color="#0F766E",
            empty_message="No branch collection figures available",
        )
        if can_fin
        else None
    )

    # Chart 10: Outstanding balances by aging category
    chart_aging_summary = (
        render_svg_bar_chart(
            fin["aging_summary"],
            label_key="bucket",
            value_key="amount",
            height=220,
            bar_color="#EF4444",
            is_currency=True,
            empty_message="Zero overdue invoices (Collections healthy)",
        )
        if can_fin
        else None
    )

    user_branches = list(accessible_branches(request.user))
    allow_all = is_owner(request.user) or request.user.is_superuser or len(user_branches) > 1

    context.update({
        "date_range": date_range,
        "selected_branch": selected_branch,
        "branch_label": branch_label,
        "branch_options": user_branches,
        "allow_all_branches": allow_all,
        "can_view_financial": can_fin,
        "financial": fin,
        "memberships": mem,
        "attendance": att,
        "kpi_cards": visible_cards,
        "chart_daily_collections": chart_daily_collections,
        "chart_monthly_collections": chart_monthly_collections,
        "chart_member_registrations": chart_member_registrations,
        "chart_status_distribution": chart_status_distribution,
        "chart_package_popularity": chart_package_popularity,
        "chart_daily_attendance": chart_daily_attendance,
        "chart_peak_hours": chart_peak_hours,
        "chart_payment_methods": chart_payment_methods,
        "chart_branch_collections": chart_branch_collections,
        "chart_aging_summary": chart_aging_summary,
    })

    return render(request, "admin/analytics/dashboard.html", context)


# =========================================================================
# 2. DOMAIN SPECIFIC ANALYTICS VIEWS
# =========================================================================

def financial_analytics_view(request):
    """Deep-dive Financial Analytics dashboard."""
    if not can_view_financial_analytics(request.user):
        raise PermissionDenied("You do not have permission to view gym financial analytics.")

    date_range, branch_qs, selected_branch, branch_label = _parse_filter_parameters(request)
    context = _get_common_context(request)
    fin = get_financial_analytics(branch_qs, date_range)

    user_branches = list(accessible_branches(request.user))
    allow_all = is_owner(request.user) or request.user.is_superuser or len(user_branches) > 1

    chart_daily = render_svg_bar_chart(
        fin["daily_collection_trend"],
        label_key="display",
        value_key="amount",
        height=260,
        bar_color="#0F766E",
        is_currency=True,
    )
    chart_monthly = render_svg_bar_chart(
        fin["monthly_collection_trend"],
        label_key="short",
        value_key="amount",
        height=260,
        bar_color="#0EA5E9",
        is_currency=True,
    )
    chart_methods = render_svg_donut_chart(
        fin["collections_by_payment_method"],
        label_key="name",
        value_key="amount",
    )
    chart_branches = render_svg_horizontal_bar(
        fin["collections_by_branch"],
        label_key="name",
        value_key="amount",
    )
    chart_aging = render_svg_bar_chart(
        fin["aging_summary"],
        label_key="bucket",
        value_key="amount",
        height=240,
        bar_color="#EF4444",
        is_currency=True,
    )

    context.update({
        "date_range": date_range,
        "selected_branch": selected_branch,
        "branch_label": branch_label,
        "branch_options": user_branches,
        "allow_all_branches": allow_all,
        "financial": fin,
        "chart_daily": chart_daily,
        "chart_monthly": chart_monthly,
        "chart_methods": chart_methods,
        "chart_branches": chart_branches,
        "chart_aging": chart_aging,
    })
    return render(request, "admin/analytics/financial.html", context)


def membership_analytics_view(request):
    """Deep-dive Membership & Subscription Analytics."""
    if not can_view_membership_analytics(request.user):
        raise PermissionDenied("You do not have permission to view membership analytics.")

    date_range, branch_qs, selected_branch, branch_label = _parse_filter_parameters(request)
    context = _get_common_context(request)
    mem = get_membership_analytics(branch_qs, date_range)

    user_branches = list(accessible_branches(request.user))
    allow_all = is_owner(request.user) or request.user.is_superuser or len(user_branches) > 1

    chart_growth = render_svg_bar_chart(
        mem["registration_trend"],
        label_key="display",
        value_key="count",
        height=260,
        bar_color="#22C55E",
    )
    chart_status = render_svg_donut_chart(
        mem["status_distribution"],
        label_key="status",
        value_key="count",
    )
    chart_packages = render_svg_horizontal_bar(
        mem["package_popularity"],
        label_key="name",
        value_key="count",
    )

    context.update({
        "date_range": date_range,
        "selected_branch": selected_branch,
        "branch_label": branch_label,
        "branch_options": user_branches,
        "allow_all_branches": allow_all,
        "memberships": mem,
        "chart_growth": chart_growth,
        "chart_status": chart_status,
        "chart_packages": chart_packages,
    })
    return render(request, "admin/analytics/memberships.html", context)


def attendance_analytics_view(request):
    """Deep-dive Attendance & Foot Traffic Analytics."""
    if not can_view_attendance_analytics(request.user):
        raise PermissionDenied("You do not have permission to view attendance analytics.")

    date_range, branch_qs, selected_branch, branch_label = _parse_filter_parameters(request)
    context = _get_common_context(request)
    att = get_attendance_analytics(branch_qs, date_range)

    user_branches = list(accessible_branches(request.user))
    allow_all = is_owner(request.user) or request.user.is_superuser or len(user_branches) > 1

    chart_daily = render_svg_bar_chart(
        att["daily_attendance_trend"],
        label_key="display",
        value_key="total",
        height=260,
        bar_color="#8B5CF6",
    )
    chart_peak = render_svg_bar_chart(
        att["peak_hours_distribution"],
        label_key="hour",
        value_key="count",
        height=260,
        bar_color="#F59E0B",
    )
    method_data = [
        {"method": "QR Code", "count": att["qr_checkins"]},
        {"method": "Manual Search", "count": att["manual_checkins"]},
    ]
    chart_methods = render_svg_donut_chart(
        method_data,
        label_key="method",
        value_key="count",
        colors=["#0F766E", "#0EA5E9"],
    )

    context.update({
        "date_range": date_range,
        "selected_branch": selected_branch,
        "branch_label": branch_label,
        "branch_options": user_branches,
        "allow_all_branches": allow_all,
        "attendance": att,
        "chart_daily": chart_daily,
        "chart_peak": chart_peak,
        "chart_methods": chart_methods,
    })
    return render(request, "admin/analytics/attendance.html", context)


# =========================================================================
# 3. REPORT CENTER (22 Reports + Live Preview + PDF/XLSX/CSV Export)
# =========================================================================

@csrf_protect
def report_center_view(request):
    """
    Centralized Report Hub supporting report selection, live paginated preview,
    and instantaneous PDF, Excel (XLSX), and CSV exports with audit logging.
    """
    if not can_access_report_center(request.user):
        raise PermissionDenied("You do not have permission to access the Report Center.")

    available_reports = get_available_reports_for_user(request.user)
    if not available_reports:
        raise PermissionDenied("No reports available for your assigned role.")

    # Selected report
    selected_code_param = request.GET.get("report", "").strip()
    if selected_code_param:
        report_def = REPORT_REGISTRY.get(selected_code_param)
        if not report_def or report_def not in available_reports:
            raise PermissionDenied("You do not have permission to view or export this report.")
        selected_code = report_def.code
    else:
        report_def = available_reports[0]
        selected_code = report_def.code

    date_range, branch_qs, selected_branch, branch_label = _parse_filter_parameters(request)
    context = _get_common_context(request)

    # Extract report data rows
    rows = report_def.extractor(branch_qs, date_range)

    # Check for Export Action
    export_format = request.GET.get("export", "").strip().lower()

    if export_format in ("pdf", "xlsx", "csv"):
        if not can_export_reports(request.user):
            raise PermissionDenied("You do not have permission to export reports.")

        # Financial export permission check
        if report_def.category == "financial" and not can_view_financial_analytics(request.user):
            raise PermissionDenied("Unauthorized: You cannot export financial reports.")

        # Log the export for security & compliance audit
        ReportExportLog.objects.create(
            actor=request.user,
            report_type=report_def.code,
            report_title=report_def.title,
            export_format=export_format.upper(),
            branch=selected_branch,
            date_preset=date_range.preset,
            start_date=date_range.start_date,
            end_date=date_range.end_date,
            row_count=len(rows),
            ip_address=request.META.get("REMOTE_ADDR"),
        )

        gym_name = context["gym_name"]

        if export_format == "pdf":
            return export_to_pdf(report_def, rows, gym_name, branch_label, date_range.label, user=request.user)
        elif export_format == "xlsx":
            return export_to_xlsx(report_def, rows, gym_name, branch_label, date_range.label)
        elif export_format == "csv":
            return export_to_csv(report_def, rows, gym_name, branch_label, date_range.label)

    # Pagination for live web preview
    page_number = request.GET.get("page", 1)
    paginator = Paginator(rows, 25)
    page_obj = paginator.get_page(page_number)

    # Calculate preview totals if currency columns exist
    column_totals = {}
    for col in report_def.columns:
        if col.is_currency:
            tot = sum(
                Decimal(str(r.get(col.key) or "0.00"))
                for r in rows
                if r.get(col.key) is not None
            )
            column_totals[col.key] = tot

    user_branches = list(accessible_branches(request.user))
    allow_all = is_owner(request.user) or request.user.is_superuser or len(user_branches) > 1

    # Group available reports by category
    reports_by_category = {
        "financial": [r for r in available_reports if r.category == "financial"],
        "memberships": [r for r in available_reports if r.category == "memberships"],
        "attendance": [r for r in available_reports if r.category == "attendance"],
    }

    context.update({
        "report_def": report_def,
        "selected_code": selected_code,
        "available_reports": available_reports,
        "reports_by_category": reports_by_category,
        "date_range": date_range,
        "selected_branch": selected_branch,
        "branch_label": branch_label,
        "branch_options": user_branches,
        "allow_all_branches": allow_all,
        "rows": page_obj,
        "total_row_count": len(rows),
        "column_totals": column_totals,
        "paginator": paginator,
        "page_obj": page_obj,
    })

    return render(request, "admin/analytics/report_center.html", context)
