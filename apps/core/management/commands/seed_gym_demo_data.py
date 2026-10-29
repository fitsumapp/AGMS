import hashlib
import os
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import BUSINESS_ROLES, seed_roles
from apps.attendance.models import (
    AccessDevice,
    Attendance,
    MemberCredential,
)
from apps.billing.models import (
    Invoice,
    InvoiceItem,
    Payment,
    PaymentMethod,
    Receipt,
)
from apps.branches.models import Branch, UserBranchAccess
from apps.classes.models import (
    ClassBooking,
    ClassSession,
    ClassType,
)
from apps.core.models import GymProfile
from apps.facilities.models import (
    Equipment,
    EquipmentCategory,
    FacilityArea,
    Locker,
    LockerAssignment,
    LockerZone,
    MaintenanceSchedule,
    MaintenanceWorkOrder,
)
from apps.finance.models import (
    BranchFinanceSettings,
    Expense,
    ExpenseCategory,
)
from apps.inventory.models import (
    CashierShift,
    Product,
    ProductCategory,
    Sale,
    SaleItem,
    SalePayment,
    StockItem,
)
from apps.members.models import (
    Member,
    MemberEmergencyContact,
    MemberStatusHistory,
)
from apps.memberships.models import (
    Membership,
    MembershipFreeze,
    MembershipPackage,
    PackageBranch,
)
from apps.staff.models import (
    StaffProfile,
    StaffShift,
    TrainerProfile,
)

User = get_user_model()
DEMO_PASSWORD = os.environ.get("AGMS_DEMO_PASSWORD", "DemoPassword2026!")


