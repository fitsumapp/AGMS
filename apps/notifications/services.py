from datetime import timedelta
from decimal import Decimal
import logging
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.core.models import GymProfile
from apps.members.models import Member
from apps.notifications.models import (
    NotificationMessage,
    NotificationChannel,
    NotificationPriority,
    NotificationStatus,
    NotificationCategory,
    DeliveryQueueItem,
    QueueStatus,
    InAppNotification,
    ScheduledNotification,
    ScheduledStatus,
    MessageTemplate,
    NotificationPreference,
    BulkCampaign,
    CampaignStatus,
    TriggerEvent,
    ProviderConfig,
)
from apps.notifications.providers.registry import get_sms_provider, get_email_provider
from apps.notifications.validators import (
    normalize_ethiopian_phone,
    calculate_sms_segments,
    render_template_safely,
)

logger = logging.getLogger(__name__)


def is_channel_allowed(member=None, user=None, channel=NotificationChannel.SMS, is_marketing=False) -> bool:
    """Check if recipient has permitted this channel and marketing type."""
    pref = None
    if member:
        pref = NotificationPreference.objects.filter(member=member).first()
    elif user:
        pref = NotificationPreference.objects.filter(user=user).first()

    if not pref:
        return True  # default allowed

    if is_marketing and pref.opt_out_marketing:
        return False

    if channel == NotificationChannel.SMS and not pref.allow_sms:
        return False
    if channel == NotificationChannel.EMAIL and not pref.allow_email:
        return False
    if channel == NotificationChannel.IN_APP and not pref.allow_in_app:
        return False

    return True


def create_and_queue_notification(
    *,
    recipient_address: str,
    body: str,
    channel: str = NotificationChannel.SMS,
    category: str = NotificationCategory.SYSTEM_OPERATIONAL,
    priority: str = NotificationPriority.NORMAL,
    subject: str = "",
    branch: Branch = None,
    recipient_member: Member = None,
    recipient_user: User = None,
    sender: User = None,
    campaign: BulkCampaign = None,
    idempotency_key: str = "",
    scheduled_at=None,
    send_immediately: bool = False,
) -> NotificationMessage:
    """
    Core function to build a NotificationMessage and enqueue it into DeliveryQueueItem safely.
    Guarantees idempotency via idempotency_key.
    """
    if idempotency_key:
        existing = NotificationMessage.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    # Normalization & preferences
    clean_address = str(recipient_address).strip()
    if channel == NotificationChannel.SMS:
        try:
            clean_address = normalize_ethiopian_phone(clean_address)
        except Exception as exc:
            logger.warning("Phone normalization failed for %s: %s", recipient_address, exc)

    # Check preference
    is_marketing = (category == NotificationCategory.MARKETING_BULK)
    if not is_channel_allowed(member=recipient_member, user=recipient_user, channel=channel, is_marketing=is_marketing):
        # Create cancelled audit record
        return NotificationMessage.objects.create(
            branch=branch,
            recipient_member=recipient_member,
            recipient_user=recipient_user,
            recipient_address=clean_address,
            channel=channel,
            category=category,
            priority=priority,
            subject=subject,
            body=body,
            status=NotificationStatus.CANCELLED,
            error_message="Recipient opted out of this channel or marketing notifications.",
            idempotency_key=idempotency_key,
            sender=sender,
            campaign=campaign,
        )

    # Segment and cost calculation
    segments_info = calculate_sms_segments(body)
    segment_count = segments_info["segment_count"] if channel == NotificationChannel.SMS else 1

    # Cost lookup
    provider_config = None
    if branch:
        provider_config = ProviderConfig.objects.filter(branch=branch, is_active=True).first()
    if not provider_config:
        provider_config = ProviderConfig.objects.filter(branch__isnull=True, is_active=True, is_default=True).first()

    cost_per_sms = provider_config.cost_per_sms_etb if provider_config else Decimal("0.35")
    estimated_cost = (cost_per_sms * Decimal(segment_count)) if channel == NotificationChannel.SMS else Decimal("0.00")
    provider_name = provider_config.get_provider_type_display() if provider_config else "MOCK"

    message = NotificationMessage.objects.create(
        branch=branch,
        recipient_member=recipient_member,
        recipient_user=recipient_user,
        recipient_address=clean_address,
        channel=channel,
        category=category,
        priority=priority,
        subject=subject,
        body=body,
        status=NotificationStatus.QUEUED,
        provider=provider_name,
        segment_count=segment_count,
        estimated_cost_etb=estimated_cost,
        idempotency_key=idempotency_key,
        sender=sender,
        campaign=campaign,
    )

    # Enqueue
    priority_map = {
        NotificationPriority.URGENT: 10,
        NotificationPriority.HIGH: 25,
        NotificationPriority.NORMAL: 50,
        NotificationPriority.LOW: 100,
    }
    q_priority = priority_map.get(priority, 50)
    queue_scheduled = scheduled_at or timezone.now()

    queue_item = DeliveryQueueItem.objects.create(
        notification=message,
        scheduled_at=queue_scheduled,
        priority=q_priority,
        next_retry_at=queue_scheduled,
    )

    if send_immediately and (not scheduled_at or scheduled_at <= timezone.now()):
        dispatch_single_queue_item(queue_item)

    return message


