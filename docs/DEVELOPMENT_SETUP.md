# Development setup

Install Python 3.12+ compatible with the lockfile, Git, and PostgreSQL. PostgreSQL 18.3 was used
for verification. Create a database and dedicated login using your PostgreSQL administrator;
no username or password is assumed. Use a password prompt rather than embedding credentials
in shell history. Development tests require permission to create their own test database.

Example SQL in an administrator `psql` session (adapt names; use `\password gym_erp_user` to
set the secret interactively):

```sql
CREATE ROLE gym_erp_user LOGIN CREATEDB;
CREATE DATABASE gym_erp OWNER gym_erp_user;
```

Production runtime roles must not have CREATEDB or superuser privileges. Provision test
credentials independently; pytest creates and destroys only the configured test database.
Never point POSTGRES_TEST_DB at a database holding real data.

Use the README commands to install, configure, migrate and create an administrator.
Activate the virtual environment if preferred with `.\.venv\Scripts\Activate.ps1`.
Explicit interpreter paths work without changing execution policy.

## Environment

| Variables | Meaning |
|---|---|
| DJANGO_SECRET_KEY | Required unique random secret; production at least 50 characters |
| DJANGO_SETTINGS_MODULE | Settings selection; manage.py defaults to development |
| DJANGO_DEBUG | Development-only boolean; production always false |
| DJANGO_ALLOWED_HOSTS | Comma-separated exact deployment hostnames |
| POSTGRES_DB / USER / PASSWORD / HOST / PORT | Required connection values; port defaults to 5432 |
| POSTGRES_TEST_DB | Disposable pytest database; default test_gym_erp |
| POSTGRES_SSLMODE | libpq TLS policy, default prefer; use verify-full for remote production DB |
| GYM_DISPLAY_NAME | Fallback brand name before a GymProfile exists |
| GYM_DEFAULT_CURRENCY / TIMEZONE | Defaults for new profiles, ETB / Africa/Addis_Ababa |
| GYM_COLOR_PRIMARY / SECONDARY / BACKGROUND / TEXT / ACCENT | Six-digit hex colors |
| DJANGO_CSRF_TRUSTED_ORIGINS | Explicit HTTPS origins required in production |
| DJANGO_TRUST_PROXY | False unless a trusted edge strips and sets forwarded protocol |
| DJANGO_HSTS_SECONDS / INCLUDE_SUBDOMAINS / PRELOAD | Enable only after HTTPS/domain review |

`.env` loads from the project root without overriding process variables. It is ignored by Git.
Generate secrets with Python's `secrets.token_urlsafe(64)` and store them locally, not in docs.
Missing required values raise a configuration error naming the variable without its contents.

## Optional isolated local PostgreSQL

`scripts/local_postgres.py` initializes only a new workspace-local cluster, random credentials,
and new `.env`; it refuses if its cluster or `.env` already exists. Set POSTGRES_BIN to your
installation's binary directory; the helper's Windows default is PostgreSQL 18 under Program Files.
It listens only on 127.0.0.1:55432 with SCRAM authentication. `start` / `stop` control only that cluster.
The helper's bootstrap role is privileged and exclusively for disposable local development.
The existing Windows PostgreSQL service is not modified. Interrupted initialization is retained
for inspection; do not automatically delete directories to recover.

## Tests and diagnostics

Run `python -m pytest --junitxml=artifacts/pytest.xml` using the virtual environment.
Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, and
`python manage.py migrate`. A refused connection means check the PostgreSQL service, port,
database and login; do not paste passwords into diagnostics. The dashboard probe reports a
generic unavailable state. Database failure may still prevent session/authentication loading.
