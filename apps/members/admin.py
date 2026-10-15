from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import display

from apps.accounts.roles import has_role, is_owner
from apps.branches.services import accessible_branches, can_access_branch, scoped_queryset
from .forms import (
    MemberChangeForm,
    MemberRegistrationForm,
    MemberStatusChangeForm,
    MemberTransferBranchForm,
)
from .models import (
    Member,
    MemberDocument,
    MemberEmergencyContact,
    MemberNote,
    MemberStatusHistory,
)
from .services import (
    archive_member,
    change_member_status,
    register_member,
    transfer_home_branch,
)


class MemberEmergencyContactInline(TabularInline):
    model = MemberEmergencyContact
    extra = 1
    fields = ("full_name", "relationship", "phone", "alternative_phone", "is_primary")


class MemberDocumentInline(TabularInline):
    model = MemberDocument
    extra = 0
    fields = ("document_type", "document_title", "file", "download_link", "uploaded_at", "uploaded_by")
    readonly_fields = ("download_link", "uploaded_at", "uploaded_by")

    def download_link(self, obj):
        if obj.pk and obj.file:
            url = reverse("member-document-download", args=[obj.pk])
            return format_html(
                '<a href="{}" class="font-semibold text-primary-600 hover:underline dark:text-primary-400" target="_blank">'
                '<span class="material-symbols-outlined align-middle text-sm">download</span> Download</a>',
                url,
            )
        return "-"
    download_link.short_description = "Secure Download"

    def has_view_permission(self, request, obj=None):
        if (has_role(request.user, "Accountant") or has_role(request.user, "Trainer") or has_role(request.user, "POS Staff")) and not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
            return False
        return super().has_view_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        if has_role(request.user, "Accountant") or has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
            return False
        return super().has_add_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.uploaded_by_id:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)