def dispatch_single_queue_item(queue_item: DeliveryQueueItem) -> bool:
    """Dispatches a single queue item via its channel provider with atomic status transition."""
    notification = queue_item.notification
    channel = notification.channel

    notification.status = NotificationStatus.SENDING
    notification.save(update_fields=["status", "updated_at"])

    success = False
    error_msg = ""
    provider_id = ""
    raw_resp = {}

    try:
        if channel == NotificationChannel.SMS:
            provider = get_sms_provider(notification.branch)
            resp = provider.send_sms(to=notification.recipient_address, message=notification.body)
            success = resp.success
            provider_id = resp.provider_message_id
            raw_resp = resp.raw_response
            error_msg = resp.error
            final_status = NotificationStatus.SENT if success else NotificationStatus.FAILED

        elif channel == NotificationChannel.EMAIL:
            provider = get_email_provider()
            resp = provider.send_email(
                to=notification.recipient_address,
                subject=notification.subject or "Notification from AGMS Gym",
                message=notification.body,
            )
            success = resp.success
            provider_id = resp.provider_message_id
            raw_resp = resp.raw_response
            error_msg = resp.error
            final_status = NotificationStatus.SENT if success else NotificationStatus.FAILED

        elif channel == NotificationChannel.IN_APP:
            if notification.recipient_user:
                InAppNotification.objects.create(
                    recipient=notification.recipient_user,
                    branch=notification.branch,
                    title=notification.subject or "System Notification",
                    message=notification.body,
                    category=notification.category,
                )
                success = True
                final_status = NotificationStatus.DELIVERED
            else:
                success = False
                error_msg = "No recipient user designated for in-app message."
                final_status = NotificationStatus.FAILED

    except Exception as exc:
        success = False
        error_msg = f"Dispatch exception: {str(exc)}"
        final_status = NotificationStatus.FAILED

    now = timezone.now()
    if success:
        notification.status = final_status
        notification.sent_at = now
        if final_status == NotificationStatus.DELIVERED:
            notification.delivered_at = now
        notification.provider_message_id = provider_id
        notification.provider_response = raw_resp
        notification.error_message = ""
        notification.save(update_fields=[
            "status", "sent_at", "delivered_at", "provider_message_id",
            "provider_response", "error_message", "updated_at"
        ])

        queue_item.status = QueueStatus.COMPLETED
        queue_item.save(update_fields=["status", "updated_at"])
        return True
    else:
        queue_item.retry_count += 1
        if queue_item.retry_count >= queue_item.max_retries:
            queue_item.status = QueueStatus.DEAD_LETTER
            notification.status = NotificationStatus.FAILED
            notification.failed_at = now
            notification.error_message = error_msg
            notification.provider_response = raw_resp
            notification.save(update_fields=["status", "failed_at", "error_message", "provider_response", "updated_at"])
        else:
            # Exponential backoff
            delay_sec = queue_item.backoff_factor * (2 ** (queue_item.retry_count - 1))
            queue_item.next_retry_at = now + timedelta(seconds=delay_sec)
            queue_item.status = QueueStatus.PENDING
            notification.status = NotificationStatus.QUEUED
            notification.error_message = f"Attempt {queue_item.retry_count} failed: {error_msg}"
            notification.save(update_fields=["status", "error_message", "updated_at"])

        queue_item.save(update_fields=["retry_count", "status", "next_retry_at", "updated_at"])
        return False


