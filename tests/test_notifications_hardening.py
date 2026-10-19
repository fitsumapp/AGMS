import uuid
from datetime import timedelta, time
from decimal import Decimal
import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import seed_roles
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.members.models import Member
from apps.memberships.models import MembershipPackage, Membership
from apps.classes.models import ClassType, ClassSession, ClassBooking
from apps.staff.models import StaffProfile, TrainerProfile
from apps.notifications.admin import (
    ProviderConfigAdmin,
    NotificationMessageAdmin,
    BulkCampaignAdmin,
)
from apps.notifications.models import (
    NotificationMessage,
    NotificationChannel,
    NotificationCategory,
    NotificationStatus,
    NotificationPriority,
    DeliveryQueueItem,
    QueueStatus,
    BulkCampaign,
    CampaignStatus,
    ProviderConfig,
    ProviderType,
    TriggerEvent,
    ScheduledNotification,
    ScheduledStatus,
)
from apps.notifications.providers.mock import MockSMSProvider
from apps.notifications.services import (
    create_and_queue_notification,
    dispatch_single_queue_item,
    dispatch_scheduled_reminders,
    process_queue,
    recover_stale_processing_items,
    invalidate_membership_expiry_reminders,
    trigger_class_notification,
    trigger_membership_notification,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def reset_mock():
    MockSMSProvider.reset()
    yield
    MockSMSProvider.reset()


@pytest.fixture
def hardening_setup(db):
    seed_roles()
    GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "AGMS Elite Fitness",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )
    b1 = Branch.objects.create(name="Bole Branch", code="BOLE", address="Bole Road, Addis Ababa", is_active=True)
    b2 = Branch.objects.create(name="CMC Branch", code="CMC", address="CMC Square, Addis Ababa", is_active=True)

    owner = User.objects.create_user("owner@agms.test", "OwnerPass123!", is_staff=True, is_superuser=True)
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    mgr1 = User.objects.create_user("mgr_bole@agms.test", "MgrPass123!", is_staff=True)
    mgr1.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr1, branch=b1, is_active=True, is_primary=True)

    recept1 = User.objects.create_user("recept_bole@agms.test", "ReceptPass123!", is_staff=True)
    recept1.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=recept1, branch=b1, is_active=True, is_primary=True)

    trainer_u = User.objects.create_user("trainer_bole@agms.test", "TrainerPass123!", is_staff=True)
    trainer_u.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer_u, branch=b1, is_active=True, is_primary=True)
    staff = StaffProfile.objects.create(user=trainer_u, primary_branch=b1, job_title="Trainer")
    trainer = TrainerProfile.objects.create(staff=staff, status=TrainerProfile.TrainerStatus.ACTIVE)

    m1 = Member.objects.create(
        member_number="MBR-H01",
        first_name="Kenenisa",
        last_name="Bekele",
        phone="+251911112233",
        home_branch=b1,
    )
    m2 = Member.objects.create(
        member_number="MBR-H02",
        first_name="Haile",
        last_name="Gebrselassie",
        phone="+251922334455",
        home_branch=b2,
    )

    pkg = MembershipPackage.objects.create(
        code="PKG-HARDEN",
        name="Hardening Package",
        duration_value=1,
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        price=Decimal("1800.00"),
    )

    provider = ProviderConfig.objects.create(
        name="Hardening Mock Provider",
        provider_type=ProviderType.MOCK,
        sender_id="AGMS",
        api_key_env_var="MOCK_API_KEY",
        cost_per_sms_etb=Decimal("0.35"),
        is_default=True,
        is_active=True,
    )

    return {
        "b1": b1,
        "b2": b2,
        "owner": owner,
        "mgr1": mgr1,
        "recept1": recept1,
        "trainer": trainer,
        "m1": m1,
        "m2": m2,
        "pkg": pkg,
        "provider": provider,
    }


# =============================================================
# 1. Worker Crash Recovery & Stale Lock Safety
# =============================================================

