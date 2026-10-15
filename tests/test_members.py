import concurrent.futures
from io import BytesIO
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.branches.models import AuditEvent, Branch, UserBranchAccess
from apps.members.models import (
    Member,
    MemberDocument,
    MemberEmergencyContact,
    MemberNote,
    MemberSequence,
    MemberStatusHistory,
)
from apps.members.selectors import get_member_metrics
from apps.members.sequence import generate_next_member_number
from apps.members.services import (
    archive_member,
    change_member_status,
    check_possible_duplicates,
    register_member,
    transfer_home_branch,
)
from apps.members.validators import normalize_ethiopian_phone

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch_a():
    return Branch.objects.create(code="BR-A", name="Bole Branch", address="Bole Road, Addis Ababa")


@pytest.fixture
def branch_b():
    return Branch.objects.create(code="BR-B", name="CMC Branch", address="CMC Road, Addis Ababa")


@pytest.fixture
def inactive_branch():
    return Branch.objects.create(code="BR-INACT", name="Closed Branch", address="Old Airport", is_active=False)


@pytest.fixture
def seeded_roles():
    call_command("seed_business_roles", verbosity=0)


@pytest.fixture
def gym_owner(seeded_roles):
    owner = User.objects.create_user("owner@example.test", "Strong-Owner-Pass-999!", is_staff=True)
    from django.contrib.auth.models import Group
    owner.groups.add(Group.objects.get(name="Gym Owner"))
    return owner


@pytest.fixture
def branch_manager_a(seeded_roles, branch_a):
    mgr = User.objects.create_user("mgr-a@example.test", "Strong-Manager-Pass-888!", is_staff=True)
    from django.contrib.auth.models import Group
    mgr.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr, branch=branch_a, is_active=True, is_primary=True)
    return mgr


@pytest.fixture
def receptionist_a(seeded_roles, branch_a):
    recep = User.objects.create_user("recep-a@example.test", "Strong-Recep-Pass-777!", is_staff=True)
    from django.contrib.auth.models import Group
    recep.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=recep, branch=branch_a, is_active=True, is_primary=True)
    return recep


@pytest.fixture
def accountant_user(seeded_roles, branch_a):
    acct = User.objects.create_user("acct@example.test", "Strong-Acct-Pass-666!", is_staff=True)
    from django.contrib.auth.models import Group
    acct.groups.add(Group.objects.get(name="Accountant"))
    UserBranchAccess.objects.create(user=acct, branch=branch_a, is_active=True, is_primary=True)
    return acct


@pytest.fixture
def trainer_user(seeded_roles, branch_a):
    trainer = User.objects.create_user("trainer@example.test", "Strong-Trainer-Pass-555!", is_staff=True)
    from django.contrib.auth.models import Group
    trainer.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer, branch=branch_a, is_active=True, is_primary=True)
    return trainer


# ==============================================================================
# 1. Member Model & Validation Tests
# ==============================================================================

def test_ethiopian_phone_normalization():
    assert normalize_ethiopian_phone("0911223344") == "+251911223344"
    assert normalize_ethiopian_phone("0711223344") == "+251711223344"
    assert normalize_ethiopian_phone("+251911223344") == "+251911223344"
    assert normalize_ethiopian_phone("251911223344") == "+251911223344"
    assert normalize_ethiopian_phone("091 122-3344") == "+251911223344"
    assert normalize_ethiopian_phone("(091) 122-3344") == "+251911223344"

    with pytest.raises(ValidationError):
        normalize_ethiopian_phone("12345")
    with pytest.raises(ValidationError):
        normalize_ethiopian_phone("abc0911223344")


def test_member_creation_and_immutability(branch_a):
    m = Member(
        first_name="Abebe",
        last_name="Bikila",
        phone="0911223344",
        home_branch=branch_a,
    )
    m.save()
    assert m.member_number.startswith("MEM-")
    assert m.phone == "+251911223344"
    assert m.status == Member.MemberStatus.ACTIVE

    # Attempt to mutate member_number
    m.member_number = "MEM-999999"
    with pytest.raises(ValidationError) as exc:
        m.clean()
    assert "member_number" in exc.value.message_dict


