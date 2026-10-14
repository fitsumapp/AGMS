import logging
from django.conf import settings
from django.db import connection, DatabaseError
from django.urls import reverse
from config.branding import PALETTE
from .models import GymProfile
from .utils import local_date

logger = logging.getLogger("gym.status")

def profile_for_request(request):
    if not hasattr(request, "_gym_profile"):
        try:
            request._gym_profile = GymProfile.objects.first()
        except DatabaseError:
            logger.error("gym_profile_unavailable: check database connectivity and migrations")
            request._gym_profile = None
    return request._gym_profile

def site_name(request):
    profile = profile_for_request(request)
    return profile.name if profile else settings.GYM_DISPLAY_NAME

def site_logo(request):
    profile = profile_for_request(request)
    return reverse("gym-logo") if request.user.is_authenticated and request.user.is_staff and profile and profile.logo else None

def database_available():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        logger.error("database_unavailable: verify service, environment and connectivity")
        return False

def dashboard(request, context):
    profile = profile_for_request(request)
    zone = profile.default_timezone if profile else settings.GYM_DEFAULT_TIMEZONE
    context.update(gym_name=site_name(request), gym_timezone=zone, current_date=local_date(zone),
        application_version=settings.APP_VERSION, database_online=database_available(), brand=PALETTE,
        foundation_cards=[("Application", "Foundation ready", "Version " + settings.APP_VERSION),
            ("Business modules", "Coming next", "Available in a future phase"),
            ("Installation", "Dedicated deployment", "Independent gym configuration")])
    return context
