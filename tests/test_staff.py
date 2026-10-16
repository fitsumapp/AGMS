from datetime import date, time, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import has_role, is_owner, seed_roles
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.members.models import Member
from apps.members.services import register_member
from apps.staff.models import (
    LeaveRequest,
    ShiftTemplate,
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
