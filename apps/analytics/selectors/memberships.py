from collections import defaultdict
from datetime import timedelta
from typing import Dict, List
from django.db.models import Count, Q
from django.utils import timezone

from apps.analytics.dates import DateFilterRange, get_gym_timezone, get_today_in_gym_tz
from apps.members.models import Member
from apps.memberships.models import Membership, MembershipPackage, MembershipRenewal


def get_membership_analytics(
    branch_qs,
    date_range: DateFilterRange,
) -> Dict:
    """
    Compute member and membership analytics.
    Strictly distinguishes unique human members from subscriptions.
    """
    today = get_today_in_gym_tz()
    tz = get_gym_timezone()

    member_qs = Member.objects.filter(home_branch__in=branch_qs)
    membership_qs = Membership.objects.filter(home_branch__in=branch_qs)

    # 1. Unique Member Counts
    total_registered_members = member_qs.count()
    active_member_profiles = member_qs.filter(status=Member.MemberStatus.ACTIVE).count()
    inactive_member_profiles = member_qs.filter(status=Member.MemberStatus.INACTIVE).count()
    suspended_member_profiles = member_qs.filter(status=Member.MemberStatus.SUSPENDED).count()

    # 2. New Members in Date Range
    new_members_in_range = member_qs.filter(
        joined_at__gte=date_range.start_datetime,
        joined_at__lte=date_range.end_datetime,
    ).count()

    # 3. Subscription Status Counts (Current snapshot)
    active_m_qs = membership_qs.filter(status=Membership.Status.ACTIVE)
    active_memberships = active_m_qs.count()
    unique_active_members = active_m_qs.values("member_id").distinct().count()
    expired_memberships = membership_qs.filter(status=Membership.Status.EXPIRED).count()
    frozen_memberships = membership_qs.filter(status=Membership.Status.FROZEN).count()
    pending_memberships = membership_qs.filter(status=Membership.Status.PENDING).count()
    cancelled_memberships = membership_qs.filter(status=Membership.Status.CANCELLED).count()

    # 4. Expiry Horizons (Active memberships)
    seven_days_out = today + timedelta(days=7)
    thirty_days_out = today + timedelta(days=30)

    expiring_7_days = membership_qs.filter(
        status=Membership.Status.ACTIVE,
        end_date__gt=today,
        end_date__lte=seven_days_out,
    ).count()

    expiring_30_days = membership_qs.filter(
        status=Membership.Status.ACTIVE,
        end_date__gt=today,
        end_date__lte=thirty_days_out,
    ).count()

    # 5. Renewals in Date Range
    renewal_qs = MembershipRenewal.objects.filter(
        membership__home_branch__in=branch_qs,
        renewal_status=MembershipRenewal.RenewalStatus.COMPLETED,
        created_at__gte=date_range.start_datetime,
        created_at__lte=date_range.end_datetime,
    )
    renewals_count = renewal_qs.count()

    # 6. Package Popularity (Active subscriptions)
    package_data = []
    package_aggregates = (
        membership_qs.filter(status=Membership.Status.ACTIVE)
        .values("package__name", "package__code")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    for p in package_aggregates:
        total = p["total"]
        pct = (
            round((total / active_memberships) * 100, 1)
            if active_memberships > 0
            else 0.0
        )
        package_data.append({
            "name": p["package__name"] or "Custom Plan",
            "code": p["package__code"] or "CUSTOM",
            "count": total,
            "percentage": pct,
        })

    # 7. Status Distribution
    status_distribution = [
        {"status": "Active", "count": active_memberships, "color": "#0F766E"},
        {"status": "Expired", "count": expired_memberships, "color": "#EF4444"},
        {"status": "Frozen", "count": frozen_memberships, "color": "#0EA5E9"},
        {"status": "Pending", "count": pending_memberships, "color": "#D97706"},
    ]

    # 8. Member Registration Growth Trend across filtered date range
    reg_map = defaultdict(int)
    for joined in member_qs.filter(
        joined_at__gte=date_range.start_datetime,
        joined_at__lte=date_range.end_datetime,
    ).values_list("joined_at", flat=True):
        if joined:
            d = joined.astimezone(tz).date()
            reg_map[d] += 1

    registration_trend = []
    cur_d = date_range.start_date
    while cur_d <= date_range.end_date:
        registration_trend.append({
            "date": cur_d.strftime("%Y-%m-%d"),
            "display": cur_d.strftime("%b %d"),
            "count": reg_map[cur_d],
        })
        cur_d += timedelta(days=1)

    # 9. Branch Membership Breakdown
    branch_distribution = list(
        membership_qs.filter(status=Membership.Status.ACTIVE)
        .values("home_branch__name", "home_branch__code")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    return {
        "total_registered_members": total_registered_members,
        "unique_active_members": unique_active_members,
        "active_member_profiles": active_member_profiles,
        "inactive_member_profiles": inactive_member_profiles,
        "suspended_member_profiles": suspended_member_profiles,
        "new_members_in_range": new_members_in_range,
        "new_registrations_period": new_members_in_range,
        "active_memberships": active_memberships,
        "expired_memberships": expired_memberships,
        "frozen_memberships": frozen_memberships,
        "pending_memberships": pending_memberships,
        "cancelled_memberships": cancelled_memberships,
        "expiring_7_days": expiring_7_days,
        "expiring_in_7_days": expiring_7_days,
        "expiring_30_days": expiring_30_days,
        "expiring_in_30_days": expiring_30_days,
        "renewals_count": renewals_count,
        "renewals_period": renewals_count,
        "package_popularity": package_data,
        "status_distribution": status_distribution,
        "registration_trend": registration_trend,
        "branch_distribution": branch_distribution,
    }