class Command(BaseCommand):
    help = "Seed realistic, idempotent gym demonstration data for AGMS (Phases 01-14)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-demo",
            action="store_true",
            help="Deprecated/disabled: Direct cascade deletion of records is forbidden.",
        )

    def handle(self, *args, **options):
        # 0. Strict Production Guard
        if not settings.DEBUG or os.environ.get("DJANGO_SETTINGS_MODULE", "").endswith(".production"):
            raise CommandError(
                "SECURITY HALT: seed_gym_demo_data cannot be executed in production environments "
                "(DEBUG=False or production settings detected)."
            )

        if options.get("reset_demo"):
            raise CommandError(
                "DESTRUCTIVE RESET BLOCKED: Direct cascade purge of members, invoices, and payments is disabled "
                "to preserve audit trail and financial integrity. For demo resets, run against an isolated disposable "
                "database (e.g. POSTGRES_DB=demo_gym_erp)."
            )

        self.stdout.write(self.style.NOTICE("==> Seeding AGMS Realistic Gym Demo Environment..."))

        # 1. Ensure Roles are seeded
        seed_roles()
        self.stdout.write(self.style.SUCCESS("[1/12] Business roles and permissions verified."))

        # 2. Gym Profile & Branches
        gym_profile = self.seed_gym_profile()
        addis_branch, bole_branch = self.seed_branches()
        self.stdout.write(self.style.SUCCESS(f"[2/12] Gym Profile and 2 Branches verified ({addis_branch.code}, {bole_branch.code})."))

        # 3. Staff Accounts & Profiles
        staff_map = self.seed_staff(addis_branch, bole_branch)
        self.stdout.write(self.style.SUCCESS(f"[3/12] Seeded {len(staff_map)} staff accounts across all 6 business roles."))

        # 5. Payment Methods & Finance Settings
        cash_method, telebirr_method, bank_method = self.seed_payment_methods()
        self.seed_finance_settings(addis_branch, bole_branch)
        self.stdout.write(self.style.SUCCESS("[4/12] Payment methods and branch finance settings configured."))

        # 6. Membership Packages & Branch Authorization
        packages = self.seed_packages(addis_branch, bole_branch, staff_map["owner"])
        self.stdout.write(self.style.SUCCESS(f"[5/12] Seeded {len(packages)} membership packages with branch mappings."))

        # 7. Realistic Fictional Members
        members = self.seed_members(addis_branch, bole_branch, staff_map["receptionist_addis"])
        self.stdout.write(self.style.SUCCESS(f"[6/12] Seeded {len(members)} realistic fictional members."))

        # 8. Memberships, Invoices, and Payments
        memberships_data = self.seed_memberships_and_billing(
            members, packages, addis_branch, bole_branch, staff_map, cash_method, telebirr_method
        )
        self.stdout.write(self.style.SUCCESS(f"[7/12] Seeded {len(memberships_data['memberships'])} memberships and corresponding billing invoices."))

        # 9. Member Credentials & Attendance Records
        attendance_count = self.seed_attendance(memberships_data["active_members"], addis_branch, bole_branch, staff_map)
        self.stdout.write(self.style.SUCCESS(f"[8/12] Seeded QR credentials and {attendance_count} attendance check-ins."))

        # 10. Group Classes, Sessions & Bookings
        classes_data = self.seed_classes(addis_branch, bole_branch, staff_map, memberships_data["active_members"])
        self.stdout.write(self.style.SUCCESS(f"[9/12] Seeded group classes, weekly sessions, and participant bookings."))

        # 11. Facilities: Lockers & Equipment
        facilities_data = self.seed_facilities(addis_branch, bole_branch, memberships_data["active_members"], staff_map)
        self.stdout.write(self.style.SUCCESS(f"[10/12] Seeded locker zones, lockers, equipment units, and maintenance work orders."))

        # 12. POS Retail & Inventory
        pos_data = self.seed_inventory_and_pos(addis_branch, bole_branch, staff_map, cash_method)
        self.stdout.write(self.style.SUCCESS(f"[11/12] Seeded retail products, stock levels, cashier shifts, and completed sales."))

        # 13. Operational Expenses
        expenses = self.seed_expenses(addis_branch, bole_branch, staff_map)
        self.stdout.write(self.style.SUCCESS(f"[12/12] Seeded {len(expenses)} operational expenses across categories."))

        self.stdout.write(self.style.SUCCESS("\n========================================================"))
        self.stdout.write(self.style.SUCCESS("AGMS DEMO ENVIRONMENT SEEDING COMPLETED SUCCESSFULLY!"))
        self.stdout.write(self.style.SUCCESS("All records are idempotent, isolated, and production-rule compliant."))
        self.stdout.write(self.style.SUCCESS("========================================================\n"))

    def seed_gym_profile(self):
        profile, _ = GymProfile.objects.get_or_create(
            singleton=1,
            defaults={
                "name": "Antigravity Gym & Fitness Center",
                "legal_name": "Antigravity Wellness & Sports PLC",
                "tax_id": "TIN-0098472615",
                "commercial_registration_number": "CR-AA-2024-9841",
                "phone": "+251911223344",
                "email": "info@antigravitygym.com",
                "website": "https://antigravitygym.com",
                "address": "Bole Sub-City, Kebele 03, House 412, Addis Ababa, Ethiopia",
                "receipt_header": "Welcome to Antigravity Gym - Empowering Your Peak Fitness",
                "receipt_footer": "Thank you for training with us! All sales and fees are non-refundable.",
                "invoice_terms": "Payment is due upon receipt. Unpaid memberships are suspended after 3 days.",
                "default_language": "en",
                "default_currency": "ETB",
                "default_timezone": "Africa/Addis_Ababa",
                "fiscal_year_start_month": 1,
                "member_id_prefix": "MEM-",
                "min_member_age": 14,
                "require_emergency_contact": True,
            },
        )
        return profile

    def seed_branches(self):
        addis, _ = Branch.objects.get_or_create(
            code="ADD-1",
            defaults={
                "name": "Addis Main Branch",
                "description": "Headquarters & Premium Performance Training Center",
                "phone": "+251911223344",
                "email": "addis@antigravitygym.com",
                "address": "Bole Medhanialem, Addis Ababa",
                "city": "Addis Ababa",
                "opening_date": date(2024, 1, 1),
                "is_active": True,
            },
        )
        bole, _ = Branch.objects.get_or_create(
            code="BOLE",
            defaults={
                "name": "Bole Express Branch",
                "description": "Bole Airport Road Cardio & Express Center",
                "phone": "+251922334455",
                "email": "bole@antigravitygym.com",
                "address": "Bole Airport Road, Africa Avenue, Addis Ababa",
                "city": "Addis Ababa",
                "opening_date": date(2024, 6, 1),
                "is_active": True,
            },
        )
        return addis, bole

    def seed_staff(self, addis, bole):
        staff_accounts = {
            "owner": {
                "email": "demo_owner@example.test",
                "role": "Gym Owner",
                "first_name": "Tewodros",
                "last_name": "Kassa",
                "branch": addis,
                "is_superuser": False,
            },
            "manager_addis": {
                "email": "demo_manager_addis@example.test",
                "role": "Branch Manager",
                "first_name": "Eleni",
                "last_name": "Gebre",
                "branch": addis,
                "is_superuser": False,
            },
            "manager_bole": {
                "email": "demo_manager_bole@example.test",
                "role": "Branch Manager",
                "first_name": "Yohannes",
                "last_name": "Mekonnen",
                "branch": bole,
                "is_superuser": False,
            },
            "receptionist_addis": {
                "email": "demo_receptionist_addis@example.test",
                "role": "Receptionist",
                "first_name": "Sara",
                "last_name": "Tesfaye",
                "branch": addis,
                "is_superuser": False,
            },
            "receptionist_bole": {
                "email": "demo_receptionist_bole@example.test",
                "role": "Receptionist",
                "first_name": "Bethlehem",
                "last_name": "Alemu",
                "branch": bole,
                "is_superuser": False,
            },
            "accountant": {
                "email": "demo_accountant@example.test",
                "role": "Accountant",
                "first_name": "Dawit",
                "last_name": "Tadesse",
                "branch": addis,
                "is_superuser": False,
            },
            "trainer_1": {
                "email": "demo_trainer_1@example.test",
                "role": "Trainer",
                "first_name": "Kenenisa",
                "last_name": "Worku",
                "branch": addis,
                "specialty": "HIIT, Cross-Training & Conditioning",
                "is_superuser": False,
            },
            "trainer_2": {
                "email": "demo_trainer_2@example.test",
                "role": "Trainer",
                "first_name": "Tirunesh",
                "last_name": "Desta",
                "branch": bole,
                "specialty": "Power Yoga, Pilates & Mobility",
                "is_superuser": False,
            },
            "trainer_3": {
                "email": "demo_trainer_3@example.test",
                "role": "Trainer",
                "first_name": "Solomon",
                "last_name": "Haile",
                "branch": addis,
                "specialty": "Olympic Lifting & Hypertrophy",
                "is_superuser": False,
            },
            "pos_addis": {
                "email": "demo_pos_1@example.test",
                "role": "POS Staff",
                "first_name": "Daniel",
                "last_name": "Assefa",
                "branch": addis,
                "is_superuser": False,
            },
            "pos_bole": {
                "email": "demo_pos_2@example.test",
                "role": "POS Staff",
                "first_name": "Hanna",
                "last_name": "Solomon",
                "branch": bole,
                "is_superuser": False,
            },
        }

        created_users = {}
        for key, info in staff_accounts.items():
            user, created = User.objects.get_or_create(
                email=info["email"],
                defaults={
                    "first_name": info["first_name"],
                    "last_name": info["last_name"],
                    "phone": f"+2519119900{list(staff_accounts.keys()).index(key):02d}",
                    "is_staff": True,
                    "is_active": True,
                    "is_superuser": info["is_superuser"],
                },
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()

            # Assign Group
            group = Group.objects.get(name=info["role"])
            user.groups.add(group)

            # Assign UserBranchAccess
            if not user.is_superuser:
                UserBranchAccess.objects.get_or_create(
                    user=user,
                    branch=info["branch"],
                    defaults={
                        "is_primary": True,
                        "is_active": True,
                    },
                )

            # Create StaffProfile
            profile, _ = StaffProfile.objects.get_or_create(
                user=user,
                defaults={
                    "employee_number": f"EMP-DEMO-{list(staff_accounts.keys()).index(key) + 1:04d}",
                    "primary_branch": info["branch"],
                    "job_title": info["role"],
                    "department": StaffProfile.Department.FITNESS if info["role"] == "Trainer" else StaffProfile.Department.FRONT_DESK,
                    "employment_type": StaffProfile.EmploymentType.FULL_TIME,
                    "employment_status": "ACTIVE",
                    "hire_date": date(2024, 1, 15),
                },
            )

            # Create TrainerProfile if role is Trainer
            if info["role"] == "Trainer":
                tprofile, _ = TrainerProfile.objects.get_or_create(
                    staff=profile,
                    defaults={
                        "bio": f"Certified coach specializing in {info.get('specialty', 'Strength')}.",
                        "specializations": info.get("specialty", "Strength & Conditioning"),
                        "experience_years": 5,
                        "status": "ACTIVE",
                        "max_active_clients": 20,
                        "hourly_rate": Decimal("350.00"),
                    },
                )
                created_users[f"{key}_profile"] = tprofile

            created_users[key] = user

        return created_users

    def seed_payment_methods(self):
        cash, _ = PaymentMethod.objects.get_or_create(
            code="CASH",
            defaults={"name": "Cash", "method_type": PaymentMethod.MethodType.CASH, "is_active": True},
        )
        telebirr, _ = PaymentMethod.objects.get_or_create(
            code="TELEBIRR",
            defaults={"name": "Telebirr SuperApp", "method_type": PaymentMethod.MethodType.TELEBIRR, "requires_reference": True, "is_active": True},
        )
        bank, _ = PaymentMethod.objects.get_or_create(
            code="CBE-TRANSFER",
            defaults={"name": "Commercial Bank of Ethiopia Transfer", "method_type": PaymentMethod.MethodType.BANK_TRANSFER, "requires_reference": True, "is_active": True},
        )
        return cash, telebirr, bank

    def seed_finance_settings(self, addis, bole):
        BranchFinanceSettings.objects.get_or_create(
            branch=addis,
            defaults={
                "manager_approval_limit": Decimal("25000.00"),
                "receptionist_can_create_expenses": True,
                "allow_self_approval": False,
            },
        )
        BranchFinanceSettings.objects.get_or_create(
            branch=bole,
            defaults={
                "manager_approval_limit": Decimal("15000.00"),
                "receptionist_can_create_expenses": True,
                "allow_self_approval": False,
            },
        )

    def seed_packages(self, addis, bole, owner):
        packages_def = [
            {
                "code": "DEMO-PKG-MONTHLY-STD",
                "name": "Standard Monthly Membership",
                "description": "Full gym floor access at home branch during all operating hours.",
                "duration_unit": MembershipPackage.DurationUnit.MONTH,
                "duration_value": 1,
                "price": Decimal("1500.00"),
                "cross_branch_enabled": False,
                "classes_included": False,
                "freeze_allowed": False,
                "branches": [addis, bole],
            },
            {
                "code": "DEMO-PKG-QUARTERLY",
                "name": "Quarterly Strength Membership",
                "description": "3-month floor access with 1 complimentary freeze allowance.",
                "duration_unit": MembershipPackage.DurationUnit.MONTH,
                "duration_value": 3,
                "price": Decimal("4000.00"),
                "cross_branch_enabled": False,
                "classes_included": True,
                "allowed_classes_per_week": 3,
                "freeze_allowed": True,
                "max_freeze_days": 30,
                "max_freeze_requests": 1,
                "branches": [addis, bole],
            },
            {
                "code": "DEMO-PKG-ANNUAL-VIP",
                "name": "Annual VIP Executive Membership",
                "description": "Unlimited multi-branch access, group classes, and VIP locker allowance.",
                "duration_unit": MembershipPackage.DurationUnit.YEAR,
                "duration_value": 1,
                "price": Decimal("15000.00"),
                "cross_branch_enabled": True,
                "classes_included": True,
                "allowed_classes_per_week": None,
                "freeze_allowed": True,
                "max_freeze_days": 60,
                "max_freeze_requests": 3,
                "branches": [addis, bole],
            },
            {
                "code": "DEMO-PKG-DAY-PASS",
                "name": "Single Day Access Pass",
                "description": "Single-entry visitor access pass to gym floor and locker facilities.",
                "duration_unit": MembershipPackage.DurationUnit.DAY,
                "duration_value": 1,
                "price": Decimal("200.00"),
                "allowed_visits": 1,
                "cross_branch_enabled": False,
                "classes_included": False,
                "freeze_allowed": False,
                "branches": [addis, bole],
            },
            {
                "code": "DEMO-PKG-STUDENT",
                "name": "Student Fitness Saver",
                "description": "Discounted off-peak monthly package for enrolled students.",
                "duration_unit": MembershipPackage.DurationUnit.MONTH,
                "duration_value": 1,
                "price": Decimal("1000.00"),
                "cross_branch_enabled": False,
                "classes_included": False,
                "freeze_allowed": False,
                "branches": [addis],
            },
        ]

        created_packages = {}
        for pdef in packages_def:
            branches = pdef.pop("branches")
            pkg, _ = MembershipPackage.objects.get_or_create(
                code=pdef["code"],
                defaults={**pdef, "currency": "ETB", "is_active": True, "created_by": owner},
            )
            for branch in branches:
                PackageBranch.objects.get_or_create(
                    package=pkg,
                    branch=branch,
                    defaults={"is_enabled": True},
                )
            created_packages[pkg.code] = pkg
        return created_packages

    def seed_members(self, addis, bole, receptionist):
        fictional_names = [
            ("Abebe", "Bikila", "MALE", "1992-04-12"),
            ("Haile", "Gebrselassie", "MALE", "1988-03-24"),
            ("Derartu", "Tulu", "FEMALE", "1990-08-15"),
            ("Kenenisa", "Bekele", "MALE", "1989-11-05"),
            ("Tirunesh", "Dibaba", "FEMALE", "1993-01-20"),
            ("Meseret", "Defar", "FEMALE", "1991-06-18"),
            ("Sileshi", "Sihine", "MALE", "1987-05-14"),
            ("Gelete", "Burka", "FEMALE", "1994-09-22"),
            ("Almaz", "Ayana", "FEMALE", "1995-10-10"),
            ("Gudaf", "Tsegay", "FEMALE", "1997-02-14"),
            ("Lamecha", "Girma", "MALE", "1999-12-01"),
            ("Selemon", "Barega", "MALE", "2000-01-20"),
            ("Worknesh", "Degefa", "FEMALE", "1992-10-28"),
            ("Berhane", "Adere", "FEMALE", "1986-07-21"),
            ("Million", "Wolde", "MALE", "1989-07-07"),
            ("Fatuma", "Roba", "FEMALE", "1985-12-18"),
            ("Addis", "Abebe", "MALE", "1990-02-02"),
            ("Mulugeta", "Wendimu", "MALE", "1991-03-15"),
            ("Tegla", "Loroupe", "FEMALE", "1988-09-09"),
            ("Tamirat", "Tola", "MALE", "1991-08-11"),
            ("Amane", "Beriso", "FEMALE", "1993-03-13"),
            ("Sisay", "Lemma", "MALE", "1990-12-12"),
            ("Gotytom", "Gebreslase", "FEMALE", "1995-01-15"),
            ("Yomif", "Kejelcha", "MALE", "1997-08-01"),
            ("Hagos", "Gebrhiwet", "MALE", "1994-05-11"),
            ("Letesenbet", "Gidey", "FEMALE", "1998-03-20"),
            ("Ejgayehu", "Taye", "FEMALE", "2000-02-10"),
            ("Senbere", "Tefferi", "FEMALE", "1995-05-03"),
            ("Muktar", "Edris", "MALE", "1994-01-14"),
            ("Getaneh", "Molla", "MALE", "1994-01-10"),
            ("Dawit", "Seyaum", "FEMALE", "1996-07-27"),
            ("Axumawit", "Emberaye", "FEMALE", "1994-10-18"),
            ("Fantu", "Worku", "FEMALE", "1999-03-29"),
            ("Lemlem", "Hailu", "FEMALE", "2001-05-25"),
            ("Diribe", "Welteji", "FEMALE", "2002-05-13"),
            ("Freweyni", "Hailu", "FEMALE", "2001-02-12"),
            ("Samuel", "Tefera", "MALE", "1999-10-23"),
            ("Taresa", "Tolosa", "MALE", "1998-06-15"),
            ("Teddese", "Lemi", "MALE", "1999-01-20"),
            ("Chala", "Beyo", "MALE", "1996-01-18"),
            ("Bikila", "Tulu", "MALE", "1993-04-05"),
            ("Genzebe", "Dibaba", "FEMALE", "1991-02-08"),
            ("Mare", "Dibaba", "FEMALE", "1989-10-20"),
            ("Tigist", "Assefa", "FEMALE", "1996-12-03"),
            ("Yalemzerf", "Yehualaw", "FEMALE", "1999-08-03"),
            ("Tsehay", "Gemechu", "FEMALE", "1998-12-12"),
            ("Ababel", "Yeshaneh", "FEMALE", "1991-07-22"),
            ("Degitu", "Azimeraw", "FEMALE", "1999-01-24"),
            ("Roza", "Dereje", "FEMALE", "1997-05-09"),
            ("Ashete", "Bekere", "FEMALE", "1988-04-17"),
            ("Shura", "Kitata", "MALE", "1996-06-09"),
            ("Lelisa", "Desisa", "MALE", "1990-01-14"),
            ("Mosinet", "Geremew", "MALE", "1992-02-12"),
            ("Mule", "Wasihun", "MALE", "1993-10-20"),
            ("Guye", "Adola", "MALE", "1990-10-20"),
            ("Birhanu", "Legese", "MALE", "1994-09-11"),
            ("Leul", "Gebresilase", "MALE", "1992-09-20"),
            ("Chalu", "Deso", "MALE", "1997-12-04"),
            ("Kinde", "Atanaw", "MALE", "1993-04-15"),
            ("Milkesa", "Mengesha", "MALE", "2000-04-16"),
        ]

        members = []
        for i, (first, last, gender, dob_str) in enumerate(fictional_names, 1):
            home_b = addis if i <= 35 else bole
            phone = f"+25191100{i:04d}"
            email = f"demo_member_{i:02d}@example.test"
            dob = datetime.strptime(dob_str, "%Y-%m-%d").date()

            member, created = Member.objects.get_or_create(
                phone=phone,
                defaults={
                    "member_number": f"MEM-DEMO-{i:04d}",
                    "first_name": first,
                    "last_name": last,
                    "gender": gender,
                    "date_of_birth": dob,
                    "email": email,
                    "home_branch": home_b,
                    "address": f"Kebele {(i % 15) + 1}, House {100 + i}",
                    "city": "Addis Ababa",
                    "status": Member.MemberStatus.ACTIVE,
                    "registration_source": Member.RegistrationSource.WALK_IN,
                    "created_by": receptionist,
                },
            )

            # Emergency Contact
            MemberEmergencyContact.objects.get_or_create(
                member=member,
                defaults={
                    "full_name": f"{last} Family Contact",
                    "relationship": "Spouse" if i % 2 == 0 else "Parent",
                    "phone": f"+25192200{i:04d}",
                    "is_primary": True,
                },
            )
            members.append(member)

        return members

    def seed_memberships_and_billing(self, members, packages, addis, bole, staff_map, cash, telebirr):
        today = timezone.localdate()
        receptionist = staff_map["receptionist_addis"]
        accountant = staff_map["accountant"]

        created_memberships = []
        active_members = []

        for i, member in enumerate(members, 1):
            # 1 to 35: ACTIVE (Paid in Full)
            if i <= 35:
                pkg_key = "DEMO-PKG-ANNUAL-VIP" if i <= 10 else ("DEMO-PKG-QUARTERLY" if i <= 25 else "DEMO-PKG-MONTHLY-STD")
                pkg = packages[pkg_key]
                start_d = today - timedelta(days=10 + (i % 20))
                end_d = start_d + timedelta(days=pkg.duration_value * (365 if pkg.duration_unit == "YEAR" else 30))

                membership, _ = Membership.objects.get_or_create(
                    membership_number=f"MSHIP-DEMO-{i:04d}",
                    defaults={
                        "member": member,
                        "package": pkg,
                        "home_branch": member.home_branch,
                        "start_date": start_d,
                        "end_date": end_d,
                        "status": Membership.Status.ACTIVE,
                        "price_snapshot": pkg.price,
                        "duration_unit_snapshot": pkg.duration_unit,
                        "duration_value_snapshot": pkg.duration_value,
                        "allowed_visits_snapshot": pkg.allowed_visits,
                        "cross_branch_snapshot": pkg.cross_branch_enabled,
                        "activation_status": Membership.ActivationStatus.ACTIVATED,
                        "payment_clearance_status": Membership.PaymentClearance.CLEARED,
                        "activated_at": timezone.now() - timedelta(days=10),
                        "created_by": receptionist,
                    },
                )
                created_memberships.append(membership)
                active_members.append((member, membership))

                # Invoice (Paid in full)
                inv, inv_created = Invoice.objects.get_or_create(
                    invoice_number=f"INV-DEMO-{i:04d}",
                    defaults={
                        "member": member,
                        "branch": member.home_branch,
                        "issue_date": start_d,
                        "due_date": start_d + timedelta(days=3),
                        "status": Invoice.Status.PAID,
                        "subtotal": pkg.price,
                        "total_amount": pkg.price,
                        "paid_amount": pkg.price,
                        "balance_due": Decimal("0.00"),
                        "issued_by": receptionist,
                        "notes": f"Full payment for {pkg.name}",
                    },
                )
                if inv_created:
                    InvoiceItem.objects.create(
                        invoice=inv,
                        description=f"Membership: {pkg.name}",
                        item_type="PACKAGE",
                        quantity=1,
                        unit_price=pkg.price,
                        line_total=pkg.price,
                    )
                    pay = Payment.objects.create(
                        payment_number=f"PAY-DEMO-{i:04d}",
                        invoice=inv,
                        branch=inv.branch,
                        amount=pkg.price,
                        payment_method=cash if i % 2 == 0 else telebirr,
                        status=Payment.Status.VERIFIED,
                        paid_at=timezone.now() - timedelta(days=9),
                        received_by=accountant,
                        transaction_reference=f"TXN-REF-{i:05d}",
                        idempotency_key=f"IDEMP-PAY-DEMO-{i:04d}",
                    )
                    Receipt.objects.create(
                        receipt_number=f"REC-DEMO-{i:04d}",
                        payment=pay,
                        issued_by=accountant,
                        issued_at=timezone.now() - timedelta(days=9),
                    )

            # 36 to 45: EXPIRED
            elif i <= 45:
                pkg = packages["DEMO-PKG-MONTHLY-STD"]
                start_d = today - timedelta(days=90)
                end_d = today - timedelta(days=60)
                membership, _ = Membership.objects.get_or_create(
                    membership_number=f"MSHIP-DEMO-{i:04d}",
                    defaults={
                        "member": member,
                        "package": pkg,
                        "home_branch": member.home_branch,
                        "start_date": start_d,
                        "end_date": end_d,
                        "status": Membership.Status.EXPIRED,
                        "price_snapshot": pkg.price,
                        "duration_unit_snapshot": pkg.duration_unit,
                        "duration_value_snapshot": pkg.duration_value,
                        "cross_branch_snapshot": False,
                        "activation_status": Membership.ActivationStatus.ACTIVATED,
                        "payment_clearance_status": Membership.PaymentClearance.CLEARED,
                        "created_by": receptionist,
                    },
                )
                created_memberships.append(membership)

            # 46 to 53: FROZEN
            elif i <= 53:
                pkg = packages["DEMO-PKG-QUARTERLY"]
                start_d = today - timedelta(days=40)
                end_d = today + timedelta(days=70)
                membership, m_created = Membership.objects.get_or_create(
                    membership_number=f"MSHIP-DEMO-{i:04d}",
                    defaults={
                        "member": member,
                        "package": pkg,
                        "home_branch": member.home_branch,
                        "start_date": start_d,
                        "end_date": end_d,
                        "status": Membership.Status.FROZEN,
                        "price_snapshot": pkg.price,
                        "duration_unit_snapshot": pkg.duration_unit,
                        "duration_value_snapshot": pkg.duration_value,
                        "cross_branch_snapshot": False,
                        "activation_status": Membership.ActivationStatus.ACTIVATED,
                        "payment_clearance_status": Membership.PaymentClearance.CLEARED,
                        "created_by": receptionist,
                    },
                )
                created_memberships.append(membership)
                if m_created:
                    MembershipFreeze.objects.create(
                        membership=membership,
                        requested_start_date=today - timedelta(days=5),
                        requested_end_date=today + timedelta(days=15),
                        approved_start_date=today - timedelta(days=5),
                        approved_end_date=today + timedelta(days=15),
                        freeze_days=20,
                        reason="Travel / Work Assignment",
                        status="APPROVED",
                        requested_by=staff_map["receptionist_addis"],
                        approved_by=staff_map["manager_addis"],
                        approved_at=timezone.now() - timedelta(days=5),
                    )

            # 54 to 60: PENDING BILLING / PARTIALLY PAID
            else:
                pkg = packages["DEMO-PKG-QUARTERLY"] if i % 2 == 0 else packages["DEMO-PKG-MONTHLY-STD"]
                start_d = today
                end_d = today + timedelta(days=30)
                membership, _ = Membership.objects.get_or_create(
                    membership_number=f"MSHIP-DEMO-{i:04d}",
                    defaults={
                        "member": member,
                        "package": pkg,
                        "home_branch": member.home_branch,
                        "start_date": start_d,
                        "end_date": end_d,
                        "status": Membership.Status.PENDING,
                        "price_snapshot": pkg.price,
                        "duration_unit_snapshot": pkg.duration_unit,
                        "duration_value_snapshot": pkg.duration_value,
                        "cross_branch_snapshot": False,
                        "activation_status": Membership.ActivationStatus.NOT_ACTIVATED,
                        "payment_clearance_status": Membership.PaymentClearance.PENDING,
                        "created_by": receptionist,
                    },
                )
                created_memberships.append(membership)

                # Partially paid invoice
                half_price = pkg.price / Decimal("2.00")
                is_partial = i <= 57
                inv, inv_created = Invoice.objects.get_or_create(
                    invoice_number=f"INV-DEMO-{i:04d}",
                    defaults={
                        "member": member,
                        "branch": member.home_branch,
                        "issue_date": today,
                        "due_date": today + timedelta(days=5),
                        "status": Invoice.Status.PARTIALLY_PAID if is_partial else Invoice.Status.ISSUED,
                        "subtotal": pkg.price,
                        "total_amount": pkg.price,
                        "paid_amount": half_price if is_partial else Decimal("0.00"),
                        "balance_due": half_price if is_partial else pkg.price,
                        "issued_by": receptionist,
                        "notes": "Pending full settlement",
                    },
                )
                if inv_created:
                    InvoiceItem.objects.create(
                        invoice=inv,
                        description=f"Membership: {pkg.name}",
                        item_type="PACKAGE",
                        quantity=1,
                        unit_price=pkg.price,
                        line_total=pkg.price,
                    )
                    if is_partial:
                        pay = Payment.objects.create(
                            payment_number=f"PAY-DEMO-{i:04d}",
                            invoice=inv,
                            branch=inv.branch,
                            amount=half_price,
                            payment_method=cash,
                            status=Payment.Status.VERIFIED,
                            paid_at=timezone.now(),
                            received_by=accountant,
                            transaction_reference=f"PARTIAL-{i:04d}",
                            idempotency_key=f"IDEMP-PAY-PARTIAL-{i:04d}",
                        )
                        Receipt.objects.create(
                            receipt_number=f"REC-DEMO-{i:04d}",
                            payment=pay,
                            issued_by=accountant,
                            issued_at=timezone.now(),
                        )

        return {
            "memberships": created_memberships,
            "active_members": active_members,
        }

    def seed_attendance(self, active_members, addis, bole, staff_map):
        devices = [
            AccessDevice.objects.get_or_create(
                device_code="DEV-ADDIS-GATE-01",
                defaults={"branch": addis, "device_name": "Turnstile Gate #1", "device_type": AccessDevice.DeviceType.TURNSTILE, "status": AccessDevice.Status.ACTIVE},
            )[0],
            AccessDevice.objects.get_or_create(
                device_code="DEV-BOLE-DESK-01",
                defaults={"branch": bole, "device_name": "Reception Desk Scanner", "device_type": AccessDevice.DeviceType.RECEPTION_DESK, "status": AccessDevice.Status.ACTIVE},
            )[0],
        ]

        total_scans = 0
        base_date = date(2026, 10, 26)

        for idx, (member, membership) in enumerate(active_members):
            token_secret = f"QR-TOKEN-{member.member_number}-{idx}"
            token_hash = hashlib.sha256(token_secret.encode()).hexdigest()

            MemberCredential.objects.get_or_create(
                member=member,
                credential_type=MemberCredential.CredentialType.QR,
                defaults={
                    "token_hash": token_hash,
                    "status": MemberCredential.Status.ACTIVE,
                    "issued_by": staff_map["receptionist_addis"],
                },
            )

            # Record historical check-ins across last 3 days
            for d in range(1, 4):
                scan_dt = timezone.make_aware(datetime.combine(base_date - timedelta(days=d), time((idx % 6) + 7, (idx * 7) % 60, 0)))
                checkout_dt = scan_dt + timedelta(minutes=75)
                scan_branch = bole if (membership.cross_branch_snapshot and d % 2 == 0) else member.home_branch

                att, created = Attendance.objects.get_or_create(
                    member=member,
                    membership=membership,
                    branch=scan_branch,
                    check_in_datetime=scan_dt,
                    defaults={
                        "check_out_datetime": checkout_dt,
                        "check_in_method": Attendance.Method.QR if idx % 2 == 0 else Attendance.Method.MANUAL,
                        "check_out_method": Attendance.CheckoutMethod.QR,
                        "status": Attendance.Status.COMPLETED,
                    },
                )
                if created:
                    total_scans += 1

        return total_scans

    def seed_classes(self, addis, bole, staff_map, active_members):
        classes_def = [
            {"name": "HIIT Athletic Bootcamp", "slug": "hiit-bootcamp", "category": ClassType.Category.CARDIO, "default_capacity": 20},
            {"name": "Power Vinyasa Yoga", "slug": "power-yoga", "category": ClassType.Category.MIND_BODY, "default_capacity": 15},
            {"name": "Spin Master Pro Cycling", "slug": "spin-cycling", "category": ClassType.Category.CYCLING, "default_capacity": 18},
            {"name": "Boxing & Combat Conditioning", "slug": "boxing-combat", "category": ClassType.Category.COMBAT, "default_capacity": 16},
        ]
        created_types = []
        for cdef in classes_def:
            slug = cdef.pop("slug")
            ctype, _ = ClassType.objects.get_or_create(
                slug=slug,
                defaults={**cdef, "default_duration_minutes": 60, "created_by": staff_map["owner"]},
            )
            created_types.append(ctype)

        # Create scheduled sessions for the anchor week
        anchor_date = date(2026, 10, 26)
        trainers_addis = [staff_map["trainer_1_profile"], staff_map["trainer_3_profile"]]
        trainers_bole = [staff_map["trainer_2_profile"]]
        created_sessions = []

        for offset in range(-2, 5):
            s_date = anchor_date + timedelta(days=offset)
            target_branch = addis if offset % 2 == 0 else bole
            trainer = trainers_addis[offset % len(trainers_addis)] if target_branch == addis else trainers_bole[0]

            existing_session = ClassSession.objects.filter(
                branch=target_branch,
                session_date=s_date,
                start_time=time(18, 0),
            ).first()
            if existing_session:
                session = existing_session
            else:
                session, _ = ClassSession.objects.get_or_create(
                    class_type=created_types[offset % len(created_types)],
                    branch=target_branch,
                    session_date=s_date,
                    start_time=time(18, 0),
                    defaults={
                        "end_time": time(19, 0),
                        "trainer": trainer,
                        "capacity": 20,
                        "status": "SCHEDULED" if offset >= 0 else "COMPLETED",
                    },
                )
            created_sessions.append(session)

            # Bookings for the session: only book members belonging to this branch or VIP
            eligible_members = [
                (m, ms) for (m, ms) in active_members
                if ms.cross_branch_snapshot or m.home_branch == target_branch
            ]
            for b_idx in range(min(5, len(eligible_members))):
                b_member, b_membership = eligible_members[b_idx]
                if ClassBooking.objects.filter(
                    member=b_member,
                    session__session_date=session.session_date,
                    session__start_time=session.start_time,
                ).exclude(session=session).exists():
                    continue

                ClassBooking.objects.get_or_create(
                    session=session,
                    member=b_member,
                    defaults={
                        "membership": b_membership,
                        "status": "BOOKED" if offset >= 0 else "ATTENDED",
                        "booked_by": staff_map["receptionist_addis"],
                    },
                )

        return {"class_types": created_types, "sessions": created_sessions}

    def seed_facilities(self, addis, bole, active_members, staff_map):
        # Locker Zones
        zone_addis, _ = LockerZone.objects.get_or_create(
            branch=addis,
            code="DEMO-ZONE-A-MALE",
            defaults={"name": "Executive Men's Locker Zone", "gender_restriction": LockerZone.GenderRestriction.MALE},
        )
        zone_bole, _ = LockerZone.objects.get_or_create(
            branch=bole,
            code="DEMO-ZONE-B-UNISEX",
            defaults={"name": "General Changing Zone", "gender_restriction": LockerZone.GenderRestriction.ALL},
        )

        # Lockers
        for i in range(1, 11):
            locker, _ = Locker.objects.get_or_create(
                branch=addis,
                zone=zone_addis,
                locker_number=f"L-ADDIS-{i:02d}",
                defaults={"locker_type": Locker.LockerType.STANDARD, "status": Locker.LockerStatus.AVAILABLE},
            )
            # Assign first 3 lockers to members
            if i <= 3 and i < len(active_members):
                assign_member, assign_mship = active_members[i - 1]
                locker.status = Locker.LockerStatus.OCCUPIED
                locker.save()
                LockerAssignment.objects.get_or_create(
                    assignment_number=f"LOCK-ASSIGN-{i:04d}",
                    defaults={
                        "branch": addis,
                        "locker": locker,
                        "member": assign_member,
                        "membership": assign_mship,
                        "assignment_type": "MEMBERSHIP_LINKED",
                        "status": "ACTIVE",
                        "start_date": timezone.localdate() - timedelta(days=10),
                        "end_date": timezone.localdate() + timedelta(days=50),
                        "deposit_charged_etb": Decimal("200.00"),
                        "deposit_status": "HELD",
                        "assigned_by": staff_map["receptionist_addis"],
                    },
                )

        # Facility Area
        area_main, _ = FacilityArea.objects.get_or_create(
            branch=addis,
            code="DEMO-AREA-CARDIO",
            defaults={"name": "Main Cardio Hall", "area_type": FacilityArea.AreaType.TRAINING_HALL},
        )
        area_bole, _ = FacilityArea.objects.get_or_create(
            branch=bole,
            code="DEMO-AREA-STRENGTH",
            defaults={"name": "Bole Free Weights Floor", "area_type": FacilityArea.AreaType.TRAINING_HALL},
        )

        # Equipment Categories
        eq_cat_cardio, _ = EquipmentCategory.objects.get_or_create(
            code="DEMO-EQ-CARDIO",
            defaults={"name": "Cardio Machines & Treadmills", "default_maintenance_interval_days": 30},
        )
        eq_cat_strength, _ = EquipmentCategory.objects.get_or_create(
            code="DEMO-EQ-STRENGTH",
            defaults={"name": "Selectorized & Plate Loaded Strength", "default_maintenance_interval_days": 60},
        )

        # Equipment
        eq1, _ = Equipment.objects.get_or_create(
            asset_code="DEMO-EQ-TRD-01",
            defaults={
                "branch": addis,
                "facility_area": area_main,
                "category": eq_cat_cardio,
                "name": "LifeFitness Commercial Treadmill T5",
                "serial_number": "LF-TRD-998822",
                "purchase_date": date(2024, 1, 10),
                "purchase_cost_etb": Decimal("180000.00"),
                "status": "OPERATIONAL",
            },
        )
        eq2, _ = Equipment.objects.get_or_create(
            asset_code="DEMO-EQ-RACK-01",
            defaults={
                "branch": bole,
                "facility_area": area_bole,
                "category": eq_cat_strength,
                "name": "Hammer Strength Power Rack HD",
                "serial_number": "HS-PR-445511",
                "purchase_date": date(2024, 3, 15),
                "purchase_cost_etb": Decimal("95000.00"),
                "status": "OPERATIONAL",
            },
        )

        # Maintenance Work Order
        MaintenanceWorkOrder.objects.get_or_create(
            work_order_number="WO-DEMO-0001",
            defaults={
                "branch": addis,
                "equipment": eq1,
                "facility_area": area_main,
                "order_type": "PREVENTIVE",
                "status": "RESOLVED",
                "priority": "MEDIUM",
                "title": "Monthly Routine Treadmill Lubrication & Motor Diagnostic",
                "description": "Routine preventive maintenance service and calibration.",
                "reported_by": staff_map["receptionist_addis"],
                "total_cost_etb": Decimal("3500.00"),
            },
        )

        return {"lockers": [zone_addis, zone_bole], "equipment": [eq1, eq2]}

    def seed_inventory_and_pos(self, addis, bole, staff_map, cash_method):
        cat_supplements, _ = ProductCategory.objects.get_or_create(
            code="DEMO-SUPP",
            defaults={"name": "Sports Nutrition & Supplements"},
        )
        cat_apparel, _ = ProductCategory.objects.get_or_create(
            code="DEMO-APPAREL",
            defaults={"name": "Gym Apparel & Accessories"},
        )

        prod_whey, _ = Product.objects.get_or_create(
            sku="DEMO-PRD-WHEY-2KG",
            defaults={
                "product_number": "DEMO-PRD-0001",
                "category": cat_supplements,
                "name": "Optimum Nutrition Gold Standard Whey 2kg",
                "selling_price": Decimal("3800.00"),
                "cost_price": Decimal("2900.00"),
                "unit_of_measurement": "BOX",
                "barcode": "0748927056419",
                "is_active": True,
            },
        )
        prod_water, _ = Product.objects.get_or_create(
            sku="DEMO-PRD-WATER-500",
            defaults={
                "product_number": "DEMO-PRD-0002",
                "category": cat_supplements,
                "name": "Arki Natural Mineral Water 500ml",
                "selling_price": Decimal("35.00"),
                "cost_price": Decimal("20.00"),
                "unit_of_measurement": "BOTTLE",
                "barcode": "8901234567890",
                "is_active": True,
            },
        )
        prod_towel, _ = Product.objects.get_or_create(
            sku="DEMO-PRD-TOWEL-GYM",
            defaults={
                "product_number": "DEMO-PRD-0003",
                "category": cat_apparel,
                "name": "Antigravity Microfiber Gym Towel",
                "selling_price": Decimal("450.00"),
                "cost_price": Decimal("250.00"),
                "unit_of_measurement": "PCS",
                "barcode": "8909876543210",
                "is_active": True,
            },
        )

        # Stock Levels
        StockItem.objects.get_or_create(
            product=prod_whey,
            branch=addis,
            defaults={"quantity_on_hand": 40, "reorder_level": 10},
        )
        StockItem.objects.get_or_create(
            product=prod_water,
            branch=addis,
            defaults={"quantity_on_hand": 150, "reorder_level": 30},
        )
        StockItem.objects.get_or_create(
            product=prod_towel,
            branch=bole,
            defaults={"quantity_on_hand": 25, "reorder_level": 5},
        )

        # Cashier Shift & Sale
        shift, _ = CashierShift.objects.get_or_create(
            shift_number="SHIFT-DEMO-0001",
            defaults={
                "branch": addis,
                "cashier": staff_map["pos_addis"],
                "opened_at": timezone.now() - timedelta(hours=4),
                "opening_float": Decimal("1000.00"),
                "status": "OPEN",
            },
        )

        sale, sale_created = Sale.objects.get_or_create(
            sale_number="DEMO-SALE-000001",
            defaults={
                "branch": addis,
                "shift": shift,
                "cashier": staff_map["pos_addis"],
                "subtotal": Decimal("3835.00"),
                "total_amount": Decimal("3835.00"),
                "paid_amount": Decimal("3835.00"),
                "status": "COMPLETED",
                "payment_status": "PAID",
            },
        )
        if sale_created:
            SaleItem.objects.create(
                sale=sale,
                product=prod_whey,
                quantity=1,
                unit_cost=Decimal("2900.00"),
                unit_price=Decimal("3800.00"),
                total_price=Decimal("3800.00"),
            )
            SaleItem.objects.create(
                sale=sale,
                product=prod_water,
                quantity=1,
                unit_cost=Decimal("20.00"),
                unit_price=Decimal("35.00"),
                total_price=Decimal("35.00"),
            )
            SalePayment.objects.create(
                sale=sale,
                payment_method=cash_method,
                amount=Decimal("3835.00"),
                tendered_amount=Decimal("4000.00"),
                change_amount=Decimal("165.00"),
            )

        return {"products": [prod_whey, prod_water, prod_towel], "sale": sale}

    def seed_expenses(self, addis, bole, staff_map):
        cat_utils, _ = ExpenseCategory.objects.get_or_create(
            code="DEMO-EXP-UTILS",
            defaults={"name": "Electricity & Utilities", "is_active": True},
        )
        cat_maint, _ = ExpenseCategory.objects.get_or_create(
            code="DEMO-EXP-MAINT",
            defaults={"name": "Facility Repairs & Maintenance", "is_active": True},
        )

        exp1, _ = Expense.objects.get_or_create(
            reference="DEMO-EXP-001",
            defaults={
                "branch": addis,
                "category": cat_utils,
                "description": "Addis Main Branch Electricity Utility Bill",
                "amount": Decimal("8450.00"),
                "total_amount": Decimal("8450.00"),
                "paid_amount": Decimal("8450.00"),
                "expense_date": date(2026, 10, 5),
                "approval_status": "APPROVED",
                "payment_status": "PAID",
                "created_by": staff_map["receptionist_addis"],
                "approved_by": staff_map["manager_addis"],
                "approved_at": timezone.now() - timedelta(days=20),
            },
        )
        exp2, _ = Expense.objects.get_or_create(
            reference="DEMO-EXP-002",
            defaults={
                "branch": bole,
                "category": cat_maint,
                "description": "Bole HVAC AC Filter Servicing",
                "amount": Decimal("4200.00"),
                "total_amount": Decimal("4200.00"),
                "paid_amount": Decimal("4200.00"),
                "expense_date": date(2026, 10, 12),
                "approval_status": "APPROVED",
                "payment_status": "PAID",
                "created_by": staff_map["manager_bole"],
                "approved_by": staff_map["owner"],
                "approved_at": timezone.now() - timedelta(days=14),
            },
        )
        return [exp1, exp2]
