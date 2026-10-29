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

        bilingual_labels = {
            "first_name": "First Name (ስም)",
            "middle_name": "Middle Name (የአባት ስም)",
            "last_name": "Last Name (የአያት ስም)",
            "phone": "Phone Number (ስልክ ቁጥር)",
            "alternative_phone": "Alternative Phone (ተጨማሪ ስልክ)",
            "email": "Email Address (ኢሜይል)",
            "date_of_birth": "Date of Birth (የትውልድ ቀን)",
            "gender": "Gender (ጾታ)",
            "home_branch": "Home Branch (ቅርንጫፍ)",
            "address": "Residential Address (አድራሻ)",
            "city": "City (ከተማ)",
            "photo": "Profile Photo (የአባል ፎቶ)",
            "registration_source": "Registration Source (የምዝገባ መንገድ)",
            "referral_source": "Referral Source (የሪፈራል ምንጭ)",
        }
        bilingual_help = {
            "first_name": "Full legal first name of the member.",
            "middle_name": "Father's name.",
            "last_name": "Grandfather's or family name.",
            "phone": "Primary Ethiopian mobile number (09... or +251...).",
            "alternative_phone": "Secondary or emergency phone number.",
            "email": "Valid email address for receipts and digital notices.",
            "home_branch": "Select primary gym facility for access and billing.",
            "photo": "Upload a portrait / passport-sized photo of the member (JPG/PNG).",
            "address": "Sub-city, Woreda, or house address.",
        }
        for name, lbl in bilingual_labels.items():
            if name in self.fields:
                self.fields[name].label = lbl
        for name, hlp in bilingual_help.items():
            if name in self.fields:
                self.fields[name].help_text = hlp

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bilingual_labels = {
            "first_name": "First Name (ስም)",
            "middle_name": "Middle Name (የአባት ስም)",
            "last_name": "Last Name (የአያት ስም)",
            "phone": "Phone Number (ስልክ ቁጥር)",
            "alternative_phone": "Alternative Phone (ተጨማሪ ስልክ)",
            "email": "Email Address (ኢሜይል)",
            "date_of_birth": "Date of Birth (የትውልድ ቀን)",
            "gender": "Gender (ጾታ)",
            "address": "Residential Address (አድራሻ)",
            "city": "City (ከተማ)",
            "photo": "Profile Photo (የአባል ፎቶ)",
            "registration_source": "Registration Source (የምዝገባ መንገድ)",
            "referral_source": "Referral Source (የሪፈራል ምንጭ)",
        }
        for name, lbl in bilingual_labels.items():
            if name in self.fields:
                self.fields[name].label = lbl

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

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            from apps.accounts.roles import has_role, is_owner
            if has_role(user, "Receptionist") and not is_owner(user) and not has_role(user, "Branch Manager"):
                self.fields["new_status"].choices = [
                    (Member.MemberStatus.ACTIVE, "Active"),
                    (Member.MemberStatus.INACTIVE, "Inactive"),
                ]

