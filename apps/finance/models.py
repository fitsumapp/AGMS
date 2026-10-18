from decimal import Decimal
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.branches.models import Branch
from apps.core.models import UUIDTimeStampedModel
from .validators import (
    mask_account_number,
    mask_tax_id,
    validate_non_negative_amount,
    validate_positive_amount,
    validate_receipt_document,
)


class FinanceSequence(models.Model):
    """
    Row-locked counter for generating atomic, concurrency-safe sequential identifiers:
    Expenses:       EXP-000001
    Payments:       EXPPAY-000001
    Transfers:      TRF-000001
    Adjustments:    ADJ-000001
    Reconciliation: RECON-000001
    """
    name = models.CharField(max_length=50, unique=True, db_index=True)
    last_number = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Finance sequence"
        verbose_name_plural = "Finance sequences"

    def __str__(self):
        return f"{self.name}: {self.last_number}"


class BranchFinanceSettings(UUIDTimeStampedModel):
    """Per-branch configurable approval limits and finance controls."""
    branch = models.OneToOneField(
        Branch,
        on_delete=models.CASCADE,
        related_name="finance_settings",
    )
    manager_approval_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("10000.00"),
        validators=[validate_positive_amount],
        help_text="Maximum expense amount (ETB) a Branch Manager can approve for this branch.",
    )
    receptionist_can_create_expenses = models.BooleanField(
        default=False,
        help_text="Allow receptionists to submit draft expense requests.",
    )
    allow_self_approval = models.BooleanField(
        default=False,
        help_text="Allow the creator of an expense to approve their own request (Strictly prohibited by default).",
    )
    allow_negative_account_balance = models.BooleanField(
        default=False,
        help_text="Allow cash accounts in this branch to have a negative balance.",
    )

    class Meta:
        verbose_name = "Branch finance settings"
        verbose_name_plural = "Branch finance settings"

    def __str__(self):
        return f"Finance Settings — {self.branch.name} (Limit: {self.manager_approval_limit} ETB)"


class ExpenseCategory(UUIDTimeStampedModel):
    """Configurable expense categories (e.g. Rent, Utilities, Maintenance, etc.)."""
    code = models.CharField(max_length=50, unique=True, db_index=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Expense category"
        verbose_name_plural = "Expense categories"
        ordering = ("order", "name")

    def __str__(self):
        return f"{self.name}{'' if self.is_active else ' (Inactive)'}"


class Supplier(UUIDTimeStampedModel):
    """Vendors, contractors, and service providers."""
    class SupplierType(models.TextChoices):
        VENDOR = "VENDOR", "Goods Vendor"
        CONTRACTOR = "CONTRACTOR", "Contractor"
        LANDLORD = "LANDLORD", "Landlord / Property Owner"
        UTILITY = "UTILITY", "Utility Provider"
        SERVICE_PROVIDER = "SERVICE_PROVIDER", "Service Provider"
        TRAINER = "TRAINER", "Freelance / External Trainer"
        OTHER = "OTHER", "Other Supplier"

    name = models.CharField(max_length=200, db_index=True)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    tax_identification_number = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="TIN / VAT Number",
    )
    supplier_type = models.CharField(
        max_length=30,
        choices=SupplierType.choices,
        default=SupplierType.VENDOR,
    )
    branch = models.ForeignKey(
        Branch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suppliers",
        help_text="Branch scope. Leave empty for gym-wide suppliers.",
    )
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_number = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Supplier & Vendor"
        verbose_name_plural = "Suppliers & Vendors"
        ordering = ("name",)

    @property
    def masked_account_number(self):
        return mask_account_number(self.bank_account_number)

    @property
    def masked_tax_id(self):
        return mask_tax_id(self.tax_identification_number)

    def __str__(self):
        branch_lbl = f" [{self.branch.code}]" if self.branch else " [Gym-wide]"
        return f"{self.name} ({self.get_supplier_type_display()}){branch_lbl}"


