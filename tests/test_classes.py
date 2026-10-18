import concurrent.futures
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import has_role, is_owner, seed_roles
from apps.branches.models import AuditEvent, Branch, UserBranchAccess
from apps.classes.models import (
    ClassBooking,
    ClassScheduleException,
    ClassScheduleTemplate,
    ClassSession,
    ClassType,
)
from apps.classes.selectors import (
    get_class_scheduling_metrics,
    get_popular_class_types,
    get_trainer_class_workload,
)
from apps.classes.services import (
    book_class_session,
    cancel_class_booking,
    generate_sessions_from_schedule,
    mark_class_attendance,
    schedule_class_session,
)
from apps.core.models import GymProfile
from apps.members.models import Member
from apps.members.services import register_member
from apps.memberships.models import Membership, MembershipPackage
from apps.staff.models import StaffProfile, TrainerProfile, TrainingSession

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_classes_prerequisites(db):
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
def test_branches():
    b1 = Branch.objects.create(code="BOLE", name="Bole Branch", address="Bole Road", is_active=True)
    b2 = Branch.objects.create(code="MEG", name="Megenagna Branch", address="Megenagna Square", is_active=True)
    return b1, b2


@pytest.fixture
def test_users(test_branches):
    b1, b2 = test_branches
    from django.contrib.auth.models import Group

    owner = User.objects.create_user("owner@agms.test", "OwnerPass12345!", is_staff=True)
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    mgr = User.objects.create_user("mgr_bole@agms.test", "ManagerPass123!", is_staff=True)
    mgr.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr, branch=b1, is_active=True, is_primary=True)

    receptionist = User.objects.create_user("recept@agms.test", "ReceptPass123!", is_staff=True)
    receptionist.groups.add(Group.objects.get(name="Receptionist"))
    UserBranchAccess.objects.create(user=receptionist, branch=b1, is_active=True, is_primary=True)

    trainer_u = User.objects.create_user("trainer1@agms.test", "TrainerPass123!", is_staff=True)
    trainer_u.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer_u, branch=b1, is_active=True, is_primary=True)

    staff = StaffProfile.objects.create(user=trainer_u, primary_branch=b1, job_title="Senior Fitness Trainer")
    trainer = TrainerProfile.objects.create(staff=staff, status=TrainerProfile.TrainerStatus.ACTIVE)

    return {
        "owner": owner,
        "manager": mgr,
        "receptionist": receptionist,
        "trainer_user": trainer_u,
        "trainer": trainer,
    }


@pytest.fixture
def test_package(test_branches):
    b1, _ = test_branches
    pkg = MembershipPackage.objects.create(
        code="GOLD-MONTHLY",
        name="Gold Monthly Package",
        price=Decimal("1500.00"),
        duration_value=1,
        duration_unit=MembershipPackage.DurationUnit.MONTH,
        cross_branch_enabled=False,
        classes_included=True,
    )
    return pkg


def create_test_membership(
    member,
    package,
    branch,
    status=Membership.Status.ACTIVE,
    payment_clearance=Membership.PaymentClearance.CLEARED,
    start_date=None,
    end_date=None,
    membership_number=None,
):
    import uuid
    today = timezone.localdate()
    start_date = start_date or (today - timedelta(days=5))
    end_date = end_date or (today + timedelta(days=25))
    membership_number = membership_number or f"MEM-{uuid.uuid4().hex[:8].upper()}"
    return Membership.objects.create(
        membership_number=membership_number,
        member=member,
        package=package,
        home_branch=branch,
        start_date=start_date,
        end_date=end_date,
        status=status,
        payment_clearance_status=payment_clearance,
        price_snapshot=package.price,
        duration_unit_snapshot=package.duration_unit,
        duration_value_snapshot=package.duration_value,
        cross_branch_snapshot=package.cross_branch_enabled,
    )


@pytest.fixture
def active_member(test_branches, test_users, test_package):
    b1, _ = test_branches
    owner = test_users["owner"]
    member = register_member(
        actor=owner,
        home_branch=b1,
        first_name="Abebe",
        last_name="Bikila",
        phone="+251911223344",
        gender=Member.Gender.MALE,
    )
    create_test_membership(member, test_package, b1, membership_number="MEM-000001")
    return member


