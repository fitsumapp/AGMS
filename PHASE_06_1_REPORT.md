# AGMS Phase 06.1 — Configurable Attendance Methods Implementation Report

**Status:** Completed  
**Branch:** `master`  
**Django Version:** 5.2.18  
**Python Version:** 3.12.1  
**Test Suite:** 202 / 202 Passed (100% Pass Rate)

---

## 1. Executive Summary

Phase 06.1 introduces configurable, independent attendance methods to the AGMS Gym ERP access control system:
1. **Method 1: QR Code Attendance** — High-speed, contactless credential scanning supporting dynamic and printed QR tokens.
2. **Method 2: Name / Phone / Member ID Attendance** — Robust front-desk check-in workflow allowing receptionists to search members via normalized Ethiopian phone formats (`09...`, `07...`, `+251...`), full/partial names, or membership numbers, requiring mandatory visual identity confirmation before registering entry or exit.

Both attendance verification methods operate under independent gym-wide administrative toggles. A strict fail-closed policy guarantees that gym operations are never stranded: at least one method must always remain enabled. All attendance attempts—regardless of whether they originate from QR scans or manual receptionist searches—pass through the exact same centralized membership eligibility rules, cross-branch access checks, visit limit validations, and balance policy controls.

---

## 2. Settings Implementation & Database Migration

### 2.1 Model Attributes & Constraints
Two boolean toggles were added to the `GymSettings` model in `apps/core/models.py`:
- `attendance_qr_enabled` (`models.BooleanField(default=True)`)
- `attendance_manual_enabled` (`models.BooleanField(default=True)`)

To enforce the non-negotiable requirement that both methods cannot be disabled simultaneously:
- **Model Validation:** `GymSettings.clean()` raises a `ValidationError("At least one attendance method (QR Code or Name/Phone) must be enabled.")` if both fields evaluate to `False`.
- **Database Constraint:** A PostgreSQL `CheckConstraint` (`at_least_one_attendance_method_enabled`) enforces `Q(attendance_qr_enabled=True) | Q(attendance_manual_enabled=True)` at the database engine level.
- **Admin Form Validation:** `GymSettingsAdminForm.clean()` catches simultaneous disabling attempts in the Django Unfold UI before hitting the database.

### 2.2 Migrations Applied
1. `apps/core/migrations/0003_gymsettings_attendance_manual_enabled_and_more.py`:
   - Adds `attendance_qr_enabled` and `attendance_manual_enabled` with defaults of `True`.
   - Adds `at_least_one_attendance_method_enabled` check constraint.
2. `apps/core/migrations/0004_gympolicy.py`:
   - Registers proxy model `GymPolicy` preserving technical foundation access restrictions while granting business roles access to gym policy administration.

---

## 3. UI, Role Permissions & Method Gating

### 3.1 Django Unfold Admin Configuration
- **Attendance Configuration Section:** Cleanly arranged with modern Unfold toggle switches and explanatory help text.
- **Role Permission Separation:**
  - **Gym Owner & Superusers:** Full view and change permissions.
  - **Branch Managers & Receptionists:** Read-only view permissions (`has_change_permission` returns `False`).
- **Sidebar & Tabs Dynamic Visibility:**
  - When both methods are active: Both "QR Check-in" and "Manual Check-in" appear in the sidebar navigation, and tabbed navigation appears at the top of the terminal pages.
  - When QR is disabled: The QR tab and sidebar item are automatically hidden; direct GET/POST requests fail closed with informative warnings.
  - When Manual is disabled: The Manual tab and sidebar item are automatically hidden; direct access fails closed.

### 3.2 Server-Side Gating
All entry points are gated through centralized enforcement functions in `apps/attendance/permissions.py`:
- `get_attendance_method_settings()`: Returns cached/queried dictionary of method availability.
- `assert_attendance_method_enabled(method)`: Raises `ValidationError` if the requested method (`QR` or `MANUAL`) is disabled.
- Enforced inside:
  - `apps.attendance.services.process_check_in`
  - `apps.attendance.services.process_check_out`
  - `apps.attendance.services.record_manual_override`
  - `apps.attendance.admin.qr_checkin_view`
  - `apps.attendance.admin.manual_checkin_view`

---

## 4. Name / Phone Attendance Terminal Workflow

Implemented in `apps/attendance/admin.py` (`manual_checkin_view`) and rendered via `templates/admin/attendance/manual_checkin.html`:

1. **Omni-Search:**
   - Multi-term name searching (`Abebe Bikila` matches `first_name` and `last_name`).
   - Ethiopian phone number normalization: Handles `0911223344`, `0711223344`, `+251911223344`, `251911223344`, and unformatted input.
   - Member ID prefix matching (`MEM-000001`).
2. **Member Card Preview:**
   - Displays member photo (or placeholder badge), full name, phone number, and member ID.
   - Real-time membership status badge (Active, Expired, Frozen, Pending).
   - Package title, tier, and exclusive end date.
   - Real-time attendance state: Indicates whether the member is currently `Inside` or `Checked Out`.
   - Outstanding balance alert with ETB balance figure when unpaid invoices exist.
3. **Mandatory Visual Confirmation:**
   - Receptionist must check the "I have visually verified this member's identity" box (`identity_confirmed=on`).
   - Requests without this confirmation are rejected with an explicit validation message.
