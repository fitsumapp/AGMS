# Deployment overview

Provision each gym independently: domain/TLS, application service, PostgreSQL database/login,
private media storage, secrets, backup storage and monitoring. Reuse the code and lockfile, not
customer credentials or data. Select `config.settings.production` for WSGI/ASGI.

1. Provision a least-privilege runtime database role; run schema migrations with a separate role
   where operationally appropriate. Configure encrypted DB transport for remote connections.
2. Inject environment secrets, explicit allowed hosts and HTTPS CSRF origins. Keep DEBUG false.
3. Install the lockfile into an isolated environment. Use an operator-selected supported WSGI/ASGI
   service behind TLS termination. No production server or platform is assumed by this repository.
4. Back up the existing customer database before applying upgrades. Run checks, migrations and
   `manage.py collectstatic --noinput`. Serve STATIC_ROOT as public static assets, never MEDIA_ROOT.
   After Phase 02 migrations, run `manage.py seed_business_roles` to install the six scoped
   business Groups. The command is idempotent and grants no technical User/Group admin rights.
5. Create the first superuser interactively, then create the gym profile and policy record.
6. Validate HTTPS and redirect handling. Only trust forwarded protocol when the proxy sanitizes it.
7. Enable HSTS after HTTPS validation; includeSubDomains/preload require control of affected domains.
8. Test login throttling with the real proxy, session expiry, private media, monitoring and restore.

`scripts/check_deployment.py` runs Django's deploy check with generated validation-only secret and
example domain plus fully enabled HSTS. Its pass validates configuration, not live TLS or proxy
behavior. The production default HSTS=0 intentionally produces warnings until rollout is verified.

Backups should use PostgreSQL-supported tools and encrypted per-customer storage. Set retention,
access control and restore tests. No automated backup or destructive restore script is provided.
Do not recreate an existing customer database to apply migrations.