def test_member_inactive_branch_rejected(inactive_branch):
    m = Member(
        first_name="Kenenisa",
        last_name="Bekele",
        phone="0911223344",
        home_branch=inactive_branch,
    )
    with pytest.raises(ValidationError) as exc:
        m.clean()
    assert "home_branch" in exc.value.message_dict


# ==============================================================================
# 2. Member Number Concurrency & Generation
# ==============================================================================

def test_member_number_sequential_and_unique():
    MemberSequence.objects.all().delete()
    num1 = generate_next_member_number()
    num2 = generate_next_member_number()
    num3 = generate_next_member_number()

    assert num1 == "MEM-000001"
    assert num2 == "MEM-000002"
    assert num3 == "MEM-000003"


# ==============================================================================
# 3. Registration Workflow & Duplicate Checks
# ==============================================================================

def test_register_member_service(gym_owner, branch_a):
    member = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Derartu",
        last_name="Tulu",
        phone="0911998877",
        email="derartu@example.test",
        gender=Member.Gender.FEMALE,
    )
    assert member.member_number.startswith("MEM-")
    assert member.phone == "+251911998877"
    assert member.created_by == gym_owner

    # Verify initial status history
    history = member.status_history.first()
    assert history is not None
    assert history.new_status == Member.MemberStatus.ACTIVE

    # Verify audit event
    event = AuditEvent.objects.filter(target_id=member.pk, action="member_registered").first()
    assert event is not None
    assert event.actor == gym_owner
    assert event.metadata["branch_code"] == branch_a.code


def test_duplicate_profile_check(gym_owner, branch_a):
    m1 = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Haile",
        last_name="Gebrselassie",
        phone="0911112233",
    )
    # Match by phone
    dupes_by_phone = check_possible_duplicates(phone="0911112233")
    assert dupes_by_phone.filter(pk=m1.pk).exists()

    # Match by name
    dupes_by_name = check_possible_duplicates(first_name="Haile", last_name="Gebrselassie")
    assert dupes_by_name.filter(pk=m1.pk).exists()

    # No match for different person
    no_dupe = check_possible_duplicates(phone="0922000000", first_name="Tirunesh", last_name="Dibaba")
    assert not no_dupe.exists()


def test_registration_unauthorized_branch_rejected(receptionist_a, branch_b):
    with pytest.raises(PermissionDenied):
        register_member(
            actor=receptionist_a,
            home_branch=branch_b,
            first_name="Sileshi",
            last_name="Sihine",
            phone="0911334455",
        )


# ==============================================================================
# 4. Status Transition & History Tests
# ==============================================================================

def test_status_change_and_suspension_reason(branch_manager_a, branch_a):
    member = register_member(
        actor=branch_manager_a,
        home_branch=branch_a,
        first_name="Meseret",
        last_name="Defar",
        phone="0911445566",
    )
    # Suspension requires reason
    with pytest.raises(ValidationError):
        change_member_status(
            actor=branch_manager_a,
            member=member,
            new_status=Member.MemberStatus.SUSPENDED,
            reason="",
        )

    # Valid suspension
    change_member_status(
        actor=branch_manager_a,
        member=member,
        new_status=Member.MemberStatus.SUSPENDED,
        reason="Repeated safety violations in weight room",
    )
    member.refresh_from_db()
    assert member.status == Member.MemberStatus.SUSPENDED
    assert member.status_history.count() == 2

    # Verify audit event
    assert AuditEvent.objects.filter(target_id=member.pk, action="member_status_changed").exists()


