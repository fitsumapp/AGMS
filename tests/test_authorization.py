import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from apps.accounts.models import User

pytestmark = pytest.mark.django_db

def test_anonymous(client):
    assert client.get(reverse("admin:index")).status_code == 302

def test_ordinary_user(client):
    user = User.objects.create_user("ordinary@example.test")
    client.force_login(user)
    assert client.get(reverse("admin:index")).status_code == 302

def test_staff_dashboard(client, staff):
    client.force_login(staff)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    assert reverse("admin:accounts_user_changelist").encode() not in response.content

@pytest.mark.parametrize("model", ["accounts_user", "auth_group", "core_gymprofile", "core_gymsettings"])
@pytest.mark.parametrize("action", ["changelist", "add"])
def test_staff_cannot_manage_even_with_permissions(client, staff, model, action):
    staff.user_permissions.set(Permission.objects.all())
    client.force_login(staff)
    url = reverse(f"admin:{model}_{action}")
    assert client.get(url).status_code == 403
    assert client.post(url, {}).status_code == 403

def test_staff_cannot_change_other_password(client, staff, root):
    staff.user_permissions.set(Permission.objects.all())
    client.force_login(staff)
    assert client.post(reverse("admin:auth_user_password_change", args=[root.pk]), {}).status_code == 403

@pytest.mark.parametrize("name", ["accounts_user", "auth_group", "core_gymprofile", "core_gymsettings"])
def test_superuser_pages(client, root, name):
    client.force_login(root)
    assert client.get(reverse(f"admin:{name}_changelist")).status_code == 200

def test_user_creation_form(client, root, password):
    client.force_login(root)
    url = reverse("admin:accounts_user_add")
    assert client.get(url).status_code == 200
    response = client.post(url, {"email": "New@Example.test", "password1": password,
        "password2": password, "first_name": "New", "last_name": "User", "_save": "Save"})
    assert response.status_code == 302
    assert User.objects.get(email="new@example.test").check_password(password)