def process_queue(batch_size: int = 50) -> dict:
    """
    Process pending queue items in priority and scheduled order.
    Employs pessimistic row-locking select_for_update(skip_locked=True)
    for safe multi-worker concurrency on PostgreSQL.
    """
    now = timezone.now()
    processed = 0
    succeeded = 0
    failed = 0

    with transaction.atomic():
        items = list(
            DeliveryQueueItem.objects.select_for_update(skip_locked=True)
            .filter(
                status=QueueStatus.PENDING,
                next_retry_at__lte=now,
            )
            .order_by("priority", "scheduled_at")[:batch_size]
        )

        for item in items:
            item.status = QueueStatus.PROCESSING
            item.locked_at = now
            item.locked_by = "worker-pid"
            item.save(update_fields=["status", "locked_at", "locked_by", "updated_at"])

    for item in items:
        ok = dispatch_single_queue_item(item)
        processed += 1
        if ok:
            succeeded += 1
        else:
            failed += 1

    return {"processed": processed, "succeeded": succeeded, "failed": failed}


def get_gym_name() -> str:
    profile = GymProfile.objects.first()
    return profile.name if profile and profile.name else "AGMS Gym"


# ---------------------------------------------------------
# Event-triggered Automated Reminders
# ---------------------------------------------------------

def trigger_membership_notification(
    event_type: str,
    membership,
    extra_context: dict = None,
    channel: str = NotificationChannel.SMS,
    send_immediately: bool = True,
):
    """
    Trigger automated membership notification with safe placeholders & deduplication.
    Supported: EXPIRY_7D, EXPIRY_3D, EXPIRY_1D, EXPIRED, ACTIVATED, RENEWED, FROZEN, REACTIVATED.
    """
    member = membership.member
    if not member or not member.phone:
        return None

    branch = getattr(membership, "home_branch", None) or getattr(membership, "branch", None) or getattr(member, "home_branch", None)
    today = timezone.localdate()
    idempotency_key = f"mem-{event_type}-{membership.id}-{today}"

    # Build context
    gym_name = get_gym_name()
    branch_name = branch.name if branch else "Main Branch"
    end_date_str = str(membership.end_date)
    days_left = max(0, (membership.end_date - today).days) if membership.end_date else 0

    context = {
        "member_name": member.first_name,
        "gym_name": gym_name,
        "branch_name": branch_name,
        "package_name": membership.package.name if membership.package else "Membership",
        "end_date": end_date_str,
        "days_left": days_left,
    }
    if extra_context:
        context.update(extra_context)

    # Resolve template or use clean default
    template = MessageTemplate.objects.filter(code=event_type, is_active=True).first()
    if template:
        body = render_template_safely(template.body, context)
        subject = render_template_safely(template.subject, context)
    else:
        # Defaults
        if event_type == TriggerEvent.MEMBERSHIP_EXPIRY_7D:
            body = f"Hello {member.first_name}, your {context['package_name']} at {gym_name} will expire in 7 days on {end_date_str}. Please renew to keep training!"
        elif event_type == TriggerEvent.MEMBERSHIP_EXPIRY_3D:
            body = f"Dear {member.first_name}, only 3 days left until your {gym_name} membership expires ({end_date_str}). Renew today to avoid interruption!"
        elif event_type == TriggerEvent.MEMBERSHIP_EXPIRY_1D:
            body = f"Reminder {member.first_name}: Your {gym_name} membership expires tomorrow ({end_date_str}). Visit the front desk to renew!"
        elif event_type == TriggerEvent.MEMBERSHIP_EXPIRED:
            body = f"Hello {member.first_name}, your {gym_name} membership expired on {end_date_str}. We would love to see you back — renew anytime!"
        elif event_type == TriggerEvent.MEMBERSHIP_ACTIVATED:
            body = f"Welcome to {gym_name}, {member.first_name}! Your {context['package_name']} is now ACTIVE until {end_date_str}. Enjoy your workouts!"
        elif event_type == TriggerEvent.MEMBERSHIP_RENEWED:
            body = f"Thank you {member.first_name}! Your {gym_name} membership has been renewed until {end_date_str}."
        elif event_type == TriggerEvent.MEMBERSHIP_FROZEN:
            body = f"Hello {member.first_name}, your {gym_name} membership freeze has been approved."
        elif event_type == TriggerEvent.MEMBERSHIP_REACTIVATED:
            body = f"Welcome back {member.first_name}! Your {gym_name} membership has been reactivated and expires on {end_date_str}."
        else:
            body = f"Update on your {gym_name} membership."
        subject = f"{gym_name} Membership Update"

    return create_and_queue_notification(
        recipient_address=member.phone,
        body=body,
        channel=channel,
        category=NotificationCategory.MEMBERSHIP,
        priority=NotificationPriority.NORMAL,
        subject=subject,
        branch=branch,
        recipient_member=member,
        idempotency_key=idempotency_key,
        send_immediately=send_immediately,
    )


