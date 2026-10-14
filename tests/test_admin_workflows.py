from io import BytesIO
import pytest
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import Permission
from django.urls import reverse
from apps.accounts.models import User
from apps.core.models import GymProfile, GymSettings

pytestmark = pytest.mark.django_db

@pytest.mark.parametrize("model", ["accounts_user", "auth_group", "core_gymprofile", "core_gymsettings"])
@pytest.mark.parametrize("action", ["change", "delete"])
def test_direct_object_mutation_denied(client, staff, root, model, action):
    from django.contrib.auth.models import Group
    profile = GymProfile.objects.create(name="Protected")
    objects = {"accounts_user": root, "auth_group": Group.objects.create(name="Protected"),
        "core_gymprofile": profile, "core_gymsettings": GymSettings.objects.create(profile=profile)}
    staff.user_permissions.set(Permission.objects.all())
    client.force_login(staff)
    url = reverse(f"admin:{model}_{action}", args=[objects[model].pk])
    assert client.post(url, {"post": "yes", "name": "Changed"}).status_code == 403
    assert type(objects[model]).objects.filter(pk=objects[model].pk).exists()

def test_weak_password_rejected(client, root):
    client.force_login(root)
    response = client.post(reverse("admin:accounts_user_add"), {"email": "weak@example.test", "password1": "123", "password2": "123"})
    assert response.status_code == 200
    assert not User.objects.filter(email="weak@example.test").exists()

def test_logo_access_and_upload(client, root, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    image = BytesIO()
    Image.new("RGB", (8, 8), "teal").save(image, format="PNG")
    profile = GymProfile.objects.create(name="Logo gym", logo=SimpleUploadedFile("logo.png", image.getvalue(), content_type="image/png"))
    assert client.get(reverse("gym-logo")).status_code == 302
    client.force_login(root)
    response = client.get(reverse("gym-logo"))
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == image.getvalue()
    directives = {item.strip() for item in response["Cache-Control"].split(",")}
    assert {"private", "no-store", "no-cache"} <= directives
    assert client.get(profile.logo.url).status_code == 404

def test_admin_edit_forms_render(client, root):
    profile = GymProfile.objects.create(name="Render test")
    policy = GymSettings.objects.create(profile=profile)
    client.force_login(root)
    for name, obj in [("accounts_user", root), ("core_gymprofile", profile), ("core_gymsettings", policy)]:
        assert client.get(reverse(f"admin:{name}_change", args=[obj.pk])).status_code == 200

def test_login_page_security_headers(client):
    response = client.get(reverse("admin:login"))
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert b"csrfmiddlewaretoken" in response.content
    assert b"gym management workspace" in response.content
