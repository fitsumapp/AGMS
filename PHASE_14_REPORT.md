# AGMS — PHASE 14 FINAL IMPLEMENTATION REPORT
## Advanced Settings, Business Configuration & Operational Administration

---

### 1. Executive Summary

Phase 14 represents the completion of the core development lifecycle of **AGMS (Antigravity Gym Management System)**. This phase consolidates centralized, multi-branch, production-grade business configuration, operational governance, security enforcement, and administrative controls into a cohesive Django Unfold management portal.

Key deliverables achieved:
- **Centralized Gym Settings:** Enhanced `GymProfile` and `GymSettings` models supporting legal entity information, tax registration numbers, receipt/invoice disclaimers, operating defaults, regional conventions, and operational policy toggles.
- **Three-Tier Precedence Resolution:** Implemented deterministic, audit-logged configuration resolution:
  $$\text{Branch Override} \longrightarrow \text{Global Gym Settings} \longrightarrow \text{Explicit Code Defaults}$$
- **Operational Health Dashboard:** Live infrastructure observability reporting PostgreSQL connection health and latency, SMS delivery queue backlogs, unapplied migrations, and pending managerial approvals.
- **PostgreSQL Disaster Recovery & Backup Readiness:** Standard operating procedures, storage headroom verification, retention enforcement, offsite reference tracking, and strict zero-browser execution restore policies.
- **Data Management & Safe Bulk Importer:** Duplicate-safe, dry-run-capable CSV import engines for Members, Retail Products, and Heavy Equipment, featuring cell-level mitigation against spreadsheet formula injection (`=`, `+`, `-`, `@`, `\t`, `\r`).
- **Feature Flag Registry:** Dynamically scoped feature toggles with branch scoping, rollout percentages, and strict dependency tree validation.
- **Django Unfold Administration Grouping:** Built the comprehensive 16-item **Administration & Settings** sidebar navigation alongside a segregated **Technical Administration** module.
- **Full Quality Assurance:** Baseline test suite expanded from 363 to **380 passing tests** with 0 regressions, 0 skipped tests, and live browser UAT verification.

> [!IMPORTANT]
> **Scope Guard Verified:** In accordance with the prompt's instructions, Phase 14 completes development and operational readiness. **Final Production Deployment has NOT been initiated.**

---

### 2. Pre-Implementation Audit

Prior to making any modifications, a pre-implementation architectural audit was conducted against the Phase 13 hardening baseline (`b92e57481ee806f7bb4d0b78cef26e1a18a4b817`):

1. **Test Baseline:** The existing test suite was executed, establishing a passing baseline of **363/363 tests**.
2. **Settings Inventory Audit:**
   - Existing models `GymProfile`, `GymSettings`, `GymBranding`, `GymPolicy` (in `apps/core`) and `BranchSettings`, `AuditEvent` (in `apps/branches`) were audited.
   - Identified that rather than introducing duplicative settings models, existing models should be extended with typed, constrained fields and proxy administration views.
3. **Database Constraints & Invariants:**
   - Single gym deployment verified (`singleton=1` CheckConstraint on `GymProfile` and `BackupConfiguration`).
   - Currency strictly maintained as `ETB` (`DecimalField(max_digits=12, decimal_places=2)`).
   - Timezone strictly maintained as `Africa/Addis_Ababa`.

---

### 3. Architecture and Integration

The operational administration architecture respects AGMS enterprise invariants:
- **Single Gym, Multi-Branch:** Single gym legal entity (`GymProfile`) operating multiple physical branches (`Branch`). Not multi-tenant SaaS.
- **Deterministic Precedence:** Configuration lookups never produce ambiguous results. The resolution engine (`apps.core.services.resolve_setting`) evaluates branch overrides first, falling back to global profile settings, and finally explicit code defaults.
- **Audit-Logged State Changes:** All mutations to branch settings, feature flags, and bulk import actions trigger immutable `AuditEvent` records in PostgreSQL.
- **Loose Coupling with Authoritative Apps:** Operational settings integrate with Billing (Phase 05), Attendance (Phase 06), Finance (Phase 10), Inventory (Phase 11), Notifications (Phase 12), and Facilities (Phase 13) without bypassing financial immutability or closed period reconciliations.

---

### 4. Models and Migrations

#### 4.1 Enhanced Models

