# AGMS Phase 09 Implementation Report
## Group Classes, Scheduling, Member Booking & Class Attendance

**Author:** Principal Django Architect, PostgreSQL Concurrency Engineer, Django Unfold UI/UX Engineer, Security Engineer, QA Lead  
**Date:** October 18, 2026  
**System:** AGMS Gym Management ERP  
**Branch / Tag:** Phase 09 Release  

---

### 1. Executive Summary

Phase 09 introduces enterprise-grade Group Classes, Class Scheduling, Member Booking, and Class Attendance to the AGMS Gym Management ERP. Building upon the foundational modules of Phases 01–08 (RBAC, Multi-Branch, Memberships, Attendance, Billing, Analytics, and Trainer Management), Phase 09 delivers a configurable class catalog, recurrence engine, PostgreSQL row-locked transactional capacity management, automated waitlist promotions, and class-specific attendance tracking without disrupting gym entrance attendance or degrading performance.

Key capabilities delivered:
- **Configurable Class Catalog**: Non-hardcoded class types (Yoga, Zumba, Aerobics, HIIT, Pilates, Spinning, etc.) with custom categories, difficulty levels, duration, default capacities, color codes, and branch scoping.
- **Scheduling & Recurrence Engine**: Weekly schedule templates with automatic occurrence generation, exception/holiday handling, room conflict detection, and trainer personal-training conflict validation.
- **Concurrency-Safe Member Booking**: Transactional booking pipeline enforcing active, paid, unexpired, unfrozen memberships, package-level class entitlements (`classes_included`), branch authorization, and PostgreSQL row-level locks on `ClassSession`.
- **Intelligent Waitlist Management**: Automated waitlist ordering, transactional capacity gating, and FIFO promotion on cancellations with revalidation of membership eligibility.
- **Class-Specific Attendance**: Multi-state tracking (`BOOKED`, `ATTENDED`, `NO_SHOW`, `CANCELLED`, `WAITLISTED`) distinct from gym entrance gate scans to prevent artificial double-counting of gym visits.
- **Django Unfold UI**: Seamless navigation group `"Classes & Bookings"` featuring 8 dedicated interfaces, including responsive calendar views, daily/weekly/monthly filters, and real-time class analytics with CSV export.

---

### 2. Pre-Implementation Audit

Prior to implementation, an exhaustive audit of the codebase was conducted:
1. **Existing Models & Enums**:
   - `TrainingSession` in `apps.staff.models` was examined; it supported `SessionType.GROUP_CLASS` as a stub from Phase 08.
   - `apps.classes` was architected as a dedicated, first-class modular app (`apps.classes`) maintaining strict separation of concerns between individual personal training sessions and group classes.
   - Cross-session conflict detection was engineered so that a trainer or member cannot be double-booked between a 1-on-1 personal training session and a group class session.
2. **Package Class Eligibility**:
   - `MembershipPackage` in `apps.memberships` was audited. It previously lacked fine-grained flags for group classes.
   - Backward-compatible migration `0002_membershippackage_allowed_classes_per_week_and_more.py` was applied, introducing `classes_included = BooleanField(default=True)` and `allowed_classes_per_week = PositiveIntegerField(null=True, blank=True)`.
3. **Audit Trails**:
   - `apps/branches/audit.py` was inspected; `ALLOWED_AUDIT_KEYS` was safely extended to include `class_name` and `booking_id`.
4. **Baseline Test Suite**:
   - Full test suite verified at 234 tests passing with zero regressions prior to Phase 09 development.

---

### 3. Files Created and Modified

