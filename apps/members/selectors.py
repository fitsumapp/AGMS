from django.db.models import Count
from django.utils import timezone

from apps.branches.services import accessible_branches
from .models import Member


def get_member_metrics(user, active_branch=None):
    """
    Calculate member metrics scoped to user's authorized branches and session active branch.
    Returns:
      - total_members: Total registered members in scope
      - active_profiles: Count of Active status members
      - inactive_profiles: Count of Inactive status members
      - suspended_members: Count of Suspended status members
      - new_this_month: Count of registrations in current month
      - by_branch: List of dicts [{'branch_code': ..., 'branch_name': ..., 'count': ...}]
    """
    allowed_branches = accessible_branches(user)
    if not allowed_branches.exists():
        return {
            "total_members": 0,
            "active_profiles": 0,
            "inactive_profiles": 0,
            "suspended_members": 0,
            "new_this_month": 0,
            "by_branch": [],
        }

    qs = Member.objects.filter(home_branch__in=allowed_branches)
    if active_branch:
        qs = qs.filter(home_branch=active_branch)

    # Status counts aggregation
    status_counts = qs.values("status").annotate(count=Count("id"))
    counts_map = {row["status"]: row["count"] for row in status_counts}

    total = sum(counts_map.values())
    active = counts_map.get(Member.MemberStatus.ACTIVE, 0)
    inactive = counts_map.get(Member.MemberStatus.INACTIVE, 0)
    suspended = counts_map.get(Member.MemberStatus.SUSPENDED, 0)

    # New this month
    now = timezone.now()
    new_this_month = qs.filter(joined_at__year=now.year, joined_at__month=now.month).count()

    # Registrations by branch
    by_branch = list(
        qs.values("home_branch__code", "home_branch__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    return {
        "total_members": total,
        "active_profiles": active,
        "inactive_profiles": inactive,
        "suspended_members": suspended,
        "new_this_month": new_this_month,
        "by_branch": [
            {
                "code": b["home_branch__code"],
                "name": b["home_branch__name"],
                "count": b["count"],
            }
            for b in by_branch
        ],
    }
