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


# 16. Weekly Class Limits & Calendar Week Boundaries
def test_weekly_class_limits_and_calendar_week_boundaries(test_branches, test_users, sample_class_type):
    b1, _ = test_branches
    owner = test_users["owner"]

    # Package allowing max 2 classes per week
    limited_pkg = MembershipPackage.objects.create(
        code="LIMITED-2",
        name="Limited 2 Classes/Week",
        price=Decimal("1500.00"),
        classes_included=True,
        allowed_classes_per_week=2,
        cross_branch_enabled=True,
    )

    member = register_member(actor=owner, home_branch=b1, first_name="Limited", last_name="M", phone="+251911445566")
    today = timezone.localdate()
    # Find Monday of this week
    monday = today - timedelta(days=today.weekday())
    tuesday = monday + timedelta(days=1)
    thursday = monday + timedelta(days=3)
    friday = monday + timedelta(days=4)
    next_monday = monday + timedelta(days=7)

    create_test_membership(
        member=member,
        package=limited_pkg,
        branch=b1,
        membership_number="MEM-LIM-01",
        start_date=monday,
        end_date=next_monday + timedelta(days=30),
    )

    # Schedule 3 classes in the same calendar week
    s_tue = schedule_class_session(owner, sample_class_type, b1, "Studio A", test_users["trainer"], tuesday, time(10, 0), time(11, 0))
    s_thu = schedule_class_session(owner, sample_class_type, b1, "Studio A", test_users["trainer"], thursday, time(10, 0), time(11, 0))
    s_fri = schedule_class_session(owner, sample_class_type, b1, "Studio A", test_users["trainer"], friday, time(10, 0), time(11, 0))
    s_next_mon = schedule_class_session(owner, sample_class_type, b1, "Studio A", test_users["trainer"], next_monday, time(10, 0), time(11, 0))

    # Booking 1 (Tuesday) -> Success (1/2)
    b1_res = book_class_session(owner, s_tue, member)
    assert b1_res.status == ClassBooking.BookingStatus.BOOKED

    # Booking 2 (Thursday) -> Success (2/2)
    b2_res = book_class_session(owner, s_thu, member)
    assert b2_res.status == ClassBooking.BookingStatus.BOOKED

    # Booking 3 (Friday, same week) -> Rejection (exceeds 2/week)
    with pytest.raises(ValidationError) as exc:
        book_class_session(owner, s_fri, member)
    assert "weekly class limit of 2 class(es)" in str(exc.value)

    # Cancel Tuesday booking -> Quota is restored
    cancel_class_booking(owner, b1_res, reason="Need Friday instead")
    b1_res.refresh_from_db()
    assert b1_res.status == ClassBooking.BookingStatus.CANCELLED

    # Booking 3 (Friday) now succeeds!
    b3_res = book_class_session(owner, s_fri, member)
    assert b3_res.status == ClassBooking.BookingStatus.BOOKED

    # Booking for next Monday (next week boundary) -> Success
    b_next = book_class_session(owner, s_next_mon, member)
    assert b_next.status == ClassBooking.BookingStatus.BOOKED


