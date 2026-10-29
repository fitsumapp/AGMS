import pytest
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone
from django.test import Client

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.members.models import Member
from apps.memberships.models import Membership, MembershipPackage
from apps.billing.models import Invoice, Payment, PaymentMethod, Refund
from apps.members.selectors import get_member_metrics
from apps.memberships.selectors import get_membership_metrics
from apps.billing.selectors import get_billing_metrics


@pytest.mark.django_db
def test_kpi_cards_no_coming_soon_badges(client):
    """Task 1: Verify 'Coming soon' badges are removed from functional KPI cards."""
    superuser = User.objects.create_superuser(
        email="superadmin@test.com", password="Password123!@#"
    )
    client.force_login(superuser)
    response = client.get("/admin/", SERVER_NAME="127.0.0.1")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Coming soon" not in content


@pytest.mark.django_db
def test_kpi_card_total_registered_members(client):
    """Task 3: Verify ambiguous 'Active Members Profiles' is renamed to 'Total Registered Members'."""
    superuser = User.objects.create_superuser(
        email="superadmin@test.com", password="Password123!@#"
    )
    branch = Branch.objects.create(name="Central", code="CEN-1", address="Bole")
    Member.objects.create(
        member_number="MEM-001",
        first_name="Abebe",
        last_name="Bikila",
        phone="+251911000001",
        home_branch=branch,
        status=Member.MemberStatus.ACTIVE,
    )
    Member.objects.create(
        member_number="MEM-002",
        first_name="Kenenisa",
        last_name="Bekele",
        phone="+251911000002",
        home_branch=branch,
        status=Member.MemberStatus.INACTIVE,
    )

    client.force_login(superuser)
    response = client.get("/admin/", SERVER_NAME="127.0.0.1")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    assert "Total Registered Members" in content
    assert "Active Members Profiles" not in content
    assert "1 active profile" in content


@pytest.mark.django_db
def test_member_growth_monthly_range(db):
    """Task 4: Audit member growth calculation to ensure correct registration-date range."""
    user = User.objects.create_superuser(
        email="superadmin@test.com", password="Password123!@#"
    )
    branch = Branch.objects.create(name="Central", code="CEN-1", address="Bole")

    today = timezone.localdate()
    month_start = today.replace(day=1)

    # Member registered this month
    m1 = Member.objects.create(
        member_number="MEM-THIS-MONTH",
        first_name="This",
        last_name="Month",
        phone="+251911000003",
        home_branch=branch,
        status=Member.MemberStatus.ACTIVE,
    )
    # Member registered last month
    m2 = Member.objects.create(
        member_number="MEM-LAST-MONTH",
        first_name="Last",
        last_name="Month",
        phone="+251911000004",
        home_branch=branch,
        status=Member.MemberStatus.ACTIVE,
    )
    # Manually shift m2 joined_at to previous month
    prev_month_date = (month_start - timedelta(days=15))
    Member.objects.filter(pk=m2.pk).update(
        joined_at=timezone.make_aware(
            timezone.datetime(prev_month_date.year, prev_month_date.month, 10, 12, 0, 0)
        )
    )

    metrics = get_member_metrics(user)
    assert metrics["total_members"] == 2
    assert metrics["new_this_month"] == 1