def trigger_billing_notification(
    event_type: str,
    invoice=None,
    payment=None,
    refund=None,
    channel: str = NotificationChannel.SMS,
    send_immediately: bool = True,
):
    """
    Trigger billing & payment notification with authoritative Phase 05 billing checks.
    NEVER sends payment-success message for unverified payments.
    """
    target_member = None
    branch = None
    ref_id = ""

    if payment:
        target_member = getattr(payment, "member", None) or (payment.invoice.member if getattr(payment, "invoice", None) else None)
        branch = getattr(payment, "branch", None) or (payment.invoice.branch if getattr(payment, "invoice", None) else None)
        ref_id = f"pay-{payment.id}"
        # Crucial security rule: Never send payment confirmation for unverified/pending payment
        if event_type == TriggerEvent.PAYMENT_CONFIRMED:
            pay_status = getattr(payment, "status", "")
            if pay_status not in ("COMPLETED", "VERIFIED"):
                logger.warning("Refusing to send payment confirmation for unverified payment #%s (status=%s)", payment.id, pay_status)
                return None
    elif invoice:
        target_member = invoice.member
        branch = invoice.branch
        ref_id = f"inv-{invoice.id}"
    elif refund:
        pay = getattr(refund, "payment", None)
        target_member = getattr(pay, "member", None) or (pay.invoice.member if pay and getattr(pay, "invoice", None) else None)
        branch = getattr(pay, "branch", None) or (pay.invoice.branch if pay and getattr(pay, "invoice", None) else None)
        ref_id = f"ref-{refund.id}"

    if not target_member or not target_member.phone:
        return None

    gym_name = get_gym_name()
    context = {
        "member_name": target_member.first_name,
        "gym_name": gym_name,
        "branch_name": branch.name if branch else "Main Branch",
    }
    if invoice:
        context.update({
            "invoice_number": invoice.invoice_number,
            "amount": f"{invoice.total_amount:.2f} ETB",
            "balance_due": f"{invoice.balance_due:.2f} ETB",
            "due_date": str(invoice.due_date),
        })
    if payment:
        context.update({
            "amount": f"{payment.amount:.2f} ETB",
            "payment_method": getattr(payment.payment_method, "name", "Cash"),
        })

    idempotency_key = f"bill-{event_type}-{ref_id}"
    template = MessageTemplate.objects.filter(code=event_type, is_active=True).first()
    if template:
        body = render_template_safely(template.body, context)
        subject = render_template_safely(template.subject, context)
    else:
        if event_type == TriggerEvent.INVOICE_ISSUED:
            body = f"Hello {target_member.first_name}, invoice {context.get('invoice_number', '')} for {context.get('amount', '')} has been issued at {gym_name}. Due by {context.get('due_date', '')}."
        elif event_type == TriggerEvent.PAYMENT_CONFIRMED:
            body = f"Payment Confirmed: We received {context.get('amount', '')} for {gym_name}. Thank you, {target_member.first_name}!"
        elif event_type == TriggerEvent.PARTIAL_PAYMENT_BALANCE:
            body = f"Hello {target_member.first_name}, partial payment confirmed. Remaining balance due: {context.get('balance_due', '')} at {gym_name}."
        elif event_type == TriggerEvent.PAYMENT_OVERDUE:
            body = f"Dear {target_member.first_name}, your invoice {context.get('invoice_number', '')} of {context.get('balance_due', '')} at {gym_name} is overdue. Please settle at your earliest convenience."
        elif event_type == TriggerEvent.REFUND_COMPLETED:
            body = f"Hello {target_member.first_name}, your refund at {gym_name} has been processed successfully."
        else:
            body = f"Billing update from {gym_name}."
        subject = f"{gym_name} Billing Notice"

    return create_and_queue_notification(
        recipient_address=target_member.phone,
        body=body,
        channel=channel,
        category=NotificationCategory.BILLING,
        priority=NotificationPriority.HIGH,
        subject=subject,
        branch=branch,
        recipient_member=target_member,
        idempotency_key=idempotency_key,
        send_immediately=send_immediately,
    )


