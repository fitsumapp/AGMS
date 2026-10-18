from datetime import timedelta
from decimal import Decimal
from django.db.models import Count, Sum, Q
from django.utils import timezone

from apps.members.models import Member
from apps.notifications.models import (
    NotificationMessage,
    NotificationStatus,
    NotificationChannel,
    InAppNotification,
    DeliveryQueueItem,
    QueueStatus,
    BulkCampaign,
    TargetAudience,
)
from apps.notifications.permissions import get_user_notification_branches


def get_notification_dashboard_metrics(user, branch=None, start_date=None, end_date=None) -> dict:
    """Aggregate high-level KPI metrics for the notification dashboard."""
    user_branches = get_user_notification_branches(user)
    qs = NotificationMessage.objects.filter(
        Q(branch__in=user_branches) | Q(branch__isnull=True)
    )

    if branch:
        qs = qs.filter(branch=branch)
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)

    total_count = qs.count()
    sent_count = qs.filter(status=NotificationStatus.SENT).count()
    delivered_count = qs.filter(status=NotificationStatus.DELIVERED).count()
    failed_count = qs.filter(status=NotificationStatus.FAILED).count()
    queued_count = qs.filter(status__in=[NotificationStatus.QUEUED, NotificationStatus.SENDING]).count()

    total_cost = qs.aggregate(sum_cost=Sum("estimated_cost_etb"))["sum_cost"] or Decimal("0.00")
    total_segments = qs.aggregate(sum_segments=Sum("segment_count"))["sum_segments"] or 0

    terminal_count = delivered_count + failed_count
    delivery_rate = round((delivered_count / terminal_count * 100), 1) if terminal_count > 0 else 100.0

    # Queue health
    queue_pending = DeliveryQueueItem.objects.filter(status=QueueStatus.PENDING).count()
    queue_dead_letter = DeliveryQueueItem.objects.filter(status=QueueStatus.DEAD_LETTER).count()

    # In-App unread
    unread_in_app = InAppNotification.objects.filter(recipient=user, is_read=False).count()

    return {
        "total_count": total_count,
        "sent_count": sent_count,
        "delivered_count": delivered_count,
        "failed_count": failed_count,
        "queued_count": queued_count,
        "total_cost_etb": total_cost,
        "total_segments": total_segments,
        "delivery_rate": delivery_rate,
        "queue_pending": queue_pending,
        "queue_dead_letter": queue_dead_letter,
        "unread_in_app": unread_in_app,
    }


def get_communication_report_metrics(user, branch=None, category=None, channel=None, start_date=None, end_date=None) -> dict:
    """Comprehensive analytical breakdowns for communication reports."""
    user_branches = get_user_notification_branches(user)
    qs = NotificationMessage.objects.filter(
        Q(branch__in=user_branches) | Q(branch__isnull=True)
    )

    if branch:
        qs = qs.filter(branch=branch)
    if category:
        qs = qs.filter(category=category)
    if channel:
        qs = qs.filter(channel=channel)
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)

    by_channel = list(qs.values("channel").annotate(
        count=Count("id"),
        cost=Sum("estimated_cost_etb"),
        segments=Sum("segment_count")
    ).order_by("-count"))

    by_category = list(qs.values("category").annotate(
        count=Count("id"),
        cost=Sum("estimated_cost_etb")
    ).order_by("-count"))

    by_branch = list(qs.values("branch__name").annotate(
        count=Count("id"),
        cost=Sum("estimated_cost_etb")
    ).order_by("-count"))

    by_status = list(qs.values("status").annotate(
        count=Count("id")
    ).order_by("-count"))

    by_provider = list(qs.values("provider").annotate(
        count=Count("id")
    ).order_by("-count"))

    return {
        "total_messages": qs.count(),
        "total_cost_etb": qs.aggregate(Sum("estimated_cost_etb"))["estimated_cost_etb__sum"] or Decimal("0.00"),
        "total_segments": qs.aggregate(Sum("segment_count"))["segment_count__sum"] or 0,
        "by_channel": by_channel,
        "by_category": by_category,
        "by_branch": by_branch,
        "by_status": by_status,
        "by_provider": by_provider,
        "records": qs.select_related("branch", "recipient_member", "sender")[:100],
    }


def get_campaign_recipients(target_audience: str, branch=None, package=None) -> list[Member]:
    """Resolve distinct active Member recipients matching the target audience."""
    members_qs = Member.objects.filter(status="ACTIVE").exclude(phone="")

    if branch:
        members_qs = members_qs.filter(home_branch=branch)

    if target_audience == TargetAudience.ALL_ACTIVE_MEMBERS:
        # All currently active members
        members_qs = members_qs.filter(memberships__status="ACTIVE").distinct()
    elif target_audience == TargetAudience.BRANCH_ACTIVE_MEMBERS:
        if branch:
            members_qs = members_qs.filter(home_branch=branch, memberships__status="ACTIVE").distinct()
        else:
            members_qs = members_qs.filter(memberships__status="ACTIVE").distinct()
    elif target_audience == TargetAudience.EXPIRING_MEMBERS:
        today = timezone.localdate()
        target_expiry = today + timedelta(days=7)
        members_qs = members_qs.filter(
            memberships__status="ACTIVE",
            memberships__end_date__gte=today,
            memberships__end_date__lte=target_expiry,
        ).distinct()
    elif target_audience == TargetAudience.SELECTED_PACKAGE:
        if package:
            members_qs = members_qs.filter(
                memberships__status="ACTIVE",
                memberships__package=package,
            ).distinct()

    # Filter out opt-outs
    members_qs = members_qs.exclude(notification_preferences__opt_out_marketing=True)

    return list(members_qs)
