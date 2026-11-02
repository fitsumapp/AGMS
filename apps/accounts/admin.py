from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm
from apps.core.admin import SuperuserOnlyMixin
from .forms import AccountChangeForm, AccountCreationForm, StaffCreationForm, StaffChangeForm, ASSIGNABLE_ROLES
from .models import User, StaffAccount
from .roles import is_owner
from apps.branches.audit import record_event
from django.core.exceptions import PermissionDenied

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
        ("Account Credentials / የተጠቃሚ ምስክር ወረቀት", {
            "classes": ("collapse",),
            "fields": (
                ("email", "password"),
            ),
        }),
        ("Personal Information / የግል መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("first_name", "last_name"),
                "phone",
            ),
        }),
        ("Permissions & Roles / ፈቃዶች እና ሚናዎች", {
            "classes": ("collapse",),
            "fields": (
                ("is_active", "is_staff", "is_superuser"),
                "groups",
                "user_permissions",
            ),
        }),
        ("Activity Dates / የቀን መረጃዎች", {
            "classes": ("collapse",),
            "fields": (
                ("last_login", "date_joined"),
                ("created_at", "updated_at"),
            ),
        }),
    )
    add_fieldsets = (
        ("Account Creation / አዲስ ተጠቃሚ መፍጠር", {
            "classes": ("collapse",),
            "fields": (
                "email",
                ("first_name", "last_name"),
                ("password1", "password2"),
            ),
        }),
    )

admin.site.unregister(Group)
@admin.register(Group)
class GroupAdmin(SuperuserOnlyMixin, BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(StaffAccount)
class StaffAccountAdmin(ModelAdmin):
    form = StaffChangeForm
    add_form = StaffCreationForm
    list_display = ("email", "first_name", "last_name", "is_active", "is_staff")
    search_fields = ("email", "first_name", "last_name")
    list_filter = ("is_active",)
    ordering = ("email",)
    fieldsets = (
        ("Staff Identity & Role / የሰራተኛ መለያ እና ሚና", {
            "classes": ("collapse",),
            "fields": (
                "email",
                ("first_name", "last_name"),
                ("phone", "role"),
                "is_active",
            ),
        }),
    )
    add_fieldsets = (
        ("New Staff Account / አዲስ የሰራተኛ አካውንት", {
            "classes": ("collapse",),
            "fields": (
                "email",
                ("first_name", "last_name"),
                ("phone", "role"),
                ("password1", "password2"),
            ),
        }),
    )
    change_form_template = "admin/change_form.html"

    def get_fieldsets(self, request, obj=None):
        return self.add_fieldsets if obj is None else self.fieldsets

    def get_form(self, request, obj=None, **kwargs):
        kwargs["form"] = StaffCreationForm if obj is None else StaffChangeForm
        return super().get_form(request, obj, **kwargs)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset.filter(is_superuser=False)
        return queryset.filter(is_superuser=False).exclude(groups__name="Gym Owner").exclude(pk=request.user.pk)

    def has_module_permission(self, request):
        return is_owner(request.user)

    def has_view_permission(self, request, obj=None):
        return is_owner(request.user) and (obj is None or self.get_queryset(request).filter(pk=obj.pk).exists())

    def has_add_permission(self, request):
        return is_owner(request.user)

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        role = form.cleaned_data["role"]
        if (not is_owner(request.user) or role.name not in ASSIGNABLE_ROLES or
            (change and not self.has_change_permission(request, obj))):
            raise PermissionDenied
        obj.is_staff = True
        obj.is_superuser = False
        super().save_model(request, obj, form, change)
        obj.groups.set([role])
        record_event(request.user, "business_role_assigned", obj, {"role": role.name})
