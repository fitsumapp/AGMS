import concurrent.futures
from datetime import date, time, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import has_role, is_owner, seed_roles
from apps.branches.models import AuditEvent, Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.members.models import Member
from apps.members.services import register_member
from apps.staff.models import (
    LeaveRequest,
    ShiftTemplate,
    StaffDocument,
    StaffProfile,
    StaffSequence,
    StaffShift,
    TrainerMemberAssignment,
    TrainerProfile,
    TrainingSession,
)
from apps.staff.selectors import (
    get_branch_staffing_summary,
    get_staff_scheduling_metrics,
    get_trainer_metrics,
)
from apps.staff.services import (
    assign_trainer_to_member,
    schedule_training_session,
    terminate_staff,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_staff_prerequisites(db):
    seed_roles()
    GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "AGMS Gym",
            "legal_name": "Antigravity Fitness PLC",
            "email": "support@agms.test",
            "phone": "+251911000000",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )


@pytest.fixture
def branches():
    b1 = Branch.objects.create(
        code="BOLE",
        name="Bole Branch",
        address="Bole Road, Addis Ababa",
        city="Addis Ababa",
        is_active=True,
    )
    b2 = Branch.objects.create(
        code="MEG",
        name="Megenagna Branch",
        address="Megenagna Square",
        city="Addis Ababa",
        is_active=True,
    )
    return b1, b2


@pytest.fixture
def staff_users(branches):
    b1, b2 = branches
    owner = User.objects.create_user("owner@agms.test", "OwnerPass12345!", is_staff=True)
    from django.contrib.auth.models import Group
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    mgr = User.objects.create_user("manager@agms.test", "ManagerPass12345!", is_staff=True)
    mgr.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr, branch=b1, is_active=True, is_primary=True)

    trainer_user = User.objects.create_user("trainer@agms.test", "TrainerPass12345!", is_staff=True)
    trainer_user.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer_user, branch=b1, is_active=True, is_primary=True)

    receptionist = User.objects.create_user("receptionist@agms.test", "ReceptPass12345!", is_staff=True)
    receptionist.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=receptionist, branch=b1, is_active=True, is_primary=True)

    return {
        "owner": owner,
        "manager": mgr,
        "trainer_user": trainer_user,
        "receptionist": receptionist,
    }


@pytest.fixture
def sample_member(branches, staff_users):
    b1, _ = branches
    owner = staff_users["owner"]
    member = register_member(
        actor=owner,
        home_branch=b1,
        first_name="Abebe",
        last_name="Bikila",
        phone="+251911223344",
        gender=Member.Gender.MALE,
    )
    return member


def test_staff_creation_and_employee_number_sequence(staff_users, branches):
    b1, _ = branches
    u = staff_users["trainer_user"]
    staff = StaffProfile.objects.create(
        user=u,
        primary_branch=b1,
        job_title="Certified Personal Trainer",
        department=StaffProfile.Department.FITNESS,
        employment_type=StaffProfile.EmploymentType.FULL_TIME,
        employment_status=StaffProfile.EmploymentStatus.ACTIVE,
        hire_date=date(2026, 1, 1),
    )
    assert staff.employee_number.startswith("EMP-")
    assert staff.full_name == u.get_full_name() or u.email
    assert staff.is_assigned_to_branch(b1)


def test_staff_termination_date_validation(staff_users, branches):
    b1, _ = branches
    u = staff_users["receptionist"]
    staff = StaffProfile(
        user=u,
        primary_branch=b1,
        job_title="Front Desk Officer",
        hire_date=date(2026, 6, 1),
        termination_date=date(2026, 5, 1),  # Before hire date
    )
    with pytest.raises(ValidationError):
        staff.clean()


def test_trainer_profile_creation_and_attributes(staff_users, branches):
    b1, _ = branches
    u = staff_users["trainer_user"]
    staff = StaffProfile.objects.create(
        user=u,
        primary_branch=b1,
        job_title="Fitness Trainer",
    )
    trainer = TrainerProfile.objects.create(
        staff=staff,
        specializations="HIIT, Strength & Conditioning",
        certifications="ACE Certified",
        experience_years=5,
        hourly_rate=Decimal("450.00"),
        max_active_clients=10,
    )
    assert trainer.full_name == staff.full_name
    assert trainer.active_client_count() == 0
    assert trainer.is_assigned_to_branch(b1)


