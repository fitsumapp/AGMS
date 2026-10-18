import uuid
from datetime import date, time, timedelta
from decimal import Decimal
import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import seed_roles
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.members.models import Member
from apps.memberships.models import MembershipPackage, Membership
from apps.billing.models import Invoice, Payment, PaymentMethod
from apps.classes.models import ClassType, ClassSession, ClassBooking
from apps.staff.models import StaffProfile, TrainerProfile
from apps.inventory.models import Product, ProductCategory, StockItem
from apps.notifications.models import (
    NotificationMessage,
    NotificationChannel,
    NotificationCategory,
    NotificationStatus,
    NotificationPriority,
    DeliveryQueueItem,
    QueueStatus,
    MessageTemplate,
    NotificationPreference,
    BulkCampaign,
    CampaignStatus,
    TargetAudience,
    ProviderConfig,
    ProviderType,
    TriggerEvent,
    InAppNotification,
)
from apps.notifications.providers.mock import MockSMSProvider
from apps.notifications.services import (
    create_and_queue_notification,
    dispatch_single_queue_item,
    process_queue,
    dispatch_scheduled_reminders,
    trigger_membership_notification,
    trigger_billing_notification,
    trigger_class_notification,
    trigger_inventory_alert,
    execute_bulk_campaign,
)
from apps.notifications.selectors import (
    get_notification_dashboard_metrics,
    get_communication_report_metrics,
)
from apps.notifications.validators import (
    normalize_ethiopian_phone,
    calculate_sms_segments,
    validate_template_placeholders,
    render_template_safely,
    sanitize_for_csv,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def reset_mock_provider():
    MockSMSProvider.reset()
    yield
    MockSMSProvider.reset()


@pytest.fixture
def test_setup(db):
    seed_roles()
    GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "AGMS Elite Fitness",
            "legal_name": "Antigravity Fitness PLC",
            "email": "support@agms.test",
            "phone": "+251911000000",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )
    branch1 = Branch.objects.create(name="Bole Branch", code="BOLE", address="Bole Road, Addis Ababa", is_active=True)
    branch2 = Branch.objects.create(name="CMC Branch", code="CMC", address="CMC Square, Addis Ababa", is_active=True)

    owner = User.objects.create_user("owner@agms.test", "OwnerPass12345!", is_staff=True, is_superuser=True)
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    manager = User.objects.create_user("manager_bole@agms.test", "ManagerPass123!", is_staff=True)
    manager.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=manager, branch=branch1, is_active=True, is_primary=True)

    receptionist = User.objects.create_user("receptionist_bole@agms.test", "ReceptPass123!", is_staff=True)
    receptionist.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=receptionist, branch=branch1, is_active=True, is_primary=True)

    trainer_u = User.objects.create_user("trainer_bole@agms.test", "TrainerPass123!", is_staff=True)
    trainer_u.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer_u, branch=branch1, is_active=True, is_primary=True)
    staff = StaffProfile.objects.create(user=trainer_u, primary_branch=branch1, job_title="Senior Fitness Trainer")
    trainer = TrainerProfile.objects.create(staff=staff, status=TrainerProfile.TrainerStatus.ACTIVE)

    member1 = Member.objects.create(
        member_number="MBR-0001",
        first_name="Abebe",
        last_name="Bikila",
        phone="+251911223344",
        home_branch=branch1,
    )
    member2 = Member.objects.create(
        member_number="MBR-0002",
        first_name="Derartu",
        last_name="Tulu",
        phone="+251711223344",
        home_branch=branch2,
    )

    pkg = MembershipPackage.objects.create(
        code="MONTHLY-GOLD",
        name="Monthly Gold",
        duration_value=1,
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        price=Decimal("2500.00"),
    )

    ProviderConfig.objects.create(
        name="Default Mock Provider",
        provider_type=ProviderType.MOCK,
        sender_id="AGMS",
        cost_per_sms_etb=Decimal("0.35"),
        is_default=True,
        is_active=True,
    )

    return {
        "branch1": branch1,
        "branch2": branch2,
        "owner": owner,
        "manager": manager,
        "receptionist": receptionist,
        "trainer": trainer,
        "member1": member1,
        "member2": member2,
        "package": pkg,
    }


