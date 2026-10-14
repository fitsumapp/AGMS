"""Check the production configuration using ephemeral validation-only values."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

env = os.environ.copy()
env.update(DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_ALLOWED_HOSTS="gym.example.test",
    DJANGO_CSRF_TRUSTED_ORIGINS="https://gym.example.test", DJANGO_DEBUG="False",
    DJANGO_HSTS_SECONDS="31536000", DJANGO_HSTS_INCLUDE_SUBDOMAINS="True",
    DJANGO_HSTS_PRELOAD="True", DJANGO_TRUST_PROXY="False")
raise SystemExit(subprocess.call([sys.executable, "manage.py", "check", "--deploy",
    "--settings=config.settings.production", "--fail-level=WARNING"],
    cwd=Path(__file__).resolve().parent.parent, env=env))