def test_trainer_member_assignment_validation(staff_users, branches, sample_member):
    b1, b2 = branches
    u = staff_users["trainer_user"]
    staff = StaffProfile.objects.create(user=u, primary_branch=b1, job_title="Fitness Trainer")
    trainer = TrainerProfile.objects.create(staff=staff)

    # Success assignment for branch b1
    assignment = TrainerMemberAssignment(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        start_date=date(2026, 10, 1),
        status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
    )
    assignment.clean()
    assignment.save()
    assert trainer.active_client_count() == 1

    # Ineligible branch b2 (trainer not assigned to b2)
    bad_assignment = TrainerMemberAssignment(
        trainer=trainer,
        member=sample_member,
        branch=b2,
        start_date=date(2026, 10, 1),
    )
    with pytest.raises(ValidationError) as exc:
        bad_assignment.clean()
    assert "not authorized for branch" in str(exc.value)

    # Duplicate active assignment rejected
    dup_assignment = TrainerMemberAssignment(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        start_date=date(2026, 10, 5),
        status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
    )
    with pytest.raises(ValidationError) as exc:
        dup_assignment.clean()
    assert "already has an active assignment" in str(exc.value)


def test_training_session_scheduling_and_conflict_prevention(staff_users, branches, sample_member):
    b1, _ = branches
    u = staff_users["trainer_user"]
    staff = StaffProfile.objects.create(user=u, primary_branch=b1, job_title="Fitness Trainer")
    trainer = TrainerProfile.objects.create(staff=staff)

    session_date = date(2026, 10, 20)

    # 1. Valid Session
    session1 = TrainingSession(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=session_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
        status=TrainingSession.SessionStatus.SCHEDULED,
    )
    session1.clean()
    session1.save()
    assert session1.duration_minutes == 60

    # 2. End time before start time
    invalid_time_session = TrainingSession(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=session_date,
        start_time=time(14, 0),
        end_time=time(13, 0),
    )
    with pytest.raises(ValidationError) as exc:
        invalid_time_session.clean()
    assert "after start time" in str(exc.value)

    # 3. Trainer Overlapping Session
    member2 = register_member(
        actor=staff_users["owner"],
        home_branch=b1,
        first_name="Haile",
        last_name="Gebrselassie",
        phone="+251911998877",
        gender=Member.Gender.MALE,
    )
    overlapping_session = TrainingSession(
        trainer=trainer,
        member=member2,
        branch=b1,
        session_date=session_date,
        start_time=time(10, 30),  # overlaps with 10:00-11:00
        end_time=time(11, 30),
        status=TrainingSession.SessionStatus.SCHEDULED,
    )
    with pytest.raises(ValidationError) as exc:
        overlapping_session.clean()
    assert "conflicting scheduled session" in str(exc.value)


def test_training_session_completion_and_cancellation(staff_users, branches, sample_member):
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    trainer = TrainerProfile.objects.create(staff=staff)

    session = TrainingSession.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=date(2026, 10, 15),
        start_time=time(9, 0),
        end_time=time(10, 0),
        status=TrainingSession.SessionStatus.SCHEDULED,
    )

    # Complete session
    session.complete_session(notes="Excellent squat performance.")
    assert session.status == TrainingSession.SessionStatus.COMPLETED
    assert session.completed_at is not None
    assert "squat" in session.trainer_notes

    # Create another session and cancel it
    session2 = TrainingSession.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=date(2026, 10, 16),
        start_time=time(9, 0),
        end_time=time(10, 0),
        status=TrainingSession.SessionStatus.SCHEDULED,
    )
    session2.cancel_session(user=staff_users["owner"], reason="Member requested reschedule.")
    assert session2.status == TrainingSession.SessionStatus.CANCELLED
    assert session2.cancelled_by == staff_users["owner"]
    assert "reschedule" in session2.cancellation_reason


def test_staff_shift_creation_and_overlap_validation(staff_users, branches):
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["receptionist"], primary_branch=b1, job_title="Receptionist")
    shift_date = date(2026, 10, 25)

    template = ShiftTemplate.objects.create(
        branch=b1,
        name="Morning Desk",
        start_time=time(6, 0),
        end_time=time(14, 0),
    )

    shift1 = StaffShift(
        staff=staff,
        branch=b1,
        shift_template=template,
        date=shift_date,
        start_time=time(6, 0),
        end_time=time(14, 0),
        status=StaffShift.ShiftStatus.SCHEDULED,
    )
    shift1.clean()
    shift1.save()

    # Overlapping shift on same date rejected
    overlapping_shift = StaffShift(
        staff=staff,
        branch=b1,
        date=shift_date,
        start_time=time(12, 0),  # overlaps with 06:00-14:00
        end_time=time(20, 0),
        status=StaffShift.ShiftStatus.SCHEDULED,
    )
    with pytest.raises(ValidationError) as exc:
        overlapping_shift.clean()
    assert "overlaps with another scheduled shift" in str(exc.value)

    # Rest day allowed
    rest_day = StaffShift(
        staff=staff,
        branch=b1,
        date=date(2026, 10, 26),
        is_rest_day=True,
    )
    rest_day.clean()
    rest_day.save()
    assert rest_day.is_rest_day