def create_test_membership_helper(member, package, branch, start_date, end_date, status=Membership.Status.ACTIVE):
    return Membership.objects.create(
        membership_number=f"MEM-{uuid.uuid4().hex[:8].upper()}",
        member=member,
        package=package,
        home_branch=branch,
        start_date=start_date,
        end_date=end_date,
        status=status,
        price_snapshot=package.price,
        duration_unit_snapshot=package.duration_unit,
        duration_value_snapshot=package.duration_value,
    )


# -------------------------------------------------------------
# 1. Phone Normalization & Unicode Segment Calculations
# -------------------------------------------------------------

def test_ethiopian_phone_normalization():
    assert normalize_ethiopian_phone("0911223344") == "+251911223344"
    assert normalize_ethiopian_phone("0711223344") == "+251711223344"
    assert normalize_ethiopian_phone("251911223344") == "+251911223344"
    assert normalize_ethiopian_phone("+251911223344") == "+251911223344"
    assert normalize_ethiopian_phone("09 11-22 (33) 44") == "+251911223344"

    with pytest.raises(ValidationError):
        normalize_ethiopian_phone("invalid_phone_123")


def test_sms_segment_calculation_gsm7():
    text_short = "Hello from AGMS Gym! Your workout awaits."
    res = calculate_sms_segments(text_short)
    assert res["is_unicode"] is False
    assert res["encoding"] == "GSM-7"
    assert res["segment_count"] == 1

    text_long = "A" * 165
    res_long = calculate_sms_segments(text_long)
    assert res_long["is_unicode"] is False
    assert res_long["segment_count"] == 2


def test_sms_segment_calculation_amharic_unicode():
    text_amharic = "እንኳን ወደ ኤጂኤምኤስ ጂም በደህና መጡ! ዛሬ የአካል ብቃት እንቅስቃሴዎን ይጀምሩ።"
    res = calculate_sms_segments(text_amharic)
    assert res["is_unicode"] is True
    assert res["encoding"] == "UCS-2"
    assert res["segment_count"] == 1

    text_amharic_long = text_amharic + " ተጨማሪ መረጃ ለማግኘት ወደ መቀበያ ጠረጴዛ ይምጡ።" * 2
    res_long = calculate_sms_segments(text_amharic_long)
    assert res_long["is_unicode"] is True
    assert res_long["segment_count"] >= 2


# -------------------------------------------------------------
# 2. Template Placeholders & Rendering
# -------------------------------------------------------------

def test_template_placeholders_and_safe_rendering():
    tpl = "Hello {{ member_name }}, welcome to {{ gym_name }} ({{ branch_name }})."
    validate_template_placeholders(tpl, allowed_placeholders={"member_name", "gym_name", "branch_name"})

    context = {"member_name": "Abebe", "gym_name": "AGMS Gym", "branch_name": "Bole"}
    rendered = render_template_safely(tpl, context)
    assert rendered == "Hello Abebe, welcome to AGMS Gym (Bole)."

    rendered_partial = render_template_safely(tpl, {"member_name": "Abebe"})
    assert "Hello Abebe" in rendered_partial
    assert "{{ gym_name }}" in rendered_partial

    with pytest.raises(ValidationError):
        validate_template_placeholders("Hello {{ unauthorized_var }}", allowed_placeholders={"member_name"})


# -------------------------------------------------------------
# 3. Notification Dispatch & Idempotency
# -------------------------------------------------------------

def test_notification_creation_and_idempotency(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]

    key = "idem-test-001"
    msg1 = create_and_queue_notification(
        recipient_address=member.phone,
        body="Test message 1",
        channel=NotificationChannel.SMS,
        branch=branch,
        recipient_member=member,
        idempotency_key=key,
        send_immediately=False,
    )
    assert msg1.status == NotificationStatus.QUEUED
    assert msg1.segment_count == 1
    assert msg1.estimated_cost_etb == Decimal("0.35")

    msg2 = create_and_queue_notification(
        recipient_address=member.phone,
        body="Test message 1 duplicate attempt",
        channel=NotificationChannel.SMS,
        branch=branch,
        recipient_member=member,
        idempotency_key=key,
        send_immediately=False,
    )
    assert msg1.id == msg2.id
    assert NotificationMessage.objects.filter(idempotency_key=key).count() == 1


