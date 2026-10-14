# AGMS — Phase 02 Implementation Report

## 1. Executive Summary

Phase 02 extends the existing Django/PostgreSQL installation with six Django Group business
roles, branch records and assignments, a restricted owner staff workflow, session-based branch
context, operational audit events and a redesigned Unfold dashboard. Phase 01 models, settings,
security controls and original migrations were preserved. Business KPIs remain explicitly empty
until their source modules exist.

## 2. Completion Status

**PARTIAL**, pending browser visual acceptance and comparison with the referenced dashboard image.
The supplied attachment directory contained only the text prompt. Browser runtime discovery
returned `No browser is available` and an empty browser list. Functional and database acceptance
passed their final checks as recorded below. No Phase 03 work was started.

## 3. Architecture Changes

- Added `apps.branches` inside the existing single-deployment, PostgreSQL-only architecture.
- Kept Django Groups/Permissions for business roles; `is_superuser` remains technical admin.
- Added proxy-based `StaffAccount` and `GymBranding` admin workflows with restricted fields.
- Centralized branch authorization in services. Session selection is UI context only.
- Kept Unfold native layout, account controls and responsive sidebar. A small
  `admin/base_site.html` block extension places the selector in the topbar.
- Version changed to `0.2.0`. No tenants, frontend framework, background queue, business metrics
  or future module model placeholders were introduced.

## 4. Files Created

Git review found **28 new Phase 02 files** (including package markers and this report).

| Area | Significant new files and purposes |
|---|---|
| `apps/branches` | `models.py`: Branch, UserBranchAccess, BranchSettings, AuditEvent; `services.py`: scope, selector, assignment and revocation; `audit.py`: safe event writer; `views.py`: POST-only switch; `admin.py`: Unfold scoped admin; `apps.py`: registration; migration `0001_initial.py`; package markers |
| `apps/accounts` | `roles.py`: role matrix/seeding/role checks; `management/commands/seed_business_roles.py`: idempotent command; migration `0002_staffaccount.py`; package markers |
| `apps/core` | Migration `0002_gymbranding.py` for owner-safe proxy |
| `templates/admin` | `base_site.html`: Unfold topbar extension and account menu; `components/branch_selector.html`, `kpi_card.html`, `empty_state.html`, `loading_state.html`: reusable interface components |
| `static/css` | `erp-admin.css`: sidebar, cards, theme and responsive rules |
| `tests` | `test_phase02_roles.py`, `test_phase02_branches.py`: role, scope, admin, selector and dashboard tests |
| `docs` | `ROLES_AND_BRANCHES.md`, `DASHBOARD_DESIGN.md`: policy contract and visual/data contracts |
| Root | `PHASE_02_REPORT.md`: this report |

## 5. Files Modified

Git review found **18 modified files**, all within Phase 02 scope.

| Area | Why |
|---|---|
| `config/settings/base.py`, `config/branding.py`, `config/urls.py` | Install branches, expose permission-aware navigation, central palette/style and switch URL |
| `apps/accounts/models.py`, `forms.py`, `admin.py` | Restricted staff proxy and forms without technical privilege fields |
| `apps/core/models.py`, `admin.py`, `dashboard.py` | Owner branding proxy, scoped dashboard and Unfold global context |
| `templates/admin/index.html` | Compact commercial dashboard with real branch context and honest empty states |
| `.env.example`, `README.md`, `docs/ARCHITECTURE.md`, `SECURITY.md`, `ROADMAP.md`, `DEPLOYMENT_OVERVIEW.md` | Brand/role setup, scope guidance and updated operations |
| `tests/test_configuration.py` | Preserve the exact all-Unfold registry assertion for the expanded admin model set |

No pre-existing uncommitted changes were present. Phase 01 migrations were not edited.

## 6. Database Models and Migrations

- `Branch`: UUID PK, normalized code, name, optional description/contact/city/manager/opening date,
  required address, active status and actor/timestamps. PostgreSQL enforces case-insensitive code
  uniqueness plus nonempty code/name; active/city index supports list queries.
- `UserBranchAccess`: UUID PK, user/branch FKs, active/primary, assigned time/by and timestamps.
  User/branch is unique; a partial PostgreSQL unique constraint permits only one active primary
  branch per user; a check forbids inactive primary assignments.
- `BranchSettings`: one-to-one branch and optional opening/closing hours.
- `AuditEvent`: actor, action, target type/UUID, time and restricted JSON metadata; action/time
  and target indexes. Its admin has view only.
- Proxy migrations add `accounts.StaffAccount` and `core.GymBranding` permissions without new
  tables. Three Phase 02 migration files were generated and applied; 33 migrations are now applied
  in the configured development PostgreSQL database.

