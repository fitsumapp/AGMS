import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from apps.accounts.models import User

pytestmark = pytest.mark.django_db

def test_user_creation_and_hash(password):
    user = User.objects.create_user("  Example@EXAMPLE.TEST ", password)
    assert user.email == "example@example.test"
    assert user.password != password and user.check_password(password)
    assert user.pk.version == 4
    assert not user.is_staff and not user.is_superuser
    assert user.created_at.tzinfo and user.updated_at.tzinfo

def test_superuser(root):
    assert root.is_active and root.is_superuser and root.is_staff

@pytest.mark.parametrize("flag", ["is_staff", "is_superuser", "is_active"])
def test_invalid_superuser(flag, password):
    with pytest.raises(ValueError):
        User.objects.create_superuser("bad@example.test", password, **{flag: False})

@pytest.mark.parametrize("email", ["", "   ", None])
def test_missing_email(email):
    with pytest.raises(ValueError):
        User.objects.create_user(email)

def test_case_insensitive_uniqueness(root):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email=root.email.upper())])

def test_login(client, root, password):
    response = client.post(reverse("admin:login"), {"username": root.email.upper(), "password": password})
    assert response.status_code == 302
    assert client.get(reverse("admin:index")).status_code == 200

def test_invalid_login(client, root):
    assert client.post(reverse("admin:login"), {"username": root.email, "password": "incorrect"}).status_code == 200
    assert "_auth_user_id" not in client.session

def test_inactive_login(client, root, password):
    root.is_active = False
    root.save()
    client.post(reverse("admin:login"), {"username": root.email, "password": password})
    assert "_auth_user_id" not in client.session

def test_password_change(client, root, password):
    client.force_login(root)
    response = client.post(reverse("admin:password_change"), {"old_password": password,
        "new_password1": "Changed-Strong-Passphrase-938!", "new_password2": "Changed-Strong-Passphrase-938!"})
    assert response.status_code == 302
    root.refresh_from_db()
    assert root.check_password("Changed-Strong-Passphrase-938!")
    assert not root.check_password(password)

def test_login_throttling(client, root, password):
    for _ in range(5):
        response = client.post(reverse("admin:login"), {"username": root.email, "password": "incorrect"})
    assert response.status_code == 429
    assert client.post(reverse("admin:login"), {"username": root.email, "password": password}).status_code == 429
    assert "_auth_user_id" not in client.session

def test_logout_requires_post(client, root):
    client.force_login(root)
    assert client.get(reverse("admin:logout")).status_code == 405
    assert client.post(reverse("admin:logout")).status_code == 200
    assert "_auth_user_id" not in client.session
