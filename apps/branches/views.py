from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from .services import select_branch


@require_POST
@admin.site.admin_view
def switch_branch(request):
    select_branch(request, request.POST.get("branch", ""))
    return HttpResponseRedirect(reverse("admin:index"))