def test_opt_out_preferences_enforced(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]

    NotificationPreference.objects.create(
        member=member,
        allow_sms=True,
        opt_out_marketing=True,
    )

    msg = create_and_queue_notification(
        recipient_address=member.phone,
        body="Special discount on supplements!",
        channel=NotificationChannel.SMS,
        category=NotificationCategory.MARKETING_BULK,
        branch=branch,
        recipient_member=member,
    )
    assert msg.status == NotificationStatus.CANCELLED
    assert "opted out" in msg.error_message.lower()


# -------------------------------------------------------------
# 4. Queue Processing & Exponential Backoff Retry
# -------------------------------------------------------------

def test_queue_processing_success(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]

    msg = create_and_queue_notification(
        recipient_address=member.phone,
        body="Immediate delivery test",
        channel=NotificationChannel.SMS,
        branch=branch,
        recipient_member=member,
        send_immediately=False,
    )
    queue_item = msg.queue_item
    assert queue_item.status == QueueStatus.PENDING

    res = process_queue(batch_size=10)
    assert res["processed"] == 1
    assert res["succeeded"] == 1

    msg.refresh_from_db()
    queue_item.refresh_from_db()
    assert msg.status == NotificationStatus.SENT
    assert msg.sent_at is not None
    assert queue_item.status == QueueStatus.COMPLETED
    assert len(MockSMSProvider.sent_messages) == 1


def test_queue_processing_retry_and_dead_letter(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]

    MockSMSProvider.should_fail = True
    MockSMSProvider.failure_reason = "Simulated Telecom Gateway 503"

    msg = create_and_queue_notification(
        recipient_address=member.phone,
        body="Retry test message",
        channel=NotificationChannel.SMS,
        branch=branch,
        recipient_member=member,
        send_immediately=False,
    )
    queue_item = msg.queue_item

    # Attempt 1
    dispatch_single_queue_item(queue_item)
    queue_item.refresh_from_db()
    assert queue_item.retry_count == 1
    assert queue_item.status == QueueStatus.PENDING

    # Attempt 2
    dispatch_single_queue_item(queue_item)
    queue_item.refresh_from_db()
    assert queue_item.retry_count == 2
    assert queue_item.status == QueueStatus.PENDING

    # Attempt 3 (reaches max_retries=3) -> DEAD_LETTER
    dispatch_single_queue_item(queue_item)
    queue_item.refresh_from_db()
    msg.refresh_from_db()
    assert queue_item.retry_count == 3
    assert queue_item.status == QueueStatus.DEAD_LETTER
    assert msg.status == NotificationStatus.FAILED
    assert "Simulated Telecom Gateway 503" in msg.error_message


# -------------------------------------------------------------
# 5. Automated Membership Expiry Reminders
# -------------------------------------------------------------

def test_membership_expiry_reminders_and_deduplication(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]
    pkg = test_setup["package"]
    today = timezone.localdate()

    m7 = create_test_membership_helper(
        member=member,
        package=pkg,
        branch=branch,
        start_date=today - timedelta(days=23),
        end_date=today + timedelta(days=7),
    )

    notif = trigger_membership_notification(TriggerEvent.MEMBERSHIP_EXPIRY_7D, m7, send_immediately=True)
    assert notif is not None
    assert notif.status == NotificationStatus.SENT
    assert "expire in 7 days" in notif.body
    assert member.first_name in notif.body

    # Running again on same day is deduplicated via idempotency key
    notif_dupe = trigger_membership_notification(TriggerEvent.MEMBERSHIP_EXPIRY_7D, m7, send_immediately=True)
    assert notif_dupe.id == notif.id


def test_dispatch_scheduled_reminders_job(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]
    pkg = test_setup["package"]
    today = timezone.localdate()

    create_test_membership_helper(
        member=member, package=pkg, branch=branch,
        start_date=today - timedelta(days=23), end_date=today + timedelta(days=7),
    )
    create_test_membership_helper(
        member=member, package=pkg, branch=branch,
        start_date=today - timedelta(days=27), end_date=today + timedelta(days=3),
    )

    counts = dispatch_scheduled_reminders()
    assert counts["expiry_7d"] == 1
    assert counts["expiry_3d"] == 1


# -------------------------------------------------------------
# 6. Billing & Payment Notifications (Authoritative Verification)
# -------------------------------------------------------------

