# Roadmap and boundaries

Phase 01 contains only `accounts` and `core`. No future Django apps or dead navigation links are created.

Phase 02: **Users, Roles, Permissions & Multi-Branch Foundation**. Define owner workflows with
restricted role assignment, explicit branch membership and scope enforcement, and tests for
cross-branch access. Existing superuser-only identity/settings administration remains the baseline
until those reviewed workflows exist.

The intended fourteen business module names are: members, memberships, packages, attendance,
billing, staff, scheduling, finance, inventory, branches, reports, notifications, security_audit,
and dashboard. These are planning names only, not installed apps or implementation commitments.

Future sidebar sections: Members; Memberships; Packages; Attendance; Payments & Billing;
Staff & Trainers; Classes & Scheduling; Expenses & Finance; POS & Inventory; Branches;
Reports; Notifications; Security & Audit. Enable each only when its views and permission checks exist.

Future KPIs: active members, expired memberships, today's attendance, monthly revenue,
outstanding balances, expiry alerts and branch performance. The current dashboard displays no
invented operational counts or amounts. Policies stored now will be enforced by their future modules.

Before Phase 02: accept the foundation verification report, complete visual checks where unavailable,
agree branch/owner permission boundaries and customer deployment conventions. Production readiness
also requires real infrastructure checks described in DEPLOYMENT_OVERVIEW.md.