1. **`GymProfile` (`apps/core/models.py`):**
   - Added legal identity fields: `legal_name`, `tax_id` (TIN), `commercial_registration_number`.
   - Added digital assets & contact: `website`, `favicon`, `phone`, `email`, `address`.
   - Added branding & print customization: `receipt_header`, `receipt_footer`, `invoice_terms`.
   - Added defaults & regional policies: `default_language` (English/Amharic), `fiscal_year_start_month` (1–12), `member_id_prefix`, `member_id_sequence_length`, `min_member_age`, `require_emergency_contact`, `require_member_photo`.
   - Added legal compliance: `terms_and_conditions`, `privacy_policy`.

2. **`GymSettings` (`apps/core/models.py`):**
   - Added billing policies: `invoice_due_days`, `invoice_prefix`, `receipt_prefix`, `cashier_shift_required`, `pos_max_discount_percent`, `stock_adjustment_approval_required`, `require_refund_approval`.
   - Added notification policies: `sms_enabled`, `email_enabled`, `sms_quiet_hours_start`, `sms_quiet_hours_end`, `max_daily_sms_per_member`, `membership_expiry_reminder_days`.
   - Added staff & class policies: `trainer_max_active_clients`, `class_booking_window_days`, `class_cancellation_cutoff_hours`, `class_waitlist_max_size`, `staff_leave_manager_approval_required`.
   - Added facility & locker policies: `default_locker_deposit_etb`, `lost_key_penalty_etb`, `facility_inspection_interval_days`, `preventive_maintenance_lead_days`.
   - Maintained database constraint: `at_least_one_attendance_method_enabled`.

3. **`FeatureFlag` (`apps/core/models.py`):**
   - New model supporting controlled feature rollout: `code`, `name`, `category`, `is_enabled`, `rollout_percentage`, `requires_features` (JSON list of prerequisite feature codes), `branch` (optional FK for branch-scoped overrides), and `metadata`.

4. **`BackupConfiguration` (`apps/core/models.py`):**
   - Singleton model enforcing backup targets: `backup_directory`, `retention_days`, `compression_algorithm`, `offsite_storage_reference`, `encrypt_backups`, `notify_on_failure`, `last_backup_at`, `last_backup_status`, `last_verified_at`.

5. **`BranchSettings` (`apps/branches/models.py`):**
   - Added override fields: `operating_days`, `receipt_header_override`, `receipt_footer_override`, `allow_access_with_balance_override`, `attendance_qr_enabled`, `attendance_manual_enabled`, `max_discount_percent_override`, `locker_deposit_etb_override`, `low_stock_threshold_override`, `sms_enabled_override`, `cleaning_inspection_frequency_hours`, `manager_notes`.

6. **`AuditEvent` (`apps/branches/models.py`):**
   - Added explicit `branch` foreign key for localized audit trail querying.

#### 4.2 Database Migrations Applied
- `apps/branches/migrations/0002_auditevent_branch_and_more.py`
- `apps/core/migrations/0005_alter_gymprofile_options_alter_gymsettings_options_and_more.py`
- `apps/core/migrations/0006_alter_gymprofile_default_language_and_more.py`

---

### 5. Centralized Gym Settings

Centralized gym configuration is managed through the enhanced `GymProfileAdmin` and `GymSettingsAdmin`:
- Custom Unfold fieldsets organize identity, branding, printing templates, financial defaults, and membership policies.
- Legal metadata (TIN, Commercial Registration) is exposed for formal invoice printing.
- Default currency (`ETB`) and business timezone (`Africa/Addis_Ababa`) remain authoritative.

---

### 6. Branch Configuration & Precedence

Branch-level configuration allows individual facilities to adapt to localized operating contexts without compromising global security:
- **Attendance Verification:** Branches can independently configure QR scanning vs. Name/Phone check-in, but the invariant that *at least one verification method must remain active* is strictly enforced at both model and admin form layers.
- **Financial Thresholds:** Local cashier discount caps and locker deposit requirements can be adjusted within owner-approved limits.
- **Precedence Resolution Service:**
```python
def resolve_setting(branch: Branch, key: str, default: Any = None) -> Any:
    # 1. Check branch override
    # 2. Check global gym setting
    # 3. Fallback to default
```

---

### 7. Membership and Attendance Policies

- **Access with Outstanding Balance:** Configurable globally and per-branch. Default is strictly `False` (access denied if balance outstanding).
- **Membership Freeze Approval:** Requires authorized manager approval by default (`require_freeze_approval=True`).
- **Early Renewal:** Preserves the `EXTEND_EXISTING_END_DATE` policy, ensuring renewal payments extend from the existing expiration date rather than the payment date.
- **Attendance Invariant:** Disabling both QR and Manual attendance simultaneously is prevented by model `clean()` and database `CheckConstraint`.