def test_receptionist_cannot_suspend_or_archive(receptionist_a, branch_a):
    member = register_member(
        actor=receptionist_a,
        home_branch=branch_a,
        first_name="Gelete",
        last_name="Burka",
        phone="0911556677",
    )
    with pytest.raises(PermissionDenied):
        change_member_status(
            actor=receptionist_a,
            member=member,
            new_status=Member.MemberStatus.SUSPENDED,
            reason="Unauthorized suspension attempt",
        )

    with pytest.raises(PermissionDenied):
        archive_member(
            actor=receptionist_a,
            member=member,
            reason="Unauthorized archive attempt",
        )


# ==============================================================================
# 5. Home-Branch Transfer Workflow Tests
# ==============================================================================

def test_authorized_branch_transfer(gym_owner, branch_a, branch_b):
    member = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Kenenisa",
        last_name="Bekele",
        phone="0911667788",
    )
    transfer_home_branch(
        actor=gym_owner,
        member=member,
        new_branch=branch_b,
        reason="Relocated residence to CMC area",
    )
    member.refresh_from_db()
    assert member.home_branch == branch_b

    # Audit event logged
    event = AuditEvent.objects.filter(target_id=member.pk, action="member_branch_transferred").first()
    assert event is not None
    assert event.metadata["old_branch_code"] == branch_a.code
    assert event.metadata["new_branch_code"] == branch_b.code


def test_receptionist_cannot_transfer_branch(receptionist_a, branch_a, branch_b):
    member = register_member(
        actor=receptionist_a,
        home_branch=branch_a,
        first_name="Almaz",
        last_name="Ayana",
        phone="0911778899",
    )
    with pytest.raises(PermissionDenied):
        transfer_home_branch(
            actor=receptionist_a,
            member=member,
            new_branch=branch_b,
            reason="Transfer request",
        )


# ==============================================================================
# 6. Emergency Contacts & Primary Uniqueness
# ==============================================================================

def test_emergency_contact_one_primary_per_member(branch_a, gym_owner):
    member = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Letesenbet",
        last_name="Gidey",
        phone="0911889900",
    )
    c1 = MemberEmergencyContact.objects.create(
        member=member,
        full_name="Parent One",
        relationship="Father",
        phone="0912112233",
        is_primary=True,
    )
    assert c1.is_primary is True

    # Creating second primary unseats the first
    c2 = MemberEmergencyContact.objects.create(
        member=member,
        full_name="Parent Two",
        relationship="Mother",
        phone="0912334455",
        is_primary=True,
    )
    c1.refresh_from_db()
    assert c1.is_primary is False
    assert c2.is_primary is True
    assert MemberEmergencyContact.objects.filter(member=member, is_primary=True).count() == 1


# ==============================================================================
# 7. Secure Document Upload & Download Protection
# ==============================================================================

def test_document_download_authorization(client, gym_owner, branch_manager_a, accountant_user, trainer_user, branch_a, branch_b):
    member = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Gudaf",
        last_name="Tsegay",
        phone="0911001122",
    )
    test_file = SimpleUploadedFile("waiver.pdf", b"%PDF-1.4 test document content", content_type="application/pdf")
    doc = MemberDocument.objects.create(
        member=member,
        document_type=MemberDocument.DocumentType.AGREEMENT_WAIVER,
        document_title="Signed Agreement",
        file=test_file,
        uploaded_by=gym_owner,
    )

    download_url = reverse("member-document-download", args=[doc.pk])

    # 1. Gym Owner: 200 OK
    client.force_login(gym_owner)
    res_owner = client.get(download_url)
    assert res_owner.status_code == 200
    assert "private" in res_owner["Cache-Control"]
    assert "no-store" in res_owner["Cache-Control"]

    # 2. Branch Manager in Branch A: 200 OK
    client.force_login(branch_manager_a)
    res_mgr = client.get(download_url)
    assert res_mgr.status_code == 200

    # 3. Accountant: 403 Forbidden (no document access by default)
    client.force_login(accountant_user)
    assert client.get(download_url).status_code == 403

    # 4. Trainer: 403 Forbidden
    client.force_login(trainer_user)
    assert client.get(download_url).status_code == 403

    # 5. Anonymous: 302 redirect to login or 403
    client.logout()
    assert client.get(download_url).status_code in (302, 403)