class FinancialAccount(UUIDTimeStampedModel):
    """Cash on hand, petty cash, and bank accounts."""
    class AccountType(models.TextChoices):
        CASH_ON_HAND = "CASH_ON_HAND", "Cash on Hand"
        PETTY_CASH = "PETTY_CASH", "Petty Cash"
        BANK_ACCOUNT = "BANK_ACCOUNT", "Bank Account"
        DIGITAL_WALLET = "DIGITAL_WALLET", "Digital Wallet / Mobile Money"

    name = models.CharField(max_length=150)
    account_type = models.CharField(
        max_length=30,
        choices=AccountType.choices,
        default=AccountType.CASH_ON_HAND,
    )
    branch = models.ForeignKey(
        Branch,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="financial_accounts",
        help_text="Branch to which this physical cash box or account is assigned.",
    )
    currency = models.CharField(max_length=3, default="ETB")
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=100, blank=True)
    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    opening_date = models.DateField(default=timezone.localdate)
    current_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    is_active = models.BooleanField(default=True, db_index=True)
    allow_negative = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Cash & Bank Account"
        verbose_name_plural = "Cash & Bank Accounts"
        ordering = ("branch__name", "name")

    @property
    def masked_account_number(self):
        return mask_account_number(self.account_number)

    def clean(self):
        super().clean()
        if self.account_type == self.AccountType.BANK_ACCOUNT and not self.bank_name.strip():
            raise ValidationError({"bank_name": "Bank name is required for bank accounts."})

    def __str__(self):
        b_name = self.branch.code if self.branch else "Headquarters"
        return f"{self.name} [{b_name}] ({self.get_account_type_display()}) — {self.current_balance} {self.currency}"


class Expense(UUIDTimeStampedModel):
    """Operational expense record with approval and payment tracking."""
    class ApprovalStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted (Pending Approval)"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        VOIDED = "VOIDED", "Voided"

    class PaymentStatus(models.TextChoices):
        UNPAID = "UNPAID", "Unpaid"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        PAID = "PAID", "Paid in Full"

    reference = models.CharField(max_length=50, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses",
    )
    description = models.TextField()
    expense_date = models.DateField(default=timezone.localdate, db_index=True)
    currency = models.CharField(max_length=3, default="ETB")

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
        help_text="Base amount before tax (ETB).",
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
        help_text="Explicit tax amount (VAT / TOT) if applicable.",
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
        help_text="Total expense amount (Amount + Tax).",
    )
    paid_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    balance_due = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    payment_due_date = models.DateField(null=True, blank=True, db_index=True)

    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.DRAFT,
        db_index=True,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses_created",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses_approved",
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses_rejected",
    )
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expenses_voided",
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    void_reason = models.TextField(blank=True)

    receipt_attachment = models.FileField(
        upload_to="finance/receipts/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_receipt_document],
    )
    internal_notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"
        ordering = ("-expense_date", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_amount__gt=Decimal("0.00")),
                name="expense_total_strictly_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(paid_amount__lte=models.F("total_amount")),
                name="expense_paid_lte_total",
            ),
            models.CheckConstraint(
                condition=models.Q(balance_due__gte=Decimal("0.00")),
                name="expense_balance_non_negative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.amount and self.tax_amount is not None:
            expected_total = self.amount + self.tax_amount
            if self.total_amount != expected_total:
                self.total_amount = expected_total
        if self.total_amount and self.paid_amount is not None:
            self.balance_due = self.total_amount - self.paid_amount
            if self.balance_due < Decimal("0.00"):
                raise ValidationError({"paid_amount": "Paid amount cannot exceed total amount."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} — {self.category.name} ({self.total_amount} {self.currency}) [{self.get_approval_status_display()}]"


class ExpensePayment(UUIDTimeStampedModel):
    """Payment disbursement made against an approved expense."""
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Verification"
        VERIFIED = "VERIFIED", "Verified (Disbursed)"
        VOIDED = "VOIDED", "Voided (Reversed)"

    payment_number = models.CharField(max_length=50, unique=True, db_index=True)
    expense = models.ForeignKey(
        Expense,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    financial_account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        related_name="expense_payments",
    )
    payment_date = models.DateField(default=timezone.localdate, db_index=True)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    payment_method = models.ForeignKey(
        "billing.PaymentMethod",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="expense_payments",
    )
    transaction_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.VERIFIED,
        db_index=True,
    )
    attachment = models.FileField(
        upload_to="finance/payments/%Y/%m/",
        null=True,
        blank=True,
        validators=[validate_receipt_document],
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expense_payments_recorded",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expense_payments_verified",
    )
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="expense_payments_voided",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=120, unique=True, db_index=True)

    class Meta:
        verbose_name = "Expense payment"
        verbose_name_plural = "Expense payments"
        ordering = ("-payment_date", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="expense_payment_amount_strictly_positive",
            ),
        ]

    def __str__(self):
        return f"{self.payment_number} — {self.amount} ETB to {self.expense.reference} ({self.get_status_display()})"