---

### 8. Billing, Finance and POS Settings

- **Separation of Duties:** `require_refund_approval` enforces two-person review for member refund disbursements.
- **Cashier Shifts:** `cashier_shift_required=True` prevents POS sales without an open, audited cashier session.
- **Discount Ceilings:** Global `pos_max_discount_percent` (default 20.00%) bounds the maximum discretionary discount cashiers may grant.
- **Historical Immutability:** Settings modifications apply only to new transactions; closed reconciliation periods and posted general ledger entries remain immutable.

---

### 9. Notification and Staff Policies

- **Quiet Hours:** Automated SMS dispatch respects `sms_quiet_hours_start` (e.g., 21:00) and `sms_quiet_hours_end` (e.g., 07:00).
- **Daily SMS Caps:** `max_daily_sms_per_member` bounds automated transaction messages to prevent spam and provider throttling.
- **Expiry Notifications:** Configurable comma-separated reminder schedule (e.g. `7,3,1` days prior to expiration).
- **Trainer Client Capacity:** Bounds maximum concurrent 1-on-1 personal training assignments per trainer (`trainer_max_active_clients`).
- **Class Booking & Cancellation Windows:** Enforces minimum advance booking limits and late cancellation cutoff windows.

---

### 10. Facility Configuration

- **Locker Deposits:** Centralized default refundable deposit (`default_locker_deposit_etb = 200.00 ETB`) and lost-key damage penalty (`lost_key_penalty_etb = 150.00 ETB`).
- **Safety Inspections:** Routine facility inspection frequency defaults to 7 days (`facility_inspection_interval_days`).
- **Preventive Maintenance Lead Time:** Generates preventive work orders 3 days in advance of scheduled maintenance milestones.

---

### 11. Advanced Role and Permission Administration

AGMS adheres to native Django authorization without parallel or competing permission engines:
- **Technical Super Admin:** Unrestricted system access, technical user management, and low-level group permission configuration.
- **Gym Owner:** Full business authority across all branches, staff directory administration, gym branding, global policies, and audit trails.
- **Branch Manager:** Scoped strictly to assigned branch facilities (`UserBranchAccess`), permitted to adjust branch overrides and review operational metrics.
- **Privilege Escalation Prevention:** Non-superusers cannot grant `is_superuser` or `is_staff` flags. Forms and views enforce server-side validation.

---

### 12. Audit Logs and Operational History

The centralized **Operational Reports & Audit Trail** view (`/admin/operations/reports/`) aggregates events from `apps.branches.models.AuditEvent`:
- Records: Timestamp (EAT), Actor, Branch Scope, Action Category, Target Reference, and Sanitized Context Metadata.
- Interactive filtering by branch and action keywords.
- Read-only interface preventing modification or deletion of historical governance logs.

---

### 13. Data Management (Bulk Import & Export)

The bulk data management interface (`/admin/operations/data-management/`) provides enterprise onboarding tools:
- **Supported Entities:**
  1. Members (First Name, Last Name, Phone, Email, Gender, Date of Birth)
  2. Retail Products (Name, SKU, Category, Unit Price, Initial Stock)
  3. Facility Equipment (Asset Code, Name, Category, Area, Manufacturer, Model)
- **Security & Integrity Safeguards:**
  - **CSV Formula Injection Mitigation:** All incoming and outgoing CSV cells starting with dangerous characters (`=`, `+`, `-`, `@`, `\t`, `\r`) are sanitized by prepending a single quote (`'`).
  - **Dry-Run Validation:** Allows previewing file contents, duplicate checks, and validation error reports without committing to the database.
  - **Atomic Transactions:** Imports execute within `transaction.atomic()`; invalid rows prevent partial or corrupted writes.
  - **Duplicate Detection:** Detects duplicate phone numbers for members, SKUs for products, and asset codes for machinery.
  - **Branch-Scoped Exports:** Generates sanitized UTF-8 BOM CSV files filtered strictly by the authorized user's branch access.

---

### 14. Operational System Health Dashboard