def test_billing_payment_notification_authoritative(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]

    inv = Invoice.objects.create(
        invoice_number="INV-NOTIF-001",
        member=member,
        branch=branch,
        issue_date=timezone.localdate(),
        due_date=timezone.localdate(),
        subtotal=Decimal("1500.00"),
        total_amount=Decimal("1500.00"),
        balance_due=Decimal("0.00"),
        paid_amount=Decimal("1500.00"),
        status=Invoice.Status.PAID,
    )
    pm, _ = PaymentMethod.objects.get_or_create(
        code="CASH_NOTIF",
        defaults={"name": "Cash", "method_type": PaymentMethod.MethodType.CASH, "is_active": True},
    )

    # 1. Unverified/Pending payment MUST be rejected from sending payment confirmation
    pending_pay = Payment.objects.create(
        payment_number="PAY-NOTIF-001",
        invoice=inv,
        branch=branch,
        payment_method=pm,
        amount=Decimal("1500.00"),
        status=Payment.Status.PENDING,
        idempotency_key="IDEM-NOTIF-001",
    )
    res_pending = trigger_billing_notification(TriggerEvent.PAYMENT_CONFIRMED, payment=pending_pay)
    assert res_pending is None

    # 2. Completed/Verified payment successfully sends confirmation
    completed_pay = Payment.objects.create(
        payment_number="PAY-NOTIF-002",
        invoice=inv,
        branch=branch,
        payment_method=pm,
        amount=Decimal("1500.00"),
        status=Payment.Status.VERIFIED,
        idempotency_key="IDEM-NOTIF-002",
    )
    res_completed = trigger_billing_notification(TriggerEvent.PAYMENT_CONFIRMED, payment=completed_pay)
    assert res_completed is not None
    assert res_completed.status == NotificationStatus.SENT
    assert "Payment Confirmed" in res_completed.body
    assert "1500.00 ETB" in res_completed.body


# -------------------------------------------------------------
# 7. Class & Session Notifications
# -------------------------------------------------------------

def test_class_session_notification_and_cancelled_safety(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]
    trainer = test_setup["trainer"]

    ctype = ClassType.objects.create(
        name="Zumba Fitness Class",
        slug="zumba-fitness-class",
        default_capacity=20,
    )
    today = timezone.localdate()
    session = ClassSession.objects.create(
        class_type=ctype,
        branch=branch,
        trainer=trainer,
        session_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        status=ClassSession.SessionStatus.SCHEDULED,
        capacity=20,
    )
    booking = ClassBooking.objects.create(
        session=session,
        member=member,
        status=ClassBooking.BookingStatus.BOOKED,
    )

    notif = trigger_class_notification(TriggerEvent.CLASS_BOOKED, booking=booking)
    assert notif is not None
    assert "Zumba Fitness Class" in notif.body
    assert notif.status == NotificationStatus.SENT

    # If session is cancelled, reminders must not be sent
    session.status = ClassSession.SessionStatus.CANCELLED
    session.save()
    reminder = trigger_class_notification(TriggerEvent.CLASS_REMINDER, booking=booking, session=session)
    assert reminder is None


# -------------------------------------------------------------
# 8. POS & Inventory Deduplicated Alerts
# -------------------------------------------------------------

def test_inventory_alert_deduplication(test_setup):
    branch = test_setup["branch1"]
    cat = ProductCategory.objects.create(name="Supplements Notif", code="SUPP_NOTIF")
    prod = Product.objects.create(
        name="Whey Protein 1kg",
        sku="SUPP-WHEY-NOTIF-01",
        barcode="9988776655443",
        category=cat,
        selling_price=Decimal("4500.00"),
        cost_price=Decimal("3500.00"),
    )
    stock = StockItem.objects.create(
        product=prod,
        branch=branch,
        quantity_on_hand=Decimal("2.00"),
        reorder_level=Decimal("5.00"),
    )

    alerts1 = trigger_inventory_alert(TriggerEvent.LOW_STOCK_ALERT, prod, stock_item=stock, branch=branch)
    assert len(alerts1) >= 1
    assert InAppNotification.objects.filter(category=NotificationCategory.POS_INVENTORY).exists()

    alerts2 = trigger_inventory_alert(TriggerEvent.LOW_STOCK_ALERT, prod, stock_item=stock, branch=branch)
    assert not isinstance(alerts2, list)


# -------------------------------------------------------------
# 9. Bulk Messaging Campaign Execution
# -------------------------------------------------------------