class AccountTransfer(UUIDTimeStampedModel):
    """Internal funds transfer between financial accounts."""
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Approval"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    transfer_number = models.CharField(max_length=50, unique=True, db_index=True)
    source_account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        related_name="transfers_out",
    )
    destination_account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        related_name="transfers_in",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    transfer_date = models.DateField(default=timezone.localdate, db_index=True)
    reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED,
        db_index=True,
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transfers_initiated",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transfers_approved",
    )
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Account transfer"
        verbose_name_plural = "Account transfers"
        ordering = ("-transfer_date", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="transfer_amount_strictly_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.source_account_id and self.destination_account_id:
            if self.source_account_id == self.destination_account_id:
                raise ValidationError({"destination_account": "Source and destination accounts must be distinct."})

    def __str__(self):
        return f"{self.transfer_number}: {self.amount} ETB from {self.source_account.name} to {self.destination_account.name}"


class AccountAdjustment(UUIDTimeStampedModel):
    """Audited balance adjustment for a cash or bank account."""
    class AdjustmentType(models.TextChoices):
        CREDIT = "CREDIT", "Credit (Increase Balance)"
        DEBIT = "DEBIT", "Debit (Decrease Balance)"

    adjustment_number = models.CharField(max_length=50, unique=True, db_index=True)
    account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        related_name="adjustments",
    )
    adjustment_type = models.CharField(
        max_length=20,
        choices=AdjustmentType.choices,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    adjustment_date = models.DateField(default=timezone.localdate, db_index=True)
    reason = models.TextField()
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="account_adjustments_performed",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="account_adjustments_approved",
    )

    class Meta:
        verbose_name = "Account adjustment"
        verbose_name_plural = "Account adjustments"
        ordering = ("-adjustment_date", "-created_at")

    def __str__(self):
        return f"{self.adjustment_number} — {self.get_adjustment_type_display()} {self.amount} ETB on {self.account.name}"


class BillingSettlementBatch(UUIDTimeStampedModel):
    """
    Durable batch record for authoritative customer payment settlements into financial accounts.
    Guarantees exactly-once posting and eliminates duplicate settlement risks.
    """
    batch_number = models.CharField(max_length=50, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="billing_settlements",
    )
    financial_account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        related_name="billing_settlements",
    )
    payment_method_type = models.CharField(max_length=30, db_index=True)
    period_start = models.DateField(db_index=True)
    period_end = models.DateField(db_index=True)
    payment_count = models.PositiveIntegerField(default=0)
    total_payments = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    refund_count = models.PositiveIntegerField(default=0)
    total_refunds = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    net_collections = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    adjustment = models.OneToOneField(
        AccountAdjustment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_settlement_batch",
    )
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_settlement_batches",
    )
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Billing settlement batch"
        verbose_name_plural = "Billing settlement batches"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.batch_number} — {self.branch.name} ({self.payment_method_type}: {self.net_collections} ETB)"


