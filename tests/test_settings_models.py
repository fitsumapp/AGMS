import pytest
from django.db import IntegrityError, transaction
from apps.core.models import GymProfile, GymSettings

pytestmark = pytest.mark.django_db

def test_defaults():
    profile = GymProfile.objects.create(name="Test gym")
    policy = GymSettings.objects.create(profile=profile)
    assert profile.default_currency == "ETB"
    assert profile.default_timezone == "Africa/Addis_Ababa"
    assert policy.allow_access_with_balance is False
    assert policy.membership_freeze_enabled is True
    assert policy.require_freeze_approval is True
    assert policy.cross_branch_access_mode == "PACKAGE_BASED"
    assert policy.early_renewal_mode == "EXTEND_EXISTING_END_DATE"

def test_singleton_database_constraint():
    GymProfile.objects.create(name="First")
    with pytest.raises(IntegrityError), transaction.atomic():
        GymProfile.objects.create(name="Second")
    with pytest.raises(IntegrityError), transaction.atomic():
        GymProfile.objects.create(name="Bypass", singleton=2)

def test_single_settings_per_profile():
    profile = GymProfile.objects.create(name="Test")
    GymSettings.objects.create(profile=profile)
    with pytest.raises(IntegrityError), transaction.atomic():
        GymSettings.objects.create(profile=profile)

def test_policy_database_constraints():
    profile = GymProfile.objects.create(name="Test")
    with pytest.raises(IntegrityError), transaction.atomic():
        GymSettings.objects.create(profile=profile, cross_branch_access_mode="INVALID")

def test_settings_audit_fields(client, root):
    from django.urls import reverse
    client.force_login(root)
    response = client.post(reverse("admin:core_gymprofile_add"), {
        "name": "Configured gym", "default_currency": "ETB", "default_timezone": "Africa/Addis_Ababa", "_save": "Save"})
    assert response.status_code == 302
    profile = GymProfile.objects.get()
    assert profile.created_by == root and profile.updated_by == root