def test_leave_request_workflow(staff_users, branches):
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")

    leave = LeaveRequest(
        staff=staff,
        leave_type=LeaveRequest.LeaveType.ANNUAL,
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 5),
        reason="Family trip to Hawassa.",
    )
    leave.clean()
    leave.save()
    assert leave.total_days == 5
    assert leave.status == LeaveRequest.LeaveStatus.PENDING

    # Reviewer approves
    leave.approve(user=staff_users["manager"])
    assert leave.status == LeaveRequest.LeaveStatus.APPROVED
    assert leave.reviewed_by == staff_users["manager"]


def test_trainer_performance_selectors(staff_users, branches, sample_member):
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    trainer = TrainerProfile.objects.create(staff=staff, max_active_clients=10)

    # Assignment
    TrainerMemberAssignment.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
    )

    # 2 completed sessions, 1 cancelled
    s1 = TrainingSession.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=date(2026, 10, 10),
        start_time=time(9, 0),
        end_time=time(10, 0),
        status=TrainingSession.SessionStatus.COMPLETED,
    )
    s2 = TrainingSession.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=date(2026, 10, 12),
        start_time=time(14, 0),
        end_time=time(15, 30),
        status=TrainingSession.SessionStatus.COMPLETED,
    )
    s3 = TrainingSession.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=date(2026, 10, 14),
        start_time=time(11, 0),
        end_time=time(12, 0),
        status=TrainingSession.SessionStatus.CANCELLED,
    )

    metrics = get_trainer_metrics(
        trainer=trainer,
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        branch=b1,
    )

    assert metrics["active_clients"] == 1
    assert metrics["completed_sessions"] == 2
    assert metrics["cancelled_sessions"] == 1
    assert metrics["total_sessions"] == 3
    assert metrics["completion_rate"] == 66.7
    assert metrics["training_hours"] == 2.5  # 1hr + 1.5hr


def test_trainer_performance_view_and_scoping(client, staff_users, branches):
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    TrainerProfile.objects.create(staff=staff)

    url = reverse("staff-performance")

    # Owner can access
    client.force_login(staff_users["owner"])
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Trainer Activity Breakdown" in resp.content

    # Trainer can access (scoped to own profile)
    client.force_login(staff_users["trainer_user"])
    resp_trainer = client.get(url)
    assert resp_trainer.status_code == 200


def test_staff_reports_view_and_csv_export(client, staff_users, branches):
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["receptionist"], primary_branch=b1, job_title="Receptionist")
    StaffShift.objects.create(
        staff=staff,
        branch=b1,
        date=date.today(),
        start_time=time(8, 0),
        end_time=time(16, 0),
        status=StaffShift.ShiftStatus.COMPLETED,
    )

    client.force_login(staff_users["owner"])
    url = reverse("staff-reports")

    # Web preview
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Staff Shifts Log" in resp.content

    # CSV export
    resp_csv = client.get(f"{url}?export=csv&report_type=shifts")
    assert resp_csv.status_code == 200
    assert resp_csv["Content-Type"] == "text/csv"
    assert b"Staff Name" in resp_csv.content
    assert staff.employee_number.encode() in resp_csv.content


def test_empty_staffing_metrics_stability():
    summary = get_branch_staffing_summary()
    assert summary["total_staff"] == 0
    assert summary["avg_clients_per_trainer"] == 0.0

    scheduling = get_staff_scheduling_metrics()
    assert scheduling["total_working_shifts"] == 0
    assert scheduling["coverage_rate"] == 0.0


# =========================================================================
# PHASE 08 FINAL SECURITY & INTEGRITY VERIFICATION TESTS
# =========================================================================

