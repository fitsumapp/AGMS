import pytest
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.roles import ROLE_PERMISSIONS, is_owner
from apps.branches.models import BUSINESS_ROLES

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    call_command("seed_business_roles", verbosity=0)
    return {name: Group.objects.get(name=name) for name in BUSINESS_ROLES}


def business_user(role, group, email=None):
    user = User.objects.create_user(email or f"{role.lower().replace(' ', '-')}@example.test",
        "Strong-Role-Passphrase-844!", is_staff=True)
    user.groups.add(group)
    return user


def test_role_seed_idempotent_and_limited(seeded):
    extra = Permission.objects.get(content_type__app_label="auth", codename="delete_group")
    seeded["Trainer"].permissions.add(extra)
    call_command("seed_business_roles", verbosity=0)
    assert Group.objects.filter(name__in=BUSINESS_ROLES).count() == 6
    for role, group in seeded.items():
        actual = {(p.content_type.app_label, p.codename) for p in group.permissions.select_related("content_type")}
        expected = {(app, codename) for app, names in ROLE_PERMISSIONS[role].items() for codename in names}
        assert actual == expected
        assert not group.permissions.filter(codename__in=["change_user", "change_group", "delete_group"]).exists()


def test_owner_is_business_role_not_superuser(client, seeded):
    owner = business_user("Gym Owner", seeded["Gym Owner"])
    assert is_owner(owner) and not owner.is_superuser
    client.force_login(owner)
    assert client.get(reverse("admin:index")).status_code == 200
    for model in ["accounts_user", "auth_group", "core_gymprofile", "core_gymsettings"]:
        assert client.get(reverse(f"admin:{model}_changelist")).status_code == 403
    assert client.get(reverse("admin:accounts_staffaccount_changelist")).status_code == 200
    assert client.get(reverse("admin:core_gymbranding_changelist")).status_code == 200


def test_staff_creation_workflow_rejects_privilege_escalation(client, seeded):
    owner = business_user("Gym Owner", seeded["Gym Owner"])
    client.force_login(owner)
    url = reverse("admin:accounts_staffaccount_add")
    password = "Strong-Staff-Passphrase-844!"
    response = client.post(url, {"email": "newstaff@example.test", "first_name": "New", "last_name": "Staff",
        "password1": password, "password2": password, "role": seeded["Receptionist"].pk,
        "is_superuser": "on", "is_staff": "on", "groups": [seeded["Gym Owner"].pk], "_save": "Save"})
    assert response.status_code == 302
    staff = User.objects.get(email="newstaff@example.test")
    assert staff.is_staff and not staff.is_superuser and staff.check_password(password)
    assert set(staff.groups.values_list("name", flat=True)) == {"Receptionist"}


def test_owner_cannot_assign_owner_group(client, seeded):
    owner = business_user("Gym Owner", seeded["Gym Owner"])
    client.force_login(owner)
    response = client.post(reverse("admin:accounts_staffaccount_add"), {
        "email": "escalation@example.test", "password1": "Strong-Staff-Passphrase-844!",
        "password2": "Strong-Staff-Passphrase-844!", "role": seeded["Gym Owner"].pk})
    assert response.status_code == 200
    assert not User.objects.filter(email="escalation@example.test").exists()


@pytest.mark.parametrize("role", ["Receptionist", "Accountant", "Trainer", "POS Staff", "Branch Manager"])
def test_business_roles_have_no_technical_admin(client, seeded, role):
    user = business_user(role, seeded[role])
    client.force_login(user)
    assert client.get(reverse("admin:auth_group_changelist")).status_code == 403
    assert client.get(reverse("admin:accounts_user_changelist")).status_code == 403
    assert client.get(reverse("admin:accounts_staffaccount_changelist")).status_code == 403
    assert client.get(reverse("admin:core_gymsettings_changelist")).status_code == 403


def test_owner_branding_form_excludes_technical_fields(client, seeded):
    from apps.core.models import GymProfile
    owner = business_user("Gym Owner", seeded["Gym Owner"])
    profile = GymProfile.objects.create(name="Original")
    client.force_login(owner)
    url = reverse("admin:core_gymbranding_change", args=[profile.pk])
    assert client.get(url).status_code == 200
    assert client.post(url, {"name": "Updated", "default_currency": "USD",
        "default_timezone": "UTC", "_save": "Save"}).status_code == 302
    profile.refresh_from_db()
    assert profile.name == "Updated" and profile.default_currency == "ETB"
    assert profile.default_timezone == "Africa/Addis_Ababa"