def trigger_class_notification(
    event_type: str,
    booking=None,
    session=None,
    channel: str = NotificationChannel.SMS,
    send_immediately: bool = True,
):
    """
    Trigger class and PT session notifications.
    Prevents sending reminders for cancelled or outdated sessions.
    """
    member = booking.member if booking else None
    target_session = session or (booking.session if booking else None)

    if not member or not member.phone:
        return None

    # Check if session is cancelled
    if target_session and getattr(target_session, "status", "") == "CANCELLED":
        if event_type != TriggerEvent.CLASS_CANCELLED:
            logger.info("Skipping reminder for cancelled session #%s", target_session.id)
            return None

    gym_name = get_gym_name()
    class_name = getattr(target_session.class_type, "name", "Fitness Class") if target_session and hasattr(target_session, "class_type") else "Session"
    start_time_str = str(getattr(target_session, "start_time", ""))

    context = {
        "member_name": member.first_name,
        "gym_name": gym_name,
        "class_name": class_name,
        "start_time": start_time_str,
    }

    ref_id = f"cls-{event_type}-{getattr(booking, 'id', target_session.id if target_session else 0)}"
    idempotency_key = f"{ref_id}-{timezone.localdate()}"

    template = MessageTemplate.objects.filter(code=event_type, is_active=True).first()
    if template:
        body = render_template_safely(template.body, context)
        subject = render_template_safely(template.subject, context)
    else:
        if event_type == TriggerEvent.CLASS_BOOKED:
            body = f"Booking Confirmed: {member.first_name}, you are booked for {class_name} at {gym_name} ({start_time_str})."
        elif event_type == TriggerEvent.CLASS_CANCELLED:
            body = f"Notice: {class_name} scheduled for {start_time_str} at {gym_name} has been cancelled."
        elif event_type == TriggerEvent.WAITLIST_PROMOTED:
            body = f"Great news {member.first_name}! A spot opened up for {class_name} ({start_time_str}) at {gym_name}. You are now booked!"
        elif event_type == TriggerEvent.CLASS_REMINDER:
            body = f"Reminder: Your {class_name} session starts soon at {start_time_str} ({gym_name}). See you there!"
        else:
            body = f"Class update from {gym_name}."
        subject = f"{gym_name} Class Update"

    return create_and_queue_notification(
        recipient_address=member.phone,
        body=body,
        channel=channel,
        category=NotificationCategory.CLASS_TRAINING,
        priority=NotificationPriority.NORMAL,
        subject=subject,
        branch=getattr(target_session, "branch", None),
        recipient_member=member,
        idempotency_key=idempotency_key,
        send_immediately=send_immediately,
    )


