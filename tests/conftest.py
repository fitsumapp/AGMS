import pytest
from apps.accounts.models import User

@pytest.fixture
def password():
    return "Strong-Test-Passphrase-927!"

@pytest.fixture
def root(db, password):
    return User.objects.create_superuser("admin@example.test", password)

@pytest.fixture
def staff(db, password):
    return User.objects.create_user("staff@example.test", password, is_staff=True)
