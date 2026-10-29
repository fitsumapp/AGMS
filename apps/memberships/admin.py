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
    MembershipAssignmentForm,
    MembershipFreezeForm,
    MembershipPackageForm,
    MembershipRenewalForm,
)
from .models import (
    Membership,
    MembershipAdjustment,
    MembershipEvent,
    MembershipFreeze,
    MembershipPackage,
    MembershipRenewal,
    PackageBranch,
)
from .services import (
    activate_membership,
    approve_freeze,
    cancel_membership,
    complete_renewal,
    create_membership,
    reject_freeze,
    request_freeze,
    request_renewal,
)


class PackageBranchInline(TabularInline):
    model = PackageBranch
    extra = 1
    fields = ("branch", "is_enabled")


@admin.register(MembershipPackage)
class MembershipPackageAdmin(ModelAdmin):
    form = MembershipPackageForm
    list_display = (
        "code",
        "name",
        "duration_display",
        "price_display",
        "cross_branch_badge",
        "freeze_badge",
        "is_active",
    )
    list_filter = ("is_active", "cross_branch_enabled", "freeze_allowed", "duration_unit")
    search_fields = ("code", "name", "description")
    inlines = [PackageBranchInline]

    fieldsets = (
        ("Package Identity", {
            "description": "Unique plan code, commercial name, and member-facing description.",
            "fields": (
                ("code", "name"),
                "description",
            ),
        }),
        ("Duration & Pricing", {
            "description": "Subscription length, recurring billing unit, and price in local ETB currency.",
            "fields": (
                ("duration_value", "duration_unit"),
                ("price", "currency"),
            ),
        }),
        ("Access & Freeze Policies", {
            "description": "Multi-branch facility access rules and suspension / freeze allowances.",
            "fields": (
                ("cross_branch_enabled", "freeze_allowed"),
                ("max_freeze_days", "is_active"),
            ),
        }),
    )

    @display(description="Duration")
    def duration_display(self, obj):
        return f"{obj.duration_value} {obj.get_duration_unit_display()}"

    @display(description="Price")
    def price_display(self, obj):
        return f"{obj.price:,.2f} {obj.currency}"

    @display(description="Cross-Branch", boolean=True)
    def cross_branch_badge(self, obj):
        return obj.cross_branch_enabled

    @display(description="Freeze Allowed", boolean=True)
    def freeze_badge(self, obj):
        return obj.freeze_allowed

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.memberships.exists():
            return False
        return super().has_delete_permission(request, obj)


class MembershipRenewalInline(TabularInline):
    model = MembershipRenewal
    extra = 0
    can_delete = False
    readonly_fields = (
        "previous_end_date",
        "new_end_date",
        "price_snapshot",
        "renewal_status",
        "billing_reference",
        "idempotency_key",
        "requested_by",
        "completed_at",
    )

    def has_add_permission(self, request, obj=None):
        return False


