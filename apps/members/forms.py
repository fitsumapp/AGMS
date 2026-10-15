from django import forms
from django.core.exceptions import ValidationError

from apps.branches.services import accessible_branches
from .models import (
    Member,
    MemberDocument,
    MemberEmergencyContact,
    MemberNote,
)
from .services import check_possible_duplicates
from .validators import normalize_ethiopian_phone


class MemberRegistrationForm(forms.ModelForm):
    confirm_possible_duplicate = forms.BooleanField(
        required=False,
        initial=False,
        label="Confirm registration despite potential duplicate matches",
        help_text="Check this box if you have reviewed matching profiles and confirmed this is a new member.",
    )

    class Meta:
        model = Member
        fields = [
            "home_branch",
            "first_name",
            "middle_name",
            "last_name",
            "gender",
            "date_of_birth",
            "photo",
            "phone",
            "alternative_phone",
            "email",
            "address",
            "city",
            "registration_source",
            "referral_source",
        ]

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        if request:
            branches = accessible_branches(request.user)
            self.fields["home_branch"].queryset = branches
            if branches.count() == 1:
                self.fields["home_branch"].initial = branches.first()

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        return normalize_ethiopian_phone(phone)

    def clean_alternative_phone(self):
        alt = self.cleaned_data.get("alternative_phone")
        if alt:
            return normalize_ethiopian_phone(alt)
        return ""

    def clean(self):
        cleaned_data = super().clean()
        phone = cleaned_data.get("phone")
        first_name = cleaned_data.get("first_name")
        last_name = cleaned_data.get("last_name")
        dob = cleaned_data.get("date_of_birth")
        confirmed = cleaned_data.get("confirm_possible_duplicate")

        if phone and first_name and last_name and not confirmed:
            dupes = check_possible_duplicates(
                phone=phone,
                first_name=first_name,
                last_name=last_name,
                date_of_birth=dob,
            )
            if dupes.exists():
                matched_numbers = ", ".join(d.member_number for d in dupes[:3])
                self.fields["confirm_possible_duplicate"].widget.attrs["class"] = "erp-duplicate-warning-checkbox"
                raise ValidationError(
                    f"Warning: Potential duplicate profile(s) found with matching phone or name: {matched_numbers}. "
                    "Review existing members, or check 'Confirm registration despite potential duplicate matches' below to proceed."
                )
        return cleaned_data


class MemberChangeForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = [
            "first_name",
            "middle_name",
            "last_name",
            "gender",
            "date_of_birth",
            "photo",
            "phone",
            "alternative_phone",
            "email",
            "address",
            "city",
            "registration_source",
            "referral_source",
        ]

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        return normalize_ethiopian_phone(phone)

    def clean_alternative_phone(self):
        alt = self.cleaned_data.get("alternative_phone")
        if alt:
            return normalize_ethiopian_phone(alt)
        return ""


class MemberTransferBranchForm(forms.Form):
    new_branch = forms.ModelChoiceField(
        queryset=None,
        label="New Home Branch",
        help_text="Select destination branch for this member.",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Transfer Reason",
        help_text="Provide reason for member branch transfer.",
        required=True,
    )

    def __init__(self, *args, user=None, current_branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        branches = accessible_branches(user) if user else Member.objects.none()
        if current_branch:
            branches = branches.exclude(pk=current_branch.pk)
        self.fields["new_branch"].queryset = branches


class MemberStatusChangeForm(forms.Form):
    new_status = forms.ChoiceField(
        choices=Member.MemberStatus.choices,
        label="New Status",
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Reason",
        help_text="Provide reason for this status change (mandatory for Suspension).",
        required=True,
    )
