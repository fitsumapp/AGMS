from typing import Dict
from apps.analytics.dates import DateFilterRange
from .financial import get_financial_analytics
from .memberships import get_membership_analytics
from .attendance import get_attendance_analytics


def get_executive_dashboard_data(
    branch_qs,
    date_range: DateFilterRange,
) -> Dict:
    """
    Aggregate cross-domain metrics for the Executive Overview Dashboard.
    Ensures date range and branch scope are applied identically across all domains.
    """
    fin = get_financial_analytics(branch_qs, date_range)
    mem = get_membership_analytics(branch_qs, date_range)
    att = get_attendance_analytics(branch_qs, date_range)

    # Top-level executive KPI cards
    kpi_cards = [
        # Financial
        {
            "category": "financial",
            "title": "Verified Collections",
            "value": f"{fin['total_verified_collections']:,.2f} ETB",
            "status": f"Net: {fin['net_cash_collections']:,.2f} ETB (after refunds)",
            "change": f"{fin['verified_payment_count']} verified payments",
            "icon": "payments",
            "color": "teal",
        },
        {
            "category": "financial",
            "title": "Outstanding Balances",
            "value": f"{fin['total_outstanding_balances']:,.2f} ETB",
            "status": f"{fin['unpaid_invoice_count']} unpaid invoices",
            "change": f"{fin['pending_payments_count']} pending verification",
            "icon": "warning",
            "color": "amber",
        },
        # Memberships
        {
            "category": "memberships",
            "title": "Active Memberships",
            "value": f"{mem['active_memberships']:,}",
            "status": f"{mem['total_registered_members']:,} total registered members",
            "change": f"+{mem['new_members_in_range']} new in period",
            "icon": "card_membership",
            "color": "teal",
        },
        {
            "category": "memberships",
            "title": "Expiring Soon",
            "value": f"{mem['expiring_7_days']:,}",
            "status": f"{mem['expiring_30_days']:,} expiring in 30 days",
            "change": f"{mem['renewals_count']} renewed in period",
            "icon": "alarm",
            "color": "sky",
        },
        # Attendance
        {
            "category": "attendance",
            "title": "Check-ins (Period)",
            "value": f"{att['total_checkins']:,}",
            "status": f"{att['currently_inside']} currently inside",
            "change": f"{att['unique_visitors']} unique visitors",
            "icon": "how_to_reg",
            "color": "emerald",
        },
        {
            "category": "attendance",
            "title": "QR vs Manual Entry",
            "value": f"{att['qr_checkins']} QR / {att['manual_checkins']} Man",
            "status": f"QR: {att['qr_checkin_pct']}% | Manual: {att['manual_checkin_pct']}%",
            "change": f"Avg verified session: {att['avg_verified_duration_minutes']} min",
            "icon": "qr_code_scanner",
            "color": "purple",
        },
    ]

    return {
        "financial": fin,
        "memberships": mem,
        "attendance": att,
        "kpi_cards": kpi_cards,
    }
