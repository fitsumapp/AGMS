import os
from django.core.exceptions import ImproperlyConfigured


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Required environment variable {name} is missing.")
    return value


def boolean(name, default=False):
    value = os.environ.get(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ImproperlyConfigured(f"{name} must be true or false.")
    return value in {"true", "1"}


def csv(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]