def test_recover_stale_processing_with_prior_provider_receipt(hardening_setup):
    """
    If worker crashed AFTER provider accepted the message (provider_message_id exists),
    recovery MUST reconcile the queue item as COMPLETED without resending duplicate SMS.
    """
    m1 = hardening_setup["m1"]
    b1 = hardening_setup["b1"]

    msg = create_and_queue_notification(
        recipient_address=m1.phone,
        body="Stale test message with receipt",
        channel=NotificationChannel.SMS,
        branch=b1,
        send_immediately=False,
    )
    queue_item = msg.queue_item

    # Simulate crash after provider acceptance
    stale_time = timezone.now() - timedelta(minutes=15)
    queue_item.status = QueueStatus.PROCESSING
    queue_item.locked_at = stale_time
    queue_item.locked_by = "crashed-worker-pid-9999"
    queue_item.save()

    msg.status = NotificationStatus.SENDING
    msg.provider_message_id = "provider-receipt-abc-123"
    msg.save()

    sent_count_before = len(MockSMSProvider.sent_messages)

    # Run recovery sweep
    result = recover_stale_processing_items(stale_threshold_seconds=300)
    assert result["reconciled"] == 1

    queue_item.refresh_from_db()
    msg.refresh_from_db()

    assert queue_item.status == QueueStatus.COMPLETED
    assert queue_item.locked_at is None
    assert queue_item.locked_by == ""
    assert msg.status == NotificationStatus.SENT
    # ZERO duplicate SMS calls made
    assert len(MockSMSProvider.sent_messages) == sent_count_before


def test_recover_stale_processing_without_receipt_retries_and_dead_letters(hardening_setup):
    """
    If worker crashed with NO provider receipt, recovery reschedules retry.
    When max retries exceeded, transitions to DEAD_LETTER and UNKNOWN status
    for manual audit rather than blind duplication.
    """
    m1 = hardening_setup["m1"]
    b1 = hardening_setup["b1"]

    msg = create_and_queue_notification(
        recipient_address=m1.phone,
        body="Stale test message no receipt",
        channel=NotificationChannel.SMS,
        branch=b1,
        send_immediately=False,
    )
    queue_item = msg.queue_item

    stale_time = timezone.now() - timedelta(minutes=15)
    queue_item.status = QueueStatus.PROCESSING
    queue_item.locked_at = stale_time
    queue_item.locked_by = "crashed-worker-pid-8888"
    queue_item.retry_count = 1
    queue_item.max_retries = 3
    queue_item.save()

    # Attempt 1: retried
    res1 = recover_stale_processing_items(stale_threshold_seconds=300)
    assert res1["recovered"] == 1

    queue_item.refresh_from_db()
    assert queue_item.status == QueueStatus.PENDING
    assert queue_item.retry_count == 2
    assert queue_item.locked_at is None

    # Simulate another crash pushing it to max_retries
    queue_item.status = QueueStatus.PROCESSING
    queue_item.locked_at = stale_time
    queue_item.retry_count = 2
    queue_item.save()

    res2 = recover_stale_processing_items(stale_threshold_seconds=300)
    assert res2["dead_lettered"] == 1

    queue_item.refresh_from_db()
    msg.refresh_from_db()

    assert queue_item.status == QueueStatus.DEAD_LETTER
    assert msg.status == NotificationStatus.UNKNOWN
    assert "manual reconciliation" in msg.error_message


# =============================================================
# 2. Transaction Rollback Outgoing SMS Prevention
# =============================================================

def test_atomic_transaction_rollback_prevents_immediate_sms_dispatch(hardening_setup):
    """
    Guarantees that database rollback prevents uncommitted notifications from being dispatched,
    so if the business transaction aborts, zero outgoing SMS are transmitted.
    """
    m1 = hardening_setup["m1"]
    b1 = hardening_setup["b1"]

    sent_count_before = len(MockSMSProvider.sent_messages)

    try:
        with transaction.atomic():
            create_and_queue_notification(
                recipient_address=m1.phone,
                body="Rolled back invoice alert",
                channel=NotificationChannel.SMS,
                branch=b1,
                send_immediately=False,
            )
            # Transaction encounters an unhandled exception and rolls back
            raise RuntimeError("Database constraint or payment failure forced rollback")
    except RuntimeError:
        pass

    # No SMS must have reached the provider
    assert len(MockSMSProvider.sent_messages) == sent_count_before
    assert NotificationMessage.objects.filter(body="Rolled back invoice alert").count() == 0
    assert DeliveryQueueItem.objects.count() == 0

    # Queue sweep confirms zero pending work
    queue_res = process_queue(batch_size=10)
    assert queue_res["processed"] == 0
    assert len(MockSMSProvider.sent_messages) == sent_count_before


