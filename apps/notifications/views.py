import csv
import json
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.roles import is_owner
from apps.branches.models import Branch
from apps.members.models import Member
from apps.memberships.models import MembershipPackage
from apps.notifications.models import (
    BulkCampaign,
    CampaignStatus,
    InAppNotification,
    MessageTemplate,
    NotificationCategory,
    NotificationChannel,
    NotificationMessage,
    NotificationPriority,
    NotificationStatus,
    ProviderConfig,
    TargetAudience,
)
from apps.notifications.permissions import (
    can_manage_templates,
    can_send_bulk_messages,
    can_send_sms,
    can_view_communication_reports,
    can_view_notification_dashboard,
    get_user_notification_branches,
)
from apps.notifications.selectors import (
    get_campaign_recipients,
    get_communication_report_metrics,
    get_notification_dashboard_metrics,
)
from apps.notifications.services import (
    create_and_queue_notification,
    execute_bulk_campaign,
)
from apps.notifications.validators import calculate_sms_segments, sanitize_for_csv


@login_required
def notification_dashboard_view(request):
    """Executive & Operational Communications Dashboard."""
    if not can_view_notification_dashboard(request.user):
        raise PermissionDenied("You do not have permission to view the communication dashboard.")

    user_branches = get_user_notification_branches(request.user)
    branch_id = request.GET.get("branch")
    selected_branch = None
    if branch_id:
        selected_branch = user_branches.filter(id=branch_id).first()

    metrics = get_notification_dashboard_metrics(request.user, branch=selected_branch)
    recent_messages = NotificationMessage.objects.filter(
        branch__in=user_branches
    ).select_related("branch", "recipient_member", "sender")[:10]

    unread_in_app_list = InAppNotification.objects.filter(
        recipient=request.user, is_read=False
    )[:5]

    context = {
        "title": "Notification Dashboard",
        "metrics": metrics,
        "branches": user_branches,
        "selected_branch": selected_branch,
        "recent_messages": recent_messages,
        "unread_in_app_list": unread_in_app_list,
        "can_send": can_send_sms(request.user),
        "can_bulk": can_send_bulk_messages(request.user),
    }
    return render(request, "admin/notifications/dashboard.html", context)


@login_required
def send_sms_view(request):
    """Ad-hoc single SMS dispatch terminal with live segment calculator."""
    if not can_send_sms(request.user):
        raise PermissionDenied("You do not have permission to send SMS messages.")

    user_branches = get_user_notification_branches(request.user)
    templates = MessageTemplate.objects.filter(is_active=True)

    if request.method == "POST":
        recipient_phone = request.POST.get("recipient_phone", "").strip()
        message_body = request.POST.get("message_body", "").strip()
        template_id = request.POST.get("template_id")
        branch_id = request.POST.get("branch_id")
        priority = request.POST.get("priority", NotificationPriority.NORMAL)

        if branch_id and not user_branches.filter(id=branch_id).exists():
            raise PermissionDenied("You do not have access to the selected branch.")
        branch = user_branches.filter(id=branch_id).first() if branch_id else user_branches.first()

        # Recipient privacy & cross-branch protection
        if recipient_phone and not (request.user.is_superuser or is_owner(request.user)):
            try:
                from apps.notifications.validators import normalize_ethiopian_phone
                norm_phone = normalize_ethiopian_phone(recipient_phone)
                other_branch_member = Member.objects.filter(phone=norm_phone).exclude(home_branch__in=user_branches).first()
                if other_branch_member and other_branch_member.home_branch:
                    raise PermissionDenied(f"Recipient belongs to another branch ({other_branch_member.home_branch.name}). Cross-branch messaging is restricted.")
            except ValidationError:
                pass

        if not recipient_phone:
            messages.error(request, "Recipient phone number is required.")
        elif not message_body:
            messages.error(request, "Message body cannot be empty.")
        else:
            try:
                notif = create_and_queue_notification(
                    recipient_address=recipient_phone,
                    body=message_body,
                    channel=NotificationChannel.SMS,
                    category=NotificationCategory.MARKETING_BULK,
                    priority=priority,
                    branch=branch,
                    sender=request.user,
                    send_immediately=True,
                )
                if notif.status in (NotificationStatus.SENT, NotificationStatus.DELIVERED):
                    messages.success(request, f"SMS dispatched successfully to {notif.recipient_address} (ID: {notif.id}).")
                elif notif.status == NotificationStatus.FAILED:
                    messages.error(request, f"SMS delivery failed: {notif.error_message}")
                elif notif.status == NotificationStatus.CANCELLED:
                    messages.warning(request, f"SMS cancelled: {notif.error_message}")
                else:
                    messages.info(request, f"SMS queued for delivery (Queue Item ID: {notif.id}).")
                return redirect("send-sms")
            except Exception as exc:
                messages.error(request, f"Error dispatching SMS: {str(exc)}")

    context = {
        "title": "Send SMS",
        "branches": user_branches,
        "templates": templates,
    }
    return render(request, "admin/notifications/send_sms.html", context)