@pytest.fixture
def sample_class_type(test_branches):
    b1, _ = test_branches
    ct = ClassType.objects.create(
        name="Power Yoga",
        slug="power-yoga",
        category=ClassType.Category.MIND_BODY,
        difficulty_level=ClassType.Difficulty.ALL_LEVELS,
        default_duration_minutes=60,
        default_capacity=20,
        default_waitlist_capacity=5,
        color_code="#0d9488",
    )
    ct.applicable_branches.add(b1)
    return ct


# 1. Class Catalog Creation & Validation
def test_class_catalog_creation_and_validation(test_branches):
    b1, _ = test_branches
    hiit = ClassType(
        name="Cardio HIIT",
        category=ClassType.Category.CARDIO,
        difficulty_level=ClassType.Difficulty.INTERMEDIATE,
        default_duration_minutes=45,
        default_capacity=15,
        default_waitlist_capacity=5,
    )
    hiit.clean()
    hiit.save()
    assert hiit.slug == "cardio-hiit"
    assert hiit.is_available_at_branch(b1)

    # Invalid duration (<15)
    invalid_class = ClassType(name="Too Short", default_duration_minutes=10)
    with pytest.raises(ValidationError):
        invalid_class.clean()


# 2. Recurring Schedule Template Creation
def test_recurring_schedule_template(test_branches, test_users, sample_class_type):
    b1, _ = test_branches
    trainer = test_users["trainer"]

    template = ClassScheduleTemplate(
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=trainer,
        day_of_week=ClassScheduleTemplate.DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        capacity=10,
        waitlist_capacity=3,
    )
    template.clean()
    template.save()

    assert template.day_of_week == 0
    assert "Monday" in str(template)


# 3. Schedule Exceptions
def test_schedule_exceptions(test_branches, test_users, sample_class_type):
    b1, _ = test_branches
    trainer = test_users["trainer"]

    template = ClassScheduleTemplate.objects.create(
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=trainer,
        day_of_week=ClassScheduleTemplate.DayOfWeek.MONDAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        capacity=10,
    )

    holiday = date(2026, 11, 2)  # Monday
    exc = ClassScheduleException.objects.create(
        schedule_template=template,
        exception_date=holiday,
        reason="National Holiday",
        is_cancelled=True,
    )
    assert exc.is_cancelled is True

    # Generation skips exception date
    sessions = generate_sessions_from_schedule(
        template=template,
        start_date=holiday,
        end_date=holiday + timedelta(days=7),
    )
    # The holiday session must NOT be generated
    assert not any(s.session_date == holiday for s in sessions)


# 4. Trainer Overlap Conflict with Other Class Sessions
def test_trainer_class_overlap_conflict(test_branches, test_users, sample_class_type):
    b1, _ = test_branches
    trainer = test_users["trainer"]
    session_date = date(2026, 11, 5)

    s1 = schedule_class_session(
        actor=test_users["owner"],
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=trainer,
        session_date=session_date,
        start_time=time(10, 0),
        end_time=time(11, 0),
    )
    assert s1.status == ClassSession.SessionStatus.SCHEDULED

    # Overlapping class for same trainer rejected
    with pytest.raises(ValidationError) as exc:
        schedule_class_session(
            actor=test_users["owner"],
            class_type=sample_class_type,
            branch=b1,
            room="Studio B",
            trainer=trainer,
            session_date=session_date,
            start_time=time(10, 30),  # overlaps
            end_time=time(11, 30),
        )
    assert "conflicting group class" in str(exc.value)


# 5. Trainer Conflict with Personal Training (PT) Session
def test_trainer_pt_conflict_prevention(test_branches, test_users, sample_class_type, active_member):
    b1, _ = test_branches
    trainer = test_users["trainer"]
    session_date = date(2026, 11, 6)

    # 1. Existing 1-on-1 PT Session scheduled in Phase 08
    TrainingSession.objects.create(
        trainer=trainer,
        member=active_member,
        branch=b1,
        session_date=session_date,
        start_time=time(14, 0),
        end_time=time(15, 0),
        status=TrainingSession.SessionStatus.SCHEDULED,
    )

    # 2. Attempting to schedule a group class at overlapping time rejected
    with pytest.raises(ValidationError) as exc:
        schedule_class_session(
            actor=test_users["owner"],
            class_type=sample_class_type,
            branch=b1,
            room="Studio A",
            trainer=trainer,
            session_date=session_date,
            start_time=time(14, 30),
            end_time=time(15, 30),
        )
    assert "conflicting personal training session" in str(exc.value)