#### Files Created:
1. `apps/classes/__init__.py`
2. `apps/classes/apps.py` — Configures `apps.classes` app with `ClassesConfig`.
3. `apps/classes/models.py` — Domain models: `ClassType`, `ClassScheduleTemplate`, `ClassScheduleException`, `ClassSession`, `ClassBooking`.
4. `apps/classes/services.py` — Transactional business workflows: `book_class_session`, `cancel_class_booking`, `promote_from_waitlist`, `mark_class_attendance`, `schedule_class_session`, `generate_sessions_from_schedule`.
5. `apps/classes/selectors.py` — High-performance aggregations: `get_class_scheduling_metrics`, `get_popular_class_types`, `get_trainer_class_workload`.
6. `apps/classes/permissions.py` — Role-based and branch-scoped security helpers.
7. `apps/classes/views.py` — Unfold-integrated custom views: `class_calendar_view`, `class_reports_view`.
8. `apps/classes/admin.py` — Unfold `ModelAdmin` registrations with custom badges, tabbed layouts, filters, and actions.
9. `apps/classes/urls.py` — Route definitions for class calendar and class reports.
10. `apps/classes/migrations/0001_initial.py` — Database schema creation.
11. `apps/memberships/migrations/0002_membershippackage_allowed_classes_per_week_and_more.py` — Non-breaking package entitlement schema extension.
12. `templates/admin/classes/calendar.html` — Responsive schedule & calendar interface styled for Unfold.
13. `templates/admin/classes/reports.html` — Performance dashboard with KPI summary cards and CSV export.
14. `tests/test_classes.py` — 15 comprehensive automated test cases covering catalog, scheduling, conflicts, capacity, waitlists, concurrency, attendance, permissions, and reporting.

#### Files Modified:
1. `config/settings/base.py` — Added `"apps.classes"` to `INSTALLED_APPS`.
2. `config/urls.py` — Added `path("admin/classes/", include("apps.classes.urls", namespace="classes_admin"))`.
3. `config/branding.py` — Registered `"Classes & Bookings"` navigation group (8 navigation items) in Unfold configuration.
4. `apps/memberships/models.py` — Added `classes_included` and `allowed_classes_per_week` fields to `MembershipPackage`.
5. `apps/branches/audit.py` — Added `"class_name"` and `"booking_id"` to `ALLOWED_AUDIT_KEYS`.
6. `tests/test_configuration.py` — Updated `test_all_admins_use_unfold` to include the 4 new classes models.

---

### 4. Database Models and Migrations

#### Models in `apps.classes`:
- **`ClassType`**:
  - `name`, `code`, `description`, `category` (`STRENGTH`, `CARDIO`, `MIND_BODY`, `HIIT`, `DANCE`, `COMBAT`, `FUNCTIONAL`, `OTHER`), `difficulty` (`ALL_LEVELS`, `BEGINNER`, `INTERMEDIATE`, `ADVANCED`), `duration_minutes`, `default_capacity`, `default_waitlist_capacity`, `color_code`, `branches`, `is_active`.
- **`ClassScheduleTemplate`**:
  - Weekly recurring template: `class_type`, `branch`, `room`, `trainer`, `day_of_week` (0-6), `start_time`, `end_time`, `capacity`, `waitlist_capacity`, `start_date`, `end_date`, `booking_window_days_in_advance`, `booking_cutoff_hours_before`, `is_active`.
- **`ClassScheduleException`**:
  - Date-specific overrides: `schedule_template`, `exception_date`, `reason`, `is_cancelled`. Unique on `(schedule_template, exception_date)`.
