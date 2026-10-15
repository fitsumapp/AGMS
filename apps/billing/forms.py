from decimal import Decimal
from django import forms
from unfold.widgets import UnfoldAdminTextInputWidget, UnfoldAdminSelectWidget

from apps.branches.models import Branch
from apps.branches.services import accessible_branches
from apps.members.models import Member
from .models import Invoice, Payment, PaymentMethod, Refund


class InvoiceCreateForm(forms.ModelForm):
    item_description = forms.CharField(
        max_length=255,
        required=True,
        widget=UnfoldAdminTextInputWidget(attrs={"placeholder": "e.g., Annual VIP Subscription"}),
    )
    unit_price = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        required=True,
        widget=UnfoldAdminTextInputWidget(attrs={"placeholder": "Amount in ETB"}),
    )
    quantity = forms.IntegerField(
        initial=1,
        min_value=1,
        widget=UnfoldAdminTextInputWidget(),
    )

    class Meta:
        model = Invoice
        fields = ("member", "branch", "due_date", "discount_amount", "tax_amount", "notes")
        widgets = {
            "member": UnfoldAdminSelectWidget(),
            "branch": UnfoldAdminSelectWidget(),
            "due_date": UnfoldAdminTextInputWidget(attrs={"type": "date"}),
            "discount_amount": UnfoldAdminTextInputWidget(),
            "tax_amount": UnfoldAdminTextInputWidget(),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields["branch"].queryset = accessible_branches(user)
            self.fields["member"].queryset = Member.objects.filter(
                home_branch__in=accessible_branches(user)
            ).order_by("first_name", "last_name")


class RecordPaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ("amount", "payment_method", "transaction_reference", "notes")
        widgets = {
            "amount": UnfoldAdminTextInputWidget(attrs={"placeholder": "Amount in ETB"}),
            "payment_method": UnfoldAdminSelectWidget(),
            "transaction_reference": UnfoldAdminTextInputWidget(attrs={"placeholder": "e.g., CBE/TXN-123456 or Telebirr ID"}),
        }

    def __init__(self, *args, **kwargs):
        self.invoice = kwargs.pop("invoice", None)
        super().__init__(*args, **kwargs)
        self.fields["payment_method"].queryset = PaymentMethod.objects.filter(is_active=True)
        if self.invoice:
            self.fields["amount"].initial = self.invoice.balance_due

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if self.invoice and amount > self.invoice.balance_due:
            raise forms.ValidationError(
                f"Amount exceeds remaining balance ({self.invoice.balance_due} ETB)."
            )
        return amount

    def clean(self):
        cleaned_data = super().clean()
        method = cleaned_data.get("payment_method")
        ref = (cleaned_data.get("transaction_reference") or "").strip()
        if method and method.requires_reference and not ref:
            self.add_error("transaction_reference", f"Transaction reference is required for {method.name}.")
        return cleaned_data


class RefundRequestForm(forms.ModelForm):
    class Meta:
        model = Refund
        fields = ("amount", "reason")
        widgets = {
            "amount": UnfoldAdminTextInputWidget(attrs={"placeholder": "Refund Amount in ETB"}),
        }

    def __init__(self, *args, **kwargs):
        self.payment = kwargs.pop("payment", None)
        super().__init__(*args, **kwargs)
        if self.payment:
            self.fields["amount"].initial = self.payment.amount

    def clean_amount(self):
        amount = self.cleaned_data.get("amount")
        if self.payment and amount > self.payment.amount:
            raise forms.ValidationError(
                f"Refund amount cannot exceed payment amount ({self.payment.amount} ETB)."
            )
        return amount