# 6. Room Conflict Prevention in Same Branch
def test_room_conflict_prevention(test_branches, test_users, sample_class_type):
    b1, _ = test_branches
    trainer = test_users["trainer"]

    # Second trainer
    u2 = User.objects.create_user("trainer2@agms.test", "TrainerPass222!", is_staff=True)
    from django.contrib.auth.models import Group
    u2.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=u2, branch=b1, is_active=True, is_primary=True)
    staff2 = StaffProfile.objects.create(user=u2, primary_branch=b1, job_title="Trainer 2")
    trainer2 = TrainerProfile.objects.create(staff=staff2)

    session_date = date(2026, 11, 7)

    # Trainer 1 books Studio A
    schedule_class_session(
        actor=test_users["owner"],
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=trainer,
        session_date=session_date,
        start_time=time(8, 0),
        end_time=time(9, 0),
    )

    # Trainer 2 attempts to book same Studio A at overlapping time
    with pytest.raises(ValidationError) as exc:
        schedule_class_session(
            actor=test_users["owner"],
            class_type=sample_class_type,
            branch=b1,
            room="Studio A",
            trainer=trainer2,
            session_date=session_date,
            start_time=time(8, 30),
            end_time=time(9, 30),
        )
    assert "already booked for another class" in str(exc.value)


# 7. Package Class Eligibility (classes_included=False rejected)
def test_package_class_eligibility_rejection(test_branches, test_users, sample_class_type):
    b1, _ = test_branches
    owner = test_users["owner"]
    member = register_member(actor=owner, home_branch=b1, first_name="GymOnly", last_name="M", phone="+251911880011")

    # Package with classes_included=False
    gym_only_pkg = MembershipPackage.objects.create(
        code="BASIC-GYM",
        name="Basic Gym Only",
        price=Decimal("1000.00"),
        classes_included=False,
    )
    today = timezone.localdate()
    create_test_membership(
        member=member,
        package=gym_only_pkg,
        branch=b1,
        membership_number="MEM-GYMONLY",
    )

    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=today,
        start_time=time(16, 0),
        end_time=time(17, 0),
    )

    # Booking rejected because package excludes classes
    with pytest.raises(ValidationError) as exc:
        book_class_session(actor=owner, session=session, member=member)
    assert "does not have an active, paid membership authorizing group classes" in str(exc.value)


# 8. Branch Restrictions (cross-branch denied if not enabled)
def test_branch_restriction_class_booking(test_branches, test_users, sample_class_type):
    b1, b2 = test_branches
    owner = test_users["owner"]
    member = register_member(actor=owner, home_branch=b2, first_name="BoleOnly", last_name="M", phone="+251911880022")

    # Member's home branch is B2, cross_branch is False
    single_branch_pkg = MembershipPackage.objects.create(
        code="SINGLE-BR",
        name="Single Branch Package",
        price=Decimal("1200.00"),
        cross_branch_enabled=False,
        classes_included=True,
    )
    today = timezone.localdate()
    create_test_membership(
        member=member,
        package=single_branch_pkg,
        branch=b2,
        membership_number="MEM-SINGLE",
    )

    # Class at Branch 1
    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=today,
        start_time=time(11, 0),
        end_time=time(12, 0),
    )

    with pytest.raises(ValidationError) as exc:
        book_class_session(actor=owner, session=session, member=member)
    assert "authorizing group classes at Bole Branch" in str(exc.value)


# 9. Duplicate Bookings Rejected
def test_duplicate_class_booking_rejected(test_branches, test_users, sample_class_type, active_member):
    b1, _ = test_branches
    owner = test_users["owner"]
    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=date.today(),
        start_time=time(17, 0),
        end_time=time(18, 0),
    )

    booking = book_class_session(actor=owner, session=session, member=active_member)
    assert booking.status == ClassBooking.BookingStatus.BOOKED

    # Duplicate booking
    with pytest.raises(ValidationError) as exc:
        book_class_session(actor=owner, session=session, member=active_member)
    assert "already booked or waitlisted" in str(exc.value)


