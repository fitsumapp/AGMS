# Security baseline

Django provides CSRF protection, password hashing/validation, session authentication, object form
validation and escaping. Password minimum length is 12, with similarity, numeric-only and common
password checks. Account creation through the admin and password changes use Django forms.
The low-level create_user manager hashes passwords but, like Django's manager, does not run
interactive password validators; callers adding future workflows must invoke validation.

User/group administration and all system settings edits require an active superuser, checked
server-side even if a staff account holds all model permissions. Ordinary staff can access
the foundation dashboard. Ownership and restricted role assignment arrive in Phase 02.

Axes stores failure counters in PostgreSQL and locks an IP after five failures for 15 minutes.
Successful logins do not erase failures. Shared NATs can share lockouts; tune this deliberately
for each installation. No untrusted forwarded IP is accepted by default. After introducing a
trusted proxy, test actual client-IP attribution as a deployment requirement.
Use `manage.py axes_reset` only for deliberate administrative recovery. Schedule a reviewed
retention cleanup using the Axes management commands; there is no task scheduler in Phase 01.
Axes' stock admin screens are disabled; all registered administration uses Unfold.

Authentication failure logs contain an event name, not entered usernames/passwords. Settings
administrative logs use object and actor UUIDs. Django's database admin history contains object
representations and is restricted to administrators. Keep operational logs access-controlled and
configure deployment retention; database/server failures may have infrastructure details.
No financial/business audit ledger is implemented yet.

Production enforces HTTPS, secure/HttpOnly session cookies, HttpOnly CSRF cookies, same-site
sessions, a one-hour session lifetime, browser-close expiration, no-sniff, DENY framing and
same-origin referrers. DEBUG is forced false. Hosts/origins and a strong secret are mandatory.
HSTS defaults to zero until HTTPS has been validated. Subdomain inclusion and preload are opt-in.
Proxy trust is also opt-in and requires an edge that strips untrusted headers.

Media has no public Django URL mapping, including in development. The logo endpoint requires
active staff authentication and has private/no-store caching. Image uploads use ImageField
validation and a 2 MB size limit. Keep the media directory private at the web server. The login
screen uses the gym name and built-in symbol; uploaded logos appear only after authentication.
Future member documents require per-document authorization, scanning, and storage policies.

There is no MFA, public signup, password-reset email delivery, external SMS, business audit trail
or production penetration test in Phase 01. Validate TLS, backups and restore, network controls,
dependency updates and server patching before deployment. Never serve this project with runserver
in production. An in-process database outage indicator is not an external uptime monitor.