The Operational Health Dashboard (`/admin/operations/system-health/`) provides live infrastructure diagnostics:
- **Database Status:** Live PostgreSQL connection ping, round-trip query latency (ms), and engine verification.
- **Notification Engine:** Queued message backlog, 24-hour delivery counts, and failure tracking.
- **Schema Migrations:** Verifies that all 19 application migrations across Phases 01–14 are synchronized.
- **Storage & Backup:** Headroom review and archive count in the configured backup mount.
- **Pending Approvals:** Real-time counters for pending member refunds, expense approvals, staff leave requests, and maintenance work orders.
- **Zero Secrets Disclosure:** Environment variables, API keys, and connection credentials are never exposed in the dashboard.

---

### 15. Backup Readiness & Disaster Recovery

The Backup Readiness interface (`/admin/operations/backup-readiness/`) establishes production disaster recovery governance:
- **Strict Zero-Browser-Execution Restore Invariant:** Database restoration CANNOT be triggered from the web interface. All restores require authenticated server console access and isolated verification.
- **Storage Target Monitoring:** Checks directory existence, write permissions, and available disk headroom.
- **Retention Lifecycle:** Automatically purges archives exceeding the configured retention threshold (default 30 days).
- **PostgreSQL CLI Standard Operating Procedures:**
  - *Snapshot Command:*
    ```bash
    pg_dump -h 127.0.0.1 -p 55432 -U postgres -d gym_erp -F c -b -v -f "backups/agms_$(date +%Y%m%d_%H%M%S).dump"
    ```
  - *Restore Verification Protocol:*
    ```bash
    createdb -h 127.0.0.1 -p 55432 -U postgres gym_erp_test_restore
    pg_restore -h 127.0.0.1 -p 55432 -U postgres -d gym_erp_test_restore -v "backups/agms_latest.dump"
    dropdb -h 127.0.0.1 -p 55432 -U postgres gym_erp_test_restore
    ```

---

### 16. Feature Flags

Implemented in `apps.core.models.FeatureFlag` and `apps.core.services.is_feature_enabled`:
- Baseline flags automatically seeded on post-migrate:
  - `ATTENDANCE_QR`: QR Code Member Attendance
  - `ATTENDANCE_MANUAL`: Name & Phone Manual Attendance
  - `ONLINE_BOOKING`: Member Online Class Booking
  - `CLASS_WAITLIST`: Automated Class Waitlist (depends on `ONLINE_BOOKING`)
  - `SMS_NOTIFICATIONS`: Automated SMS Gateway
  - `BULK_MESSAGING`: Bulk SMS Campaigns (depends on `SMS_NOTIFICATIONS`)
  - `POS_MODULE`: Front Desk Retail POS
  - `INVENTORY_MANAGEMENT`: Stock & Warehouse Tracking (depends on `POS_MODULE`)
  - `FINANCE_MODULE`: Finance & Expense Management
  - `FACILITY_MANAGEMENT`: Lockers & Equipment Maintenance
  - `MULTI_BRANCH_ACCESS`: Cross-Branch Package Roaming
- **Dependency Tree Validation:** Enabling a dependent feature is prohibited if its required parent feature is disabled.

---

### 17. Django Unfold UI/UX Implementation

Configured in `config/branding.py` and `apps/core/admin.py`:
- **Navigation Group: Administration & Settings (16 Items):**
  1. *Gym Profile* (`admin:core_gymprofile_changelist`)
  2. *Branch Configuration* (`admin:branches_branchsettings_changelist`)
  3. *Membership Policies* (`admin:core_gympolicy_changelist`)
  4. *Attendance Settings* (`admin:branches_branchsettings_changelist`)
  5. *Billing & Finance Policies* (`admin:core_gymsettings_changelist`)
  6. *POS & Inventory Settings* (`admin:core_gymsettings_changelist`)
  7. *Notification Settings* (`admin:notifications_providerconfig_changelist`)
  8. *Staff & Class Policies* (`admin:core_gymsettings_changelist`)
  9. *Facility Policies* (`admin:core_gymsettings_changelist`)
  10. *Feature Management* (`admin:core_featureflag_changelist`)
  11. *Business User Administration* (`admin:accounts_staffaccount_changelist`)
  12. *Audit Logs* (`admin:branches_auditevent_changelist`)
  13. *Data Import & Export* (`data-management`)
  14. *System Health* (`system-health`)
  15. *Backup Readiness* (`backup-readiness`)
  16. *Operational Reports* (`operational-reports`)
- **Technical Administration:**
  - Technical Users, Technical Groups, Gym Branding, Branch Assignments.

---

### 18. Security and Concurrency

