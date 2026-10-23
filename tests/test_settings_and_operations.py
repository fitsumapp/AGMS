import csv
from decimal import Decimal
import io
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.branches.models import Branch, BranchSettings, AuditEvent, UserBranchAccess
from apps.core.models import GymProfile, GymSettings, FeatureFlag, BackupConfiguration
from apps.core.services import (
    resolve_setting,
    update_branch_settings,
    is_feature_enabled,
    seed_feature_flags,
    get_system_health_metrics,
    get_backup_readiness_report,
)
from apps.core.importers import (
    sanitize_csv_cell,
    import_members,
    import_products,
    import_equipment,
    export_branch_data,
)
from apps.members.models import Member
from apps.inventory.models import Product, ProductCategory, StockItem
from apps.facilities.models import Equipment, EquipmentCategory, FacilityArea

pytestmark = pytest.mark.django_db


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def superuser(db):
    return User.objects.create_superuser("superadmin@agms.test", "Password123!")


@pytest.fixture
def gym_owner(db):
    user = User.objects.create_user("owner@agms.test", "Password123!", is_staff=True)
    from django.contrib.auth.models import Group
    group, _ = Group.objects.get_or_create(name="Gym Owner")
    user.groups.add(group)
    return user


@pytest.fixture
def branch_manager(db, main_branch):
    user = User.objects.create_user("manager@agms.test", "Password123!", is_staff=True)
    from django.contrib.auth.models import Group
    group, _ = Group.objects.get_or_create(name="Branch Manager")
    user.groups.add(group)
    UserBranchAccess.objects.create(user=user, branch=main_branch, is_active=True, is_primary=True)
    return user


@pytest.fixture
def unauthorized_staff(db):
    user = User.objects.create_user("staff@agms.test", "Password123!", is_staff=True)
    from django.contrib.auth.models import Group
    group, _ = Group.objects.get_or_create(name="Trainer")
    user.groups.add(group)
    return user


@pytest.fixture
def gym_profile(db):
    profile, _ = GymProfile.objects.get_or_create(
        singleton=1,
        defaults={
            "name": "Bole Alpha Gym",
            "legal_name": "Alpha Fitness PLC",
            "default_currency": "ETB",
            "default_timezone": "Africa/Addis_Ababa",
            "default_language": "en",
            "fiscal_year_start_month": 1,
            "member_id_prefix": "BAL-",
            "member_id_sequence_length": 5,
            "min_member_age": 16,
        },
    )
    return profile


@pytest.fixture
def gym_settings(db, gym_profile):
    settings, _ = GymSettings.objects.get_or_create(
        profile=gym_profile,
        defaults={
            "allow_access_with_balance": False,
            "membership_freeze_enabled": True,
            "require_freeze_approval": True,
            "attendance_qr_enabled": True,
            "attendance_manual_enabled": True,
            "invoice_due_days": 14,
            "pos_max_discount_percent": Decimal("10.00"),
            "default_locker_deposit_etb": Decimal("150.00"),
        },
    )
    return settings


@pytest.fixture
def main_branch(db):
    return Branch.objects.create(
        code="BOLE",
        name="Bole Medhanialem Branch",
        phone="+251911000001",
        email="bole@alphagym.test",
        address="Bole Road, Addis Ababa",
        is_active=True,
    )


@pytest.fixture
def secondary_branch(db):
    return Branch.objects.create(
        code="KIRKOS",
        name="Kirkos Branch",
        phone="+251911000002",
        email="kirkos@alphagym.test",
        address="Kirkos, Addis Ababa",
        is_active=True,
    )


# ==============================================================================
# 1. Gym Settings Lifecycle and Authoritative Defaults
# ==============================================================================

def test_gym_settings_authoritative_defaults(gym_profile, gym_settings):
    assert gym_profile.default_currency == "ETB"
    assert gym_profile.default_timezone == "Africa/Addis_Ababa"
    assert gym_settings.allow_access_with_balance is False
    assert gym_settings.require_freeze_approval is True
    assert gym_settings.attendance_qr_enabled is True
    assert gym_settings.attendance_manual_enabled is True
    assert gym_settings.pos_max_discount_percent == Decimal("10.00")