@pytest.mark.django_db(transaction=True)
def test_concurrent_training_session_bookings_serialize(branches, staff_users, sample_member):
    """
    Test 1: Verify PostgreSQL row locks serialize concurrent requests to schedule
    overlapping sessions for the same trainer. Exactly one must succeed and the
    second must raise ValidationError.
    """
    b1, _ = branches
    owner = staff_users["owner"]
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    trainer = TrainerProfile.objects.create(staff=staff)

    member2 = register_member(
        actor=owner,
        home_branch=b1,
        first_name="Kenenisa",
        last_name="Bekele",
        phone="+251911990011",
        gender=Member.Gender.MALE,
    )

    session_date = date(2026, 11, 10)

    def attempt_schedule(mem, st, et):
        try:
            schedule_training_session(
                actor=owner,
                trainer=trainer,
                member=mem,
                branch=b1,
                session_date=session_date,
                start_time=st,
                end_time=et,
            )
            return "SUCCESS"
        except Exception as e:
            return str(e)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_schedule, sample_member, time(10, 0), time(11, 0))
        f2 = executor.submit(attempt_schedule, member2, time(10, 30), time(11, 30))
        r1 = f1.result()
        r2 = f2.result()

    results = [r1, r2]
    success_count = sum(1 for r in results if r == "SUCCESS")
    conflict_count = sum(1 for r in results if "conflicting scheduled session" in r)

    assert success_count == 1
    assert conflict_count == 1
    assert TrainingSession.objects.filter(
        trainer=trainer,
        session_date=session_date,
        status=TrainingSession.SessionStatus.SCHEDULED,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_member_session_bookings_serialize(branches, staff_users, sample_member):
    """
    Test 1b: Verify concurrent requests to schedule overlapping sessions for the SAME
    member across different trainers serialize and prevent double-booking the member.
    """
    b1, _ = branches
    owner = staff_users["owner"]
    staff1 = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer 1")
    trainer1 = TrainerProfile.objects.create(staff=staff1)

    u2 = User.objects.create_user("trainer2@agms.test", "Trainer2Pass123!", is_staff=True)
    from django.contrib.auth.models import Group
    u2.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=u2, branch=b1, is_active=True, is_primary=True)
    staff2 = StaffProfile.objects.create(user=u2, primary_branch=b1, job_title="Trainer 2")
    trainer2 = TrainerProfile.objects.create(staff=staff2)

    session_date = date(2026, 11, 12)

    def attempt_schedule_member(tr, st, et):
        try:
            schedule_training_session(
                actor=owner,
                trainer=tr,
                member=sample_member,
                branch=b1,
                session_date=session_date,
                start_time=st,
                end_time=et,
            )
            return "SUCCESS"
        except Exception as e:
            return str(e)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_schedule_member, trainer1, time(14, 0), time(15, 0))
        f2 = executor.submit(attempt_schedule_member, trainer2, time(14, 30), time(15, 30))
        r1 = f1.result()
        r2 = f2.result()

    results = [r1, r2]
    success_count = sum(1 for r in results if r == "SUCCESS")
    conflict_count = sum(1 for r in results if "already scheduled for another training session" in r)

    assert success_count == 1
    assert conflict_count == 1
    assert TrainingSession.objects.filter(
        member=sample_member,
        session_date=session_date,
        status=TrainingSession.SessionStatus.SCHEDULED,
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_trainer_capacity_enforcement_boundary_and_concurrency(branches, staff_users):
    """
    Test 3: Verify max_active_clients capacity limit is strictly enforced at boundary
    and protects against concurrent assignments exceeding capacity.
    """
    b1, _ = branches
    owner = staff_users["owner"]
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    trainer = TrainerProfile.objects.create(staff=staff, max_active_clients=2)

    m1 = register_member(actor=owner, home_branch=b1, first_name="Cap1", last_name="M", phone="+251911000011")
    m2 = register_member(actor=owner, home_branch=b1, first_name="Cap2", last_name="M", phone="+251911000012")
    m3 = register_member(actor=owner, home_branch=b1, first_name="Cap3", last_name="M", phone="+251911000013")

    # Boundary 1: First assignment fills slot 1 of 2
    a1 = assign_trainer_to_member(actor=owner, trainer=trainer, member=m1, branch=b1)
    assert a1.status == TrainerMemberAssignment.AssignmentStatus.ACTIVE
    assert trainer.active_client_count() == 1

    # Concurrency: Two concurrent requests compete for the 1 remaining slot
    def attempt_assignment(mem):
        try:
            assign_trainer_to_member(actor=owner, trainer=trainer, member=mem, branch=b1)
            return "SUCCESS"
        except Exception as e:
            return str(e)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_assignment, m2)
        f2 = executor.submit(attempt_assignment, m3)
        r1 = f1.result()
        r2 = f2.result()

    results = [r1, r2]
    assert results.count("SUCCESS") == 1
    capacity_rejected = [r for r in results if "maximum client capacity" in r]
    assert len(capacity_rejected) == 1

    # In database: capacity is strictly 2, not 3
    assert TrainerMemberAssignment.objects.filter(
        trainer=trainer,
        status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
    ).count() == 2

    # Boundary 2: Subsequent assignment is rejected
    m4 = register_member(actor=owner, home_branch=b1, first_name="Cap4", last_name="M", phone="+251911000014")
    with pytest.raises(ValidationError) as exc:
        assign_trainer_to_member(actor=owner, trainer=trainer, member=m4, branch=b1)
    assert "maximum client capacity (2)" in str(exc.value)


