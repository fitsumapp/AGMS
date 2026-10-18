# AGMS — PHASE 08 MASTER IMPLEMENTATION REPORT
## Trainer, Staff, Scheduling & Performance Management

**Author:** Principal Django Architect, PostgreSQL Analytics Engineer, Security Engineer & QA Lead  
**Date:** October 16, 2026  
**System:** AGMS Multi-Branch Gym Management System  
**Environment:** Python 3.12, Django 5.2, PostgreSQL, Django Unfold Admin  
**Timezone:** `Africa/Addis_Ababa` (UTC+3)  
**Currency:** ETB (Ethiopian Birr, `Decimal` precision)

---

### 1. Executive Summary

Phase 08 establishes a comprehensive, multi-branch Staff, Trainer, Scheduling, and Performance Management module (`apps.staff`) integrated into the AGMS Gym ERP system. Building directly upon the existing `User` and `UserBranchAccess` models, Phase 08 provides end-to-end operational visibility over gym personnel without creating duplicate authentication systems or sensitive data leaks.

The module supports atomic employee numbering (`EMP-0001`), departmental categorization, employment lifecycle tracking, document storage, trainer profiles with specialization and client capacity tracking, member-to-trainer assignments, conflict-free personal training session scheduling, branch-based shift scheduling with rest day recognition, leave request workflows, and real-data performance metrics.

All features are natively surfaced through the Django Unfold admin interface under a dedicated **Staff & Trainers** navigation group, enforcing strict branch-level isolation, separation of duties, and role-based permissions.

---

### 2. Implementation Status

| Component | Status | Details |
| :--- | :---: | :--- |
| **Staff App (`apps.staff`)** | **COMPLETE** | Models, migration, selectors, admin, views, and URL routing. |
| **Staff Profiles & Sequences** | **COMPLETE** | Sequential employee numbers (`EMP-XXXX`), emergency contacts, lifecycle. |
| **Employment Documents** | **COMPLETE** | `StaffDocument` with typed attachments and restricted access. |
| **Trainer Profiles** | **COMPLETE** | Specializations, certifications, hourly rates, client capacity caps. |
| **Trainer Member Assignments** | **COMPLETE** | Pairings with conflict detection and branch authorization validation. |
| **Training Sessions** | **COMPLETE** | Session scheduling, overlap conflict prevention, completion & cancellation. |
| **Staff Shift Scheduling** | **COMPLETE** | Shift templates, scheduled working shifts, rest days, overlap prevention. |
| **Leave Management** | **COMPLETE** | Leave requests, date validation, manager review and approval workflows. |
| **Performance Analytics** | **COMPLETE** | Real data calculations: completed hours, client loads, shift coverage %. |
| **Django Unfold UI** | **COMPLETE** | All 8 navigation items implemented with AGMS dark-teal styling. |
| **Role Permissions & Security** | **COMPLETE** | Gym Owner, Branch Manager, Trainer, Receptionist, Accountant access. |
| **Automated Test Suite** | **COMPLETE** | 226/226 tests passed (100%), zero regressions. |

---

### 3. Pre-Implementation Audit Findings

Prior to Phase 08 implementation, an audit of existing models and tests was conducted:
1. **Existing User and Staff Representation**:
   - `apps.accounts.models.User` is the authoritative custom user model (`AbstractBaseUser`, `PermissionsMixin`).
   - `apps.accounts.models.StaffAccount` is a proxy model for staff administration.
   - `apps.branches.models.UserBranchAccess` manages multi-branch assignments with `is_primary` and `is_active` flags.
   - Decision: Link `StaffProfile` 1-to-1 with `settings.AUTH_USER_MODEL`, avoiding duplicate staff user tables.
2. **Attendance Model Consistency**:
   - In Phase 06/07, `auto_closed` sessions were separated from physical checkouts. Staff attendance is kept strictly independent of member gym floor check-ins.
3. **Pre-Check Test Verification**:
   - All 214 pre-existing tests passed with 100% success rate before introducing Phase 08 modifications.

---

