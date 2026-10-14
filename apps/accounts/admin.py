from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm
from apps.core.admin import SuperuserOnlyMixin
from .forms import AccountChangeForm, AccountCreationForm
from .models import User

@admin.register(User)
class UserAdmin(SuperuserOnlyMixin, BaseUserAdmin, ModelAdmin):
    form = AccountChangeForm
    add_form = AccountCreationForm
    change_password_form = AdminPasswordChangeForm
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name")
    readonly_fields = ("last_login", "date_joined", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal information", {"fields": ("first_name", "last_name", "phone")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name", "password1", "password2")}),)

admin.site.unregister(Group)
@admin.register(Group)
class GroupAdmin(SuperuserOnlyMixin, BaseGroupAdmin, ModelAdmin):
    pass
