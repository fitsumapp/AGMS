from django import forms
from apps.branches.models import Branch
from apps.members.models import Member
from .models import Attendance


class QRCheckinForm(forms.Form):
    raw_token = forms.CharField(
        label="Scan QR Code or Enter Token",
        widget=forms.TextInput(
            attrs={
                "class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500 text-lg px-4 py-3 font-mono",
                "placeholder": "Scan with USB QR scanner or paste token...",
                "autofocus": "autofocus",
                "autocomplete": "off",
            }
        ),
        required=True,
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.filter(is_active=True),
        required=True,
        empty_label=None,
        widget=forms.Select(
            attrs={"class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500"}
        ),
    )


class ManualCheckinForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.filter(status=Member.MemberStatus.ACTIVE).select_related("home_branch"),
        label="Select Member",
        required=True,
        widget=forms.Select(
            attrs={"class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500"}
        ),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.filter(is_active=True),
        label="Check-in Branch",
        required=True,
        empty_label=None,
        widget=forms.Select(
            attrs={"class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500"}
        ),
    )
    notes = forms.CharField(
        label="Front-Desk Notes",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500",
                "placeholder": "Optional check-in notes (e.g. forgot physical QR card)...",
            }
        ),
    )


class ManualOverrideForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.all().select_related("home_branch"),
        label="Member",
        required=True,
        widget=forms.Select(
            attrs={"class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500"}
        ),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.filter(is_active=True),
        label="Branch",
        required=True,
        empty_label=None,
        widget=forms.Select(
            attrs={"class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500"}
        ),
    )
    override_reason = forms.CharField(
        label="Override Justification (Required)",
        required=True,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500",
                "placeholder": "Provide detailed business justification for exceptional entry override...",
            }
        ),
    )
    notes = forms.CharField(
        label="Staff Notes",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "w-full border-gray-300 rounded-md shadow-sm focus:border-teal-500 focus:ring-teal-500",
                "placeholder": "Additional notes...",
            }
        ),
    )


class AttendanceReportFilterForm(forms.Form):
    start_date = forms.DateField(
        label="From Date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "border-gray-300 rounded-md shadow-sm text-sm"}),
    )
    end_date = forms.DateField(
        label="To Date",
        required=False,
        widget=forms.DateInput(attrs={"type": "date", "class": "border-gray-300 rounded-md shadow-sm text-sm"}),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.filter(is_active=True),
        required=False,
        empty_label="All Branches",
        widget=forms.Select(attrs={"class": "border-gray-300 rounded-md shadow-sm text-sm"}),
    )
    method = forms.ChoiceField(
        choices=[("", "All Methods")] + Attendance.Method.choices,
        required=False,
        widget=forms.Select(attrs={"class": "border-gray-300 rounded-md shadow-sm text-sm"}),
    )
    status = forms.ChoiceField(
        choices=[("", "All Statuses")] + Attendance.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": "border-gray-300 rounded-md shadow-sm text-sm"}),
    )