- **Row-Level Locking:** Settings updates use PostgreSQL `select_for_update()` inside atomic transactions to eliminate race conditions during concurrent modifications.
- **CSV Formula Injection:** Evaluated cell-by-cell; dangerous initial characters are escaped.
- **Permission Guard:** Views and endpoints enforce role checks (`is_owner`, `has_role`, `is_superuser`); cross-branch operations reject unauthorized branch IDs with `PermissionDenied (HTTP 403)`.
- **Closed Period Protection:** Settings changes never trigger retroactive financial recomputations.

---

### 19. New Test Results

The new test suite `tests/test_settings_and_operations.py` contains **17 new automated tests**, all passing:

| Test Name | Focus | Result |
|---|---|---|
| `test_gym_settings_authoritative_defaults` | Validates default ETB, Africa/Addis_Ababa, and policy invariants | **PASSED** |
| `test_attendance_safety_invariant_rejects_disabling_both` | Verifies rejection of disabling both QR & Manual attendance | **PASSED** |
| `test_branch_settings_attendance_safety_invariant` | Branch-level attendance method constraint enforcement | **PASSED** |
| `test_resolve_setting_three_tier_precedence` | Verifies Branch Override $\rightarrow$ Global $\rightarrow$ Code Default | **PASSED** |
| `test_update_branch_settings_atomic_and_audited` | Atomic update and AuditEvent generation | **PASSED** |
| `test_feature_flag_seeding_and_evaluation` | Baseline feature seeding and parent dependency check | **PASSED** |
| `test_feature_flag_branch_scoping` | Scoped evaluation across multiple branches | **PASSED** |
| `test_feature_flag_dependency_validation_error` | Model validation when missing required feature flag | **PASSED** |
| `test_csv_formula_injection_sanitization` | Mitigates `=, +, -, @, \t, \r` spreadsheet execution | **PASSED** |
| `test_member_import_dry_run_and_commit` | Dry-run preview mode and duplicate phone skipping | **PASSED** |
| `test_product_import_and_duplicate_sku` | Retail product onboarding with automatic StockItem creation | **PASSED** |
| `test_equipment_import` | Machinery asset registry onboarding with area mapping | **PASSED** |
| `test_export_branch_data_isolation` | Branch data isolation; secondary branch data excluded | **PASSED** |
| `test_system_health_metrics_contract` | Live PostgreSQL connectivity, latency ms, approval counters | **PASSED** |
| `test_backup_readiness_report` | Backup configuration, directory access, retention days | **PASSED** |
| `test_operational_views_access_control` | Role boundaries (Superuser vs Owner vs Manager vs Trainer) | **PASSED** |
| `test_cross_branch_export_permission_denial` | HTTP 403 on unauthorized cross-branch export attempt | **PASSED** |

---

### 20. Full Regression Results

Full regression testing was executed with `pytest -q`:

```
=================================== 380 passed, 4 warnings in 1205.18s (0:20:05) ===================================
```

- **Phase 01–13 Baseline:** 363 tests passed (100%).
- **Phase 14 Additions:** 17 tests passed (100%).
- **Total Suite:** 380 tests passed with **zero failures and zero regressions**.

---

### 21. Browser UAT Results

Real browser UAT was conducted via `browser_subagent` on the local development server (`http://127.0.0.1:8000`), verifying all new interfaces:

1. **Authentication:** Successfully logged in as Superuser (`fitsum.worku58@gmail.com`).
2. **Navigation Bar:** Verified that the **Administration & Settings** group is fully rendered with all 16 items and appropriate icons.
3. **Operational System Health Dashboard:** Verified PostgreSQL 18 connection status (`ONLINE`, live latency), SMS delivery counters, schema migration status, and pending operational approvals.
4. **Backup Readiness & Disaster Recovery:** Verified storage status card, free headroom, retention window (30 days), zero-browser-restore warning banner, and PostgreSQL CLI instructions.
5. **Data Management:** Verified CSV Bulk Importer with dry-run toggle, sample CSV download links, branch data exporter, and formula-injection security notice.
6. **Operational Audit Trail:** Verified chronological audit event listing with timestamp, actor, branch, action badge, and sanitized metadata.
7. **Feature Management:** Verified changelist for feature flags.

#### UAT Artifacts
- System Health Screenshot: `system_health_1792785696531.png`
- Backup Readiness Screenshot: `backup_readiness_1792785708373.png`
- Data Management Screenshot: `data_management_1792785717887.png`
- Operational Reports Screenshot: `operational_reports_1792785734483.png`
- Session Video: `phase14_admin_uat_1792785495987.webp`