### 4. Database Models & Schema Changes

Migration `apps/staff/migrations/0001_initial.py` created 8 new database models:

1. **`StaffSequence`**:
   - Atomic counter for sequential employee numbers (`EMP-0001`, `EMP-0002`).
2. **`StaffProfile`**:
   - `user`: OneToOneField(`settings.AUTH_USER_MODEL`, on_delete=CASCADE).
   - `employee_number`: CharField(max_length=32, unique=True, db_index=True).
   - `primary_branch`: ForeignKey(`branches.Branch`, on_delete=SET_NULL, null=True, blank=True).
   - `job_title`: CharField(max_length=120).
   - `department`: CharField with choices (`FITNESS`, `OPERATIONS`, `FRONT_DESK`, `MANAGEMENT`, `MAINTENANCE`, `FINANCE`, `OTHER`).
   - `employment_type`: CharField with choices (`FULL_TIME`, `PART_TIME`, `CONTRACT`, `INTERN`, `CASUAL`).
   - `employment_status`: CharField with choices (`ACTIVE`, `ON_LEAVE`, `PROBATION`, `SUSPENDED`, `TERMINATED`).
   - `hire_date`: DateField.
   - `termination_date`: DateField(null=True, blank=True).
   - `emergency_contact_name`, `emergency_contact_phone`, `emergency_contact_relation`.
   - `photo`: ImageField with secure path generator.
   - `notes`, `qualifications`: TextFields.
3. **`StaffDocument`**:
   - ForeignKey to `StaffProfile`.
   - `title`, `document_type` (`CONTRACT`, `CERTIFICATE`, `ID_PROOF`, `RESUME`, `OTHER`), `file`, `uploaded_at`, `uploaded_by`.
4. **`TrainerProfile`**:
   - `staff`: OneToOneField(`StaffProfile`, on_delete=CASCADE).
   - `specializations`, `certifications`, `experience_years`, `bio`.
   - `status`: choices (`ACTIVE`, `INACTIVE`, `UNAVAILABLE`).
   - `max_active_clients`: PositiveIntegerField(default=15).
   - `hourly_rate`: DecimalField(max_digits=10, decimal_places=2, default=0.00).
   - `availability_schedule`: JSONField(default=dict, blank=True).
5. **`TrainerMemberAssignment`**:
   - `trainer`: ForeignKey(`TrainerProfile`, on_delete=PROTECT).
   - `member`: ForeignKey(`members.Member`, on_delete=PROTECT).
   - `branch`: ForeignKey(`branches.Branch`, on_delete=PROTECT).
   - `start_date`, `end_date`, `status` (`ACTIVE`, `COMPLETED`, `CANCELLED`), `notes`.
6. **`TrainingSession`**:
   - `trainer`, `member`, `branch`, `assignment`.
   - `session_date`, `start_time`, `end_time`.
   - `status` (`SCHEDULED`, `COMPLETED`, `CANCELLED`, `NO_SHOW`).
   - `session_type` (`PERSONAL_TRAINING`, `FITNESS_ASSESSMENT`, `CONSULTATION`, `GROUP_CLASS`).
   - `trainer_notes`, `cancellation_reason`, `cancelled_by`, `cancelled_at`, `completed_at`.
7. **`ShiftTemplate` & `StaffShift`**:
   - `ShiftTemplate`: standard shift definition per branch (e.g. "Morning Desk" 06:00-14:00).
   - `StaffShift`: dated assignment with `is_rest_day`, start/end times, and status (`SCHEDULED`, `COMPLETED`, `ABSENT`, `CANCELLED`).
8. **`LeaveRequest`**:
   - `staff`, `leave_type` (`ANNUAL`, `SICK`, `EMERGENCY`, `UNPAID`, `MATERNITY_PATERNITY`, `OTHER`), `start_date`, `end_date`, `reason`, `status` (`PENDING`, `APPROVED`, `REJECTED`, `CANCELLED`), `reviewed_by`, `reviewed_at`.

---

### 5. Conflict Prevention & Business Rules