# 17. Membership Payment Policy and Balance Access
def test_membership_payment_policy_and_balance_access(test_branches, test_users, sample_class_type, test_package):
    from apps.core.models import GymSettings
    from apps.billing.models import Invoice, Payment

    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    profile = GymProfile.objects.get(singleton=1)
    settings_obj, _ = GymSettings.objects.get_or_create(profile=profile)
    settings_obj.allow_access_with_balance = False
    settings_obj.save()

    member = register_member(actor=owner, home_branch=b1, first_name="Policy", last_name="M", phone="+251911776655")
    session = schedule_class_session(owner, sample_class_type, b1, "Studio A", test_users["trainer"], today, time(12, 0), time(13, 0))

    # Case 1: Pending payment clearance with allow_access_with_balance=False -> Denied
    ms_pending = create_test_membership(
        member=member,
        package=test_package,
        branch=b1,
        membership_number="MEM-PENDING",
        payment_clearance=Membership.PaymentClearance.PENDING,
    )
    with pytest.raises(ValidationError) as exc:
        book_class_session(owner, session, member)
    assert "does not have an active, paid membership" in str(exc.value)

    # Case 2: Enable allow_access_with_balance=True, but 0 payments made -> Still Denied (unpaid)
    settings_obj.allow_access_with_balance = True
    settings_obj.save()

    with pytest.raises(ValidationError) as exc:
        book_class_session(owner, session, member)
    assert "does not have an active, paid membership" in str(exc.value)

    # Case 3: Create a partial verified payment -> Access Allowed!
    from apps.billing.models import PaymentMethod
    pm, _ = PaymentMethod.objects.get_or_create(code="CASH", defaults={"name": "Cash", "is_active": True})
    inv = Invoice.objects.create(
        invoice_number="INV-CLASS-001",
        member=member,
        membership=ms_pending,
        branch=b1,
        subtotal=Decimal("1500.00"),
        total_amount=Decimal("1500.00"),
        paid_amount=Decimal("500.00"),
        balance_due=Decimal("1000.00"),
        issue_date=today,
        due_date=today,
        status=Invoice.Status.PARTIALLY_PAID,
        created_by=owner,
    )
    Payment.objects.create(
        payment_number="PAY-CLASS-001",
        invoice=inv,
        branch=b1,
        payment_method=pm,
        amount=Decimal("500.00"),
        status=Payment.Status.VERIFIED,
        received_by=owner,
        idempotency_key="IDEM-CLASS-001",
    )

    # Booking now succeeds under shared allow_access_with_balance policy!
    b_partial = book_class_session(owner, session, member)
    assert b_partial.status == ClassBooking.BookingStatus.BOOKED

    # Case 4: Waived complimentary membership -> Allowed
    member_comp = register_member(actor=owner, home_branch=b1, first_name="Comp", last_name="M", phone="+251911776656")
    create_test_membership(
        member=member_comp,
        package=test_package,
        branch=b1,
        membership_number="MEM-COMP",
        payment_clearance=Membership.PaymentClearance.WAIVED_COMPLIMENTARY,
    )
    session_comp = schedule_class_session(owner, sample_class_type, b1, "Studio B", test_users["trainer"], today, time(13, 0), time(14, 0))
    b_comp = book_class_session(owner, session_comp, member_comp)
    assert b_comp.status == ClassBooking.BookingStatus.BOOKED


# 18. Concurrent Waitlist Promotions & Capacity Safety
@pytest.mark.django_db(transaction=True)
def test_concurrent_waitlist_promotions_and_capacity_safety(test_branches, test_users, sample_class_type, test_package):
    from django.db import connection
    import concurrent.futures

    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    # Session: capacity 2, waitlist capacity 3
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
        waitlist_capacity=3,
    )

    def create_member(num, name):
        m = register_member(actor=owner, home_branch=b1, first_name=name, last_name="M", phone=f"+2519119933{num}")
        create_test_membership(member=m, package=test_package, branch=b1, membership_number=f"MEM-WL-{num}")
        return m

    m_confirmed_1 = create_member(1, "BookedOne")
    m_confirmed_2 = create_member(2, "BookedTwo")
    m_waitlist_1 = create_member(3, "WaitOne")
    m_waitlist_2 = create_member(4, "WaitTwo")
    m_waitlist_3 = create_member(5, "WaitThree")

    # 2 confirmed bookings
    b1 = book_class_session(owner, session, m_confirmed_1)
    b2 = book_class_session(owner, session, m_confirmed_2)
    assert b1.status == ClassBooking.BookingStatus.BOOKED
    assert b2.status == ClassBooking.BookingStatus.BOOKED

    # 3 waitlisted bookings
    w1 = book_class_session(owner, session, m_waitlist_1)
    w2 = book_class_session(owner, session, m_waitlist_2)
    w3 = book_class_session(owner, session, m_waitlist_3)
    assert w1.waitlist_position == 1
    assert w2.waitlist_position == 2
    assert w3.waitlist_position == 3

    # Both confirmed bookings are cancelled simultaneously by two competing threads
    def cancel_task(bk):
        try:
            cancel_class_booking(actor=owner, booking=bk, reason="Concurrent cancellation test")
            return "CANCELLED"
        except Exception as e:
            return str(e)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(cancel_task, b1)
        f2 = executor.submit(cancel_task, b2)
        r1 = f1.result()
        r2 = f2.result()

    assert r1 == "CANCELLED"
    assert r2 == "CANCELLED"

    # Verify capacity invariants:
    # Exactly 2 confirmed spots exist (m_waitlist_1 and m_waitlist_2 promoted)
    active_booked = ClassBooking.objects.filter(session=session, status=ClassBooking.BookingStatus.BOOKED)
    assert active_booked.count() == 2
    promoted_members = set(active_booked.values_list("member_id", flat=True))
    assert m_waitlist_1.id in promoted_members
    assert m_waitlist_2.id in promoted_members

    # Exactly 1 member remains on waitlist (m_waitlist_3) with renumbered position 1
    remaining_waitlist = ClassBooking.objects.filter(session=session, status=ClassBooking.BookingStatus.WAITLISTED)
    assert remaining_waitlist.count() == 1
    assert remaining_waitlist.first().member_id == m_waitlist_3.id
    assert remaining_waitlist.first().waitlist_position == 1