# 10. Capacity Boundaries and Sequential Waitlist
def test_capacity_boundaries_and_waitlist(test_branches, test_users, sample_class_type, test_package):
    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    # Class with capacity 2 and waitlist capacity 2
    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        capacity=2,
        waitlist_capacity=2,
    )

    def create_active_member(num, name):
        m = register_member(actor=owner, home_branch=b1, first_name=name, last_name="Tester", phone=f"+2519110000{num}")
        create_test_membership(
            member=m,
            package=test_package,
            branch=b1,
            membership_number=f"MEM-TEST-{num}",
        )
        return m

    m1 = create_active_member(1, "M1")
    m2 = create_active_member(2, "M2")
    m3 = create_active_member(3, "M3")
    m4 = create_active_member(4, "M4")
    m5 = create_active_member(5, "M5")

    # Slot 1 -> BOOKED
    b1_res = book_class_session(owner, session, m1)
    assert b1_res.status == ClassBooking.BookingStatus.BOOKED
    assert session.available_spots == 1

    # Slot 2 -> BOOKED
    b2_res = book_class_session(owner, session, m2)
    assert b2_res.status == ClassBooking.BookingStatus.BOOKED
    assert session.is_full is True
    assert session.available_spots == 0

    # Slot 3 -> WAITLISTED (position 1)
    b3_res = book_class_session(owner, session, m3)
    assert b3_res.status == ClassBooking.BookingStatus.WAITLISTED
    assert b3_res.waitlist_position == 1

    # Slot 4 -> WAITLISTED (position 2)
    b4_res = book_class_session(owner, session, m4)
    assert b4_res.status == ClassBooking.BookingStatus.WAITLISTED
    assert b4_res.waitlist_position == 2

    # Slot 5 -> Rejected (class & waitlist full)
    with pytest.raises(ValidationError) as exc:
        book_class_session(owner, session, m5)
    assert "completely full" in str(exc.value)


# 11. Concurrency-Safe Booking with PostgreSQL Row Locks
@pytest.mark.django_db(transaction=True)
def test_concurrent_class_booking_serialization(test_branches, test_users, sample_class_type, test_package):
    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    # Session with capacity of 1 (and 0 waitlist spots)
    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=today,
        start_time=time(19, 0),
        end_time=time(20, 0),
        capacity=1,
        waitlist_capacity=0,
    )

    def create_member(num, name):
        m = register_member(actor=owner, home_branch=b1, first_name=name, last_name="M", phone=f"+2519119900{num}")
        create_test_membership(
            member=m,
            package=test_package,
            branch=b1,
            membership_number=f"MEM-CC-{num}",
        )
        return m

    m_a = create_member(1, "ConcurrentA")
    m_b = create_member(2, "ConcurrentB")

    def book_task(mem):
        try:
            book_class_session(actor=owner, session=session, member=mem)
            return "SUCCESS"
        except Exception as e:
            return str(e)
        finally:
            connection.close()

    # Two concurrent threads race for the single spot
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(book_task, m_a)
        f2 = executor.submit(book_task, m_b)
        r1 = f1.result()
        r2 = f2.result()

    results = [r1, r2]
    assert results.count("SUCCESS") == 1
    assert any("completely full" in r for r in results)
    assert ClassBooking.objects.filter(session=session, status=ClassBooking.BookingStatus.BOOKED).count() == 1


