from datetime import timedelta
from django.db.models import Count, Q

from apps.accounts.roles import is_owner
from apps.branches.services import accessible_branches
from .dates import get_gym_today
from .models import Membership


def get_membership_metrics(user, active_branch=None) -> dict:
    """
    Compute branch-scoped membership statistics for the dashboard and analytics.
    Enforces role-based branch scoping:
    - Gym Owner: sees all branches or filtered by active_branch
    - Branch Manager / Receptionist: restricted to authorized branches
    """
    branches = accessible_branches(user)
    qs = Membership.objects.filter(home_branch__in=branches)

    if active_branch:
        qs = qs.filter(home_branch=active_branch)

    today = get_gym_today()
    seven_days = today + timedelta(days=7)

    aggregations = qs.aggregate(
        active_memberships=Count("id", filter=Q(status=Membership.Status.ACTIVE)),
        pending_memberships=Count("id", filter=Q(status=Membership.Status.PENDING)),
        frozen_memberships=Count("id", filter=Q(status=Membership.Status.FROZEN)),
        expired_memberships=Count("id", filter=Q(status=Membership.Status.EXPIRED)),
        expiring_7_days=Count(
            "id",
            filter=Q(
                status=Membership.Status.ACTIVE,
                end_date__gt=today,
                end_date__lte=seven_days,
            ),
        ),
    )

    by_package = list(
        qs.filter(status=Membership.Status.ACTIVE)
        .values("package__name")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )

    by_branch = list(
        qs.filter(status=Membership.Status.ACTIVE)
        .values("home_branch__name")
        .annotate(total=Count("id"))
        .order_by("-total")[:5]
    )

    return {
        "active_memberships": aggregations["active_memberships"] or 0,
        "pending_memberships": aggregations["pending_memberships"] or 0,
        "frozen_memberships": aggregations["frozen_memberships"] or 0,
        "expired_memberships": aggregations["expired_memberships"] or 0,
        "expiring_7_days": aggregations["expiring_7_days"] or 0,
        "memberships_by_package": by_package,
        "memberships_by_branch": by_branch,
    }
