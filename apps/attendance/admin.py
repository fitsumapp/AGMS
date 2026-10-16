from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import display

from apps.accounts.roles import is_owner
from apps.branches.services import accessible_branches, active_branch
from apps.members.models import Member
from .credentials import generate_qr_code_base64, issue_member_credential, lookup_credential, reissue_member_credential, revoke_credential
from .forms import AttendanceReportFilterForm, ManualCheckinForm, ManualOverrideForm, QRCheckinForm
from .models import AccessAttempt, AccessDevice, Attendance, AttendanceOverride, MemberCredential
from .permissions import can_checkin_members, can_manage_access_devices, can_override_checkin, can_view_attendance_reports
from .reports import generate_attendance_csv, generate_attendance_pdf
from .selectors import get_attendance_metrics, get_currently_inside
from .services import process_check_in, process_check_out, record_manual_override


@admin.register(Attendance)
class AttendanceAdmin(ModelAdmin):
    list_display = (
        "member_link",
        "branch_badge",
        "check_in_time",
        "check_out_time",
        "duration_display",
        "method_badge",
        "status_badge",
        "attendance_actions",
    )
    list_filter = ("status", "is_estimated_duration", "check_in_method", "branch", "check_in_datetime")
    search_fields = (
        "member__member_number",
        "member__first_name",
        "member__last_name",
        "member__phone",
        "notes",
    )
    readonly_fields = (
        "member",
        "membership",
        "branch",
        "check_in_datetime",
        "check_out_datetime",
        "check_in_method",
        "check_out_method",
        "recorded_by",
        "access_device",
        "status",
        "notes",
        "created_at",
        "updated_at",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "member", "branch", "membership", "membership__package", "recorded_by"
        )
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        return qs.filter(branch__in=accessible_branches(request.user))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("checkin/", self.admin_site.admin_view(self.qr_checkin_view), name="attendance_qr_checkin"),
            path("manual-checkin/", self.admin_site.admin_view(self.manual_checkin_view), name="attendance_manual_checkin"),
            path("dashboard/", self.admin_site.admin_view(self.attendance_dashboard_view), name="attendance_dashboard"),
            path("reports/", self.admin_site.admin_view(self.attendance_reports_view), name="attendance_reports"),
            path("<uuid:attendance_id>/checkout/", self.admin_site.admin_view(self.checkout_action_view), name="attendance_checkout_action"),
        ]
        return custom + urls

    @display(description="Member")
    def member_link(self, obj):
        url = reverse("admin:members_member_change", args=[obj.member.pk])
        return format_html(
            "<a href='{}' class='text-teal-600 font-semibold hover:underline'>{}<br><small class='text-gray-500'>{}</small></a>",
            url,
            obj.member.full_name,
            obj.member.member_number,
        )

    @display(description="Branch")
    def branch_badge(self, obj):
        return format_html(
            "<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800'>{}</span>",
            obj.branch.code,
        )

    @display(description="Check-In")
    def check_in_time(self, obj):
        tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
        try:
            tz = ZoneInfo(tz_name)
            t = obj.check_in_datetime.astimezone(tz)
        except Exception:
            t = obj.check_in_datetime
        return t.strftime("%b %d, %H:%M")

    @display(description="Check-Out")
    def check_out_time(self, obj):
        if not obj.check_out_datetime:
            return format_html("<span class='text-amber-600 font-medium italic'>Inside</span>")
        tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
        try:
            tz = ZoneInfo(tz_name)
            t = obj.check_out_datetime.astimezone(tz)
        except Exception:
            t = obj.check_out_datetime
        return t.strftime("%H:%M")

    @display(description="Duration")
    def duration_display(self, obj):
        mins = obj.duration_minutes
        if mins is None:
            return "-"
        h = mins // 60
        m = mins % 60
        text = f"{h}h {m}m" if h > 0 else f"{m}m"
        if obj.is_estimated_duration:
            return format_html("<span class='text-amber-700 italic font-medium' title='Unconfirmed departure - estimated duration'>{} (Est.)</span>", text)
        return text

    @display(description="Method")
    def method_badge(self, obj):
        return format_html(
            "<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-teal-50 text-teal-700'>{}</span>",
            obj.get_check_in_method_display(),
        )

    @display(description="Status")
    def status_badge(self, obj):
        if obj.status == Attendance.Status.PRESENT:
            return format_html("<span class='inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-green-100 text-green-800 animate-pulse'>INSIDE</span>")
        elif obj.status == Attendance.Status.COMPLETED:
            return format_html("<span class='inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700'>COMPLETED</span>")
        elif obj.status == Attendance.Status.AUTO_CLOSED:
            return format_html("<span class='inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-800 border border-amber-300' title='Auto-closed unconfirmed departure'>AUTO-CLOSED (EST.)</span>")
        return format_html("<span class='inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800'>VOIDED</span>")

    @display(description="Actions")
    def attendance_actions(self, obj):
        if obj.status == Attendance.Status.PRESENT:
            url = reverse("admin:attendance_checkout_action", args=[obj.pk])
            return format_html(
                "<form method='POST' action='{}' style='display:inline;' onsubmit=\"return confirm('Confirm check-out for {}?');\">"
                "<input type='hidden' name='csrfmiddlewaretoken' value=''>"  # Handled via JS/POST in view
                "<button type='submit' class='inline-flex items-center px-2 py-1 text-xs font-semibold rounded bg-amber-50 text-amber-700 hover:bg-amber-100 border border-amber-300'>"
                "<span class='material-symbols-outlined text-xs mr-1'>logout</span> Check Out</button></form>",
                url,
                obj.member.full_name,
            )
        return "-"

    # Custom Admin Action Views
    def checkout_action_view(self, request, attendance_id):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:attendance_attendance_changelist"))

        att = get_object_or_404(Attendance, pk=attendance_id)
        if not can_checkin_members(request.user, att.branch):
            messages.error(request, "You are not authorized to check out members at this branch.")
            return HttpResponseRedirect(reverse("admin:attendance_attendance_changelist"))

        try:
            process_check_out(
                branch=att.branch,
                actor=request.user,
                attendance_id=att.pk,
                method=Attendance.CheckoutMethod.MANUAL,
                notes="Checked out from admin changelist.",
            )
            messages.success(request, f"Successfully checked out {att.member.full_name}.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))

        return HttpResponseRedirect(reverse("admin:attendance_attendance_changelist"))

    def qr_checkin_view(self, request):
        """Dedicated QR scanner check-in terminal."""
        user = request.user
        branches = accessible_branches(user)
        selected_branch = active_branch(request) or branches.first()

        scan_result = None
        error_message = None
        denied_member = None

        if request.method == "POST":
            raw_token = request.POST.get("raw_token", "").strip()
            branch_id = request.POST.get("branch")
            action = request.POST.get("action", "checkin")

            branch = branches.filter(pk=branch_id).first() if branch_id else selected_branch

            if not branch or not can_checkin_members(user, branch):
                messages.error(request, "Invalid or unauthorized branch.")
            elif not raw_token:
                messages.error(request, "Please scan or enter a QR token.")
            else:
                try:
                    if action == "checkout":
                        att = process_check_out(
                            branch=branch,
                            actor=user,
                            raw_token=raw_token,
                            method=Attendance.CheckoutMethod.QR,
                        )
                        scan_result = {
                            "type": "checkout",
                            "member": att.member,
                            "attendance": att,
                            "message": f"Check-out confirmed for {att.member.full_name}.",
                        }
                    else:
                        att, elig = process_check_in(
                            branch=branch,
                            actor=user,
                            raw_token=raw_token,
                            method=Attendance.Method.QR,
                        )
                        scan_result = {
                            "type": "checkin",
                            "member": att.member,
                            "attendance": att,
                            "eligibility": elig,
                            "message": "ACCESS GRANTED — Welcome to " + branch.name,
                        }
                except (ValidationError, PermissionDenied) as e:
                    error_message = str(e)
                    matched_cred = lookup_credential(raw_token)
                    if matched_cred:
                        denied_member = matched_cred.member

        context = {
            **self.admin_site.each_context(request),
            "branches": branches,
            "selected_branch": selected_branch,
            "scan_result": scan_result,
            "error_message": error_message,
            "denied_member": denied_member,
            "title": "QR Check-in & Access Control Terminal",
        }
        return render(request, "admin/attendance/checkin.html", context)

    def manual_checkin_view(self, request):
        """Front-desk manual check-in and exceptional override view."""
        user = request.user
        branches = accessible_branches(user)
        selected_branch = active_branch(request) or branches.first()

        checkin_result = None
        error_message = None
        can_override = can_override_checkin(user, selected_branch) if selected_branch else False

        if request.method == "POST":
            action = request.POST.get("action", "checkin")
            member_id = request.POST.get("member_id")
            branch_id = request.POST.get("branch_id") or (str(selected_branch.pk) if selected_branch else None)
            notes = request.POST.get("notes", "")

            target_branch = branches.filter(pk=branch_id).first() if branch_id else selected_branch
            member = Member.objects.filter(pk=member_id).first() if member_id else None

            if not target_branch or not can_checkin_members(user, target_branch):
                messages.error(request, "Unauthorized for the target branch.")
            elif not member:
                messages.error(request, "Valid member and branch must be selected.")
            elif action == "override":
                if not can_override_checkin(user, target_branch):
                    messages.error(request, "Only Branch Managers or Gym Owners are authorized to grant manual overrides.")
                    error_message = "Only Branch Managers or Gym Owners are authorized to grant manual overrides."
                else:
                    reason = request.POST.get("override_reason", "")
                    try:
                        att = record_manual_override(
                            actor=user,
                            branch=target_branch,
                            member=member,
                            override_reason=reason,
                            notes=notes,
                        )
                        messages.success(request, f"Manual override granted for {member.full_name}.")
                        checkin_result = {"attendance": att, "member": member, "type": "override"}
                    except (ValidationError, PermissionDenied) as e:
                        error_message = str(e)
            else:
                try:
                    att, elig = process_check_in(
                        branch=target_branch,
                        actor=user,
                        member=member,
                        method=Attendance.Method.MANUAL,
                        notes=notes,
                    )
                    messages.success(request, f"Manual check-in recorded for {member.full_name}.")
                    checkin_result = {"attendance": att, "member": member, "eligibility": elig, "type": "manual"}
                except (ValidationError, PermissionDenied) as e:
                    error_message = str(e)

        # Recent members for quick search
        recent_members = Member.objects.filter(status=Member.MemberStatus.ACTIVE).order_by("-created_at")[:15]

        context = {
            **self.admin_site.each_context(request),
            "branches": branches,
            "selected_branch": selected_branch,
            "recent_members": recent_members,
            "checkin_result": checkin_result,
            "error_message": error_message,
            "can_override": can_override,
            "title": "Manual Check-in & Override Desk",
        }
        return render(request, "admin/attendance/manual_checkin.html", context)

    def attendance_dashboard_view(self, request):
        """Dedicated visual analytics and real-time occupancy monitor."""
        user = request.user
        branches = accessible_branches(user)
        selected_branch = active_branch(request)

        metrics = get_attendance_metrics(user, active_branch=selected_branch)
        currently_inside = get_currently_inside(user, active_branch=selected_branch)[:20]

        context = {
            **self.admin_site.each_context(request),
            "metrics": metrics,
            "currently_inside": currently_inside,
            "branches": branches,
            "selected_branch": selected_branch,
            "title": "Attendance & Occupancy Dashboard",
        }
        return render(request, "admin/attendance/dashboard.html", context)

    def attendance_reports_view(self, request):
        """Attendance reports, filtering, and PDF/CSV export."""
        user = request.user
        branches = accessible_branches(user)
        selected_branch = active_branch(request)

        form = AttendanceReportFilterForm(request.GET)
        qs = Attendance.objects.filter(branch__in=branches).select_related(
            "member", "branch", "membership", "membership__package", "recorded_by"
        )

        date_label = "All Time"
        if form.is_valid():
            if form.cleaned_data.get("branch"):
                qs = qs.filter(branch=form.cleaned_data["branch"])
            if form.cleaned_data.get("method"):
                qs = qs.filter(check_in_method=form.cleaned_data["method"])
            if form.cleaned_data.get("status"):
                qs = qs.filter(status=form.cleaned_data["status"])
            if form.cleaned_data.get("start_date"):
                qs = qs.filter(check_in_datetime__date__gte=form.cleaned_data["start_date"])
                date_label = f"From {form.cleaned_data['start_date']}"
            if form.cleaned_data.get("end_date"):
                qs = qs.filter(check_in_datetime__date__lte=form.cleaned_data["end_date"])
                date_label += f" To {form.cleaned_data['end_date']}"

        export = request.GET.get("export")
        if export == "csv":
            if not can_view_attendance_reports(user):
                raise PermissionDenied("Unauthorized to export attendance reports.")
            csv_bytes = generate_attendance_csv(qs)
            response = HttpResponse(csv_bytes, content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="attendance_report.csv"'
            return response
        elif export == "pdf":
            if not can_view_attendance_reports(user):
                raise PermissionDenied("Unauthorized to export attendance reports.")
            b_name = form.cleaned_data["branch"].name if form.is_valid() and form.cleaned_data.get("branch") else "All Authorized Branches"
            pdf_bytes = generate_attendance_pdf(qs, branch_name=b_name, date_range_label=date_label)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = 'inline; filename="attendance_report.pdf"'
            return response

        records = qs.order_by("-check_in_datetime")[:100]

        context = {
            **self.admin_site.each_context(request),
            "form": form,
            "records": records,
            "total_count": qs.count(),
            "title": "Attendance Reports & Audit Logs",
        }
        return render(request, "admin/attendance/reports.html", context)


@admin.register(MemberCredential)
class MemberCredentialAdmin(ModelAdmin):
    list_display = (
        "member_link",
        "credential_type",
        "status_badge",
        "issued_date",
        "expiry_date",
        "credential_actions",
    )
    list_filter = ("credential_type", "status", "issued_date")
    search_fields = ("member__member_number", "member__first_name", "member__last_name")
    readonly_fields = ("member", "token_hash", "issued_date", "revoked_date", "revoked_reason", "issued_by")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("member", "member__home_branch", "issued_by")
        if is_owner(request.user) or request.user.is_superuser:
            return qs
        return qs.filter(member__home_branch__in=accessible_branches(request.user))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("<uuid:credential_id>/badge/", self.admin_site.admin_view(self.member_badge_view), name="attendance_member_badge"),
            path("<uuid:credential_id>/reissue/", self.admin_site.admin_view(self.reissue_credential_view), name="attendance_credential_reissue"),
            path("<uuid:credential_id>/revoke/", self.admin_site.admin_view(self.revoke_credential_view), name="attendance_credential_revoke"),
        ]
        return custom + urls

    @display(description="Member")
    def member_link(self, obj):
        url = reverse("admin:members_member_change", args=[obj.member.pk])
        return format_html(
            "<a href='{}' class='text-teal-600 font-semibold hover:underline'>{}<br><small class='text-gray-500'>{}</small></a>",
            url,
            obj.member.full_name,
            obj.member.member_number,
        )

    @display(description="Status")
    def status_badge(self, obj):
        if obj.status == MemberCredential.Status.ACTIVE:
            return format_html("<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-green-100 text-green-800'>ACTIVE</span>")
        elif obj.status == MemberCredential.Status.REVOKED:
            return format_html("<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-800'>REVOKED</span>")
        return format_html("<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-gray-100 text-gray-800'>EXPIRED</span>")

    @display(description="Actions")
    def credential_actions(self, obj):
        badge_url = reverse("admin:attendance_member_badge", args=[obj.pk])
        reissue_url = reverse("admin:attendance_credential_reissue", args=[obj.pk])
        revoke_url = reverse("admin:attendance_credential_revoke", args=[obj.pk])

        html = f"<a href='{badge_url}' class='inline-flex items-center px-2 py-1 text-xs font-semibold rounded bg-teal-50 text-teal-700 hover:bg-teal-100 mr-2'><span class='material-symbols-outlined text-xs mr-1'>badge</span> View Card</a>"
        if obj.status == MemberCredential.Status.ACTIVE:
            html += f"<a href='{reissue_url}' class='inline-flex items-center px-2 py-1 text-xs font-semibold rounded bg-sky-50 text-sky-700 hover:bg-sky-100 mr-2'><span class='material-symbols-outlined text-xs mr-1'>autorenew</span> Reissue & Print</a>"
            html += f"<a href='{revoke_url}' class='inline-flex items-center px-2 py-1 text-xs font-semibold rounded bg-red-50 text-red-700 hover:bg-red-100'><span class='material-symbols-outlined text-xs mr-1'>block</span> Revoke</a>"
        return format_html(html)

    def reissue_credential_view(self, request, credential_id):
        credential = get_object_or_404(MemberCredential, pk=credential_id)
        if not can_checkin_members(request.user, credential.member.home_branch):
            raise PermissionDenied("Unauthorized for this member's branch.")

        if request.method == "POST":
            reason = request.POST.get("reason", "Card replacement / re-print")
            new_cred, raw_token = reissue_member_credential(
                member=credential.member,
                credential_type=credential.credential_type,
                reissued_by=request.user,
                reason=reason,
            )
            # Store in session for immediate one-time badge printing
            request.session[f"badge_raw_token_{new_cred.pk}"] = raw_token
            messages.success(request, f"Reissued credential for {credential.member.full_name}. Prior credential revoked.")
            return HttpResponseRedirect(reverse("admin:attendance_member_badge", args=[new_cred.pk]))

        context = {
            **self.admin_site.each_context(request),
            "credential": credential,
            "member": credential.member,
            "title": f"Reissue Credential & Print Badge — {credential.member.member_number}",
        }
        return render(request, "admin/attendance/reissue_credential.html", context)

    def member_badge_view(self, request, credential_id):
        """Render printable member ID card with QR code."""
        credential = get_object_or_404(MemberCredential, pk=credential_id)
        if not can_checkin_members(request.user, credential.member.home_branch):
            raise PermissionDenied("Unauthorized for this member's branch.")

        raw_token = request.session.pop(f"badge_raw_token_{credential.pk}", None)
        qr_uri = generate_qr_code_base64(raw_token) if raw_token else None

        context = {
            **self.admin_site.each_context(request),
            "credential": credential,
            "member": credential.member,
            "qr_uri": qr_uri,
            "has_raw_token": bool(raw_token),
            "title": f"Member ID Badge — {credential.member.member_number}",
        }
        return render(request, "admin/attendance/member_qr_badge.html", context)

    def revoke_credential_view(self, request, credential_id):
        credential = get_object_or_404(MemberCredential, pk=credential_id)
        if not can_checkin_members(request.user, credential.member.home_branch):
            raise PermissionDenied("Unauthorized for this member's branch.")

        if request.method == "POST":
            reason = request.POST.get("reason", "Revoked via admin.")
            revoke_credential(credential, reason=reason, revoked_by=request.user)
            messages.success(request, f"Credential for {credential.member.member_number} revoked.")
            return HttpResponseRedirect(reverse("admin:attendance_membercredential_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "credential": credential,
            "title": f"Revoke Credential for {credential.member.member_number}",
        }
        return render(request, "admin/attendance/revoke_credential.html", context)


@admin.register(AccessDevice)
class AccessDeviceAdmin(ModelAdmin):
    list_display = ("device_name", "device_code", "branch", "device_type", "status_badge", "last_seen")
    list_filter = ("device_type", "status", "branch")
    search_fields = ("device_name", "device_code", "branch__name")

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            AccessDevice.Status.ACTIVE: "bg-green-100 text-green-800",
            AccessDevice.Status.INACTIVE: "bg-gray-100 text-gray-800",
            AccessDevice.Status.MAINTENANCE: "bg-amber-100 text-amber-800",
        }
        cls = colors.get(obj.status, "bg-gray-100 text-gray-800")
        return format_html("<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold {}'>{}</span>", cls, obj.status)


@admin.register(AttendanceOverride)
class AttendanceOverrideAdmin(ModelAdmin):
    list_display = ("member_link", "branch", "override_reason_short", "requested_by", "approved_by", "created_at")
    list_filter = ("branch", "created_at")
    search_fields = ("member__member_number", "member__first_name", "override_reason")
    readonly_fields = ("attendance", "member", "branch", "override_reason", "requested_by", "approved_by", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="Member")
    def member_link(self, obj):
        url = reverse("admin:members_member_change", args=[obj.member.pk])
        return format_html("<a href='{}' class='text-teal-600 font-semibold hover:underline'>{}</a>", url, obj.member.full_name)

    @display(description="Reason")
    def override_reason_short(self, obj):
        return obj.override_reason[:60] + ("..." if len(obj.override_reason) > 60 else "")


@admin.register(AccessAttempt)
class AccessAttemptAdmin(ModelAdmin):
    list_display = ("timestamp_display", "result_badge", "reason_code", "member_link", "branch", "check_in_method", "device_or_operator")
    list_filter = ("result", "reason_code", "branch", "check_in_method", "timestamp")
    search_fields = ("member__member_number", "member__first_name", "reason_code", "device_or_operator")
    readonly_fields = ("member", "branch", "timestamp", "result", "reason_code", "message", "check_in_method", "device_or_operator", "metadata")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @display(description="Time")
    def timestamp_display(self, obj):
        tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
        try:
            tz = ZoneInfo(tz_name)
            t = obj.timestamp.astimezone(tz)
        except Exception:
            t = obj.timestamp
        return t.strftime("%b %d, %H:%M:%S")

    @display(description="Result")
    def result_badge(self, obj):
        if obj.result == AccessAttempt.Result.GRANTED:
            return format_html("<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-green-100 text-green-800'>GRANTED</span>")
        return format_html("<span class='inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold bg-red-100 text-red-800'>DENIED</span>")

    @display(description="Member")
    def member_link(self, obj):
        if not obj.member:
            return "-"
        url = reverse("admin:members_member_change", args=[obj.member.pk])
        return format_html("<a href='{}' class='text-teal-600 font-medium hover:underline'>{}</a>", url, obj.member.full_name)