def test_attendance_safety_invariant_rejects_disabling_both(gym_settings):
    gym_settings.attendance_qr_enabled = False
    gym_settings.attendance_manual_enabled = False

    with pytest.raises(ValidationError) as exc_info:
        gym_settings.clean()
    assert "At least one attendance method" in str(exc_info.value)

    # Invariant in database CheckConstraint
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            GymSettings.objects.filter(pk=gym_settings.pk).update(
                attendance_qr_enabled=False, attendance_manual_enabled=False
            )


def test_branch_settings_attendance_safety_invariant(main_branch):
    branch_settings, _ = BranchSettings.objects.get_or_create(branch=main_branch)
    branch_settings.attendance_qr_enabled = False
    branch_settings.attendance_manual_enabled = False

    with pytest.raises(ValidationError) as exc_info:
        branch_settings.clean()
    assert "At least one attendance method" in str(exc_info.value)


# ==============================================================================
# 2. Three-Tier Precedence Resolution System
# ==============================================================================

def test_resolve_setting_three_tier_precedence(main_branch, gym_settings):
    # Tier 1: Fallback to global setting when no branch override exists
    branch_settings, _ = BranchSettings.objects.get_or_create(branch=main_branch)
    branch_settings.max_discount_percent_override = None
    branch_settings.save()

    resolved = resolve_setting(main_branch, "pos_max_discount_percent")
    assert resolved == Decimal("10.00")

    # Tier 2: Branch override takes precedence when present
    branch_settings.max_discount_percent_override = Decimal("25.00")
    branch_settings.save()

    resolved_override = resolve_setting(main_branch, "pos_max_discount_percent")
    assert resolved_override == Decimal("25.00")

    # Tier 3: Explicit code default when neither branch nor global exists
    resolved_default = resolve_setting(main_branch, "non_existent_key", default="DEFAULT_VALUE")
    assert resolved_default == "DEFAULT_VALUE"


def test_update_branch_settings_atomic_and_audited(gym_owner, main_branch):
    updated = update_branch_settings(
        actor=gym_owner,
        branch=main_branch,
        updates={
            "max_discount_percent_override": Decimal("18.50"),
            "locker_deposit_etb_override": Decimal("300.00"),
        },
    )
    assert updated.max_discount_percent_override == Decimal("18.50")
    assert updated.locker_deposit_etb_override == Decimal("300.00")

    # Verify audit event
    audits = AuditEvent.objects.filter(
        branch=main_branch,
        action="branch_settings_updated",
    )
    assert audits.exists()
    assert all(a.actor == gym_owner for a in audits)
    setting_names = {a.metadata.get("setting_name") for a in audits}
    assert "max_discount_percent_override" in setting_names
    assert "locker_deposit_etb_override" in setting_names


# ==============================================================================
# 3. Feature Flag Engine & Dependency Validation
# ==============================================================================

def test_feature_flag_seeding_and_evaluation(main_branch):
    seed_feature_flags()
    assert FeatureFlag.objects.filter(code="ATTENDANCE_QR").exists()
    assert FeatureFlag.objects.filter(code="CLASS_WAITLIST").exists()

    # Base feature enabled
    assert is_feature_enabled("ATTENDANCE_QR", branch=main_branch) is True

    # Dependency check: disable parent feature
    online_booking = FeatureFlag.objects.get(code="ONLINE_BOOKING")
    online_booking.is_enabled = False
    online_booking.save()

    # CLASS_WAITLIST requires ONLINE_BOOKING -> must evaluate to False
    assert is_feature_enabled("CLASS_WAITLIST", branch=main_branch) is False