4. **Action Execution:**
   - Single-click Check-in and Check-out.
   - Supervisor manual override drawer with reason and note requirements for edge-case entry.

---

## 5. Telemetry & Analytics

The attendance dashboard (`templates/admin/attendance/dashboard.html` and `apps/attendance/selectors.py`) has been upgraded with dedicated method metrics:
- **QR Check-ins:** Count of entries authenticated via QR code today.
- **Manual Check-ins:** Count of entries authenticated via front-desk name/phone search today.
- **QR Check-outs:** Count of departures logged via QR code today.
- **Manual Check-outs:** Count of departures logged via receptionist search today.
- **Audit Logging:** Every check-in and check-out record stores its exact `check_in_method` and `check_out_method` (`QR` vs `MANUAL`).

---

## 6. Automated Test Suite Results

A dedicated test suite `tests/test_attendance_methods.py` was implemented covering 27 explicit scenarios across configuration, security, search, permissions, and business rules:

```
tests/test_attendance_methods.py::test_both_methods_enabled_by_default PASSED
tests/test_attendance_methods.py::test_qr_only_mode PASSED
tests/test_attendance_methods.py::test_manual_only_mode PASSED
tests/test_attendance_methods.py::test_both_methods_disabled_rejected PASSED
tests/test_attendance_methods.py::test_settings_permissions PASSED
tests/test_attendance_methods.py::test_qr_endpoint_blocked_when_disabled PASSED
tests/test_attendance_methods.py::test_manual_endpoint_blocked_when_disabled PASSED
tests/test_attendance_methods.py::test_forged_post_attempts_fail_closed PASSED
tests/test_attendance_methods.py::test_search_by_full_name PASSED
tests/test_attendance_methods.py::test_search_by_partial_name PASSED
tests/test_attendance_methods.py::test_search_by_ethiopian_phone PASSED
tests/test_attendance_methods.py::test_search_by_member_number PASSED
tests/test_attendance_methods.py::test_duplicate_name_results PASSED
tests/test_attendance_methods.py::test_identity_confirmation_required PASSED
tests/test_attendance_methods.py::test_manual_checkin_success PASSED
tests/test_attendance_methods.py::test_manual_checkout_success PASSED
tests/test_attendance_methods.py::test_qr_checkin_regression PASSED
tests/test_attendance_methods.py::test_qr_checkout_regression PASSED
tests/test_attendance_methods.py::test_expired_membership_denial PASSED
tests/test_attendance_methods.py::test_frozen_membership_denial PASSED
tests/test_attendance_methods.py::test_outstanding_balance_policy PASSED
tests/test_attendance_methods.py::test_cross_branch_access PASSED
tests/test_attendance_methods.py::test_visit_limits PASSED
tests/test_attendance_methods.py::test_duplicate_attendance_prevention PASSED
tests/test_attendance_methods.py::test_qr_credentials_retained_when_disabled PASSED
tests/test_attendance_methods.py::test_qr_works_after_reenabling PASSED
tests/test_attendance_methods.py::test_attendance_method_reporting PASSED

======================== 27 passed in 51.76s ========================
```

### Full Project Regression Summary
```
======================= 202 passed in 286.43s (0:04:46) =======================
```
- **Accounts & Roles:** 15 / 15 Passed
- **Admin Workflows:** 12 / 12 Passed
- **Attendance Core & Verification:** 26 / 26 Passed
- **Attendance Methods (Phase 06.1):** 27 / 27 Passed
- **Authorization & Security:** 17 / 17 Passed
- **Billing & Payments:** 16 / 16 Passed
- **Dashboard Telemetry:** 4 / 4 Passed
- **Members:** 22 / 22 Passed
- **Memberships & Subscriptions:** 23 / 23 Passed
- **Branches & Scope:** 15 / 15 Passed
- **Phase 02 Roles & Boundaries:** 10 / 10 Passed
- **Settings Models & Configuration:** 15 / 15 Passed

---

## 7. Final Acceptance Checklist

| Item | Requirement | Status |
|:---|:---|:---:|
| 1 | `attendance_qr_enabled` and `attendance_manual_enabled` on GymSettings | Verified |
| 2 | Enforce at least one method enabled (Model clean, form, DB CheckConstraint) | Verified |
| 3 | Safe migrations applied with default=True | Verified |
| 4 | GymSettings / GymPolicy Unfold UI with toggle fieldsets | Verified |
| 5 | Owner/Superuser can edit; Manager/Receptionist can view only | Verified |
| 6 | Server-side gating raises ValidationError when method is disabled | Verified |
| 7 | Dynamic sidebar & tabs display based on active methods | Verified |
| 8 | Search by full name, partial name, Ethiopian phone, member ID | Verified |
| 9 | Detailed member profile, photo, status, and inside/outside state | Verified |
| 10 | Unpaid balance warning with ETB amount displayed | Verified |
| 11 | Receptionist visual identity confirmation required | Verified |
| 12 | Check-in and check-out via Name/Phone workflow | Verified |
| 13 | Shared eligibility rules applied identically across both methods | Verified |
| 14 | Method telemetry breakdown (QR vs Manual) on dashboard | Verified |
| 15 | QR credentials preserved when QR is disabled and functional when re-enabled | Verified |
| 16 | Zero regressions across Phases 01–06 (202 / 202 tests passing) | Verified |