def trigger_inventory_alert(
    event_type: str,
    product,
    stock_item=None,
    branch: Branch = None,
):
    """
    Trigger in-app and staff alerts for inventory anomalies (low stock, expiring batch).
    Deduplicated per day to prevent alert flooding.
    """
    branch = branch or (stock_item.branch if stock_item else None)
    today = timezone.localdate()
    idempotency_key = f"inv-{event_type}-{product.id}-{branch.id if branch else 0}-{today}"

    existing = InAppNotification.objects.filter(
        url__icontains=f"product_id={product.id}",
        created_at__date=today,
        category=NotificationCategory.POS_INVENTORY,
    ).first()
    if existing:
        return existing

    gym_name = get_gym_name()
    if event_type == TriggerEvent.LOW_STOCK_ALERT:
        title = f"Low Stock Alert: {product.name}"
        qty = stock_item.quantity_on_hand if stock_item else 0
        message = f"Product '{product.name}' (SKU: {product.sku}) has reached low stock level ({qty} on hand) at {branch.name if branch else 'gym'}."
    elif event_type == TriggerEvent.OUT_OF_STOCK_ALERT:
        title = f"Out of Stock: {product.name}"
        message = f"Product '{product.name}' (SKU: {product.sku}) is completely OUT OF STOCK at {branch.name if branch else 'gym'}."
    elif event_type == TriggerEvent.EXPIRING_PRODUCT_ALERT:
        title = f"Expiring Product: {product.name}"
        message = f"Batches of '{product.name}' are approaching expiration date. Please inspect inventory."
    else:
        title = f"Inventory Notice: {product.name}"
        message = f"Inventory update for {product.name}."

    # Broadcast in-app notification to branch staff & managers
    staff_users = User.objects.filter(is_active=True, is_staff=True)
    if branch:
        staff_users = staff_users.filter(branch_access__branch=branch, branch_access__is_active=True)

    created_notifs = []
    for user in staff_users.distinct():
        notif = InAppNotification.objects.create(
            recipient=user,
            branch=branch,
            title=title,
            message=message,
            url=f"/admin/inventory/stockitem/?product_id={product.id}",
            category=NotificationCategory.POS_INVENTORY,
        )
        created_notifs.append(notif)

    return created_notifs


def trigger_staff_operational_alert(
    event_type: str,
    branch: Branch,
    title: str,
    message: str,
    url: str = "",
    target_role: str = None,
):
    """
    Broadcast authorized in-app alert for operational events (expense approval, leave request).
    """
    staff_qs = User.objects.filter(is_active=True, is_staff=True)
    if branch:
        staff_qs = staff_qs.filter(branch_access__branch=branch, branch_access__is_active=True)
    if target_role:
        from apps.accounts.roles import has_role
        staff_qs = [u for u in staff_qs if has_role(u, target_role)]

    created = []
    for user in staff_qs:
        n = InAppNotification.objects.create(
            recipient=user,
            branch=branch,
            title=title,
            message=message,
            url=url,
            category=NotificationCategory.STAFF_MANAGEMENT,
        )
        created.append(n)
    return created