def test_bulk_messaging_campaign(test_setup):
    branch1 = test_setup["branch1"]
    owner = test_setup["owner"]
    member1 = test_setup["member1"]
    pkg = test_setup["package"]

    create_test_membership_helper(
        member=member1, package=pkg, branch=branch1,
        start_date=timezone.localdate(), end_date=timezone.localdate() + timedelta(days=30),
    )

    campaign = BulkCampaign.objects.create(
        title="Ramadan Schedule Announcement",
        branch=branch1,
        target_audience=TargetAudience.ALL_ACTIVE_MEMBERS,
        message_body="Dear {{ member_name }}, our branches will operate 24/7 during Ramadan.",
        channel=NotificationChannel.SMS,
        created_by=owner,
    )

    res = execute_bulk_campaign(campaign, confirmed_by=owner)
    assert res["recipients"] >= 1
    assert res["sent"] >= 1

    campaign.refresh_from_db()
    assert campaign.status == CampaignStatus.COMPLETED
    assert campaign.sent_count >= 1
    assert campaign.confirmed_by == owner


# -------------------------------------------------------------
# 10. Rollback Transaction Safety
# -------------------------------------------------------------

def test_no_notifications_on_rolled_back_transaction(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]

    initial_count = NotificationMessage.objects.count()

    try:
        with transaction.atomic():
            create_and_queue_notification(
                recipient_address=member.phone,
                body="This should rollback",
                channel=NotificationChannel.SMS,
                branch=branch,
                recipient_member=member,
                send_immediately=False,
            )
            raise ValueError("Forced Rollback Test")
    except ValueError:
        pass

    assert NotificationMessage.objects.count() == initial_count


# -------------------------------------------------------------
# 11. Reports, Metrics & CSV Sanitization
# -------------------------------------------------------------

def test_communication_reports_and_csv_sanitization(test_setup):
    member = test_setup["member1"]
    branch = test_setup["branch1"]
    owner = test_setup["owner"]

    create_and_queue_notification(
        recipient_address=member.phone,
        body="Report test 1",
        channel=NotificationChannel.SMS,
        category=NotificationCategory.MEMBERSHIP,
        branch=branch,
        recipient_member=member,
        send_immediately=True,
    )

    metrics = get_notification_dashboard_metrics(owner)
    assert metrics["total_count"] >= 1
    assert metrics["sent_count"] >= 1

    report = get_communication_report_metrics(owner)
    assert report["total_messages"] >= 1
    assert len(report["by_channel"]) >= 1

    assert sanitize_for_csv("=1+2") == "'=1+2"
    assert sanitize_for_csv("@cmd") == "'@cmd"
    assert sanitize_for_csv("Normal Text") == "Normal Text"


# -------------------------------------------------------------
# 12. HTTP Endpoint Verification (Django Client)
# -------------------------------------------------------------

def test_notification_http_views(test_setup):
    client = Client()
    owner = test_setup["owner"]
    client.force_login(owner)

    # 1. Dashboard
    resp = client.get("/admin/notifications/dashboard/")
    assert resp.status_code == 200
    assert b"Notification" in resp.content

    # 2. Send SMS GET
    resp = client.get("/admin/notifications/sendsms/")
    assert resp.status_code == 200
    assert b"Send SMS Notification" in resp.content

    # 3. Send SMS POST
    resp_post = client.post("/admin/notifications/sendsms/", {
        "recipient_phone": "+251911223344",
        "message_body": "HTTP client test message",
        "branch_id": str(test_setup["branch1"].id),
    })
    assert resp_post.status_code == 302

    # 4. Bulk Campaign GET
    resp = client.get("/admin/notifications/bulkcampaign/")
    assert resp.status_code == 200
    assert b"Bulk Messaging Campaign" in resp.content

    # 5. Reports GET & CSV export
    resp = client.get("/admin/notifications/reports/")
    assert resp.status_code == 200
    assert b"Communication Reports" in resp.content

    resp_csv = client.get("/admin/notifications/reports/?export=csv")
    assert resp_csv.status_code == 200
    assert resp_csv["Content-Type"] == "text/csv"

    # 6. In-App unread API
    resp_api = client.get("/admin/notifications/api/inapp/unread/")
    assert resp_api.status_code == 200
    assert "count" in resp_api.json()