## 7. Roles and Permissions Matrix

| Role | Implemented Phase 02 capabilities | Explicit limits |
|---|---|---|
| Technical Super Admin | All Django admin and branch administration | Uses `is_superuser`, no business super-admin Group |
| Gym Owner | All active branches, branch creation/change, staff/assignment workflow, gym branding, audit view | No technical User/Group screens, technical policy fields, or owner-role creation |
| Branch Manager | View/change assigned active branches and operating hours | No other branch, assignment, identity or branch activation rights |
| Receptionist | View assigned branches | Member/attendance/payment modules deferred |
| Accountant | View assigned branches | Billing/finance modules deferred |
| Trainer | View assigned branches | Class/training modules deferred |
| POS Staff | View assigned branches | POS/inventory modules deferred |

`seed_business_roles` resets these six Groups to only the permissions for implemented workflows.
It can be rerun. Direct User/Group administration remains restricted to technical superusers.

## 8. Branch Management Implementation

Unfold Branch admin includes search, active/city filters, pagination, audit fields and organized
fieldsets. Owners may create or change branches, including reactivating inactive ones. Assigned
Branch Managers can edit a limited field set on their active branches. They cannot change code,
manager, opening date or active status. Delete and bulk delete are disabled. Branch Settings
holds only operating hours. A manager FK accepts only an assigned active Branch Manager.

## 9. User-Branch Assignment Logic

Assignments are explicit and unique per user/branch. Active assignments require an active
business staff user and active branch; deactivation clears primary status. Owners may assign or
revoke through admin or service methods. The audit writer records actor/action/time/target with
safe metadata. Revocation and removal of a business role both remove runtime branch scope.
Database constraints backstop concurrent primary assignments. `docs/ROLES_AND_BRANCHES.md`
defines the future module scoping contract.

## 10. Active Branch Selector

The topbar form lists only currently accessible active branches. Technical superusers and owners
may select All Branches; other staff receive an assigned branch by default and may switch among
their assignments. POST and CSRF protection apply. Each switch rechecks authorization and stores
the selected UUID in the authenticated session. Invalid, inactive and revoked selections are
rejected or discarded. Audit events record allowed and denied switch attempts without logging
session tokens or entered secrets. Object permissions always query current assignment/role state.

## 11. Dashboard UI/UX Implementation

Unfold's responsive sidebar, topbar account controls, branding and theme handling remain in use.
The dashboard now has a compact welcome strip, active branch context, a topbar avatar/account menu,
four reusable KPI cards,
branch list and six analytics sections. Layout uses a restrained dark teal sidebar, a two-column
panel grid, tablet/mobile breakpoints, consistent card sizing, dark theme colors and empty states.
Each KPI value and trend is absent until a real data provider exists. Branch count/names reflect
actual accessible active records, not invented performance figures. The card and chart contracts
are documented in `docs/DASHBOARD_DESIGN.md`.

## 12. Reference Design Comparison

The approved image mentioned in the prompt was **not present** in the supplied attachment folder;
only `Pasted text.txt` was available. The stated colors, compact layout, dark sidebar and card
hierarchy guided the implementation. A pixel-level or proportion comparison to the image is
**NOT VERIFIED**. No screenshot is claimed.

## 13. Security Controls

Phase 01 CSRF, Axes throttling, cookies, HTTPS production settings, password validation and
superuser-only technical administration remain. New protections include queryset scope, object
checks, readonly and constrained owner forms, server-side branch switch validation, PostgreSQL
uniqueness/check constraints, POST-only selection and metadata whitelisting. Crafting foreign
keys or assigning an owner/superuser role through the business workflow is tested. Existing
staff with stale assignment rows lose access if their business role is removed.

## 14. Audit Logging

Admin and service actions record branch create/update/activation/deactivation, assignment
creation/update/revocation, staff business role assignment, gym branding edits, and allowed/denied
branch switches. Events contain actor, action, timestamp, target and safe metadata. No password,
session key or secret is recorded. Owner and technical superusers may view entries; no admin user
may add/change/delete them. Direct database writes bypass the service-level audit trail and are
outside its guarantee; financial immutable ledgers remain future work.

## 15. Automated Tests and Results

