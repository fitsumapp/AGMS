from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import UUIDTimeStampedModel
from .validators import validate_non_negative_amount, validate_positive_amount


class BillingSequence(models.Model):
    """
    Row-locked counter for generating atomic, concurrency-safe sequential identifiers:
    Invoices: INV-000001
    Payments: PAY-000001
    Receipts: REC-000001
    Refunds:  REF-000001
    """
    name = models.CharField(max_length=50, unique=True, db_index=True)
    last_number = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Billing sequence"
        verbose_name_plural = "Billing sequences"

    def __str__(self):
        return f"{self.name}: {self.last_number}"


class PaymentMethod(UUIDTimeStampedModel):
    class MethodType(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        TELEBIRR = "TELEBIRR", "Telebirr"
        MPESA = "MPESA", "M-PESA"
        OTHER = "OTHER", "Other Electronic / POS"

    code = models.CharField(max_length=30, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    method_type = models.CharField(max_length=20, choices=MethodType.choices, default=MethodType.CASH)
    is_active = models.BooleanField(default=True, db_index=True)
    requires_reference = models.BooleanField(default=False)
    requires_verification = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.get_method_type_display()})"


class Invoice(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        PAID = "PAID", "Paid in Full"
        VOID = "VOID", "Void"
        REFUNDED = "REFUNDED", "Refunded in Full"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially Refunded"

    invoice_number = models.CharField(max_length=30, unique=True, db_index=True)
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    membership = models.ForeignKey(
        "memberships.Membership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    renewal = models.ForeignKey(
        "memberships.MembershipRenewal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    issue_date = models.DateField(default=timezone.now, db_index=True)
    due_date = models.DateField(db_index=True)
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
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
        validators=[validate_non_negative_amount],
    )
    status = models.CharField(
        max_length=25,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices_created",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices_issued",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(subtotal__gte=Decimal("0.00")),
                name="invoice_subtotal_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=Decimal("0.00")),
                name="invoice_discount_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_amount__gte=Decimal("0.00")),
                name="invoice_tax_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=Decimal("0.00")),
                name="invoice_total_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(paid_amount__gte=Decimal("0.00")),
                name="invoice_paid_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(balance_due__gte=Decimal("0.00")),
                name="invoice_balance_non_negative",
            ),
        ]

    def clean(self):
        super().clean()
        if self.due_date and self.issue_date and self.due_date < self.issue_date:
            raise ValidationError({"due_date": "Due date cannot precede issue date."})
        if self.discount_amount and self.subtotal and self.discount_amount > self.subtotal:
            raise ValidationError({"discount_amount": "Discount cannot exceed invoice subtotal."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} — {self.member} ({self.total_amount} ETB, {self.get_status_display()})"


class InvoiceItem(UUIDTimeStampedModel):
    class ItemType(models.TextChoices):
        PACKAGE = "PACKAGE", "Membership Package"
        RENEWAL = "RENEWAL", "Subscription Renewal"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        OTHER = "OTHER", "Service / Other"

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="items",
    )
    description = models.CharField(max_length=255)
    item_type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
        default=ItemType.PACKAGE,
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
    )
    related_membership = models.ForeignKey(
        "memberships.Membership",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_items",
    )
    related_renewal = models.ForeignKey(
        "memberships.MembershipRenewal",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoice_items",
    )

    class Meta:
        ordering = ("created_at",)

    def clean(self):
        super().clean()
        expected = (self.quantity * self.unit_price) - self.discount_amount + self.tax_amount
        if self.line_total is None or self.line_total != expected:
            self.line_total = max(Decimal("0.00"), expected)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.description} ({self.quantity} x {self.unit_price} = {self.line_total} ETB)"


class Payment(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Verification"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"
        VOIDED = "VOIDED", "Voided"

    payment_number = models.CharField(max_length=30, unique=True, db_index=True)
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    transaction_reference = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payments_received",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payments_verified",
    )
    paid_at = models.DateTimeField(default=timezone.now, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=120, unique=True, db_index=True)

    class Meta:
        ordering = ("-paid_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="payment_amount_strictly_positive",
            ),
            models.UniqueConstraint(
                fields=["payment_method", "transaction_reference"],
                condition=models.Q(status="VERIFIED") & ~models.Q(transaction_reference=""),
                name="unique_verified_transaction_ref",
            ),
        ]

    def clean(self):
        super().clean()
        if self.payment_method and self.payment_method.requires_reference and not self.transaction_reference.strip():
            raise ValidationError({"transaction_reference": f"Transaction reference is required for {self.payment_method.name}."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.payment_number} — {self.amount} ETB ({self.get_status_display()})"


class Refund(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested (Pending Approval)"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    refund_number = models.CharField(max_length=30, unique=True, db_index=True)
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="refunds",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REQUESTED,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds_requested",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds_approved",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds_completed",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="refund_amount_strictly_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.payment and self.amount:
            if self.amount > self.payment.amount:
                raise ValidationError({"amount": "Refund amount cannot exceed original payment amount."})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.refund_number} — {self.amount} ETB [{self.get_status_display()}]"


class PaymentAdjustment(UUIDTimeStampedModel):
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="adjustments",
    )
    adjustment_type = models.CharField(max_length=50)
    previous_amount = models.DecimalField(max_digits=12, decimal_places=2)
    new_amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_adjustments_approved",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_adjustments_created",
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Adjustment {self.payment.payment_number}: {self.previous_amount} -> {self.new_amount} ETB"


class Receipt(UUIDTimeStampedModel):
    receipt_number = models.CharField(max_length=30, unique=True, db_index=True)
    payment = models.OneToOneField(
        Payment,
        on_delete=models.PROTECT,
        related_name="receipt",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="receipts_issued",
    )
    receipt_snapshot = models.JSONField(default=dict)
    voided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-issued_at",)

    @property
    def is_void(self):
        return self.voided_at is not None

    def __str__(self):
        return f"{self.receipt_number} — Payment {self.payment.payment_number}"


class FinancialAuditEvent(UUIDTimeStampedModel):
    class EventType(models.TextChoices):
        INVOICE_CREATED = "INVOICE_CREATED", "Invoice Created"
        INVOICE_ISSUED = "INVOICE_ISSUED", "Invoice Issued"
        INVOICE_VOIDED = "INVOICE_VOIDED", "Invoice Voided"
        PAYMENT_RECORDED = "PAYMENT_RECORDED", "Payment Recorded"
        PAYMENT_VERIFIED = "PAYMENT_VERIFIED", "Payment Verified"
        PAYMENT_REJECTED = "PAYMENT_REJECTED", "Payment Rejected"
        PAYMENT_VOIDED = "PAYMENT_VOIDED", "Payment Voided"
        REFUND_REQUESTED = "REFUND_REQUESTED", "Refund Requested"
        REFUND_APPROVED = "REFUND_APPROVED", "Refund Approved"
        REFUND_REJECTED = "REFUND_REJECTED", "Refund Rejected"
        REFUND_COMPLETED = "REFUND_COMPLETED", "Refund Completed"
        RECEIPT_ISSUED = "RECEIPT_ISSUED", "Receipt Issued"
        RECEIPT_VOIDED = "RECEIPT_VOIDED", "Receipt Voided"

    event_type = models.CharField(max_length=30, choices=EventType.choices, db_index=True)
    invoice = models.ForeignKey(
        Invoice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    payment = models.ForeignKey(
        Payment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="financial_audit_events",
    )
    branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="financial_audit_events",
    )
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"[{self.get_event_type_display()}] by {self.actor} at {self.created_at}"
