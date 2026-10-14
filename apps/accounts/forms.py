from unfold.forms import UserCreationForm, UserChangeForm
from django import forms
from django.contrib.auth.models import Group
from .models import User, StaffAccount

class AccountCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("email", "first_name", "last_name")

class AccountChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


ASSIGNABLE_ROLES = ("Branch Manager", "Receptionist", "Accountant", "Trainer", "POS Staff")


class StaffCreationForm(UserCreationForm):
    role = forms.ModelChoiceField(queryset=Group.objects.filter(name__in=ASSIGNABLE_ROLES).order_by("name"))

    class Meta(UserCreationForm.Meta):
        model = StaffAccount
        fields = ("email", "first_name", "last_name", "phone")


class StaffChangeForm(UserChangeForm):
    role = forms.ModelChoiceField(queryset=Group.objects.filter(name__in=ASSIGNABLE_ROLES).order_by("name"))

    class Meta(UserChangeForm.Meta):
        model = StaffAccount
        fields = ("email", "first_name", "last_name", "phone", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.fields["role"].initial = self.instance.groups.filter(name__in=ASSIGNABLE_ROLES).first()