@login_required
def bulk_campaign_view(request):
    """Bulk Messaging Campaign Wizard with audience preview and estimated cost."""
    if not can_send_bulk_messages(request.user):
        raise PermissionDenied("You do not have permission to launch bulk messaging campaigns.")

    user_branches = get_user_notification_branches(request.user)
    packages = MembershipPackage.objects.filter(is_active=True)
    templates = MessageTemplate.objects.filter(is_active=True)

    if request.method == "POST":
        action = request.POST.get("action")
        title = request.POST.get("title", "").strip()
        target_audience = request.POST.get("target_audience", TargetAudience.ALL_ACTIVE_MEMBERS)
        branch_id = request.POST.get("branch_id")
        package_id = request.POST.get("package_id")
        message_body = request.POST.get("message_body", "").strip()

        if branch_id and not user_branches.filter(id=branch_id).exists():
            raise PermissionDenied("You do not have permission to launch campaigns for the selected branch.")
        branch = user_branches.filter(id=branch_id).first() if branch_id else (user_branches.first() if not (request.user.is_superuser or is_owner(request.user)) else None)
        package = packages.filter(id=package_id).first() if package_id else None

        if action == "execute":
            if not title or not message_body:
                messages.error(request, "Campaign title and message body are required.")
            else:
                recipients = get_campaign_recipients(target_audience=target_audience, branch=branch, package=package)
                if not recipients:
                    messages.warning(request, "No eligible active recipients matched the selected audience filter.")
                else:
                    segments_info = calculate_sms_segments(message_body)
                    provider_conf = ProviderConfig.objects.filter(is_active=True).first()
                    unit_cost = provider_conf.cost_per_sms_etb if provider_conf else Decimal("0.35")
                    est_cost = unit_cost * Decimal(segments_info["segment_count"]) * Decimal(len(recipients))

                    campaign = BulkCampaign.objects.create(
                        title=title,
                        branch=branch,
                        target_audience=target_audience,
                        target_package=package,
                        message_body=message_body,
                        channel=NotificationChannel.SMS,
                        total_recipients=len(recipients),
                        estimated_cost_etb=est_cost,
                        created_by=request.user,
                    )
                    res = execute_bulk_campaign(campaign, confirmed_by=request.user)
                    messages.success(
                        request,
                        f"Bulk Campaign '{campaign.title}' completed: {res['sent']} sent, {res['failed']} failed across {res['recipients']} recipients."
                    )
                    return redirect("notification-dashboard")

    # Calculate preview
    target_audience = request.GET.get("target_audience", TargetAudience.ALL_ACTIVE_MEMBERS)
    branch_id = request.GET.get("branch_id")
    preview_branch = user_branches.filter(id=branch_id).first() if branch_id else None
    preview_recipients = get_campaign_recipients(target_audience=target_audience, branch=preview_branch)

    context = {
        "title": "Bulk Messaging Campaign",
        "branches": user_branches,
        "packages": packages,
        "templates": templates,
        "target_audiences": TargetAudience.choices,
        "preview_count": len(preview_recipients),
        "preview_sample": preview_recipients[:8],
    }
    return render(request, "admin/notifications/bulk_campaign.html", context)


@login_required
def communication_reports_view(request):
    """Communication Analytics and Reporting Center with CSV Export."""
    if not can_view_communication_reports(request.user):
        raise PermissionDenied("You do not have permission to view communication reports.")

    user_branches = get_user_notification_branches(request.user)
    branch_id = request.GET.get("branch")
    category = request.GET.get("category")
    channel = request.GET.get("channel")
    selected_branch = user_branches.filter(id=branch_id).first() if branch_id else None

    # Date range
    period = request.GET.get("period", "this_month")
    today = timezone.localdate()
    start_date = None
    end_date = today

    if period == "today":
        start_date = today
    elif period == "last_7_days":
        start_date = today - timezone.timedelta(days=7)
    elif period == "this_month":
        start_date = today.replace(day=1)
    elif period == "last_month":
        first_this = today.replace(day=1)
        prev_month_end = first_this - timezone.timedelta(days=1)
        start_date = prev_month_end.replace(day=1)
        end_date = prev_month_end

    metrics = get_communication_report_metrics(
        request.user,
        branch=selected_branch,
        category=category,
        channel=channel,
        start_date=start_date,
        end_date=end_date,
    )

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="communication_report_{today}.csv"'
        writer = csv.writer(response)
        writer.writerow(["ID", "Date", "Branch", "Channel", "Category", "Recipient", "Status", "Segments", "Cost ETB", "Provider"])
        for msg in metrics["records"]:
            writer.writerow([
                msg.id,
                msg.created_at.strftime("%Y-%m-%d %H:%M"),
                sanitize_for_csv(msg.branch.name if msg.branch else "Global"),
                msg.get_channel_display(),
                msg.get_category_display(),
                sanitize_for_csv(msg.recipient_address),
                msg.get_status_display(),
                msg.segment_count,
                f"{msg.estimated_cost_etb:.2f}",
                sanitize_for_csv(msg.provider),
            ])
        return response

    context = {
        "title": "Communication Reports",
        "metrics": metrics,
        "branches": user_branches,
        "selected_branch": selected_branch,
        "categories": NotificationCategory.choices,
        "channels": NotificationChannel.choices,
        "selected_category": category,
        "selected_channel": channel,
        "period": period,
    }
    return render(request, "admin/notifications/reports.html", context)


