# Architecture

One reusable codebase, one installation per gym customer. Multiple branches will live inside
that installation in a later phase. There is no tenant layer or customer routing middleware.

| Directory | Responsibility |
|---|---|
| config/settings | Shared, development, test and fail-closed production configuration |
| config/branding.py | Validated environment palette and functional Unfold navigation |
| apps/accounts | Email/UUID user, manager, styled forms, restricted admin, auth logging |
| apps/core | UUID/timestamp and audit abstractions, gym profile and policies, dashboard |
| templates/admin | Django-only Unfold dashboard and reusable status component |
| static / media | Local assets / private files; neither contains business module placeholders |
| tests | PostgreSQL-backed authentication, authorization, settings and security tests |
| scripts | Isolated development PostgreSQL and production configuration validation |
| docs | Operations, architecture and scope |

`AUTH_USER_MODEL` precedes initial migrations. User email is stripped and lowercased on save;
a PostgreSQL functional unique constraint rejects case variants even for bulk writes.
Django handles password hashing, groups and permissions. Unfold forms preserve Django password
validation. No user or group permission grants can bypass the superuser admin restriction.

GymProfile has a unique singleton discriminator constrained to 1, allowing at most one profile
even with concurrent writes. GymSettings is one-to-one with that profile. Database checks restrict
policy enum values. The empty installation is valid; neither table is required to render the UI.
Deletion is disabled in the settings admin. Defaults are policy configuration only, not enforcement
of future membership, balances or branch rules.

AuditFieldMixin references the configured user by lazy model name. User does not inherit it,
so there is no circular creation dependency. Django LogEntry records administrative changes;
settings also record creator/updater UUIDs. Future transactional business event auditing should
be implemented alongside the business transaction, without adding premature event tables here.

Only PostgreSQL is configured. Tests use a separate test database, real constraints and normal
password hashing. There is no SQLite, API framework, task queue or separate frontend.

References used: [Unfold authentication](https://unfoldadmin.com/docs/installation/auth/),
[Unfold installation](https://unfoldadmin.com/docs/installation/quickstart/),
[Axes installation](https://django-axes.readthedocs.io/en/latest/2_installation.html).
