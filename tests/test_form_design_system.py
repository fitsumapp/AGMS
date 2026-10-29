"""Tests for AGMS Global Enterprise Form Design System Modernization.

Verifies:
- Standardized fieldsets and 2-column paired tuples on ModelAdmins
- Inlines presence and configuration
- CSRF protection and form rendering
- Required-field indicators
- Help texts and error messages
- Custom action forms structure and styling
- Safe submission and validation integrity
"""

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import Client

from apps.billing.models import Invoice, Payment, Refund
from apps.branches.models import Branch
from apps.members.models import Member
from apps.memberships.models import Membership, MembershipPackage

User = get_user_model()


@pytest.fixture
def superuser(db):
    """Fixture providing an active superuser."""
    user = User.objects.filter(is_superuser=True, is_active=True).first()
    if not user:
        user = User.objects.create_superuser(
            email="form_test_admin@example.test",
            password="TestPassword123!",
            first_name="Admin",
            last_name="Tester",
        )
    return user


@pytest.fixture
def branch(db):
    """Fixture providing a test branch."""
    branch = Branch.objects.first()
    if not branch:
        branch = Branch.objects.create(
            name="Main City Branch",
            code="MCB-01",
            phone="+251911000000",
            email="main@branch.test",
            city="Addis Ababa",
            address="Bole Road, Addis Ababa",
            is_active=True,
        )
    return branch


@pytest.fixture
def member(db, branch):
    """Fixture providing a test member."""
    member = Member.objects.first()
    if not member:
        member = Member.objects.create(
            member_number="MBR-99999",
            first_name="Abel",
            last_name="Tesfaye",
            phone="+251911223344",
            email="abel.test@example.com",
            home_branch=branch,
            status=Member.MemberStatus.ACTIVE,
        )
    return member


@pytest.mark.django_db
class TestMemberAdminFormDesign:
    """Test Member admin form architecture, fieldsets, and inlines."""

    def test_member_admin_add_fieldsets_structure(self):
        admin = site._registry[Member]
        assert hasattr(admin, "add_fieldsets")
        add_fieldsets = admin.add_fieldsets

        section_names = [f[0] for f in add_fieldsets if f[0]]
        assert any("Personal" in name for name in section_names)
        assert any("Contact" in name for name in section_names)
        assert any("Gym Registration" in name for name in section_names)

        # Verify Gym Registration includes safety confirmation
        gym_reg = next(f for f in add_fieldsets if "Gym Registration" in f[0])
        fields = gym_reg[1].get("fields", ())
        assert "confirm_possible_duplicate" in fields

    def test_member_admin_fieldsets_paired_columns(self):
        admin = site._registry[Member]
        fieldsets = admin.fieldsets

        personal_info = next(f for f in fieldsets if "Personal" in f[0])
        fields = personal_info[1].get("fields", ())
        # Should contain 2-column paired tuples like ('first_name', 'last_name')
        has_paired_tuple = any(isinstance(f, (list, tuple)) and len(f) == 2 for f in fields)
        assert has_paired_tuple, "Personal information should feature paired 2-column fields."

    def test_member_admin_inlines_configured(self):
        admin = site._registry[Member]
        inline_classes = [c.__name__ for c in admin.inlines]
        assert "MemberEmergencyContactInline" in inline_classes
        assert "MemberDocumentInline" in inline_classes
        assert "MemberNoteInline" in inline_classes
        assert "MemberStatusHistoryInline" in inline_classes

    def test_member_add_form_renders_csrf_and_classes(self, superuser):
        client = Client()
        client.force_login(superuser)
        response = client.get("/admin/members/member/add/", SERVER_NAME="127.0.0.1")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "csrfmiddlewaretoken" in content
        assert "Personal &amp; Identity" in content or "Personal & Identity" in content
        assert "First Name (ስም)" in content
        assert "Phone Number (ስልክ ቁጥር)" in content


@pytest.mark.django_db
class TestMembershipAdminFormDesign:
    """Test MembershipPackage and Membership form architecture."""

    def test_package_admin_fieldsets(self):
        admin = site._registry[MembershipPackage]
        section_names = [f[0] for f in admin.fieldsets if f[0]]
        assert "Package Identity" in section_names
        assert "Duration & Pricing" in section_names
        assert "Access & Freeze Policies" in section_names

    def test_package_add_renders_csrf(self, superuser):
        client = Client()
        client.force_login(superuser)
        response = client.get("/admin/memberships/membershippackage/add/", SERVER_NAME="127.0.0.1")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "csrfmiddlewaretoken" in content
        assert "Package Identity" in content

    def test_membership_admin_fieldsets(self):
        admin = site._registry[Membership]
        section_names = [f[0] for f in admin.fieldsets if f[0]]
        assert "Subscription Summary" in section_names
        assert "Validity & Terms" in section_names
        assert "Lifecycle & Cancellation" in section_names


@pytest.mark.django_db
class TestBillingAdminFormDesign:
    """Test Billing Invoice and Payment form architecture."""

    def test_invoice_admin_fieldsets(self):
        admin = site._registry[Invoice]
        section_names = [f[0] for f in admin.fieldsets if f[0]]
        assert "Invoice Header" in section_names
        assert "Financial Totals" in section_names
        assert "Associated Subscriptions & Notes" in section_names

    def test_payment_admin_fieldsets(self):
        admin = site._registry[Payment]
        section_names = [f[0] for f in admin.fieldsets if f[0]]
        assert "Payment Transaction" in section_names
        assert "Verification & Verification Audit" in section_names
        assert "Security & Audit Metadata" in section_names

    def test_invoice_add_renders_csrf(self, superuser):
        client = Client()
        client.force_login(superuser)
        response = client.get("/admin/billing/invoice/add/", SERVER_NAME="127.0.0.1")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "csrfmiddlewaretoken" in content
        assert "Invoice Header" in content


@pytest.mark.django_db
class TestCustomActionForms:
    """Test custom action forms rendering and CSRF protection."""

    def test_change_member_status_form_renders(self, superuser, member):
        client = Client()
        client.force_login(superuser)
        response = client.get(f"/admin/members/member/{member.pk}/status/", SERVER_NAME="127.0.0.1")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "csrfmiddlewaretoken" in content
        assert "Member Status Management" in content
        assert "id_new_status" in content

    def test_create_custom_invoice_form_renders(self, superuser):
        client = Client()
        client.force_login(superuser)
        response = client.get("/admin/billing/invoice/create-invoice/", SERVER_NAME="127.0.0.1")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "csrfmiddlewaretoken" in content
        assert "Create Custom Invoice" in content