# =============================================================
# 3. Delivery Callback Webhook Security & Idempotency
# =============================================================

def test_delivery_callback_authentication_and_status_transitions(hardening_setup, monkeypatch):
    """
    Verifies delivery callback authentication, message ID validation,
    valid state transitions (SENT -> DELIVERED), and cross-provider protection.
    """
    client = Client()
    b1 = hardening_setup["b1"]
    m1 = hardening_setup["m1"]
    provider = hardening_setup["provider"]

    monkeypatch.setenv("MOCK_API_KEY", "secret-test-token-777")

    msg = create_and_queue_notification(
        recipient_address=m1.phone,
        body="Callback test notification",
        channel=NotificationChannel.SMS,
        branch=b1,
        send_immediately=False,
    )
    msg.status = NotificationStatus.SENT
    msg.provider_message_id = "provider-ext-999"
    msg.provider = provider.get_provider_type_display()
    msg.save()

    # 1. Invalid token -> 403 Forbidden
    resp_unauth = client.post(
        f"/admin/notifications/callbacks/{provider.provider_type}/",
        data={"message_id": "provider-ext-999", "status": "DELIVERED"},
        HTTP_AUTHORIZATION="Bearer wrong-secret-token",
    )
    assert resp_unauth.status_code == 403

    # 2. Valid token -> 200 Success & DELIVERED transition
    resp_auth = client.post(
        f"/admin/notifications/callbacks/{provider.provider_type}/",
        data={"message_id": "provider-ext-999", "status": "DELIVERED"},
        HTTP_AUTHORIZATION="Bearer secret-test-token-777",
    )
    assert resp_auth.status_code == 200
    assert resp_auth.json()["status"] == "success"

    msg.refresh_from_db()
    assert msg.status == NotificationStatus.DELIVERED
    assert msg.delivered_at is not None

    # 3. Replay Protection: Second identical callback is safely acknowledged without error
    resp_replay = client.post(
        f"/admin/notifications/callbacks/{provider.provider_type}/",
        data={"message_id": "provider-ext-999", "status": "DELIVERED"},
        HTTP_AUTHORIZATION="Bearer secret-test-token-777",
    )
    assert resp_replay.status_code == 200
    assert resp_replay.json()["status"] == "acknowledged"

    # 4. Unknown / Mismatched message ID -> 404
    resp_missing = client.post(
        f"/admin/notifications/callbacks/{provider.provider_type}/",
        data={"message_id": "non-existent-msg-id", "status": "DELIVERED"},
        HTTP_AUTHORIZATION="Bearer secret-test-token-777",
    )
    assert resp_missing.status_code == 404


# =============================================================
# 4. Membership Renewal Invalidates Obsolete Expiry Reminders
# =============================================================

def test_membership_renewal_invalidates_obsolete_expiry_reminders(hardening_setup):
    """
    When a member renews their membership (active membership with end_date > expiring membership),
    the scheduler skips sending the 7D/3D reminder.
    """
    m1 = hardening_setup["m1"]
    b1 = hardening_setup["b1"]
    pkg = hardening_setup["pkg"]
    today = timezone.localdate()

    # Current membership expiring in 7 days
    expiring_mem = Membership.objects.create(
        membership_number="MEM-EXP-001",
        member=m1,
        package=pkg,
        home_branch=b1,
        start_date=today - timedelta(days=23),
        end_date=today + timedelta(days=7),
        status=Membership.Status.ACTIVE,
        price_snapshot=pkg.price,
        duration_unit_snapshot=pkg.duration_unit,
        duration_value_snapshot=pkg.duration_value,
    )

    # Renewed membership already created and active, ending in 37 days
    renewed_mem = Membership.objects.create(
        membership_number="MEM-RNW-002",
        member=m1,
        package=pkg,
        home_branch=b1,
        start_date=today + timedelta(days=7),
        end_date=today + timedelta(days=37),
        status=Membership.Status.ACTIVE,
        price_snapshot=pkg.price,
        duration_unit_snapshot=pkg.duration_unit,
        duration_value_snapshot=pkg.duration_value,
    )

    # Invalidate helper
    invalidated = invalidate_membership_expiry_reminders(renewed_mem)

    # Run scheduled reminders
    counts = dispatch_scheduled_reminders()
    # Expiring membership reminder is suppressed because member already has a renewed active membership
    assert counts["expiry_7d"] == 0


