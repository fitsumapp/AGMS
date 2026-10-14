from django.http import FileResponse, Http404
from .models import GymProfile

def logo(request):
    profile = GymProfile.objects.first()
    if not profile or not profile.logo:
        raise Http404
    try:
        response = FileResponse(profile.logo.open("rb"))
    except FileNotFoundError:
        raise Http404
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