class SettledBillingPayment(UUIDTimeStampedModel):
    """
    Per-payment tracking record with a database UNIQUE constraint on `payment`.
    Guarantees that a billing.Payment can NEVER be settled into financial accounts more than once.
    """
    payment = models.OneToOneField(
        "billing.Payment",
        on_delete=models.PROTECT,
        related_name="settlement_entry",
    )
    settlement_batch = models.ForeignKey(
        BillingSettlementBatch,
        on_delete=models.CASCADE,
        related_name="settled_payments",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    class Meta:
        verbose_name = "Settled billing payment"
        verbose_name_plural = "Settled billing payments"

    def __str__(self):
        return f"Settled Payment {self.payment_id} ({self.amount} ETB) in {self.settlement_batch.batch_number}"


class SettledBillingRefund(UUIDTimeStampedModel):
    """
    Per-refund tracking record with a database UNIQUE constraint on `refund`.
    Guarantees that a billing.Refund can NEVER be settled into financial accounts more than once.
    """
    refund = models.OneToOneField(
        "billing.Refund",
        on_delete=models.PROTECT,
        related_name="settlement_entry",
    )
    settlement_batch = models.ForeignKey(
        BillingSettlementBatch,
        on_delete=models.CASCADE,
        related_name="settled_refunds",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    class Meta:
        verbose_name = "Settled billing refund"
        verbose_name_plural = "Settled billing refunds"

    def __str__(self):
        return f"Settled Refund {self.refund_id} ({self.amount} ETB) in {self.settlement_batch.batch_number}"


class FinancialReconciliation(UUIDTimeStampedModel):
    """Daily or periodic cash and bank balance reconciliation."""
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft / In Progress"
        SUBMITTED = "SUBMITTED", "Submitted for Review"
        APPROVED_CLOSED = "APPROVED_CLOSED", "Approved & Period Closed"
        DISCREPANCY_REJECTED = "DISCREPANCY_REJECTED", "Rejected Discrepancy"

    reconciliation_number = models.CharField(max_length=50, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="reconciliations",
    )
    account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        related_name="reconciliations",
    )
    reconciliation_date = models.DateField(default=timezone.localdate, db_index=True)
    period_start = models.DateField(default=timezone.localdate)
    period_end = models.DateField(default=timezone.localdate)

    opening_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    total_inflows = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_outflows = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    expected_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Calculated: Opening Balance + Total Inflows - Total Outflows.",
    )
    actual_counted_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Physical counted cash or verified bank statement ending balance.",
    )
    variance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Variance = Actual Counted - Expected Balance.",
    )
    variance_reason = models.TextField(
        blank=True,
        help_text="Mandatory explanation if variance is non-zero.",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reconciliations_conducted",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reconciliations_approved",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Financial reconciliation"
        verbose_name_plural = "Financial reconciliations"
        ordering = ("-reconciliation_date", "-created_at")

    def clean(self):
        super().clean()
        if self.pk:
            original = FinancialReconciliation.objects.filter(pk=self.pk).first()
            if original and original.status == self.Status.APPROVED_CLOSED and self.status == self.Status.APPROVED_CLOSED:
                for field in ["opening_balance", "total_inflows", "total_outflows", "expected_balance", "actual_counted_balance", "variance", "period_start", "period_end"]:
                    if getattr(self, field) != getattr(original, field):
                        raise ValidationError(f"Closed reconciliation periods are immutable. Cannot alter '{field}'.")
        if self.actual_counted_balance is not None and self.expected_balance is not None:
            self.variance = self.actual_counted_balance - self.expected_balance
        if self.status in [self.Status.SUBMITTED, self.Status.APPROVED_CLOSED]:
            if self.variance != Decimal("0.00") and not self.variance_reason.strip():
                raise ValidationError({"variance_reason": "A detailed explanation is required when there is a cash/bank variance."})

    def delete(self, *args, **kwargs):
        if self.status == self.Status.APPROVED_CLOSED:
            raise ValidationError("Closed reconciliation periods cannot be deleted.")
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reconciliation_number} — {self.account.name} on {self.reconciliation_date} [{self.get_status_display()}]"


class FinanceAuditEvent(UUIDTimeStampedModel):
    """Immutable audit trail for financial actions."""
    class EventType(models.TextChoices):
        EXPENSE_CREATED = "EXPENSE_CREATED", "Expense Created"
        EXPENSE_SUBMITTED = "EXPENSE_SUBMITTED", "Expense Submitted"
        EXPENSE_APPROVED = "EXPENSE_APPROVED", "Expense Approved"
        EXPENSE_REJECTED = "EXPENSE_REJECTED", "Expense Rejected"
        EXPENSE_VOIDED = "EXPENSE_VOIDED", "Expense Voided"
        PAYMENT_RECORDED = "PAYMENT_RECORDED", "Expense Payment Recorded"
        PAYMENT_VOIDED = "PAYMENT_VOIDED", "Expense Payment Voided"
        TRANSFER_COMPLETED = "TRANSFER_COMPLETED", "Account Transfer Completed"
        ADJUSTMENT_APPLIED = "ADJUSTMENT_APPLIED", "Account Adjustment Applied"
        RECONCILIATION_SUBMITTED = "RECONCILIATION_SUBMITTED", "Reconciliation Submitted"
        RECONCILIATION_CLOSED = "RECONCILIATION_CLOSED", "Reconciliation Approved & Closed"
        RECONCILIATION_REOPENED = "RECONCILIATION_REOPENED", "Reconciliation Reopened"
        REPORT_EXPORTED = "REPORT_EXPORTED", "Financial Report Exported"

    event_type = models.CharField(max_length=40, choices=EventType.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="finance_audit_events",
    )
    branch = models.ForeignKey(
        Branch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="finance_audit_events",
    )
    target_type = models.CharField(max_length=60, db_index=True)
    target_id = models.CharField(max_length=60, db_index=True)
    action_summary = models.TextField(blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Finance audit event"
        verbose_name_plural = "Finance audit events"
        ordering = ("-created_at",)

    def __str__(self):
        actor_name = self.actor.email if self.actor else "System"
        return f"[{self.get_event_type_display()}] by {actor_name} on {self.created_at}"
