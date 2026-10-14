from django.core.exceptions import ImproperlyConfigured
from .base import *
DEBUG = False
ALLOWED_HOSTS = csv("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = csv("DJANGO_CSRF_TRUSTED_ORIGINS")
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Production requires explicit DJANGO_ALLOWED_HOSTS.")
if not CSRF_TRUSTED_ORIGINS or any(not origin.startswith("https://") or "*" in origin for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("Production requires explicit HTTPS DJANGO_CSRF_TRUSTED_ORIGINS.")
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("replace-"):
    raise ImproperlyConfigured("Production requires a strong DJANGO_SECRET_KEY of at least 50 characters.")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = boolean("DJANGO_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = boolean("DJANGO_HSTS_PRELOAD")
if boolean("DJANGO_TRUST_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
