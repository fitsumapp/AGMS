from .base import *
DEBUG = boolean("DJANGO_DEBUG", True)
ALLOWED_HOSTS = csv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
