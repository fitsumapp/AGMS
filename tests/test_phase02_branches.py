import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.core.management import call_command
from django.contrib.auth.models import Group

from apps.accounts.models import User
from apps.branches.models import Branch, BranchSettings, UserBranchAccess, AuditEvent
from apps.branches.services import (accessible_branches, active_branch, assign_branch,
    can_access_branch, can_manage_branch, revoke_branch, scoped_queryset)

pytestmark = pytest.mark.django_db


@pytest.fixture
def roles():
    call_command("seed_business_roles", verbosity=0)
    return {name: Group.objects.get(name=name) for name in ["Gym Owner", "Branch Manager", "Receptionist", "Trainer"]}


@pytest.fixture
def owner(roles):
    user = User.objects.create_user("owner@example.test", is_staff=True)
    user.groups.add(roles["Gym Owner"])
    return user


@pytest.fixture
def manager(roles):
    user = User.objects.create_user("manager@example.test", is_staff=True)
    user.groups.add(roles["Branch Manager"])
    return user


@pytest.fixture
def branches():
    return (Branch.objects.create(code="  ADD-1 ", name="Addis", address="Bole"),
        Branch.objects.create(code="DIRE-2", name="Dire", address="Central"))


def test_branch_creation_normalization_and_constraints(branches):
    first, _ = branches
    assert first.code == "ADD-1" and first.pk.version == 4
    with pytest.raises(ValidationError):
        Branch.objects.create(code="add-1", name="Duplicate", address="Here")
    with pytest.raises(IntegrityError), transaction.atomic():
        Branch.objects.bulk_create([Branch(code="add-1", name="Bypass", address="Here")])
    with pytest.raises(ValidationError):
        Branch.objects.create(code="X", name="  ", address="Here")
    with pytest.raises(ValidationError):
        Branch.objects.create(code="Y", name="Name", address="Here", email="invalid")


def test_assignment_constraints_and_manager(owner, manager, branches):
    first, second = branches
    assignment = assign_branch(actor=owner, user=manager, branch=first, primary=True)
    assert assignment.is_primary and assignment.assigned_by == owner
    first.manager = manager
    first.save()
    assert first.manager == manager
    second_assignment = assign_branch(actor=owner, user=manager, branch=second, primary=True)
    assignment.refresh_from_db()
    assert not assignment.is_primary and second_assignment.is_primary
    assert UserBranchAccess.objects.filter(user=manager, branch=second).count() == 1
    with pytest.raises(IntegrityError), transaction.atomic():
        UserBranchAccess.objects.bulk_create([UserBranchAccess(user=manager, branch=second)])
    with pytest.raises(IntegrityError), transaction.atomic():
        UserBranchAccess.objects.bulk_create([UserBranchAccess(user=manager, branch=first,
            is_primary=True, is_active=True, id=assignment.pk)])
    assert AuditEvent.objects.filter(action="branch_access_assigned").count() == 2