@login_required
@require_POST
def mark_in_app_notification_read(request, notification_id):
    """Mark an in-app notification as read."""
    notif = get_object_or_404(InAppNotification, id=notification_id, recipient=request.user)
    notif.is_read = True
    notif.read_at = timezone.now()
    notif.save(update_fields=["is_read", "read_at"])
    return JsonResponse({"status": "ok", "id": notif.id})


@login_required
def get_unread_in_app_notifications_api(request):
    """JSON API to fetch unread in-app alerts for real-time header bell indicator."""
    unread = InAppNotification.objects.filter(recipient=request.user, is_read=False)[:10]
    data = [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "url": n.url,
            "category": n.get_category_display(),
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for n in unread
    ]
    return JsonResponse({"count": len(data), "notifications": data})


@csrf_exempt
@require_POST
def sms_delivery_callback_view(request, provider_code):
    """
    Secure delivery callback webhook for SMS gateways (e.g. AfroMessage, Custom HTTP).
    Authenticates provider token, validates message ID, enforces valid state transitions,
    and prevents replay/provider-spoofing attacks.
    """
    provider_code = provider_code.strip().upper()
    provider_config = ProviderConfig.objects.filter(
        provider_type=provider_code, is_active=True
    ).first()

    if not provider_config:
        provider_config = ProviderConfig.objects.filter(
            name__iexact=provider_code, is_active=True
        ).first()

    if not provider_config:
        return JsonResponse({"error": "Unknown or inactive provider"}, status=404)

    # 1. Authenticate callback token
    expected_token = provider_config.get_api_key()
    auth_header = request.headers.get("Authorization", "")
    callback_secret = request.headers.get("X-Callback-Secret") or request.GET.get("token") or ""

    received_token = ""
    if auth_header.startswith("Bearer "):
        received_token = auth_header[7:].strip()
    elif callback_secret:
        received_token = callback_secret.strip()

    if expected_token and received_token != expected_token:
        return JsonResponse({"error": "Unauthorized callback credentials"}, status=403)

    # 2. Parse payload
    try:
        if request.content_type == "application/json":
            payload = json.loads(request.body.decode("utf-8"))
        else:
            payload = request.POST.dict()
    except Exception:
        return JsonResponse({"error": "Invalid payload format"}, status=400)

    provider_msg_id = payload.get("message_id") or payload.get("id") or payload.get("msg_id") or ""
    if not provider_msg_id:
        return JsonResponse({"error": "Missing message_id"}, status=400)

    # 3. Find NotificationMessage - prevent one provider from modifying another provider's message
    notif = NotificationMessage.objects.filter(
        provider_message_id=str(provider_msg_id),
        provider__iexact=provider_config.get_provider_type_display(),
    ).first()

    if not notif:
        notif = NotificationMessage.objects.filter(
            provider_message_id=str(provider_msg_id),
            provider__iexact=provider_code,
        ).first()

    if not notif:
        return JsonResponse({"error": "Notification record not found for provider"}, status=404)

    # 4. Enforce valid state transitions & Replay Protection
    raw_status = str(payload.get("status", "")).upper()
    now = timezone.now()

    if notif.status == NotificationStatus.DELIVERED:
        return JsonResponse({"status": "acknowledged", "detail": "Already marked delivered"}, status=200)

    if raw_status in ("DELIVERED", "SUCCESS", "COMPLETED"):
        notif.status = NotificationStatus.DELIVERED
        notif.delivered_at = now
        notif.error_message = ""
        notif.save(update_fields=["status", "delivered_at", "error_message", "updated_at"])
    elif raw_status in ("FAILED", "UNDELIVERED", "REJECTED", "EXPIRED"):
        if notif.status != NotificationStatus.DELIVERED:
            notif.status = NotificationStatus.FAILED
            notif.failed_at = now
            notif.error_message = payload.get("error") or payload.get("reason") or "Delivery failed at handset"
            notif.save(update_fields=["status", "failed_at", "error_message", "updated_at"])
    elif raw_status in ("SENT", "ACCEPTED", "BUFFERED"):
        if notif.status in (NotificationStatus.PENDING, NotificationStatus.QUEUED, NotificationStatus.SENDING):
            notif.status = NotificationStatus.ACCEPTED
            notif.sent_at = now
            notif.save(update_fields=["status", "sent_at", "updated_at"])
    else:
        return JsonResponse({"error": f"Invalid delivery status: {raw_status}"}, status=400)

    return JsonResponse({"status": "success", "message_id": notif.id, "delivery_status": notif.status})