1. **Scheduling Conflict Prevention**:
   - Training sessions enforce `end_time > start_time`.
   - The model automatically queries existing `SCHEDULED` sessions on the same date:
     - Rejects any trainer overlap: `start_time < existing.end_time and end_time > existing.start_time`.
     - Rejects any member overlap: prevents members from being double-booked with multiple trainers simultaneously.
2. **Shift Overlap Prevention**:
   - Working shifts on the same date for the same staff member cannot have overlapping working hours.
   - Rest days (`is_rest_day=True`) do not require start/end times and allow clear off-duty tracking.
3. **Branch Authorization**:
   - Trainer assignments and training sessions strictly require that the trainer is assigned to or has active access to the session branch.
4. **Duplicate Active Assignments**:
   - A member cannot have multiple concurrent active assignments with the same trainer.

---

### 6. Performance Analytics & Selectors

Built in `apps/staff/selectors.py` using PostgreSQL aggregations with zero fabricated figures:
- `get_trainer_metrics(trainer, start_date, end_date, branch)`:
  - Active client count
  - Client capacity utilization rate (`active_clients / max_clients * 100`)
  - Completed sessions count
  - Cancelled sessions count
  - No-show sessions count
  - Scheduled upcoming sessions count
  - Completion rate percentage
  - Total training hours delivered (sum of duration minutes / 60)
- `get_staff_scheduling_metrics(branch, start_date, end_date)`:
  - Total working shifts scheduled
  - Completed shifts
  - Absent shifts
  - Cancelled shifts
  - Rest days recorded
  - Shift coverage rate percentage
  - Pending & approved leave request counts
- `get_branch_staffing_summary(branch)`:
  - Headcount active vs on leave
  - Department distribution breakdown
  - Active client-to-trainer ratio

---

### 7. Django Unfold Interface & Navigation

Added the **Staff & Trainers** navigation group in `config/branding.py`:
1. **Staff Directory**: `/admin/staff/staffprofile/`
2. **Trainer Directory**: `/admin/staff/trainerprofile/`
3. **Trainer Assignments**: `/admin/staff/trainermemberassignment/`
4. **Training Sessions**: `/admin/staff/trainingsession/`
5. **Staff Schedules**: `/admin/staff/staffshift/`
6. **Leave Requests**: `/admin/staff/leaverequest/`
7. **Trainer Performance**: `/admin/staff/performance/` (Dedicated analytics dashboard)
8. **Staff Reports**: `/admin/staff/reports/` (Operational shift logs & leave requests with CSV export)

---

### 8. Role Permissions & Branch Scoping

Integrated into `apps/accounts/roles.py`:
- **Gym Owner**: Full CRUD access across all staff, trainers, assignments, shifts, and leaves across all branches.
- **Branch Manager**: Scoped to staff and trainers assigned to their managed branch. Can manage assignments, sessions, shifts, and approve/reject leave requests.
- **Trainer**: Restricted in changelists and views to their own trainer profile, own member assignments, own training sessions, and own schedule.
- **Receptionist**: Visibility into staff directory, trainer directory, schedules, and training session booking to coordinate front-desk member requests.
- **Accountant**: Read-only visibility into staff profiles and leave logs for payroll/headcount context.

---

### 9. Verification & Test Suite Results

```bash
# System Checks
python manage.py check
System check identified no issues (0 silenced).

python manage.py makemigrations --check --dry-run
No changes detected

# Full Pytest Test Suite
pytest -v
======================= 234 passed in 398.59s (0:06:38) =======================
```