def test_primary_constraint_on_distinct_rows(owner, manager, branches):
    first, second = branches
    assign_branch(actor=owner, user=manager, branch=first, primary=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        UserBranchAccess.objects.bulk_create([UserBranchAccess(user=manager, branch=second,
            is_primary=True, is_active=True)])


def test_scope_and_inactive_branch(owner, manager, branches):
    first, second = branches
    assign_branch(actor=owner, user=manager, branch=first)
    assert set(accessible_branches(owner)) == {first, second}
    assert list(accessible_branches(manager)) == [first]
    assert can_access_branch(manager, first) and not can_access_branch(manager, second)
    assert can_manage_branch(manager, first) and not can_manage_branch(manager, second)
    assert list(scoped_queryset(BranchSettings.objects.all(), manager)) == []
    second.is_active = False
    second.save()
    assert list(accessible_branches(owner)) == [first]
    with pytest.raises(ValidationError):
        assign_branch(actor=owner, user=manager, branch=second)


def test_role_removal_revokes_existing_branch_scope(owner, manager, branches):
    first, _ = branches
    assign_branch(actor=owner, user=manager, branch=first)
    assert can_access_branch(manager, first)
    manager.groups.clear()
    assert not can_access_branch(manager, first)
    assert not accessible_branches(manager).exists()


def test_unauthorized_assignment_rejected(manager, branches, roles):
    user = User.objects.create_user("trainer@example.test", is_staff=True)
    user.groups.add(roles["Trainer"])
    with pytest.raises(PermissionDenied):
        assign_branch(actor=manager, user=user, branch=branches[0])
    with pytest.raises(ValidationError):
        UserBranchAccess.objects.create(user=user, branch=branches[0], is_active=False, is_primary=True)


def test_revoke_clears_primary_and_audits(owner, manager, branches):
    assignment = assign_branch(actor=owner, user=manager, branch=branches[0], primary=True)
    revoke_branch(actor=owner, assignment=assignment)
    assignment.refresh_from_db()
    assert not assignment.is_active and not assignment.is_primary
    assert AuditEvent.objects.filter(action="branch_access_revoked", actor=owner).exists()


def test_manager_admin_scope_and_crafted_requests(client, owner, manager, branches):
    first, second = branches
    assign_branch(actor=owner, user=manager, branch=first)
    client.force_login(manager)
    list_url = reverse("admin:branches_branch_changelist")
    content = client.get(list_url).content.decode()
    assert first.name in content and second.name not in content
    assert client.get(reverse("admin:branches_branch_change", args=[second.pk])).status_code == 302
    assert client.post(reverse("admin:branches_branch_change", args=[second.pk]),
        {"name": "Hijacked", "code": "HIJACK", "address": "Bad"}).status_code == 302
    second.refresh_from_db()
    assert second.name == "Dire"
    response = client.post(reverse("admin:branches_branch_change", args=[first.pk]), {
        "name": "Updated", "description": "", "phone": "", "email": "", "address": "Bole",
        "city": "", "code": "ESCALATE", "is_active": "", "manager": owner.pk,
        "_save": "Save"})
    assert response.status_code == 302
    first.refresh_from_db()
    assert first.name == "Updated" and first.code == "ADD-1" and first.is_active
    assert first.manager is None


def test_manager_cannot_edit_other_branch_hours(client, owner, manager, branches):
    first, second = branches
    assign_branch(actor=owner, user=manager, branch=first)
    allowed = BranchSettings.objects.create(branch=first)
    forbidden = BranchSettings.objects.create(branch=second)
    client.force_login(manager)
    assert client.get(reverse("admin:branches_branchsettings_change", args=[allowed.pk])).status_code == 200
    response = client.post(reverse("admin:branches_branchsettings_change", args=[forbidden.pk]),
        {"opens_at": "06:00", "closes_at": "22:00", "branch": first.pk})
    assert response.status_code in (302, 403)
    forbidden.refresh_from_db()
    assert forbidden.opens_at is None


def test_owner_assignment_admin_ignores_crafted_foreign_keys(client, owner, manager, branches, roles):
    first, second = branches
    assignment = assign_branch(actor=owner, user=manager, branch=first)
    other = User.objects.create_user("other@example.test", is_staff=True)
    other.groups.add(roles["Trainer"])
    client.force_login(owner)
    response = client.post(reverse("admin:branches_userbranchaccess_change", args=[assignment.pk]),
        {"user": other.pk, "branch": second.pk, "is_active": "on", "is_primary": "on", "_save": "Save"})
    assert response.status_code == 302
    assignment.refresh_from_db()
    assert assignment.user == manager and assignment.branch == first and assignment.is_primary


def test_receptionist_cannot_change_branch_or_settings(client, roles, branches):
    user = User.objects.create_user("receptionist@example.test", is_staff=True)
    user.groups.add(roles["Receptionist"])
    UserBranchAccess.objects.create(user=user, branch=branches[0])
    client.force_login(user)
    assert client.get(reverse("admin:branches_branch_changelist")).status_code == 200
    assert client.post(reverse("admin:branches_branch_change", args=[branches[0].pk]), {}).status_code == 403
    assert client.get(reverse("admin:core_gymsettings_changelist")).status_code == 403


def test_audit_immutable_for_business_users(client, owner, branches):
    event = AuditEvent.objects.create(actor=owner, action="branch_created", target_type="branches.Branch", target_id=branches[0].pk)
    client.force_login(owner)
    assert client.get(reverse("admin:branches_auditevent_changelist")).status_code == 200
    assert client.post(reverse("admin:branches_auditevent_change", args=[event.pk]), {"action": "changed"}).status_code == 403
    assert client.post(reverse("admin:branches_auditevent_delete", args=[event.pk]), {"post": "yes"}).status_code == 403


def test_branch_selector_valid_unauthorized_and_revoked(client, owner, manager, branches):
    first, second = branches
    assignment = assign_branch(actor=owner, user=manager, branch=first)
    client.force_login(manager)
    assert active_branch(client.get(reverse("admin:index")).wsgi_request).pk == first.pk
    switch = reverse("switch-branch")
    assert client.post(switch, {"branch": str(second.pk)}).status_code == 403
    assert client.post(switch, {"branch": str(first.pk)}).status_code == 302
    assert client.session["active_branch_id"] == str(first.pk)
    revoke_branch(actor=owner, assignment=assignment)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    assert "active_branch_id" not in client.session
    assert "No branch assigned" in response.content.decode()


def test_owner_switch_all_and_inactive(client, owner, branches):
    first, second = branches
    client.force_login(owner)
    switch = reverse("switch-branch")
    assert client.post(switch, {"branch": str(first.pk)}).status_code == 302
    assert client.session["active_branch_id"] == str(first.pk)
    second.is_active = False
    second.save()
    assert client.post(switch, {"branch": str(second.pk)}).status_code == 403
    assert client.post(switch, {"branch": "all"}).status_code == 302
    assert "active_branch_id" not in client.session


def test_dashboard_empty_states_and_navigation(client, owner, manager, branches):
    assign_branch(actor=owner, user=manager, branch=branches[0])
    client.force_login(manager)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    html = response.content.decode()
    for expected in ["Active Members", "Today&#x27;s Check-ins", "Monthly Revenue",
        "Expired Memberships", "Membership Overview", "Revenue Overview",
        "Branch Performance", "Today&#x27;s Attendance", "Upcoming Classes",
        "Membership Expiry Alerts", "Addis", "css/erp-admin.css"]:
        assert expected in html
    assert "482" not in html and "348,750" not in html
    assert reverse("admin:accounts_staffaccount_changelist") not in html
    assert reverse("admin:accounts_user_changelist") not in html
    client.force_login(owner)
    html = client.get(reverse("admin:index")).content.decode()
    assert reverse("admin:accounts_staffaccount_changelist") in html
    assert reverse("admin:accounts_user_changelist") not in html