class MembershipFreezeInline(TabularInline):
    model = MembershipFreeze
    extra = 0
    can_delete = False
    fields = (
        "requested_start_date",
        "requested_end_date",
        "freeze_days",
        "reason",
        "status",
        "approved_by",
        "approved_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class MembershipAdjustmentInline(TabularInline):
    model = MembershipAdjustment
    extra = 0
    can_delete = False
    fields = ("adjustment_type", "previous_value", "new_value", "reason", "approved_by", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class MembershipEventInline(TabularInline):
    model = MembershipEvent
    extra = 0
    can_delete = False
    fields = ("event_type", "actor", "timestamp", "metadata")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Membership)
class MembershipAdmin(ModelAdmin):
    list_display = (
        "membership_number",
        "member_link",
        "package_name",
        "home_branch",
        "start_date",
        "end_date",
        "status_badge",
        "payment_badge",
        "membership_actions",
    )
    list_filter = (
        "home_branch",
        "status",
        "payment_clearance_status",
        "package",
        "start_date",
        "end_date",
    )
    search_fields = (
        "membership_number",
        "member__first_name",
        "member__middle_name",
        "member__last_name",
        "member__phone",
        "member__member_number",
    )
    date_hierarchy = "start_date"
    ordering = ("-created_at",)
    list_select_related = ("member", "package", "home_branch")

    inlines = [
        MembershipRenewalInline,
        MembershipFreezeInline,
        MembershipAdjustmentInline,
        MembershipEventInline,
    ]

    readonly_fields = (
        "membership_number",
        "member",
        "package",
        "home_branch",
        "start_date",
        "end_date",
        "status",
        "price_snapshot",
        "duration_unit_snapshot",
        "duration_value_snapshot",
        "allowed_visits_snapshot",
        "cross_branch_snapshot",
        "activation_status",
        "payment_clearance_status",
        "activated_at",
        "cancelled_at",
        "cancellation_reason",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        ("Subscription Summary", {
            "description": "Agreement identifiers, assigned member profile, and package tier.",
            "fields": (
                ("membership_number", "home_branch"),
                ("member", "package"),
                ("status", "payment_clearance_status"),
            ),
        }),
        ("Validity & Terms", {
            "description": "Subscription start/end dates, visit allowances, and historical price snapshot.",
            "fields": (
                ("start_date", "end_date"),
                ("price_snapshot", "allowed_visits_snapshot"),
                ("duration_value_snapshot", "duration_unit_snapshot"),
                "cross_branch_snapshot",
            ),
        }),
        ("Lifecycle & Cancellation", {
            "description": "Activation timestamps and cancellation records.",
            "fields": (
                ("activation_status", "activated_at"),
                ("cancelled_at", "cancellation_reason"),
            ),
        }),
        ("Audit Metadata", {
            "classes": ("collapse",),
            "description": "Creation and last modification tracking.",
            "fields": (
                ("created_at", "updated_at"),
                ("created_by", "updated_by"),
            ),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        branches = accessible_branches(request.user)
        return qs.filter(home_branch__in=branches)

    @display(description="Member")
    def member_link(self, obj):
        url = reverse("admin:members_member_change", args=[obj.member.pk])
        return mark_safe(f'<a href="{url}" class="font-medium text-primary-600 hover:underline">{obj.member.member_number} · {obj.member.full_name}</a>')

    @display(description="Package")
    def package_name(self, obj):
        return obj.package.name

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            "ACTIVE": "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
            "PENDING": "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
            "FROZEN": "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
            "EXPIRED": "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
            "CANCELLED": "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return mark_safe(f'<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium {cls}">{obj.get_status_display()}</span>')

    @display(description="Payment")
    def payment_badge(self, obj):
        colors = {
            "CLEARED": "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
            "PENDING": "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
            "WAIVED_COMPLIMENTARY": "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
        }
        cls = colors.get(obj.payment_clearance_status, "bg-gray-100 text-gray-800")
        return mark_safe(f'<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold {cls}">{obj.get_payment_clearance_status_display()}</span>')

    @display(description="Actions")
    def membership_actions(self, obj):
        links = []
        if obj.status == Membership.Status.PENDING:
            act_url = reverse("admin:membership-activate", args=[obj.pk])
            links.append(f'<a href="{act_url}" class="text-xs font-medium text-amber-600 hover:underline me-2">Activate</a>')
        if obj.status in (Membership.Status.ACTIVE, Membership.Status.EXPIRED):
            renew_url = reverse("admin:membership-renew", args=[obj.pk])
            links.append(f'<a href="{renew_url}" class="text-xs font-medium text-primary-600 hover:underline me-2">Renew</a>')
        if obj.status == Membership.Status.ACTIVE and obj.package.freeze_allowed:
            freeze_url = reverse("admin:membership-freeze", args=[obj.pk])
            links.append(f'<a href="{freeze_url}" class="text-xs font-medium text-blue-600 hover:underline me-2">Freeze</a>')
        return mark_safe("".join(links))

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("assign/", self.admin_site.admin_view(self.assign_membership_view), name="membership-assign"),
            path("<uuid:membership_id>/activate/", self.admin_site.admin_view(self.activate_membership_view), name="membership-activate"),
            path("<uuid:membership_id>/renew/", self.admin_site.admin_view(self.renew_membership_view), name="membership-renew"),
            path("<uuid:membership_id>/freeze/", self.admin_site.admin_view(self.freeze_membership_view), name="membership-freeze"),
        ]
        return custom_urls + urls

    def assign_membership_view(self, request):
        if not request.user.is_authenticated or not request.user.is_staff:
            raise PermissionDenied("Authentication required.")
        if has_role(request.user, "Trainer") or has_role(request.user, "POS Staff"):
            raise PermissionDenied("Unauthorized to assign memberships.")

        if request.method == "POST":
            form = MembershipAssignmentForm(request.POST, user=request.user)
            if form.is_valid():
                try:
                    m = create_membership(
                        actor=request.user,
                        member=form.cleaned_data["member"],
                        package=form.cleaned_data["package"],
                        home_branch=form.cleaned_data["home_branch"],
                        start_date=form.cleaned_data["start_date"],
                        is_complimentary=form.cleaned_data.get("is_complimentary", False),
                        complimentary_reason=form.cleaned_data.get("complimentary_reason", ""),
                    )
                    messages.success(request, f"Membership {m.membership_number} successfully registered for {m.member.full_name} [{m.get_status_display()}].")
                    return HttpResponseRedirect(reverse("admin:memberships_membership_change", args=[m.pk]))
                except (ValidationError, PermissionDenied) as exc:
                    messages.error(request, str(exc))
        else:
            form = MembershipAssignmentForm(user=request.user)

        context = {
            **self.admin_site.each_context(request),
            "title": "Assign Membership Package",
            "form": form,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/memberships/assign_membership.html", context)

    def activate_membership_view(self, request, membership_id):
        m = get_object_or_404(Membership, pk=membership_id)
        if not is_owner(request.user) and not can_access_branch(request.user, m.home_branch):
            raise PermissionDenied("Unauthorized for this branch.")

        if request.method == "POST":
            is_comp = request.POST.get("is_complimentary") == "true"
            ref = request.POST.get("payment_reference", "").strip()
            try:
                activate_membership(actor=request.user, membership=m, payment_reference=ref, is_complimentary=is_comp)
                messages.success(request, f"Membership {m.membership_number} activated successfully.")
                return HttpResponseRedirect(reverse("admin:memberships_membership_change", args=[m.pk]))
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, str(exc))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Activate Membership: {m.membership_number}",
            "membership": m,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/memberships/activate_membership.html", context)

    def renew_membership_view(self, request, membership_id):
        m = get_object_or_404(Membership, pk=membership_id)
        if not is_owner(request.user) and not can_access_branch(request.user, m.home_branch):
            raise PermissionDenied("Unauthorized for this branch.")

        if request.method == "POST":
            form = MembershipRenewalForm(request.POST)
            if form.is_valid():
                try:
                    renewal = request_renewal(
                        actor=request.user,
                        membership=m,
                        idempotency_key=form.cleaned_data["idempotency_key"],
                        new_package=form.cleaned_data.get("new_package"),
                        billing_reference=form.cleaned_data.get("billing_reference", ""),
                    )
                    # Complete renewal if actor is authorized manager or owner
                    if is_owner(request.user) or has_role(request.user, "Branch Manager"):
                        complete_renewal(actor=request.user, renewal=renewal)
                        messages.success(request, f"Membership {m.membership_number} renewed successfully until {renewal.new_end_date}.")
                    else:
                        messages.info(request, f"Renewal requested for {m.membership_number}. Awaiting manager clearance.")
                    return HttpResponseRedirect(reverse("admin:memberships_membership_change", args=[m.pk]))
                except (ValidationError, PermissionDenied) as exc:
                    messages.error(request, str(exc))
        else:
            form = MembershipRenewalForm()

        context = {
            **self.admin_site.each_context(request),
            "title": f"Renew Membership: {m.membership_number} ({m.member.full_name})",
            "membership": m,
            "form": form,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/memberships/renew_membership.html", context)

    def freeze_membership_view(self, request, membership_id):
        m = get_object_or_404(Membership, pk=membership_id)
        if not is_owner(request.user) and not can_access_branch(request.user, m.home_branch):
            raise PermissionDenied("Unauthorized for this branch.")

        if request.method == "POST":
            form = MembershipFreezeForm(request.POST)
            if form.is_valid():
                try:
                    freeze = request_freeze(
                        actor=request.user,
                        membership=m,
                        start_date=form.cleaned_data["requested_start_date"],
                        end_date=form.cleaned_data["requested_end_date"],
                        reason=form.cleaned_data["reason"],
                    )
                    # If actor is Owner or Manager, they can immediately approve
                    if (is_owner(request.user) or has_role(request.user, "Branch Manager")) and request.POST.get("immediate_approval") == "true":
                        approve_freeze(actor=request.user, freeze=freeze)
                        messages.success(request, f"Freeze approved for {m.membership_number} ({freeze.freeze_days} days). New expiry: {m.end_date}.")
                    else:
                        messages.info(request, f"Freeze requested ({freeze.freeze_days} days). Pending manager approval.")
                    return HttpResponseRedirect(reverse("admin:memberships_membership_change", args=[m.pk]))
                except (ValidationError, PermissionDenied) as exc:
                    messages.error(request, str(exc))
        else:
            form = MembershipFreezeForm()

        context = {
            **self.admin_site.each_context(request),
            "title": f"Request Freeze: {m.membership_number} ({m.member.full_name})",
            "membership": m,
            "form": form,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/memberships/freeze_membership.html", context)


@admin.register(MembershipFreeze)
class MembershipFreezeAdmin(ModelAdmin):
    list_display = (
        "membership",
        "requested_start_date",
        "requested_end_date",
        "freeze_days",
        "status_badge",
        "requested_by",
        "approved_by",
        "freeze_actions",
    )
    list_filter = ("status", "requested_start_date", "approved_start_date")
    search_fields = ("membership__membership_number", "membership__member__first_name", "reason")
    readonly_fields = (
        "membership",
        "requested_start_date",
        "requested_end_date",
        "freeze_days",
        "reason",
        "requested_by",
        "approved_by",
        "approved_at",
        "rejection_reason",
    )

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            "REQUESTED": "bg-amber-100 text-amber-800",
            "APPROVED": "bg-green-100 text-green-800",
            "REJECTED": "bg-red-100 text-red-800",
            "COMPLETED": "bg-blue-100 text-blue-800",
            "CANCELLED": "bg-gray-100 text-gray-800",
        }
        return mark_safe(f'<span class="px-2 py-0.5 rounded text-xs font-semibold {colors.get(obj.status, "")}">{obj.get_status_display()}</span>')

    @display(description="Actions")
    def freeze_actions(self, obj):
        if obj.status == MembershipFreeze.FreezeStatus.REQUESTED:
            app_url = reverse("admin:membership-freeze-approve", args=[obj.pk])
            return mark_safe(f'<a href="{app_url}" class="text-xs font-medium text-primary-600 hover:underline">Approve / Reject</a>')
        return "-"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("<uuid:freeze_id>/decision/", self.admin_site.admin_view(self.freeze_decision_view), name="membership-freeze-approve"),
        ]
        return custom_urls + urls

    def freeze_decision_view(self, request, freeze_id):
        freeze = get_object_or_404(MembershipFreeze, pk=freeze_id)
        if not is_owner(request.user) and not has_role(request.user, "Branch Manager"):
            raise PermissionDenied("Only a Gym Owner or Branch Manager can approve/reject freeze requests.")

        if request.method == "POST":
            action = request.POST.get("action")
            if action == "approve":
                try:
                    approve_freeze(actor=request.user, freeze=freeze)
                    messages.success(request, f"Freeze approved for {freeze.membership.membership_number}.")
                    return HttpResponseRedirect(reverse("admin:memberships_membershipfreeze_changelist"))
                except (ValidationError, PermissionDenied) as exc:
                    messages.error(request, str(exc))
            elif action == "reject":
                reason = request.POST.get("rejection_reason", "").strip()
                try:
                    reject_freeze(actor=request.user, freeze=freeze, rejection_reason=reason)
                    messages.warning(request, f"Freeze rejected for {freeze.membership.membership_number}.")
                    return HttpResponseRedirect(reverse("admin:memberships_membershipfreeze_changelist"))
                except (ValidationError, PermissionDenied) as exc:
                    messages.error(request, str(exc))

        context = {
            **self.admin_site.each_context(request),
            "title": f"Review Freeze: {freeze.membership.membership_number}",
            "freeze": freeze,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/memberships/approve_freeze.html", context)


@admin.register(MembershipRenewal)
class MembershipRenewalAdmin(ModelAdmin):
    list_display = (
        "membership",
        "previous_end_date",
        "new_end_date",
        "price_snapshot",
        "renewal_status",
        "idempotency_key",
        "requested_by",
        "completed_at",
    )
    list_filter = ("renewal_status", "previous_end_date", "new_end_date")
    search_fields = ("membership__membership_number", "idempotency_key", "billing_reference")
    readonly_fields = [f.name for f in MembershipRenewal._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MembershipEvent)
class MembershipEventAdmin(ModelAdmin):
    list_display = ("membership", "event_type", "actor", "timestamp")
    list_filter = ("event_type", "timestamp")
    search_fields = ("membership__membership_number",)
    readonly_fields = [f.name for f in MembershipEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