- **Total Tests**: **234 passed, 0 failed** (100% pass rate).
- **Regression Check**: All 214 pre-existing tests from Phases 01–07 passed with zero regressions.
- **Phase 08 Integration & Security Tests (`tests/test_staff.py`)**:
  1. `test_staff_creation_and_employee_number_sequence`: Verifies `EMP-0001` auto-generation, sequentiality, and branch association.
  2. `test_staff_termination_date_validation`: Verifies `termination_date >= hire_date` validation.
  3. `test_trainer_profile_creation_and_attributes`: Verifies specializations, rates, and client capacity metrics.
  4. `test_trainer_member_assignment_validation`: Verifies branch validation and duplicate active assignment rejection.
  5. `test_training_session_scheduling_and_conflict_prevention`: Verifies end-time order and detects trainer & member scheduling overlaps.
  6. `test_training_session_completion_and_cancellation`: Verifies session completion and cancellation with reason audit.
  7. `test_staff_shift_creation_and_overlap_validation`: Verifies working shift overlap rejection and rest day support.
  8. `test_leave_request_workflow`: Verifies date boundaries, status lifecycle, and reviewer approval.
  9. `test_trainer_performance_selectors`: Verifies hours calculation and completion rate logic.
  10. `test_trainer_performance_view_and_scoping`: Verifies HTTP 200 response and trainer self-scoping.
  11. `test_staff_reports_view_and_csv_export`: Verifies web preview and CSV export with proper MIME type and data.
  12. `test_empty_staffing_metrics_stability`: Verifies zero-division protection when database has 0 staff.
  13. `test_concurrent_training_session_bookings_serialize`: Multi-threaded PostgreSQL concurrency test verifying row locks prevent overlapping trainer bookings.
  14. `test_concurrent_member_session_bookings_serialize`: Multi-threaded PostgreSQL concurrency test verifying row locks prevent double-booking members across trainers.
  15. `test_trainer_capacity_enforcement_boundary_and_concurrency`: Boundary and multi-threaded concurrency test verifying `max_active_clients` cap is strictly enforced.
  16. `test_private_staff_documents_unauthenticated_denied`: Verifies unauthenticated users are redirected to login (HTTP 302).
  17. `test_private_staff_documents_unauthorized_roles_denied`: Verifies unauthorized roles (Receptionist, peer Trainers) receive HTTP 403 Forbidden and audit logs record event.
  18. `test_private_staff_documents_cross_branch_denied`: Verifies cross-branch managers cannot download documents of staff outside their branch.
  19. `test_private_staff_documents_authorized_access`: Verifies self, primary Branch Manager, and Gym Owner can securely download with `no-store` and `nosniff` headers.
  20. `test_staff_termination_workflow_and_access_revocation`: Verifies employee termination immediately revokes login, trainer status, and branch access while preserving historical completed records.

---

### 10. Final Security & Integrity Verification

#### 10.1 Concurrent Training Session Bookings
- **Vulnerability Addressed**: Race conditions during high-concurrency booking where two members or managers attempt to schedule the same trainer for overlapping time slots, or two trainers schedule the same member simultaneously.
- **Concurrency Control Implementation**:
  - In `apps/staff/services.py` (`schedule_training_session`) and `apps/staff/models.py` (`TrainingSession.save`):
    - Wrapped in `transaction.atomic()`.
    - Row-level locking on `TrainerProfile.objects.select_for_update().get(pk=trainer_id)` followed by `Member.objects.select_for_update().get(pk=member_id)`.
    - Strict deterministic lock hierarchy (`TrainerProfile` then `Member`) prevents AB-BA deadlocks.
    - Under PostgreSQL `READ COMMITTED`, the second concurrent transaction waits on the row lock, unblocks after the first commits, re-evaluates the conflict check query, detects the newly committed session, and raises a clean `ValidationError`.
- **Verification Evidence**: `test_concurrent_training_session_bookings_serialize` and `test_concurrent_member_session_bookings_serialize` executed multi-threaded requests in parallel against PostgreSQL test database: exactly 1 booking succeeded and 1 was cleanly rejected.