def test_feature_flag_branch_scoping(main_branch, secondary_branch):
    flag = FeatureFlag.objects.create(
        code="EXPERIMENTAL_POS",
        name="Experimental POS Layout",
        category=FeatureFlag.Category.POS,
        is_enabled=True,
        branch=main_branch,
    )

    # Enabled for main_branch
    assert is_feature_enabled("EXPERIMENTAL_POS", branch=main_branch) is True
    # Not enabled for secondary_branch
    assert is_feature_enabled("EXPERIMENTAL_POS", branch=secondary_branch) is False


def test_feature_flag_dependency_validation_error():
    flag = FeatureFlag(
        code="TEST_DEP",
        name="Test Dependency",
        is_enabled=True,
        requires_features=["NON_EXISTENT_FEATURE"],
    )
    with pytest.raises(ValidationError) as exc:
        flag.clean()
    assert "non-existent feature" in str(exc.value)


# ==============================================================================
# 4. Data Import Engine & CSV Formula Injection Protection
# ==============================================================================

def test_csv_formula_injection_sanitization():
    # Dangerous formula prefixes
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert sanitize_csv_cell("-123.45") == "'-123.45"
    assert sanitize_csv_cell("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_cell("\tTABBED") == "'\tTABBED"
    assert sanitize_csv_cell("\rRETURN") == "'\rRETURN"

    # Safe cells
    assert sanitize_csv_cell("Standard Name") == "Standard Name"
    assert sanitize_csv_cell("Abebe") == "Abebe"
    assert sanitize_csv_cell(None) == ""


def test_member_import_dry_run_and_commit(gym_owner, main_branch):
    csv_content = (
        "first_name,last_name,phone,email,gender,date_of_birth\n"
        "Abebe,Bikila,+251911112233,abebe.bikila@example.com,MALE,1990-01-01\n"
        "Meseret,Defar,+251922334455,meseret.defar@example.com,FEMALE,1992-05-15\n"
    )

    # 1. Dry run
    dry_result = import_members(
        actor=gym_owner,
        branch=main_branch,
        file_obj=io.BytesIO(csv_content.encode("utf-8")),
        dry_run=True,
    )
    assert dry_result["valid_count"] == 2
    assert dry_result["created_count"] == 0
    assert Member.objects.filter(phone="+251911112233").exists() is False

    # 2. Live commit
    live_result = import_members(
        actor=gym_owner,
        branch=main_branch,
        file_obj=io.BytesIO(csv_content.encode("utf-8")),
        dry_run=False,
    )
    assert live_result["created_count"] == 2
    assert Member.objects.filter(phone="+251911112233").exists() is True

    # 3. Duplicate detection on re-import
    dup_result = import_members(
        actor=gym_owner,
        branch=main_branch,
        file_obj=io.BytesIO(csv_content.encode("utf-8")),
        dry_run=False,
    )
    assert dup_result["created_count"] == 0
    assert len(dup_result["duplicates"]) == 2


def test_product_import_and_duplicate_sku(gym_owner, main_branch):
    ProductCategory.objects.get_or_create(code="SUPP", defaults={"name": "Supplements"})
    ProductCategory.objects.get_or_create(code="BEV", defaults={"name": "Beverages"})

    csv_content = (
        "name,sku,category_code,unit_price_etb,initial_stock\n"
        "Alpha Whey 2kg,WHEY-2KG,SUPP,4500.00,20\n"
        "Energy Drink 500ml,NRG-500,BEV,80.00,100\n"
    )

    result = import_products(
        actor=gym_owner,
        branch=main_branch,
        file_obj=io.BytesIO(csv_content.encode("utf-8")),
        dry_run=False,
    )
    assert result["created_count"] == 2

    # Stock items created in main_branch
    product = Product.objects.get(sku="WHEY-2KG")
    stock_item = StockItem.objects.get(product=product, branch=main_branch)
    assert stock_item.quantity_on_hand == Decimal("20.00")


def test_equipment_import(gym_owner, main_branch):
    EquipmentCategory.objects.get_or_create(code="CARDIO", defaults={"name": "Cardio Equipment"})
    FacilityArea.objects.get_or_create(branch=main_branch, code="MAIN-GYM", defaults={"name": "Main Gym Floor"})

    csv_content = (
        "asset_code,name,category_code,area_code,manufacturer,model_number\n"
        "TRD-900,Technogym Run Excite,CARDIO,MAIN-GYM,Technogym,Excite-900\n"
    )

    result = import_equipment(
        actor=gym_owner,
        branch=main_branch,
        file_obj=io.BytesIO(csv_content.encode("utf-8")),
        dry_run=False,
    )
    assert result["created_count"] == 1
    assert Equipment.objects.filter(asset_code="TRD-900", branch=main_branch).exists()


def test_export_branch_data_isolation(gym_owner, main_branch, secondary_branch):
    Member.objects.create(
        home_branch=main_branch,
        member_number="BAL-0001",
        first_name="Main",
        last_name="Member",
        phone="+251911888999",
        date_of_birth="1995-01-01",
    )
    Member.objects.create(
        home_branch=secondary_branch,
        member_number="KIR-0001",
        first_name="Secondary",
        last_name="Member",
        phone="+251922888999",
        date_of_birth="1996-01-01",
    )

    response = export_branch_data(actor=gym_owner, branch=main_branch, data_type="members")
    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")

    assert "BAL-0001" in content
    assert "+251911888999" in content
    # Strict branch isolation: secondary branch member NOT present
    assert "KIR-0001" not in content
    assert "+251922888999" not in content


# ==============================================================================
# 5. System Health, Storage, and Backup Verification
# ==============================================================================

def test_system_health_metrics_contract():
    metrics = get_system_health_metrics()
    assert "status" in metrics
    assert "database" in metrics
    assert "notifications" in metrics
    assert "migrations" in metrics
    assert "storage" in metrics
    assert "approvals" in metrics

    # Real DB connection ping
    assert metrics["database"]["connected"] is True
    assert isinstance(metrics["database"]["latency_ms"], float)
    assert metrics["database"]["latency_ms"] >= 0


def test_backup_readiness_report():
    report = get_backup_readiness_report()
    assert "config" in report
    assert "directory_exists" in report
    assert "directory_writable" in report
    assert "free_space_mb" in report
    assert "retention_days" in report
    assert report["retention_days"] == 30


# ==============================================================================
# 6. Role Boundaries & Privilege Escalation Defenses
# ==============================================================================

def test_operational_views_access_control(client, superuser, gym_owner, branch_manager, unauthorized_staff):
    health_url = reverse("system-health")
    backup_url = reverse("backup-readiness")
    data_url = reverse("data-management")

    # Superuser has access
    client.force_login(superuser)
    assert client.get(health_url).status_code == 200
    assert client.get(backup_url).status_code == 200
    assert client.get(data_url).status_code == 200

    # Gym Owner has access
    client.force_login(gym_owner)
    assert client.get(health_url).status_code == 200
    assert client.get(backup_url).status_code == 200
    assert client.get(data_url).status_code == 200

    # Branch Manager has operational access, but NOT technical backup config
    client.force_login(branch_manager)
    assert client.get(health_url).status_code == 200
    assert client.get(data_url).status_code == 200
    # Backup readiness is restricted to Owner/Superuser
    assert client.get(backup_url).status_code == 403

    # Trainer / Unauthorized Staff is rejected across all operational endpoints
    client.force_login(unauthorized_staff)
    assert client.get(health_url).status_code == 403
    assert client.get(backup_url).status_code == 403
    assert client.get(data_url).status_code == 403


def test_cross_branch_export_permission_denial(client, branch_manager, secondary_branch):
    # branch_manager only has access to main_branch, NOT secondary_branch
    client.force_login(branch_manager)
    url = f"{reverse('data-export')}?type=members&branch={secondary_branch.pk}"
    response = client.get(url)
    assert response.status_code == 403
