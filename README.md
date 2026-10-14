# Gym Management ERP — Phase 01

A Django Unfold administration foundation for independently deployed Ethiopian gyms.
Each customer owns a separate deployment, domain, PostgreSQL database, secrets and backup storage.
There are no tenants, shared customer databases or SaaS billing. Branch support belongs to Phase 02.

Stack: Python 3.12, Django 5.2 LTS, PostgreSQL, Django Unfold, django-axes and pytest.
Exact verified dependency versions are in `requirements/lock.txt`.

## Start developing (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements/lock.txt
Copy-Item .env.example .env # Only when .env does not already exist
# Edit .env: supply a random secret and credentials for your own PostgreSQL database.
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver
```

Open http://127.0.0.1:8000/admin/. Create one Gym Profile under System Settings and
one policy record under Access Policies. The dashboard works before either exists.
Only superusers may administer identities or settings in Phase 01.

For this workspace, an ignored `.env` and isolated local database may already exist;
do not overwrite them. The local helper supports `start` and `stop`.
No persistent administrator password is seeded.

```powershell
.\.venv\Scripts\python scripts/local_postgres.py start
.\.venv\Scripts\python manage.py check
.\.venv\Scripts\python manage.py makemigrations --check --dry-run
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python scripts/check_deployment.py
```

Read [setup](docs/DEVELOPMENT_SETUP.md), [architecture](docs/ARCHITECTURE.md),
[security](docs/SECURITY.md), [deployment](docs/DEPLOYMENT_OVERVIEW.md),
[roadmap](docs/ROADMAP.md), and [actual Phase 01 results](PHASE_01_REPORT.md).