---

### 22. Known Limitations & Safe Defaults

1. **Offline Backup Storage Reference:** Offsite cloud backup references (e.g. S3, SFTP) are stored as configuration metadata; actual transfer automation requires server-level cron/systemd scripts.
2. **Large File Uploads:** Uploaded CSV imports are bounded to 5 MB and 1,000 rows per batch to avoid request timeouts on shared web workers.
3. **Browser Restore Intentional Absence:** Browser-triggered database restore is intentionally omitted as an enterprise security defense.

---

### 23. Files Created and Modified

#### Created Files:
- `apps/core/services.py`: Configuration precedence engine, feature flag evaluation, health metrics collector, and backup report generator.
- `apps/core/importers.py`: Formula-safe CSV bulk import/export engine for members, products, and equipment.
- `apps/core/views.py`: Views for system health, backup readiness, data management, data export, sample downloads, and operational reports.
- `templates/admin/operations/system_health.html`: System Health Dashboard template.
- `templates/admin/operations/backup_readiness.html`: Backup Readiness & Disaster Recovery template.
- `templates/admin/operations/data_management.html`: Data Management Import & Export template.
- `templates/admin/operations/operational_reports.html`: Audit Trail & Governance Log template.
- `apps/core/migrations/0005_alter_gymprofile_options_alter_gymsettings_options_and_more.py`: Model migration for core models.
- `apps/core/migrations/0006_alter_gymprofile_default_language_and_more.py`: Migration for optional fields on profile and settings.
- `apps/branches/migrations/0002_auditevent_branch_and_more.py`: Branch foreign key on audit events.
- `tests/test_settings_and_operations.py`: 17 comprehensive automated Phase 14 tests.

#### Modified Files:
- `apps/core/models.py`: Enhanced `GymProfile` and `GymSettings`, added `FeatureFlag` and `BackupConfiguration`.
- `apps/core/admin.py`: Registered Unfold ModelAdmins for profile, settings, feature flags, backup configuration, and branding.
- `apps/core/apps.py`: Connected post-migrate hook for feature flag baseline seeding.
- `apps/branches/models.py`: Enhanced `BranchSettings` with override fields and `AuditEvent` with branch FK.
- `apps/branches/admin.py`: Updated `BranchSettingsAdmin` and `AuditEventAdmin` fieldsets and search.
- `apps/branches/audit.py`: Updated audit recording with Phase 14 action types, UUID translation, and branch resolution.
- `config/branding.py`: Replaced old management navigation with 16-item **Administration & Settings** group and **Technical Administration**.
- `config/urls.py`: Wired operational routes under `admin/operations/`.
- `tests/test_configuration.py`: Updated `test_all_admins_use_unfold` with new registered models.

---

### 24. Git Commit Details

- **Commit Message:** `feat: implement advanced gym settings and operational administration`
- **Branch:** `main`

---

### 25. Final Acceptance Checklist

- [x] Baseline audit completed; 363 tests verified passing before changes.
- [x] `GymProfile` enhanced with legal, branding, print, and regional settings.
- [x] `GymSettings` enhanced with billing, notification, staff, class, and facility policies.
- [x] Three-tier precedence resolution engine (`resolve_setting`) implemented and verified.
- [x] Attendance safety invariant (at least one method enabled) enforced globally and per-branch.
- [x] Cashier discount caps, cashier shift requirement, and refund approval separation of duties enforced.
- [x] Feature flag engine with dependency tree validation and branch scoping implemented.
- [x] Bulk CSV import/export for members, products, and equipment with dry-run support implemented.
- [x] CSV formula-injection defense (`=`, `+`, `-`, `@`, `\t`, `\r`) implemented and tested.
- [x] Operational System Health Dashboard showing real PostgreSQL connection, latency, and queue health built.
- [x] Backup Readiness view with zero-browser-restore invariant and standard PostgreSQL CLI commands implemented.
- [x] Operational Audit Trail view with branch filtering and event categorization implemented.
- [x] Unfold sidebar navigation configured with the exact 16-item **Administration & Settings** group.
- [x] 17 new automated tests added in `tests/test_settings_and_operations.py`.
- [x] Full regression test suite passing (380/380 tests, 100%).
- [x] Real browser UAT conducted, recorded, and verified.
- [x] Final Production Deployment NOT initiated.
