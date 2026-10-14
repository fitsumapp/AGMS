from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from apps.core.models import UUIDTimeStampedModel
from apps.core.validators import phone_validator

class UserManager(BaseUserManager):
    use_in_migrations = True
    @classmethod
    def normalize_email(cls, email):
        return super().normalize_email(email.strip()).lower() if email else ""
    def create_user(self, email, password=None, **extra_fields):
        if not email or not email.strip():
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    def create_superuser(self, email, password=None, **extra_fields):
        for field in ("is_staff", "is_superuser", "is_active"):
            extra_fields.setdefault(field, True)
            if extra_fields[field] is not True:
                raise ValueError(f"Superuser must have {field}=True.")
        if not password:
            raise ValueError("Superuser must have a password.")
        return self.create_user(email, password, **extra_fields)
    def get_by_natural_key(self, username):
        return self.get(email__iexact=username.strip())

class User(AbstractBaseUser, PermissionsMixin, UUIDTimeStampedModel):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=25, blank=True, validators=[phone_validator])
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="user_email_case_insensitive_unique")]
    def clean(self):
        super().clean()
        self.email = UserManager.normalize_email(self.email)
    def save(self, *args, **kwargs):
        self.email = UserManager.normalize_email(self.email)
        super().save(*args, **kwargs)
    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    def get_short_name(self):
        return self.first_name or self.email
    def __str__(self):
        return self.email