# 12. Cancellation and Automatic Waitlist Promotion
def test_cancellation_and_automatic_waitlist_promotion(test_branches, test_users, sample_class_type, test_package):
    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=today,
        start_time=time(12, 0),
        end_time=time(13, 0),
        capacity=1,
        waitlist_capacity=2,
    )

    def make_m(num, name):
        m = register_member(actor=owner, home_branch=b1, first_name=name, last_name="WL", phone=f"+2519117700{num}")
        create_test_membership(
            member=m,
            package=test_package,
            branch=b1,
            membership_number=f"MEM-WL-{num}",
        )
        return m

    m1 = make_m(1, "MemberOne")
    m2 = make_m(2, "MemberTwo")
    m3 = make_m(3, "MemberThree")

    b1 = book_class_session(owner, session, m1)  # BOOKED
    b2 = book_class_session(owner, session, m2)  # WAITLIST 1
    b3 = book_class_session(owner, session, m3)  # WAITLIST 2

    assert b1.status == ClassBooking.BookingStatus.BOOKED
    assert b2.status == ClassBooking.BookingStatus.WAITLISTED
    assert b2.waitlist_position == 1
    assert b3.waitlist_position == 2

    # m1 cancels booking
    cancel_class_booking(actor=owner, booking=b1, reason="Work conflict")

    b1.refresh_from_db()
    b2.refresh_from_db()
    b3.refresh_from_db()

    assert b1.status == ClassBooking.BookingStatus.CANCELLED
    # m2 promoted to BOOKED automatically!
    assert b2.status == ClassBooking.BookingStatus.BOOKED
    assert b2.waitlist_position is None
    assert b2.promoted_from_waitlist_at is not None

    # m3 waitlist renumbered from 2 to 1!
    assert b3.status == ClassBooking.BookingStatus.WAITLISTED
    assert b3.waitlist_position == 1


# 13. Class Attendance Marking (Attended & No-Show)
def test_class_attendance_marking(test_branches, test_users, sample_class_type, active_member):
    b1, _ = test_branches
    owner = test_users["owner"]
    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=date.today(),
        start_time=time(15, 0),
        end_time=time(16, 0),
    )

    booking = book_class_session(owner, session, active_member)

    # Mark attended
    mark_class_attendance(actor=test_users["receptionist"], booking=booking, status=ClassBooking.BookingStatus.ATTENDED, notes="Arrived on time.")
    booking.refresh_from_db()
    assert booking.status == ClassBooking.BookingStatus.ATTENDED
    assert booking.attendance_marked_at is not None
    assert booking.attendance_marked_by == test_users["receptionist"]
    assert "on time" in booking.notes

    # Verify session attended count
    assert session.attended_count == 1
    assert session.no_show_count == 0


# 14. Class Analytics & Selectors
def test_class_analytics_metrics(test_branches, test_users, sample_class_type, active_member):
    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    session = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=today,
        start_time=time(7, 0),
        end_time=time(8, 0),
        capacity=10,
    )
    b = book_class_session(owner, session, active_member)
    mark_class_attendance(owner, b, ClassBooking.BookingStatus.ATTENDED)
    session.status = ClassSession.SessionStatus.COMPLETED
    session.save()

    metrics = get_class_scheduling_metrics(branch=b1, start_date=today, end_date=today)
    assert metrics["total_classes"] == 1
    assert metrics["completed_classes"] == 1
    assert metrics["total_bookings"] == 1
    assert metrics["attended_count"] == 1
    assert metrics["attendance_rate"] == 100.0
    assert metrics["capacity_utilization"] == 10.0  # 1/10

    popular = get_popular_class_types(branch=b1, start_date=today, end_date=today)
    assert len(popular) == 1
    assert popular[0]["name"] == "Power Yoga"

    workload = get_trainer_class_workload(branch=b1, start_date=today, end_date=today)
    assert len(workload) == 1
    assert workload[0]["full_name"] == test_users["trainer"].full_name
    assert workload[0]["completed_classes"] == 1


# 15. Calendar View & Web Views
def test_class_calendar_and_reports_views(client, test_branches, test_users, sample_class_type):
    b1, _ = test_branches
    owner = test_users["owner"]
    schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=test_users["trainer"],
        session_date=date.today(),
        start_time=time(10, 0),
        end_time=time(11, 0),
    )

    # 1. Calendar View
    client.force_login(owner)
    cal_resp = client.get(reverse("class-calendar"))
    assert cal_resp.status_code == 200
    assert b"Class Schedule Calendar" in cal_resp.content
    assert b"Power Yoga" in cal_resp.content

    # 2. Reports View (Web Preview)
    rep_resp = client.get(reverse("class-reports"))
    assert rep_resp.status_code == 200
    assert b"Group Class Analytics &amp; Reports" in rep_resp.content or b"Group Class Analytics" in rep_resp.content

    # 3. Reports CSV Export
    csv_resp = client.get(f"{reverse('class-reports')}?export=csv")
    assert csv_resp.status_code == 200
    assert csv_resp["Content-Type"] == "text/csv"
    assert b"Total Classes" in csv_resp.content
