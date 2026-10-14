"""Render admin HTML into ignored artifacts; roll back the temporary verification user."""
import os
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.testing")
import django
django.setup()
from django.db import transaction
from django.test import Client
from django.urls import reverse
from apps.accounts.models import User

artifacts = ROOT / "artifacts"
artifacts.mkdir(exist_ok=True)
with transaction.atomic():
    client = Client()
    login = client.get(reverse("admin:login"))
    assert login.status_code == 200
    (artifacts / "admin-login.html").write_bytes(login.content)
    user = User.objects.create_superuser(f"verification-{uuid.uuid4()}@example.test", secrets.token_urlsafe(32))
    client.force_login(user)
    dashboard = client.get(reverse("admin:index"))
    assert dashboard.status_code == 200
    assert b"Database connected" in dashboard.content
    (artifacts / "admin-dashboard.html").write_bytes(dashboard.content)
    transaction.set_rollback(True)
print("PASS: /admin/login/ and /admin/ rendered HTTP 200; HTML in artifacts; temporary user rolled back.")