def test_private_staff_documents_unauthenticated_denied(client, branches, staff_users):
    """
    Test 2a: Unauthenticated requests to private staff documents must be redirected to login.
    """
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    doc_file = SimpleUploadedFile("contract.pdf", b"%PDF-1.4 mock contract content", content_type="application/pdf")
    doc = StaffDocument.objects.create(
        staff=staff,
        title="Trainer Contract",
        document_type=StaffDocument.DocumentType.CONTRACT,
        file=doc_file,
    )
    url = reverse("staff-document-download", kwargs={"document_id": doc.pk})
    resp = client.get(url)
    assert resp.status_code == 302
    assert "/admin/login/" in resp.url


def test_private_staff_documents_unauthorized_roles_denied(client, branches, staff_users):
    """
    Test 2b: Unauthorized roles (Receptionist, peer Trainers) are rejected with 403 Forbidden.
    """
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    doc_file = SimpleUploadedFile("id_card.png", b"\x89PNG\r\n\x1a\nmock id", content_type="image/png")
    doc = StaffDocument.objects.create(
        staff=staff,
        title="National ID",
        document_type=StaffDocument.DocumentType.ID_PROOF,
        file=doc_file,
    )
    url = reverse("staff-document-download", kwargs={"document_id": doc.pk})

    # Receptionist denied
    client.force_login(staff_users["receptionist"])
    resp = client.get(url)
    assert resp.status_code == 403

    # Peer trainer denied
    u_other = User.objects.create_user("othertrainer@agms.test", "OtherPass123!", is_staff=True)
    from django.contrib.auth.models import Group
    u_other.groups.add(Group.objects.get(name="Trainer"))
    client.force_login(u_other)
    resp_other = client.get(url)
    assert resp_other.status_code == 403

    # Audit log records unauthorized attempts
    assert AuditEvent.objects.filter(
        action="document_download_unauthorized",
        metadata__employee_number=staff.employee_number,
    ).exists()


def test_private_staff_documents_cross_branch_denied(client, branches, staff_users):
    """
    Test 2c: Branch Manager of Branch 2 cannot access documents for staff at Branch 1.
    """
    b1, b2 = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    doc_file = SimpleUploadedFile("diploma.pdf", b"%PDF-1.4 diploma", content_type="application/pdf")
    doc = StaffDocument.objects.create(
        staff=staff,
        title="Diploma",
        document_type=StaffDocument.DocumentType.CERTIFICATE,
        file=doc_file,
    )
    url = reverse("staff-document-download", kwargs={"document_id": doc.pk})

    mgr2 = User.objects.create_user("mgr2@agms.test", "Mgr2Pass123!", is_staff=True)
    from django.contrib.auth.models import Group
    mgr2.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr2, branch=b2, is_active=True, is_primary=True)

    client.force_login(mgr2)
    resp = client.get(url)
    assert resp.status_code == 403


def test_private_staff_documents_authorized_access(client, branches, staff_users):
    """
    Test 2d: Staff member (self), primary Branch Manager, and Gym Owner can securely download.
    """
    b1, _ = branches
    staff = StaffProfile.objects.create(user=staff_users["trainer_user"], primary_branch=b1, job_title="Trainer")
    doc_file = SimpleUploadedFile("contract.pdf", b"%PDF-1.4 authorized contract content", content_type="application/pdf")
    doc = StaffDocument.objects.create(
        staff=staff,
        title="Employment Contract",
        document_type=StaffDocument.DocumentType.CONTRACT,
        file=doc_file,
    )
    url = reverse("staff-document-download", kwargs={"document_id": doc.pk})

    # 1. Staff member themselves
    client.force_login(staff_users["trainer_user"])
    resp_self = client.get(url)
    assert resp_self.status_code == 200
    assert "private" in resp_self["Cache-Control"]
    assert "no-store" in resp_self["Cache-Control"]
    assert resp_self["X-Content-Type-Options"] == "nosniff"

    # 2. Authorized Branch Manager
    client.force_login(staff_users["manager"])
    resp_mgr = client.get(url)
    assert resp_mgr.status_code == 200

    # 3. Gym Owner
    client.force_login(staff_users["owner"])
    resp_owner = client.get(url)
    assert resp_owner.status_code == 200