#### 10.2 Private Staff Documents
- **Vulnerability Addressed**: Direct Object Reference (IDOR) or unauthenticated media exposure of sensitive contracts, ID copies, and employment records.
- **Protection Architecture**:
  - Media URLs are NOT served publicly in `config/urls.py` (no `static(settings.MEDIA_URL)` exposure).
  - File upload paths use UUID-based random filenames (`staff/{staff_id}/documents/{uuid4()}{ext}`).
  - Dedicated authenticated download endpoint `/admin/staff/documents/<uuid:document_id>/download/` guarded by `apps/staff/views.py:download_staff_document` and `apps/staff/services.py:authorize_staff_document_download`.
  - Object-level authorization strictly limits downloads to:
    1. Gym Owner & Superusers.
    2. Branch Managers authorized for the staff member's assigned branches.
    3. The staff member themselves downloading their own file.
  - Unauthorized roles (Receptionists, Accountants, peer Trainers, cross-branch managers) receive HTTP 403 Forbidden, and attempts are logged to `AuditEvent` (`action="document_download_unauthorized"`).
  - Security headers enforced on every file download response:
    - `Cache-Control: private, no-store, must-revalidate`
    - `X-Content-Type-Options: nosniff`
- **Verification Evidence**: `test_private_staff_documents_unauthenticated_denied`, `test_private_staff_documents_unauthorized_roles_denied`, `test_private_staff_documents_cross_branch_denied`, and `test_private_staff_documents_authorized_access` pass 100%.

#### 10.3 Trainer Capacity Enforcement
- **Vulnerability Addressed**: Trainer overloading and concurrent assignment race conditions exceeding `max_active_clients`.
- **Enforcement Implementation**:
  - In `TrainerMemberAssignment.clean()`: Validates `active_clients_count < trainer.max_active_clients`.
  - In `TrainerMemberAssignment.save()` and `apps/staff/services.py:assign_trainer_to_member`:
    - Row-level lock `TrainerProfile.objects.select_for_update().get(pk=trainer_id)` inside `transaction.atomic()`.
    - Serializes concurrent assignment requests so that competing requests for the last capacity slot cannot both commit.
- **Verification Evidence**: `test_trainer_capacity_enforcement_boundary_and_concurrency` verified that with 1 remaining capacity slot, two concurrent threads competing for the slot resulted in exactly 1 success, 1 `ValidationError`, and database active count remained strictly at 2 (never 3).

#### 10.4 Staff Termination Security
- **Vulnerability Addressed**: Former employees retaining system credentials, branch permissions, or active trainer scheduling slots after termination.
- **Termination Workflow (`apps/staff/services.py:terminate_staff` & `StaffProfile.save` cascade)**:
  - Updates `employment_status` to `TERMINATED` and sets `termination_date`.
  - Immediately revokes login credentials: `user.is_active = False` and `user.is_staff = False`.
  - Revokes all active branch assignments: `user.branch_access.filter(is_active=True).update(is_active=False, is_primary=False)`.
  - Deactivates trainer status: `trainer.status = INACTIVE`.
  - Cancels active client assignments (`TrainerMemberAssignment.status = CANCELLED`).
  - Cancels upcoming scheduled sessions on or after termination date (`TrainingSession.status = CANCELLED`).
  - Cancels upcoming shifts on or after termination date (`StaffShift.status = CANCELLED`).
  - Historical completed sessions (`COMPLETED`) and past shifts are strictly preserved.
  - Subsequent session booking attempts for the terminated trainer are rejected with `ValidationError` ("is not active").
  - Audit event logged: `staff_terminated` with `employee_number` and `reason`.
- **Verification Evidence**: `test_staff_termination_workflow_and_access_revocation` verified credential revocation, login failure, trainer inactivation, schedule cancellation, and historical record preservation.

---

### 11. Browser Testing Documentation

Browser verification was performed in Django Unfold Admin:
1. **Staff Directory (`/admin/staff/staffprofile/`)**:
   - Filtered by Department, Employment Status, and Primary Branch.
   - Verified Staff Document inline renders the secure download link (`Secure Download`).
   - Verified "Terminate employment and revoke staff access" admin action executes cleanly and displays confirmation message.
2. **Trainer Directory (`/admin/staff/trainerprofile/`)**:
   - Verified active client count reflects real assignments.
   - Verified client capacity indicator.
3. **Training Sessions (`/admin/staff/trainingsession/`)**:
   - Tested scheduling form with valid times.
   - Tested overlap validation: attempting to schedule an overlapping session in the UI displays a clear form validation error (`Trainer already has a conflicting scheduled session at this time.`).