| Command | Result | Actual evidence |
|---|---|---|
| Phase 01 baseline pytest | PASS | 63 passed in 108.97 seconds, before Phase 02 edits |
| Focused Phase 02 tests | PASS | 22 passed in 21.67 seconds, before additional edge tests |
| First full regression run | FAIL | 84 passed, 1 failed: Phase 01 test hard-coded version 0.1.0 after the intentional bump to 0.2.0 |
| Final full regression pytest | PASS | 88 passed, 0 failed, 0 skipped in 97.91 seconds; `artifacts/pytest-phase02-final.xml` |
| Final dashboard/selector check | PASS | 5 passed in 9.87 seconds after last template changes |
| `manage.py check` | PASS | No issues |
| `makemigrations --check --dry-run` | PASS | No changes detected |
| `manage.py migrate` | PASS | Three new migrations applied; 33 total applied; subsequent run: no migrations to apply |
| Deployment check | PASS | Sample production configuration, no issues |
| `collectstatic --noinput` | PASS | 1 new CSS file copied, 153 unmodified; expected Unfold stock collisions reported |
| Admin HTTP smoke | PASS | Dashboard and six new admin changelist pages returned HTTP 200 for a temporary superuser |
| Live development HTTP check | PASS | `/admin/` login and authenticated dashboard returned 200 against the running server; temporary account removed; dashboard CSS served HTTP 200 |

## 16. Browser Visual Test Results

**NOT VERIFIED.** Browser setup returned `No browser is available`; discovery returned `[]`.
Desktop 1440px, tablet 768px, mobile 390px, light/dark mode, sidebar collapse, selector,
login and admin form appearance could not be inspected. No screenshots were captured. HTTP
rendering and static collection are recorded separately from visual acceptance.

## 17. Problems Encountered and Fixes

1. The reference image was absent. Implemented against the textual palette/layout and retained
   the missing comparison as an acceptance gap.
2. Resolving `static()` during settings import raised `AppRegistryNotReady`. Changed the Unfold
   style entry to a request-time callable; system check passed afterward.
3. A development smoke test initially used Django Client's `testserver` host, which the local
   development hosts intentionally reject. Reran it with `localhost`; pages returned HTTP 200.
4. A stale branch assignment would survive removal of all business groups. The scope service now
   requires an active business role; a regression test covers this.
5. The inherited Phase 01 dashboard test expected literal `0.1.0`. Updated it to assert the
   configured `APP_VERSION`, retaining the branding, empty-state and honest-metrics assertions.

## 18. Known Limitations

Visual acceptance and comparison with the missing reference image remain outstanding. Business
KPI sources, membership/attendance/billing/finance/POS workflows, external audit storage, staff
email invitations, MFA and production proxy/TLS/backup validation are deferred. Direct ORM/bulk
updates can bypass service audit and model `clean()` checks; future modules must use services and
database constraints. Optional Branch.manager display can become stale after an assignment is
revoked, but does not grant access. The local development bootstrap role is not production-ready.

## 19. Git Commit Information

Initial Phase 02 inspection found a clean `master` working tree at
`2c75bcc8d13d25597468dc7bfbc3fcc3002ecc88`. Final Phase 02 commit hash and clean status
are reported in the delivery response and available via `git log -1 --format=%H`; a commit cannot
embed its own hash. Local `.env`, PostgreSQL directories, virtualenv, static
output and test artifacts are ignored and excluded from staging. Staged review found 46 files
(28 new, 18 modified), no local secret values and no database/runtime files.

## 20. Phase 03 Readiness

The branch scope and role APIs can support the next business module once acceptance and
integration design are reviewed. Each future branch-owned model must enforce scope in list,
detail, mutation, aggregate and export paths. Financial approval rules and business event
auditing need their own implementation. Phase 03 has not been started.

## 21. Final Acceptance Criteria

| Criterion | State |
|---|---|
| Preserve Phase 01 authentication, settings, migrations and tests | PASS — final 88-test suite |
| Django/PostgreSQL/Unfold-only architecture and separate gym deployments | PASS |
| Six seeded roles with least necessary implemented permissions | PASS focused tests |
| Restricted owner staff management and technical admin separation | PASS focused tests |
| Branch model, constraints, settings and assignments | PASS focused tests/migrations |
| Server-side branch scope and secure selector | PASS focused tests |
| Branch/action audit foundation | PASS focused tests |
| Redesigned dashboard with honest empty states | PASS HTML tests |
| Responsive light/dark visual acceptance | NOT VERIFIED — browser unavailable |
| Match supplied reference image | NOT VERIFIED — image absent |
| Documentation, deployment/static/check commands | PASS |
| Final full test run | PASS — 88 passed, 0 failed, 0 skipped |
| Final Git commit | PASS — commit contains this report; hash in delivery response |

**Overall Phase 02: PARTIAL** until all visual acceptance items can be verified. The automated security and functional
checks pass; no visual acceptance or reference-image comparison is claimed.
