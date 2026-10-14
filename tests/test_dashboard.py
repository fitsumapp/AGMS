from unittest.mock import patch
import pytest
from django.db import OperationalError
from django.urls import reverse
from apps.core.models import GymProfile
from apps.core.dashboard import database_available

pytestmark = pytest.mark.django_db

def test_empty_dashboard(client, root, settings):
    settings.GYM_DISPLAY_NAME = "Addis Fitness"
    client.force_login(root)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    html = response.content.decode()
    for expected in ["Addis Fitness", "Africa/Addis_Ababa", "Database connected", "Available in a future phase", "0.1.0"]:
        assert expected in html
    assert "Monthly revenue" not in html and "Active members" not in html

def test_database_profile_branding(client, root):
    GymProfile.objects.create(name="Bole Fitness")
    client.force_login(root)
    assert b"Bole Fitness" in client.get(reverse("admin:index")).content
    client.logout()
    assert b"Bole Fitness" in client.get(reverse("admin:login")).content

def test_connectivity_failure_safe(caplog):
    with patch("apps.core.dashboard.connection.cursor", side_effect=OperationalError("secret_password")):
        assert not database_available()
    assert "secret_password" not in caplog.text

def test_private_media_not_public(client):
    assert client.get("/private-media/branding/example.png").status_code == 404
    assert client.get(reverse("gym-logo")).status_code == 302