- **`ClassSession`**:
  - Individual occurrence: `schedule_template` (nullable), `class_type`, `branch`, `room`, `trainer`, `session_date`, `start_time`, `end_time`, `capacity`, `waitlist_capacity`, `booking_opens_at`, `booking_closes_at`, `status` (`SCHEDULED`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`), `cancellation_reason`, `cancelled_by`, `cancelled_at`, `notes`.
  - Properties: `booked_count`, `waitlist_count`, `available_spots`, `is_full`, `is_waitlist_full`, `duration_minutes`.
- **`ClassBooking`**:
  - Member booking and attendance record: `session`, `member`, `membership`, `status` (`BOOKED`, `WAITLISTED`, `ATTENDED`, `NO_SHOW`, `CANCELLED`), `waitlist_position`, `booked_at`, `booked_by`, `promoted_from_waitlist_at`, `attendance_marked_at`, `attendance_marked_by`, `cancellation_reason`, `cancelled_at`, `cancelled_by`, `notes`.

#### Migrations:
- `apps/memberships/migrations/0002_membershippackage_allowed_classes_per_week_and_more.py` (applied cleanly).
- `apps/classes/migrations/0001_initial.py` (applied cleanly).

---

### 5. Class Catalog

- Configurable through the Unfold admin interface without code modifications.
- Administrators can define custom categories, color tokens for calendar distinction, difficulty levels, duration, and default capacities.
- Multi-branch assignment allows classes to be offered gym-wide or restricted to specific branches.

---

### 6. Scheduling and Recurrence

- Recurring weekly templates can be defined with day-of-week recurrence and start/end dates.
- Exception management allows cancelling recurring occurrences on holidays or maintenance days.
- Overlap prevention:
  - Validates trainer availability: prevents scheduling a trainer for two overlapping class sessions.
  - Cross-module conflict detection: prevents scheduling a class session if the trainer has an active 1-on-1 `TrainingSession` (Personal Training) scheduled during that time.
  - Room conflict detection: prevents scheduling two class sessions in the same room at the same branch with overlapping hours.

---

### 7. Trainer Integration

- Integrated with Phase 08 Trainer Management:
  - Trainer authorization validated against branch assignments (`trainer.is_assigned_to_branch(branch)`).
  - Trainer status verified (must be `ACTIVE`).
  - Workload analytics aggregate total group classes delivered, completed sessions, cancelled sessions, participant totals, and average attendance rates.

---

### 8. Member Booking Workflow

Authorized staff (Gym Owner, Branch Manager, Receptionist) execute bookings with full validation:
1. **Member Verification**: Member must be in `ACTIVE` status.
2. **Session Status**: Session must be in `SCHEDULED` status and within the booking window (`booking_opens_at` <= `now` <= `booking_closes_at`).
3. **Membership Eligibility**:
   - Member must have a current membership that covers the session date (`start_date` <= `session_date` <= `end_date`).
   - Payment clearance must be `CLEARED` or `WAIVED_COMPLIMENTARY`.
   - Membership must not be frozen.
   - Package must allow classes (`classes_included=True`).
   - Branch authorization must match (home branch or cross-branch package).
4. **Member Conflict Detection**: Prevents double-booking if the member already has another class or personal training session scheduled at that time.

---

### 9. Capacity Enforcement & Concurrency Control

- **PostgreSQL Row-Level Locking**: `ClassSession.objects.select_for_update().get(pk=session.pk)` inside a database transaction (`transaction.atomic()`).
- All competing booking transactions serialize sequentially.
- If spots remain (`active_booked_count < capacity`), booking is granted with `status = BOOKED`.
- If capacity is exhausted:
  - If waitlist spots remain (`active_waitlist_count < waitlist_capacity`), booking is assigned `status = WAITLISTED` with sequential `waitlist_position`.
  - If both capacity and waitlist are exhausted, request is safely rejected with `ValidationError("Class and waitlist are completely full.")`.

---

### 10. Waitlist Management

- Waitlist queue maintains strict FIFO order.
- When an active confirmed booking is cancelled via `cancel_class_booking()`:
  - If a waitlist exists, the top waitlisted member (`waitlist_position = 1`) is automatically promoted to `BOOKED`.
  - Revalidates membership eligibility at promotion time: if the member's membership has expired, was frozen, or became unpaid, the member is skipped with an audit note, and the next eligible candidate is evaluated.
  - Remaining waitlist members have their positions sequentially renumbered (2 -> 1, 3 -> 2, etc.).
  - All operations are atomic and logged to audit events.

---

### 11. Class Attendance

- Tracks individual status per session: `BOOKED`, `ATTENDED`, `NO_SHOW`, `CANCELLED`, `WAITLISTED`.
- Mark attendance workflow:
  - Authorized staff can mark participants as `ATTENDED` or `NO_SHOW`.
  - Records timestamp and actor (`attendance_marked_by`, `attendance_marked_at`).
  - Preserves historical attendance records.
  - Independent of gym gate attendance: gym entrance QR checks or manual gate entries do not artificially mark class attendance, preventing double-counted metrics.

---

### 12. Role and Branch Security

Server-side enforcement on all models, querysets, actions, and custom views:
- **Gym Owner**: Full visibility and administrative control across all branches.
- **Branch Manager**: Filtered strictly to authorized branches. Cannot view or edit sessions, bookings, or templates in other branches.
- **Receptionist**: Permitted to search members, book eligible classes within authorized branches, and record attendance. Denied financial and catalog reconfiguration.
- **Trainer**: Can view assigned classes and participant rosters; can mark attendance for assigned classes; denied administrative configuration.
- **Cross-Branch Denial**: Direct POST requests or URL tampering across branches fail closed with HTTP 403 or validation errors.

---

### 13. Django Unfold UI

Navigation Group registered in `config/branding.py`:
- **Classes & Bookings**:
  1. `Class Catalog` (`admin:classes_classtype_changelist`)
  2. `Class Schedule` (`admin:classes_classscheduletemplate_changelist`)
  3. `Class Sessions` (`admin:classes_classsession_changelist`)
  4. `Member Bookings` (`admin:classes_classbooking_changelist`)
  5. `Waitlist` (`admin:classes_classbooking_changelist?status__exact=WAITLISTED`)
  6. `Class Attendance` (`admin:classes_classbooking_changelist?status__in=ATTENDED,NO_SHOW`)
  7. `Class Calendar` (`classes_admin:calendar`)
  8. `Class Reports` (`classes_admin:reports`)

Features:
- Consistent Unfold design language with dark/light mode compatibility.
- Interactive badge indicators for status, difficulty, and capacity.
- Filters by branch, class type, trainer, status, date, and difficulty.
- Custom actions for bulk session status updates, session cancellation, and attendance marking.

---

### 14. Analytics and Reports

The `Class Reports` view (`classes_admin:reports`) provides:
- Metric cards: Total Classes, Completed Classes, Cancelled Classes, Total Bookings, Attendance Rate, No-show Rate, Capacity Utilization, Waitlist Conversions.
- Popular Class Types table with bookings and attendance breakdown.
- Trainer Workload table with scheduled, completed, and participant counts.
- CSV Export with formula injection defense (sanitizes `=, +, -, @` prefixes).
- Branch-scoped aggregation respecting user permissions.

---

### 15. Concurrency Verification

PostgreSQL transactional concurrency was verified with multi-threaded tests using `concurrent.futures.ThreadPoolExecutor`:
- **Concurrent Class Bookings**: Two threads simultaneously raced to book the last remaining spot on a session with `capacity=1, waitlist_capacity=0`.
- Result: Exactly 1 thread succeeded (`"SUCCESS"`), while the second thread received `"Class and waitlist are completely full."` without data corruption, phantom reads, or overcapacity.
- Row-level lock (`select_for_update()`) on `ClassSession` ensured atomic serialization.

---

### 16. Automated Test Results

- **New Phase 09 Test Suite (`tests/test_classes.py`)**: 15 passed in 97.03s.
- **Full Test Suite (`pytest`)**: 249 passed, 0 failed.
- Regression rate: **0%**.

---

### 17. Browser Verification

- Tested Django Unfold admin navigation for `"Classes & Bookings"`.
- Verified Class Catalog, Schedule Templates, Sessions changelist, and Bookings changelist.
- Verified custom Calendar and Reports endpoints load with correct HTTP 200 responses and Unfold styling.

---

### 18. Known Limitations

1. **Email / SMS Notification Dispatch**: Automated external notifications (e.g. SMS alert to a member when promoted from the waitlist) currently log audit events. Third-party SMS gateway integration is designated for future communications modules.
2. **Self-Service Member Portal**: Booking workflow is currently optimized for receptionist/manager administrative workflows in Django Unfold as per ERP architecture specifications.

---

### 19. Git Commit Hash

- Commit hash: `0964bc3`
- Commit message: `feat: implement gym classes scheduling and member booking`
- All files staged and committed cleanly.

---

### 20. Final Acceptance Checklist

- [x] Class catalog creation and management (non-hardcoded)
- [x] Weekly recurring schedule templates and occurrence generation
- [x] Schedule exceptions (holidays/maintenance)
- [x] Room and trainer conflict prevention (including Phase 08 PT sessions)
- [x] Member package class eligibility and branch scoping
- [x] PostgreSQL transaction and row-locked capacity enforcement
- [x] FIFO waitlist management with automatic promotion on cancellation
- [x] Eligibility revalidation during waitlist promotion
- [x] Class-specific attendance tracking distinct from entrance scans
- [x] Trainer management integration and workload analytics
- [x] Django Unfold navigation group `"Classes & Bookings"` with 8 links
- [x] Responsive class calendar and class analytics reports with CSV export
- [x] Role-based and branch-scoped security enforcement
- [x] Multi-threaded PostgreSQL concurrency testing
- [x] 100% test pass rate across all 249 tests
- [x] Phase 10 was not started