# ==============================================================================
# 8. Member Notes Category & Visibility
# ==============================================================================

def test_member_notes_category_and_creation(gym_owner, branch_a):
    member = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Ejgayehu",
        last_name="Taye",
        phone="0911221122",
    )
    n1 = MemberNote.objects.create(
        member=member,
        category=MemberNote.NoteCategory.GENERAL,
        note="Member prefers morning workouts.",
        created_by=gym_owner,
    )
    n2 = MemberNote.objects.create(
        member=member,
        category=MemberNote.NoteCategory.SENSITIVE,
        note="Payment dispute resolved by management.",
        created_by=gym_owner,
    )
    assert member.notes.count() == 2
    assert n1.category == MemberNote.NoteCategory.GENERAL
    assert n2.category == MemberNote.NoteCategory.SENSITIVE


# ==============================================================================
# 9. Dashboard Integration & Member Metrics Selectors
# ==============================================================================

def test_dashboard_member_metrics(client, gym_owner, branch_manager_a, branch_a, branch_b):
    # Branch A: 2 active members, 1 suspended
    m1 = register_member(actor=gym_owner, home_branch=branch_a, first_name="A1", last_name="M", phone="0911000001")
    m2 = register_member(actor=gym_owner, home_branch=branch_a, first_name="A2", last_name="M", phone="0911000002")
    m3 = register_member(actor=gym_owner, home_branch=branch_a, first_name="A3", last_name="M", phone="0911000003")
    change_member_status(actor=gym_owner, member=m3, new_status=Member.MemberStatus.SUSPENDED, reason="Test suspension")

    # Branch B: 1 active member
    register_member(actor=gym_owner, home_branch=branch_b, first_name="B1", last_name="M", phone="0911000004")

    # Owner sees all branches: total 4, active 3, suspended 1
    owner_metrics = get_member_metrics(gym_owner)
    assert owner_metrics["total_members"] == 4
    assert owner_metrics["active_profiles"] == 3
    assert owner_metrics["suspended_members"] == 1
    assert owner_metrics["new_this_month"] == 4

    # Branch Manager A sees only Branch A: total 3, active 2, suspended 1
    mgr_metrics = get_member_metrics(branch_manager_a)
    assert mgr_metrics["total_members"] == 3
    assert mgr_metrics["active_profiles"] == 2
    assert mgr_metrics["suspended_members"] == 1

    # Filtered by active branch
    owner_branch_b_metrics = get_member_metrics(gym_owner, active_branch=branch_b)
    assert owner_branch_b_metrics["total_members"] == 1
    assert owner_branch_b_metrics["active_profiles"] == 1

    # Verify dashboard HTTP endpoint displays Active Member Profiles
    client.force_login(gym_owner)
    res = client.get(reverse("admin:index"))
    assert res.status_code == 200
    html = res.content.decode()
    assert "Active Members Profiles" in html
    assert "4 total registered" in html


# ==============================================================================
# 10. Admin UI Changelist & Scoped Access
# ==============================================================================

def test_admin_changelist_branch_scoping(client, branch_manager_a, receptionist_a, branch_a, branch_b, gym_owner):
    m_a = register_member(actor=gym_owner, home_branch=branch_a, first_name="Bole", last_name="Member", phone="0911888888")
    m_b = register_member(actor=gym_owner, home_branch=branch_b, first_name="CMC", last_name="Member", phone="0911999999")

    # Branch Manager A sees only member in Branch A
    client.force_login(branch_manager_a)
    res = client.get(reverse("admin:members_member_changelist"))
    assert res.status_code == 200
    assert m_a.member_number.encode() in res.content
    assert m_b.member_number.encode() not in res.content

    # Receptionist A sees member in Branch A
    client.force_login(receptionist_a)
    res_recep = client.get(reverse("admin:members_member_changelist"))
    assert res_recep.status_code == 200
    assert m_a.member_number.encode() in res_recep.content
    assert m_b.member_number.encode() not in res_recep.content

    # Gym Owner sees all members
    client.force_login(gym_owner)
    res_owner = client.get(reverse("admin:members_member_changelist"))
    assert res_owner.status_code == 200
    assert m_a.member_number.encode() in res_owner.content
    assert m_b.member_number.encode() in res_owner.content