class MemberNoteInline(StackedInline):
    model = MemberNote
    extra = 0
    fields = ("category", "note", "created_by", "created_at")
    readonly_fields = ("created_by", "created_at")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Role-based visibility: Receptionists and Accountants cannot view Sensitive / Restricted notes
        if has_role(request.user, "Receptionist") or has_role(request.user, "Accountant"):
            if not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
                return qs.filter(category=MemberNote.NoteCategory.GENERAL)
        return qs

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "category":
            if (has_role(request.user, "Receptionist") or has_role(request.user, "Accountant")) and not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
                kwargs["choices"] = [(MemberNote.NoteCategory.GENERAL, "General / Public")]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def has_add_permission(self, request, obj=None):
        if has_role(request.user, "Accountant") or has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
            return False
        return super().has_add_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class MemberStatusHistoryInline(TabularInline):
    model = MemberStatusHistory
    extra = 0
    can_delete = False
    fields = ("previous_status", "new_status", "reason", "changed_by", "changed_at")
    readonly_fields = ("previous_status", "new_status", "reason", "changed_by", "changed_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Member)
class MemberAdmin(ModelAdmin):
    list_display = (
        "photo_thumbnail",
        "member_number",
        "member_full_name",
        "phone",
        "home_branch",
        "status_badge",
        "joined_at",
        "member_actions",
    )
    list_filter = ("home_branch", "status", "registration_source", "gender", "joined_at")
    search_fields = ("member_number", "first_name", "middle_name", "last_name", "phone", "email")
    date_hierarchy = "joined_at"
    ordering = ("-joined_at",)
    list_select_related = ("home_branch", "created_by", "updated_by")

    inlines = [
        MemberEmergencyContactInline,
        MemberDocumentInline,
        MemberNoteInline,
        MemberStatusHistoryInline,
    ]

    fieldsets = (
        ("Personal Information", {
            "fields": (
                "photo",
                ("first_name", "middle_name", "last_name"),
                ("gender", "date_of_birth"),
            )
        }),
        ("Contact Information", {
            "fields": (
                ("phone", "alternative_phone"),
                "email",
                ("address", "city"),
            )
        }),
        ("Gym Registration", {
            "fields": (
                "member_number",
                "home_branch",
                "status",
                "joined_at",
                ("registration_source", "referral_source"),
            )
        }),
    )

    readonly_fields = ("member_number", "joined_at")

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = MemberRegistrationForm
            form = super().get_form(request, obj, **kwargs)
            form.request = request
            return form
        kwargs["form"] = MemberChangeForm
        return super().get_form(request, obj, **kwargs)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return scoped_queryset(qs, request.user, branch_field="home_branch")

    @display(description="Photo")
    def photo_thumbnail(self, obj):
        if obj.photo:
            url = reverse("member-photo-view", args=[obj.pk])
            return format_html(
                '<img src="{}" alt="{}" class="w-9 h-9 rounded-full object-cover border border-base-200 shadow-xs" />',
                url,
                obj.full_name,
            )
        return format_html(
            '<div class="w-9 h-9 rounded-full bg-primary-100 text-primary-700 flex items-center justify-center font-bold text-xs">'
            '{}</div>',
            obj.first_name[:1].upper() if obj.first_name else "M",
        )

    @display(description="Full Name", ordering="last_name")
    def member_full_name(self, obj):
        return obj.full_name

    @display(description="Status", ordering="status")
    def status_badge(self, obj):
        colors = {
            Member.MemberStatus.ACTIVE: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
            Member.MemberStatus.INACTIVE: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
            Member.MemberStatus.SUSPENDED: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
            Member.MemberStatus.ARCHIVED: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html(
            '<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold {}">{}</span>',
            cls,
            obj.get_status_display(),
        )

    @display(description="Actions")
    def member_actions(self, obj):
        links = []
        transfer_url = reverse("admin:member-transfer-branch", args=[obj.pk])
        status_url = reverse("admin:member-change-status", args=[obj.pk])
        links.append(f'<a href="{status_url}" class="text-xs font-medium text-primary-600 hover:underline me-2">Status</a>')
        links.append(f'<a href="{transfer_url}" class="text-xs font-medium text-primary-600 hover:underline">Transfer</a>')
        return mark_safe("".join(links))

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("<uuid:member_id>/transfer/", self.admin_site.admin_view(self.transfer_branch_view), name="member-transfer-branch"),
            path("<uuid:member_id>/status/", self.admin_site.admin_view(self.change_status_view), name="member-change-status"),
        ]
        return custom_urls + urls

    def transfer_branch_view(self, request, member_id):
        member = get_object_or_404(Member, pk=member_id)
        if not is_owner(request.user) and not can_access_branch(request.user, member.home_branch):
            raise PermissionDenied("You cannot transfer members from this branch.")

        if not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
            raise PermissionDenied("Only Gym Owners and Branch Managers can transfer member home branches.")

        if request.method == "POST":
            form = MemberTransferBranchForm(request.POST, user=request.user, current_branch=member.home_branch)
            if form.is_valid():
                try:
                    transfer_home_branch(
                        actor=request.user,
                        member=member,
                        new_branch=form.cleaned_data["new_branch"],
                        reason=form.cleaned_data["reason"],
                    )
                    messages.success(request, f"Member {member.member_number} transferred successfully to {member.home_branch.code}.")
                    return HttpResponseRedirect(reverse("admin:members_member_change", args=[member.pk]))
                except (ValidationError, PermissionDenied) as exc:
                    messages.error(request, str(exc))
        else:
            form = MemberTransferBranchForm(user=request.user, current_branch=member.home_branch)

        context = {
            **self.admin_site.each_context(request),
            "title": f"Transfer Home Branch: {member.member_number} ({member.full_name})",
            "member": member,
            "form": form,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/members/transfer_branch.html", context)

    def change_status_view(self, request, member_id):
        member = get_object_or_404(Member, pk=member_id)
        if not is_owner(request.user) and not can_access_branch(request.user, member.home_branch):
            raise PermissionDenied("You cannot change member status for this branch.")

        if request.method == "POST":
            form = MemberStatusChangeForm(request.POST, user=request.user)
            if form.is_valid():
                try:
                    change_member_status(
                        actor=request.user,
                        member=member,
                        new_status=form.cleaned_data["new_status"],
                        reason=form.cleaned_data["reason"],
                    )
                    messages.success(request, f"Status for {member.member_number} updated to {member.get_status_display()}.")
                    return HttpResponseRedirect(reverse("admin:members_member_change", args=[member.pk]))
                except (ValidationError, PermissionDenied) as exc:
                    messages.error(request, str(exc))
        else:
            form = MemberStatusChangeForm(initial={"new_status": member.status}, user=request.user)

        context = {
            **self.admin_site.each_context(request),
            "title": f"Change Member Status: {member.member_number} ({member.full_name})",
            "member": member,
            "form": form,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/members/change_status.html", context)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def has_module_permission(self, request):
        if has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
            if not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
                return False
        return super().has_module_permission(request)

    def has_add_permission(self, request):
        if has_role(request.user, "Accountant") or has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
            if not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
                return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if has_role(request.user, "Accountant") or has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
            if not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
                return False
        if obj and not is_owner(request.user):
            return can_access_branch(request.user, obj.home_branch)
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Ordinary staff cannot hard-delete members. Archive workflow must be used.
        if not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)