def test_staff_termination_workflow_and_access_revocation(client, branches, staff_users, sample_member):
    """
    Test 4: Verify staff termination revokes user authentication, trainer privileges,
    and branch permissions while preserving historical training/shift records.
    """
    b1, _ = branches
    owner = staff_users["owner"]
    trainer_user = staff_users["trainer_user"]

    # Verify trainer can log in prior to termination
    login_resp = client.post(reverse("admin:login"), {"username": trainer_user.email, "password": "TrainerPass12345!"})
    assert login_resp.status_code == 302
    assert "_auth_user_id" in client.session
    client.post(reverse("admin:logout"))

    staff = StaffProfile.objects.create(
        user=trainer_user,
        primary_branch=b1,
        job_title="Fitness Trainer",
        hire_date=date(2026, 1, 1),
    )
    trainer = TrainerProfile.objects.create(staff=staff, status=TrainerProfile.TrainerStatus.ACTIVE)

    # Active pairing
    assignment = TrainerMemberAssignment.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        status=TrainerMemberAssignment.AssignmentStatus.ACTIVE,
    )

    # Historical completed session
    past_session = TrainingSession.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=date(2026, 10, 1),
        start_time=time(10, 0),
        end_time=time(11, 0),
        status=TrainingSession.SessionStatus.COMPLETED,
    )

    # Upcoming scheduled session
    future_session = TrainingSession.objects.create(
        trainer=trainer,
        member=sample_member,
        branch=b1,
        session_date=timezone.localdate() + timedelta(days=5),
        start_time=time(10, 0),
        end_time=time(11, 0),
        status=TrainingSession.SessionStatus.SCHEDULED,
    )

    # Upcoming scheduled shift
    future_shift = StaffShift.objects.create(
        staff=staff,
        branch=b1,
        date=timezone.localdate() + timedelta(days=2),
        start_time=time(8, 0),
        end_time=time(16, 0),
        status=StaffShift.ShiftStatus.SCHEDULED,
    )

    # Execute termination
    terminate_staff(actor=owner, staff_profile=staff, reason="Breach of security policy")

    # Assert user credentials revoked
    trainer_user.refresh_from_db()
    assert trainer_user.is_active is False
    assert trainer_user.is_staff is False
    assert trainer_user.branch_access.filter(is_active=True).count() == 0

    # Login MUST fail
    login_attempt = client.post(reverse("admin:login"), {"username": trainer_user.email, "password": "TrainerPass12345!"})
    assert "_auth_user_id" not in client.session

    # Trainer profile marked INACTIVE
    trainer.refresh_from_db()
    assert trainer.status == TrainerProfile.TrainerStatus.INACTIVE

    # Active assignments cancelled
    assignment.refresh_from_db()
    assert assignment.status == TrainerMemberAssignment.AssignmentStatus.CANCELLED

    # Upcoming session cancelled
    future_session.refresh_from_db()
    assert future_session.status == TrainingSession.SessionStatus.CANCELLED
    assert "terminated" in future_session.cancellation_reason.lower()

    # Upcoming shift cancelled
    future_shift.refresh_from_db()
    assert future_shift.status == StaffShift.ShiftStatus.CANCELLED

    # Historical session preserved
    past_session.refresh_from_db()
    assert past_session.status == TrainingSession.SessionStatus.COMPLETED

    # Future session booking rejected
    with pytest.raises(ValidationError) as exc:
        schedule_training_session(
            actor=owner,
            trainer=trainer,
            member=sample_member,
            branch=b1,
            session_date=date(2026, 10, 28),
            start_time=time(10, 0),
            end_time=time(11, 0),
        )
    assert "not active" in str(exc.value)

    # Audit event logged
    assert AuditEvent.objects.filter(
        action="staff_terminated",
        metadata__employee_number=staff.employee_number,
    ).exists()