def dispatch_scheduled_reminders() -> dict:
    """
    Periodic job: inspects upcoming membership expiries and unpaid overdue invoices,
    and dispatches automated reminder notifications avoiding duplicate runs via idempotency keys.
    """
    from apps.memberships.models import Membership
    from apps.billing.models import Invoice

    today = timezone.localdate()
    counts = {
        "expiry_7d": 0,
        "expiry_3d": 0,
        "expiry_1d": 0,
        "expired": 0,
        "overdue_invoices": 0,
    }

    # 1. Membership Expiry 7 Days
    day_7 = today + timedelta(days=7)
    for m in Membership.objects.filter(status="ACTIVE", end_date=day_7):
        res = trigger_membership_notification(TriggerEvent.MEMBERSHIP_EXPIRY_7D, m, send_immediately=False)
        if res:
            counts["expiry_7d"] += 1

    # 2. Membership Expiry 3 Days
    day_3 = today + timedelta(days=3)
    for m in Membership.objects.filter(status="ACTIVE", end_date=day_3):
        res = trigger_membership_notification(TriggerEvent.MEMBERSHIP_EXPIRY_3D, m, send_immediately=False)
        if res:
            counts["expiry_3d"] += 1

    # 3. Membership Expiry 1 Day
    day_1 = today + timedelta(days=1)
    for m in Membership.objects.filter(status="ACTIVE", end_date=day_1):
        res = trigger_membership_notification(TriggerEvent.MEMBERSHIP_EXPIRY_1D, m, send_immediately=False)
        if res:
            counts["expiry_1d"] += 1

    # 4. Expired today or yesterday
    for m in Membership.objects.filter(status="EXPIRED", end_date=today):
        res = trigger_membership_notification(TriggerEvent.MEMBERSHIP_EXPIRED, m, send_immediately=False)
        if res:
            counts["expired"] += 1

    # 5. Overdue Invoices
    for inv in Invoice.objects.filter(status__in=["ISSUED", "PARTIALLY_PAID"], due_date__lt=today):
        res = trigger_billing_notification(TriggerEvent.PAYMENT_OVERDUE, invoice=inv, send_immediately=False)
        if res:
            counts["overdue_invoices"] += 1

    return counts


def execute_bulk_campaign(campaign: BulkCampaign, confirmed_by: User) -> dict:
    """
    Execute an authorized bulk messaging campaign to distinct filtered recipients.
    Deduplicates phone numbers and respects opt-out communication preferences.
    """
    from apps.notifications.selectors import get_campaign_recipients

    if campaign.status == CampaignStatus.COMPLETED:
        raise ValidationError("Campaign has already completed.")

    recipients = get_campaign_recipients(
        target_audience=campaign.target_audience,
        branch=campaign.branch,
        package=campaign.target_package,
    )

    campaign.total_recipients = len(recipients)
    campaign.status = CampaignStatus.PROCESSING
    campaign.confirmed_by = confirmed_by
    campaign.confirmed_at = timezone.now()
    campaign.save(update_fields=["total_recipients", "status", "confirmed_by", "confirmed_at"])

    sent_count = 0
    failed_count = 0
    gym_name = get_gym_name()

    for member in recipients:
        context = {
            "member_name": member.first_name,
            "gym_name": gym_name,
            "branch_name": campaign.branch.name if campaign.branch else "Main Branch",
        }
        body = render_template_safely(campaign.message_body, context)
        idempotency_key = f"cmp-{campaign.id}-mem-{member.id}"

        msg = create_and_queue_notification(
            recipient_address=member.phone,
            body=body,
            channel=campaign.channel,
            category=NotificationCategory.MARKETING_BULK,
            priority=NotificationPriority.LOW,
            subject=campaign.title,
            branch=campaign.branch,
            recipient_member=member,
            sender=confirmed_by,
            campaign=campaign,
            idempotency_key=idempotency_key,
            send_immediately=True,
        )
        if msg.status in (NotificationStatus.SENT, NotificationStatus.DELIVERED):
            sent_count += 1
        elif msg.status == NotificationStatus.FAILED:
            failed_count += 1

    campaign.sent_count = sent_count
    campaign.failed_count = failed_count
    campaign.status = CampaignStatus.COMPLETED
    campaign.save(update_fields=["sent_count", "failed_count", "status"])

    return {"recipients": len(recipients), "sent": sent_count, "failed": failed_count}
