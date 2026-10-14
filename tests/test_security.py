import importlib
from decimal import Decimal
import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import Client
from django.urls import reverse
from config.env import required, boolean
from apps.core.utils import format_money
from apps.core.validators import validate_timezone

@pytest.mark.django_db
def test_csrf(client, root):
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(root)
    assert strict.post(reverse("admin:core_gymprofile_add"), {"name": "Bad request"}).status_code == 403
    assert strict.post(reverse("admin:logout")).status_code == 403

def test_missing_environment(monkeypatch):
    monkeypatch.delenv("REQUIRED_TEST_VALUE", raising=False)
    with pytest.raises(ImproperlyConfigured, match="REQUIRED_TEST_VALUE"):
        required("REQUIRED_TEST_VALUE")

def test_invalid_boolean(monkeypatch):
    monkeypatch.setenv("BOOLEAN_TEST", "maybe")
    with pytest.raises(ImproperlyConfigured):
        boolean("BOOLEAN_TEST")

def test_production_settings(monkeypatch):
    from config.settings import base
    monkeypatch.setattr(base, "SECRET_KEY", "test-only-" + "1234567890abcdefghij" * 4)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "gym.example.test")
    monkeypatch.setenv("DJANGO_CSRF_TRUSTED_ORIGINS", "https://gym.example.test")
    monkeypatch.setenv("DJANGO_TRUST_PROXY", "False")
    module = importlib.import_module("config.settings.production")
    assert module.DEBUG is False
    assert module.SECURE_SSL_REDIRECT and module.SESSION_COOKIE_SECURE and module.CSRF_COOKIE_SECURE
    assert module.SESSION_COOKIE_HTTPONLY
    assert not hasattr(module, "SECURE_PROXY_SSL_HEADER")
    assert module.SECURE_HSTS_SECONDS == 0

def test_money_and_timezone():
    assert format_money(Decimal("1234.565")) == "1,234.57 ETB"
    with pytest.raises(ValueError):
        format_money(1.5)
    validate_timezone("Africa/Addis_Ababa")
    with pytest.raises(ValidationError):
        validate_timezone("Invalid/Timezone")