# ==============================================================================
# 11. Safety Verification & Edge Case Tests
# ==============================================================================

def test_direct_save_immutability_enforced(gym_owner, branch_a):
    m = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Immutable",
        last_name="Test",
        phone="0911776655",
    )
    original_number = m.member_number
    m.member_number = "MEM-999999"
    with pytest.raises(ValidationError) as exc:
        m.save()
    assert "member_number" in exc.value.message_dict


def test_empty_sequence_table_generation():
    MemberSequence.objects.all().delete()
    assert MemberSequence.objects.count() == 0
    num1 = generate_next_member_number()
    assert num1 == "MEM-000001"
    num2 = generate_next_member_number()
    assert num2 == "MEM-000002"
    assert MemberSequence.objects.count() == 1


def test_receptionist_crafted_post_status_suspension_denied(client, receptionist_a, gym_owner, branch_a):
    m = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Target",
        last_name="Member",
        phone="0911554433",
    )
    client.force_login(receptionist_a)
    status_url = reverse("admin:member-change-status", args=[m.pk])

    # Crafted POST attempting suspension
    res = client.post(status_url, {
        "new_status": Member.MemberStatus.SUSPENDED,
        "reason": "Crafted suspension attempt",
    })
    # Form rejects choice as invalid for receptionist, and member status remains ACTIVE
    m.refresh_from_db()
    assert m.status == Member.MemberStatus.ACTIVE


def test_receptionist_crafted_post_transfer_denied(client, receptionist_a, gym_owner, branch_a, branch_b):
    m = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Transfer",
        last_name="Target",
        phone="0911223388",
    )
    client.force_login(receptionist_a)
    transfer_url = reverse("admin:member-transfer-branch", args=[m.pk])

    # Crafted POST attempting branch transfer
    res = client.post(transfer_url, {
        "new_branch": str(branch_b.pk),
        "reason": "Unauthorized transfer",
    })
    assert res.status_code == 403
    m.refresh_from_db()
    assert m.home_branch == branch_a


def test_receptionist_sensitive_note_rejected_at_model_level(gym_owner, receptionist_a, branch_a):
    m = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Note",
        last_name="Target",
        phone="0911445566",
    )
    note = MemberNote(
        member=m,
        category=MemberNote.NoteCategory.SENSITIVE,
        note="Top secret note",
        created_by=receptionist_a,
    )
    with pytest.raises(ValidationError) as exc:
        note.save()
    assert "category" in exc.value.message_dict


def test_emergency_contact_atomic_unseat_primary(gym_owner, branch_a):
    member = register_member(
        actor=gym_owner,
        home_branch=branch_a,
        first_name="Primary",
        last_name="ContactCheck",
        phone="0911332211",
    )
    c1 = MemberEmergencyContact.objects.create(
        member=member, full_name="Contact One", relationship="Friend", phone="0911000001", is_primary=True,
    )
    c2 = MemberEmergencyContact.objects.create(
        member=member, full_name="Contact Two", relationship="Brother", phone="0911000002", is_primary=True,
    )
    c3 = MemberEmergencyContact.objects.create(
        member=member, full_name="Contact Three", relationship="Sister", phone="0911000003", is_primary=True,
    )

    c1.refresh_from_db()
    c2.refresh_from_db()
    c3.refresh_from_db()

    assert c1.is_primary is False
    assert c2.is_primary is False
    assert c3.is_primary is True
    assert MemberEmergencyContact.objects.filter(member=member, is_primary=True).count() == 1

