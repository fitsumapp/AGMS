# Roles, branch scope, and staff workflows

Phase 02 uses Django Groups and Permissions. Run `python manage.py seed_business_roles`
after migrations; rerunning it is safe and restores the defined grants on those six groups.
Technical Super Admin is `is_superuser`, never a business group. Seeded permissions only
cover workflows that currently exist. Future module permissions must be added when their
models, forms, branch filters, and tests exist.

| Role | Phase 02 grants | Scope and restrictions |
|---|---|---|
| Gym Owner | Branches, assignments, staff account workflow, gym branding, audit view | All active branches for operations; no technical User/Group admin, credentials or technical policies |
| Branch Manager | View/change assigned branches and their operating hours | Explicit active assignments only; cannot reassign users, change branch identity or activate/deactivate branches |
| Receptionist | View assigned branches | No member/attendance/payment permission until those modules exist |
| Accountant | View assigned branches | No billing/finance permission until those modules exist |
| Trainer | View assigned branches | No training/class permission until those modules exist |
| POS Staff | View assigned branches | No sales/stock permission until those modules exist |

Business owners create staff through the separate **Staff Accounts** admin, with a validated
password and a single role from Branch Manager, Receptionist, Accountant, Trainer, or POS Staff.
The workflow sets `is_staff=True` and `is_superuser=False` itself. It never exposes Django's
user/group permission editors, owner-role assignment, or arbitrary flags. Owners cannot alter
their own technical identity or another owner's account. Technical superusers retain the Phase 01
User and Group screens. A technical superuser must provision the first Gym Owner account and role.

The separate **Gym Branding** proxy permits owners to edit name, legal name, contact details,
address and logo. Currency, timezone and policy settings remain under the technical screens.
This preserves the Phase 01 restriction on those settings while giving owners a safe business
branding workflow. Separate owner policy workflows can be added when their effects are implemented.

## Branch model

`Branch` stores an uppercase code, required name/address, contact details, optional lead manager,
opening date, active flag and audit actors/timestamps. Code uniqueness is enforced case-insensitively
by PostgreSQL. Branch Manager must already have an active assignment before being selected as a
branch's named manager. A former manager loses access when the assignment is revoked even if the
optional display field still points to that user; operators should clear or update stale displays.
`BranchSettings` currently stores only optional opening and closing times.

`UserBranchAccess` is unique per user/branch. A conditional PostgreSQL constraint permits at most
one active primary branch per user, with a check disallowing an inactive primary assignment.
Admin and service validation require active business staff and an active branch for active access.
Owners edit assignments; a disabled assignment remains as history. Changing a user's role or
active status immediately affects runtime access because scope checks read current group/user state.
Assignment rows should be reviewed when offboarding staff.

## Scope contract

Use `accessible_branches(user)`, `can_access_branch(user, branch)`, `can_manage_branch(user, branch)`
and `scoped_queryset(queryset, user, branch_field="branch")` from `apps.branches.services`.
These query the database on each request. `active_branch(request)` is a UI context, never an
authorization grant. Every object lookup or mutation must still use the accessible-branch queryset
and an action-specific permission. UUIDs supplied by browsers are validated before selection.
Inactive branches are excluded from ordinary access and selector options. Owners can still
reactivate or edit inactive branches in the Branch admin.

The topbar selector persists an authorized UUID in the session. Owners and technical superusers
can choose All Branches. Other staff automatically select their first assigned active branch,
and may switch among active assignments. Invalid or revoked selections are discarded on the next
request; a forged switch is rejected server-side. Switching uses POST and CSRF protection.

Future Membership, Attendance, Billing, Finance, and Inventory models must include a Branch FK,
scope list/detail/edit/bulk actions through `scoped_queryset`, and verify permissions again inside
transactional services. Financial approvals must be distinct from recording permissions. Reports
must scope aggregates before aggregation. APIs, exports and background jobs need the same checks;
session selection is insufficient. Cross-branch package policy remains configuration until the
membership module implements it.

AuditEvent records actor, action, time, target type/UUID, and whitelisted metadata for admin and
service operations. It has no admin add/edit/delete interface, even for owners. It is a basic
operational trail; direct database writes bypass services and are outside its guarantee. A
financial ledger and immutable external audit storage belong to later modules and deployment.