# 19. Concurrent Class Scheduling & Room/Trainer Conflict Prevention
@pytest.mark.django_db(transaction=True)
def test_concurrent_class_scheduling_trainer_and_room_serialization(test_branches, test_users, sample_class_type):
    from django.db import connection
    import concurrent.futures

    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    # Two competing threads attempt to schedule overlapping class sessions with the same trainer
    def schedule_task(room_name):
        try:
            schedule_class_session(
                actor=owner,
                class_type=sample_class_type,
                branch=b1,
                room=room_name,
                trainer=test_users["trainer"],
                session_date=today,
                start_time=time(14, 0),
                end_time=time(15, 0),
            )
            return "SUCCESS"
        except Exception as e:
            return str(e)
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(schedule_task, "Studio A")
        f2 = executor.submit(schedule_task, "Studio B")
        r1 = f1.result()
        r2 = f2.result()

    results = [r1, r2]
    # Exactly 1 must succeed; the other must fail with conflict without deadlock
    assert results.count("SUCCESS") == 1
    assert any("conflicting group class" in r for r in results)
    assert ClassSession.objects.filter(
        trainer=test_users["trainer"],
        session_date=today,
        start_time=time(14, 0),
        status=ClassSession.SessionStatus.SCHEDULED,
    ).count() == 1


# 20. Cross-Conflict Prevention: Group Classes vs Personal Training
def test_cross_conflict_pt_and_group_classes(test_branches, test_users, sample_class_type, test_package):
    from apps.staff.services import schedule_training_session

    b1, _ = test_branches
    owner = test_users["owner"]
    today = timezone.localdate()

    trainer = test_users["trainer"]
    member = register_member(actor=owner, home_branch=b1, first_name="Cross", last_name="M", phone="+251911884433")
    create_test_membership(member=member, package=test_package, branch=b1, membership_number="MEM-CROSS-01")

    # Group class scheduled for trainer from 10:00 to 11:00
    schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=trainer,
        session_date=today,
        start_time=time(10, 0),
        end_time=time(11, 0),
    )

    # Attempting to schedule personal training session for the same trainer at 10:30 is rejected
    with pytest.raises(ValidationError) as exc:
        schedule_training_session(
            actor=owner,
            trainer=trainer,
            member=member,
            branch=b1,
            session_date=today,
            start_time=time(10, 30),
            end_time=time(11, 30),
        )
    assert "already leading a group class" in str(exc.value)

    # Member books group class from 15:00 to 16:00
    session_pm = schedule_class_session(
        actor=owner,
        class_type=sample_class_type,
        branch=b1,
        room="Studio A",
        trainer=trainer,
        session_date=today,
        start_time=time(15, 0),
        end_time=time(16, 0),
    )
    book_class_session(owner, session_pm, member)

    # Create a second trainer to isolate member conflict check
    from django.contrib.auth.models import Group
    trainer_u2 = User.objects.create_user("trainer2@agms.test", "TrainerPass123!", is_staff=True)
    trainer_u2.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer_u2, branch=b1, is_active=True, is_primary=True)
    staff2 = StaffProfile.objects.create(user=trainer_u2, primary_branch=b1, job_title="Trainer 2")
    trainer2 = TrainerProfile.objects.create(staff=staff2, status=TrainerProfile.TrainerStatus.ACTIVE)

    # Attempting to schedule personal training session for the member at 15:30 is rejected
    with pytest.raises(ValidationError) as exc:
        schedule_training_session(
            actor=owner,
            trainer=trainer2,
            member=member,
            branch=b1,
            session_date=today,
            start_time=time(15, 30),
            end_time=time(16, 30),
        )
    assert "already booked for a group class" in str(exc.value)