@pytest.mark.django_db
def test_currency_formatting_standardization(client):
    """Task 2 & 5: Verify ETB currency formatting with thousand separators and 2 decimal places."""
    superuser = User.objects.create_superuser(
        email="superadmin@test.com", password="Password123!@#"
    )
    branch = Branch.objects.create(name="Central", code="CEN-1", address="Bole")
    member = Member.objects.create(
        member_number="MEM-001",
        first_name="Abebe",
        last_name="Bikila",
        phone="+251911000005",
        home_branch=branch,
        status=Member.MemberStatus.ACTIVE,
    )

    today = timezone.localdate()
    # Create invoice and verified payment
    inv = Invoice.objects.create(
        invoice_number="INV-TEST-001",
        member=member,
        branch=branch,
        issue_date=today,
        due_date=today + timedelta(days=5),
        status=Invoice.Status.PARTIALLY_PAID,
        subtotal=Decimal("12500.50"),
        total_amount=Decimal("12500.50"),
        paid_amount=Decimal("10000.00"),
        balance_due=Decimal("2500.50"),
    )
    pm, _ = PaymentMethod.objects.get_or_create(code="CASH", defaults={"name": "Cash", "is_active": True})
    Payment.objects.create(
        payment_number="PAY-TEST-001",
        invoice=inv,
        branch=branch,
        payment_method=pm,
        amount=Decimal("10000.00"),
        status=Payment.Status.VERIFIED,
        paid_at=timezone.now(),
    )

    client.force_login(superuser)
    response = client.get("/admin/", SERVER_NAME="127.0.0.1")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Formatted values with commas and 2 decimal places
    assert "10,000.00 ETB" in content
    assert "2,500.50 ETB" in content


@pytest.mark.django_db
def test_branch_aware_kpi_filtering(db):
    """Task 9: Verify KPI totals adjust correctly per selected branch."""
    owner = User.objects.create_user(
        email="owner@test.com", password="Password123!@#", is_staff=True
    )
    from django.contrib.auth.models import Group
    owner_group, _ = Group.objects.get_or_create(name="Gym Owner")
    owner.groups.add(owner_group)

    b1 = Branch.objects.create(name="Branch One", code="B1", address="Bole")
    b2 = Branch.objects.create(name="Branch Two", code="B2", address="Addis")

    # Member in B1
    Member.objects.create(
        member_number="MEM-B1",
        first_name="One",
        last_name="Member",
        phone="+251911000006",
        home_branch=b1,
        status=Member.MemberStatus.ACTIVE,
    )
    # Member in B2
    Member.objects.create(
        member_number="MEM-B2",
        first_name="Two",
        last_name="Member",
        phone="+251911000007",
        home_branch=b2,
        status=Member.MemberStatus.ACTIVE,
    )

    # All branches
    all_metrics = get_member_metrics(owner)
    assert all_metrics["total_members"] == 2

    # Scoped to Branch 1
    b1_metrics = get_member_metrics(owner, active_branch=b1)
    assert b1_metrics["total_members"] == 1

    # Scoped to Branch 2
    b2_metrics = get_member_metrics(owner, active_branch=b2)
    assert b2_metrics["total_members"] == 1


@pytest.mark.django_db
def test_gym_owner_vs_superuser_technical_admin_permissions(client):
    """Task 8: Verify Gym Owner cannot access technical users/groups, while Superuser can."""
    from django.contrib.auth.models import Group
    owner_group, _ = Group.objects.get_or_create(name="Gym Owner")

    owner = User.objects.create_user(
        email="gymowner@test.com", password="Password123!@#", is_staff=True, is_superuser=False
    )
    owner.groups.add(owner_group)

    superuser = User.objects.create_superuser(
        email="techsuper@test.com", password="Password123!@#"
    )

    # 1. Gym Owner
    client.force_login(owner)
    owner_dash = client.get("/admin/", SERVER_NAME="127.0.0.1")
    assert owner_dash.status_code == 200

    owner_user_admin = client.get("/admin/accounts/user/", SERVER_NAME="127.0.0.1")
    assert owner_user_admin.status_code in (302, 403)

    owner_group_admin = client.get("/admin/auth/group/", SERVER_NAME="127.0.0.1")
    assert owner_group_admin.status_code in (302, 403)

    # 2. Superuser
    client.force_login(superuser)
    super_dash = client.get("/admin/", SERVER_NAME="127.0.0.1")
    assert super_dash.status_code == 200

    super_user_admin = client.get("/admin/accounts/user/", SERVER_NAME="127.0.0.1")
    assert super_user_admin.status_code == 200

    super_group_admin = client.get("/admin/auth/group/", SERVER_NAME="127.0.0.1")
    assert super_group_admin.status_code == 200
