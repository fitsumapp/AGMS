"""Verify the running local admin over HTTP without retaining a test account."""

import os
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
import secrets
import sys
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()
from apps.accounts.models import User


class CSRFParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            attributes = dict(attrs)
            if attributes.get("name") == "csrfmiddlewaretoken":
                self.token = attributes.get("value")


base = "http://127.0.0.1:8000"
opener = build_opener(HTTPCookieProcessor(CookieJar()))
email = f"live-check-{uuid4()}@example.test"
password = secrets.token_urlsafe(40)
user = User.objects.create_superuser(email=email, password=password)
try:
    response = opener.open(base + "/admin/", timeout=10)
    login_html = response.read().decode()
    assert response.status == 200 and "/admin/login/" in response.url
    assert "gym management workspace" in login_html.lower()
    parser = CSRFParser()
    parser.feed(login_html)
    assert parser.token
    request = Request(
        base + "/admin/login/?next=/admin/",
        data=urlencode({"username": email, "password": password,
                        "csrfmiddlewaretoken": parser.token, "next": "/admin/"}).encode(),
        headers={"Referer": base + "/admin/login/?next=/admin/"},
    )
    response = opener.open(request, timeout=10)
    dashboard = response.read().decode()
    assert response.status == 200 and response.url.rstrip("/") == base + "/admin"
    assert "Database connected" in dashboard
    assert "Available in a future phase" in dashboard
    print("PASS: running server login and authenticated dashboard returned HTTP 200 with branding, database status and honest empty state.")
finally:
    User.objects.filter(pk=user.pk).delete()
    assert not User.objects.filter(pk=user.pk).exists()
    print("PASS: temporary random-credential superuser removed; no permanent default password created.")