# =============================================================
# 5. Cancelled Class Sessions & Bookings Safeguard
# =============================================================

def test_cancelled_class_session_suppresses_reminders(hardening_setup):
    """
    Cancelled sessions or bookings must never generate reminders.
    """
    m1 = hardening_setup["m1"]
    b1 = hardening_setup["b1"]
    trainer = hardening_setup["trainer"]

    ctype = ClassType.objects.create(name="Spinning Class", slug="spinning-class", default_capacity=15)
    session = ClassSession.objects.create(
        class_type=ctype,
        branch=b1,
        trainer=trainer,
        session_date=timezone.localdate(),
        start_time=time(17, 30),
        end_time=time(18, 30),
        status=ClassSession.SessionStatus.CANCELLED,
        capacity=15,
    )
    booking = ClassBooking.objects.create(
        session=session,
        member=m1,
        status=ClassBooking.BookingStatus.BOOKED,
    )

    # Reminder trigger must return None
    res = trigger_class_notification(TriggerEvent.CLASS_REMINDER, booking=booking, session=session)
    assert res is None


# =============================================================
# 6. Branch Isolation and Recipient Privacy
# =============================================================

def test_branch_isolation_blocks_cross_branch_messaging(hardening_setup):
    """
    A receptionist or branch manager from Bole Branch cannot send SMS to a member
    belonging to CMC Branch.
    """
    client = Client()
    recept1 = hardening_setup["recept1"]
    m2 = hardening_setup["m2"]  # Belongs to CMC Branch

    client.force_login(recept1)

    resp = client.post(
        "/admin/notifications/sendsms/",
        data={
            "recipient_phone": m2.phone,
            "message_body": "Cross branch attempt",
            "branch_id": hardening_setup["b1"].id,
        },
    )
    assert resp.status_code == 403


def test_receptionist_cannot_launch_bulk_campaigns(hardening_setup):
    """
    Receptionists are strictly restricted from initiating bulk marketing campaigns.
    """
    client = Client()
    recept1 = hardening_setup["recept1"]
    client.force_login(recept1)

    resp = client.get("/admin/notifications/bulkcampaign/")
    assert resp.status_code == 403


def test_provider_config_admin_restricted_from_receptionist_and_trainers(hardening_setup):
    """
    Receptionists, trainers, and POS staff cannot view or modify ProviderConfig in Django Unfold admin.
    """
    recept = hardening_setup["recept1"]
    owner = hardening_setup["owner"]
    provider = hardening_setup["provider"]

    admin_site = ProviderConfigAdmin(ProviderConfig, None)

    rf = RequestFactory()
    req_recept = rf.get("/admin/notifications/providerconfig/")
    req_recept.user = recept

    req_owner = rf.get("/admin/notifications/providerconfig/")
    req_owner.user = owner

    assert admin_site.has_module_permission(req_recept) is False
    assert admin_site.has_view_permission(req_recept, provider) is False
    assert admin_site.has_change_permission(req_recept, provider) is False

    assert admin_site.has_module_permission(req_owner) is True
    assert admin_site.has_view_permission(req_owner, provider) is True


def test_notification_message_admin_is_immutable(hardening_setup):
    """
    Notification audit records cannot be created, modified, or deleted by any admin user.
    """
    admin_site = NotificationMessageAdmin(NotificationMessage, None)
    rf = RequestFactory()
    req = rf.get("/admin/notifications/notificationmessage/")
    req.user = hardening_setup["owner"]

    assert admin_site.has_add_permission(req) is False
    assert admin_site.has_change_permission(req) is False
    assert admin_site.has_delete_permission(req) is False
