import uuid
from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.roles import is_owner
from apps.branches.services import accessible_branches
from apps.members.models import Member
from .dates import get_gym_today
from .models import (
    Membership,
    MembershipFreeze,
    MembershipPackage,
    MembershipRenewal,
    PackageBranch,
)


class MembershipPackageForm(forms.ModelForm):
    class Meta:
        model = MembershipPackage
        fields = [
            "code",
            "name",
            "description",
            "duration_unit",
            "duration_value",
            "price",
            "currency",
            "allowed_visits",
            "cross_branch_enabled",
            "freeze_allowed",
            "max_freeze_days",
            "max_freeze_requests",
            "is_active",
        ]


class MembershipAssignmentForm(forms.Form):
    member = forms.ModelChoiceField(
        queryset=Member.objects.filter(status=Member.MemberStatus.ACTIVE),
        label="Member",
        help_text="Select active member to enroll.",
    )
    home_branch = forms.ModelChoiceField(
        queryset=None,
        label="Home Branch",
        help_text="Select branch for this membership.",
    )
    package = forms.ModelChoiceField(
        queryset=MembershipPackage.objects.filter(is_active=True),
        label="Membership Package",
        help_text="Select active membership package.",
    )
    start_date = forms.DateField(
        initial=get_gym_today,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Start Date (Inclusive)",
    )
    is_complimentary = forms.BooleanField(
        required=False,
        label="Complimentary Membership (Owner Only)",
        help_text="Waive billing clearance and activate immediately.",
    )
    complimentary_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Complimentary Reason",
        help_text="Required if complimentary is checked.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user:
            branches = accessible_branches(user)
            self.fields["home_branch"].queryset = branches
            if branches.count() == 1:
                self.fields["home_branch"].initial = branches.first()
            if not is_owner(user) and not user.is_superuser:
                self.fields["is_complimentary"].disabled = True
                self.fields["is_complimentary"].widget = forms.HiddenInput()
                self.fields["complimentary_reason"].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        branch = cleaned_data.get("home_branch")
        package = cleaned_data.get("package")
        is_comp = cleaned_data.get("is_complimentary")
        reason = cleaned_data.get("complimentary_reason")

        if branch and package:
            # Check package branch authorization
            allowed = PackageBranch.objects.filter(package=package, branch=branch, is_enabled=True).exists()
            if not allowed:
                raise ValidationError(f"Package '{package.name}' is not authorized for branch '{branch.name}'.")

        if is_comp and not (reason or "").strip():
            raise ValidationError({"complimentary_reason": "A reason is mandatory for complimentary memberships."})

        return cleaned_data


class MembershipFreezeForm(forms.ModelForm):
    class Meta:
        model = MembershipFreeze
        fields = [
            "requested_start_date",
            "requested_end_date",
            "reason",
        ]
        widgets = {
            "requested_start_date": forms.DateInput(attrs={"type": "date"}),
            "requested_end_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }


class MembershipRenewalForm(forms.Form):
    new_package = forms.ModelChoiceField(
        queryset=MembershipPackage.objects.filter(is_active=True),
        required=False,
        label="Renewal Package (Optional)",
        help_text="Leave blank to renew using the current package.",
    )
    billing_reference = forms.CharField(
        required=False,
        label="Billing Reference",
        help_text="Optional reference to billing order or receipt.",
    )
    idempotency_key = forms.CharField(
        widget=forms.HiddenInput(),
        initial=lambda: uuid.uuid4().hex,
    )