4. **Trainer Performance Dashboard (`/admin/staff/performance/`)**:
   - Verified summary cards (Total Trainers, Active Client Pairings, Sessions Completed, Training Hours Delivered).
   - Tested branch selector and date range filtering.
5. **Operational Reports (`/admin/staff/reports/`)**:
   - Verified web preview and tested CSV export (`/admin/staff/reports/?export=csv&report_type=shifts`).

---

### 12. Known Limitations

1. **External Calendar Synchronization**: Personal training sessions are stored in the PostgreSQL database; direct bi-directional synchronization with Google Calendar or Apple iCal is not included in Phase 08 and can be added via future webhook integrations.
2. **Automated Shift Rotations**: Recurring weekly shift scheduling is managed via Shift Templates and individual shifts; automated AI-based shift roster generation is not implemented.

---

### 13. Files Modified & Added in Security Verification

| File | Change Summary |
|---|---|
| `apps/branches/audit.py` | Registered `"employee_number"` and `"session_id"` in `ALLOWED_AUDIT_KEYS`. |
| `apps/staff/models.py` | Added row-locking `save()` on `TrainingSession` and `TrainerMemberAssignment`, active trainer & member checks in `clean()`, cascading termination security in `StaffProfile.save()`. |
| `apps/staff/services.py` | Implemented `schedule_training_session`, `assign_trainer_to_member`, `terminate_staff`, and `authorize_staff_document_download`. |
| `apps/staff/views.py` | Implemented `download_staff_document` with object-level authorization, anti-IDOR checks, security headers, and audit logging. |
| `apps/staff/admin.py` | Added `download_link` and role visibility permissions in `StaffDocumentInline`, added `terminate_selected_staff` admin action. |
| `config/urls.py` | Registered `admin/staff/documents/<uuid:document_id>/download/` route. |
| `tests/test_staff.py` | Added 8 comprehensive security, concurrency, boundary, document authorization, and termination tests (20 tests total). |
| `PHASE_08_REPORT.md` | Documented Final Security & Integrity Verification, test results, browser testing, and audit evidence. |

---

### 14. Git Commit Hashes

- **Initial Implementation:** `86f15271bf16436fce2fddae3471afef5b91e35b` (`feat: implement gym trainer and staff management`)
- **Security & Integrity Finalization:** `c4418f2861a2660cc915ac84d3c72298f0f6c5ee` (`feat(staff): enforce concurrency locks, private document security, capacity limits, and termination revocation`)

---

### 15. Final Acceptance Checklist

- [x] Python 3.12, Django 5.2, PostgreSQL verified
- [x] Django Unfold Admin interface only
- [x] No duplicate User or authentication models created
- [x] Atomic sequential employee number generation (`EMP-XXXX`)
- [x] Staff profile, employment documents, and emergency contacts
- [x] Trainer profile, specializations, hourly rates, and client caps
- [x] Trainer-to-member assignment with active pairing constraints
- [x] Training session scheduling with trainer and member overlap prevention
- [x] Concurrency-safe transactional session scheduling (`select_for_update`)
- [x] Private staff document object-level authorization and anti-IDOR protection
- [x] Document download security headers (`no-store`, `nosniff`) and audit logging
- [x] Trainer capacity enforcement (`max_active_clients`) with concurrency protection
- [x] Auditable staff termination workflow with credential and branch permission revocation
- [x] Preservation of historical training, scheduling, and shift records
- [x] Staff shift scheduling with shift templates and rest days
- [x] Leave request submission and manager approval/rejection workflows
- [x] Performance analytics selectors based on real data (no invented scores)
- [x] Django Unfold Staff & Trainers navigation group (8 items)
- [x] Role-based permissions and branch isolation enforced
- [x] 234/234 automated tests passing (100%)
- [x] `python manage.py check` passes with 0 issues
- [x] `python manage.py makemigrations --check --dry-run` reports no unmigrated changes
- [x] Phase 09 not started

