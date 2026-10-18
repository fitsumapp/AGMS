from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import UUIDTimeStampedModel
from apps.branches.models import Branch
from .validators import (
    validate_positive_amount,
    validate_non_negative_amount,
    validate_positive_quantity,
    validate_non_negative_quantity,
    validate_sku,
    validate_barcode,
)


class InventorySequence(models.Model):
    """
    Row-locked counter for generating atomic, concurrency-safe sequential identifiers:
    PRD-000001, PO-000001, GRN-000001, ST-000001, SA-000001, SC-000001,
    SALE-000001, REC-POS-000001, RET-000001, SHIFT-000001, SETTLE-POS-000001
    """
    name = models.CharField(max_length=50, unique=True, db_index=True)
    last_number = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Inventory Sequence"
        verbose_name_plural = "Inventory Sequences"

    def __str__(self):
        return f"{self.name}: {self.last_number}"


def generate_next_inventory_number(sequence_name: str, prefix: str) -> str:
    """Generate next sequential number with atomic row-locking."""
    with transaction.atomic():
        seq, _created = InventorySequence.objects.select_for_update().get_or_create(
            name=sequence_name,
            defaults={"last_number": 0},
        )
        seq.last_number += 1
        seq.save(update_fields=["last_number", "updated_at"])
        return f"{prefix}-{seq.last_number:06d}"


class ProductCategory(UUIDTimeStampedModel):
    """Product categories and subcategories."""
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, unique=True, db_index=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="subcategories",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = "Product Category"
        verbose_name_plural = "Product Categories"
        ordering = ("name",)

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name


class Product(UUIDTimeStampedModel):
    """Retail items: supplements, drinks, accessories, apparel, etc."""
    class Unit(models.TextChoices):
        PCS = "PCS", "Pieces"
        BOTTLE = "BOTTLE", "Bottle"
        CAN = "CAN", "Can"
        PACK = "PACK", "Pack"
        BOX = "BOX", "Box"
        KG = "KG", "Kilogram"
        SCOOP = "SCOOP", "Scoop / Serving"
        PAIR = "PAIR", "Pair"

    product_number = models.CharField(max_length=30, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, unique=True, db_index=True, validators=[validate_sku])
    barcode = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        validators=[validate_barcode],
    )
    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.PROTECT,
        related_name="products",
    )
    unit_of_measurement = models.CharField(
        max_length=20,
        choices=Unit.choices,
        default=Unit.PCS,
    )
    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
        help_text="Standard or default purchasing cost in ETB.",
    )
    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
        help_text="Retail selling price in ETB.",
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
        help_text="Applicable tax/VAT percentage (e.g. 15.00 for 15%).",
    )
    is_tax_inclusive = models.BooleanField(
        default=True,
        help_text="Whether the selling price already includes tax.",
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text="Stock quantity at or below which reorder alert triggers.",
    )
    tracks_expiry = models.BooleanField(
        default=False,
        help_text="Enable expiry date and batch tracking for perishable items (e.g. supplements, drinks).",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    image = models.ImageField(upload_to="products/", null=True, blank=True)
    description = models.TextField(blank=True)
    branches = models.ManyToManyField(
        Branch,
        blank=True,
        related_name="available_products",
        help_text="Branches selling this product. Leave empty for all branches.",
    )

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.sku})"

    def clean(self):
        super().clean()
        if self.cost_price is not None and self.selling_price is not None:
            if self.selling_price < self.cost_price:
                # Warning or notice, but allowed if on clearance; we can allow it or warn
                pass


class StockItem(UUIDTimeStampedModel):
    """Branch-specific inventory balance for a product."""
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="stock_items",
    )
    quantity_on_hand = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_quantity],
    )
    quantity_reserved = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_quantity],
    )
    reorder_level = models.PositiveIntegerField(default=5)
    last_cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    average_cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
        help_text="Weighted moving average cost price.",
    )

    class Meta:
        verbose_name = "Stock Overview"
        verbose_name_plural = "Stock Overview"
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "product"],
                name="unique_branch_product_stock",
            ),
            models.CheckConstraint(
                condition=Q(quantity_on_hand__gte=0),
                name="prevent_negative_stock_on_hand",
            ),
            models.CheckConstraint(
                condition=Q(quantity_reserved__gte=0),
                name="prevent_negative_stock_reserved",
            ),
        ]
        ordering = ("product__name",)

    def __str__(self):
        return f"{self.product.name} @ {self.branch.name}: {self.quantity_on_hand} {self.product.unit_of_measurement}"

    @property
    def is_low_stock(self):
        return self.quantity_on_hand <= self.product.low_stock_threshold

    @property
    def total_inventory_value(self):
        """Valuation using weighted average cost price."""
        return (self.quantity_on_hand * self.average_cost_price).quantize(Decimal("0.01"))


class StockBatch(UUIDTimeStampedModel):
    """Specific batch with lot number and expiry date."""
    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.CASCADE,
        related_name="batches",
    )
    batch_number = models.CharField(max_length=50)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_quantity],
    )
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
    )
    received_date = models.DateField(default=timezone.localdate)

    class Meta:
        verbose_name = "Stock Batch"
        verbose_name_plural = "Stock Batches"
        ordering = ("expiry_date", "received_date")

    def __str__(self):
        exp = self.expiry_date.strftime("%Y-%m-%d") if self.expiry_date else "No Expiry"
        return f"{self.stock_item.product.name} - Batch {self.batch_number} (Exp: {exp}): {self.quantity}"

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        return self.expiry_date < timezone.localdate()

    @property
    def is_expiring_soon(self):
        if not self.expiry_date:
            return False
        today = timezone.localdate()
        return today <= self.expiry_date <= (today + timezone.timedelta(days=30))


class StockMovement(UUIDTimeStampedModel):
    """Immutable audit trail of all inventory quantity changes."""
    class MovementType(models.TextChoices):
        OPENING_STOCK = "OPENING_STOCK", "Opening Stock"
        GOODS_RECEIVED = "GOODS_RECEIVED", "Goods Received"
        SALE_DEDUCTION = "SALE_DEDUCTION", "Sale Deduction"
        SALE_RETURN = "SALE_RETURN", "Customer Return Restock"
        DAMAGE_WASTAGE = "DAMAGE_WASTAGE", "Damage & Wastage"
        TRANSFER_OUT = "TRANSFER_OUT", "Inter-branch Transfer Out"
        TRANSFER_IN = "TRANSFER_IN", "Inter-branch Transfer In"
        COUNT_ADJUSTMENT = "COUNT_ADJUSTMENT", "Stock Count Reconciliation"
        MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT", "Manual Stock Adjustment"
        PURCHASE_RETURN = "PURCHASE_RETURN", "Purchase Return to Supplier"

    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    movement_type = models.CharField(
        max_length=30,
        choices=MovementType.choices,
        db_index=True,
    )
    quantity_change = models.DecimalField(max_digits=12, decimal_places=2)
    balance_before = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reference = models.CharField(max_length=100, db_index=True)
    notes = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Stock Movement"
        verbose_name_plural = "Stock Movements"
        ordering = ("-timestamp", "-created_at")

    def __str__(self):
        sign = "+" if self.quantity_change > 0 else ""
        return f"{self.stock_item.product.name} @ {self.stock_item.branch.code}: {sign}{self.quantity_change} ({self.get_movement_type_display()})"

    def clean(self):
        super().clean()
        if not self._state.adding:
            raise ValidationError(_("StockMovement records are immutable and cannot be updated."))

    def delete(self, *args, **kwargs):
        raise ValidationError(_("StockMovement records are immutable and cannot be deleted."))


class PurchaseOrder(UUIDTimeStampedModel):
    """Purchase orders placed with suppliers."""
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        APPROVED = "APPROVED", "Approved"
        PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED", "Partially Received"
        RECEIVED = "RECEIVED", "Received in Full"
        CANCELLED = "CANCELLED", "Cancelled"

    po_number = models.CharField(max_length=30, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )
    supplier = models.ForeignKey(
        "finance.Supplier",
        on_delete=models.PROTECT,
        related_name="purchase_orders",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    order_date = models.DateField(default=timezone.localdate)
    expected_delivery_date = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(
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
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_purchase_orders",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_purchase_orders",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Purchase Order"
        verbose_name_plural = "Purchase Orders"
        ordering = ("-order_date", "-created_at")

    def __str__(self):
        return f"{self.po_number} - {self.supplier.name} ({self.get_status_display()})"


class PurchaseOrderItem(UUIDTimeStampedModel):
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="po_items",
    )
    quantity_ordered = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_quantity],
    )
    quantity_received = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_quantity],
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )

    class Meta:
        verbose_name = "Purchase Order Item"
        verbose_name_plural = "Purchase Order Items"

    def __str__(self):
        return f"{self.product.name} ({self.quantity_ordered} @ {self.unit_cost} ETB)"


class GoodsReceivedNote(UUIDTimeStampedModel):
    """GRN representing physical inventory intake."""
    grn_number = models.CharField(max_length=30, unique=True, db_index=True)
    purchase_order = models.ForeignKey(
        PurchaseOrder,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="grns",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="grns",
    )
    supplier = models.ForeignKey(
        "finance.Supplier",
        on_delete=models.PROTECT,
        related_name="grns",
    )
    received_date = models.DateField(default=timezone.localdate)
    delivery_note_reference = models.CharField(max_length=100, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="received_grns",
    )
    notes = models.TextField(blank=True)
    expense_record = models.ForeignKey(
        "finance.Expense",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_grns",
        help_text="Integrated Phase 10 expense liability for this inventory shipment.",
    )
    idempotency_key = models.CharField(max_length=120, blank=True, default="", db_index=True)

    class Meta:
        verbose_name = "Goods Received Note"
        verbose_name_plural = "Goods Received Notes"
        ordering = ("-received_date", "-created_at")

    def __str__(self):
        return f"{self.grn_number} - {self.supplier.name} ({self.received_date})"


class GoodsReceivedItem(UUIDTimeStampedModel):
    grn = models.ForeignKey(
        GoodsReceivedNote,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="grn_items",
    )
    po_item = models.ForeignKey(
        PurchaseOrderItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="grn_items",
    )
    quantity_received = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_quantity],
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    batch_number = models.CharField(max_length=50, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Goods Received Item"
        verbose_name_plural = "Goods Received Items"

    def __str__(self):
        return f"{self.product.name} ({self.quantity_received} received)"


class StockTransfer(UUIDTimeStampedModel):
    """Inter-branch stock transfer."""
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Dispatch"
        DISPATCHED = "DISPATCHED", "In Transit / Dispatched"
        COMPLETED = "COMPLETED", "Received / Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    transfer_number = models.CharField(max_length=30, unique=True, db_index=True)
    source_branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transfers_sent",
    )
    target_branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="transfers_received",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transfers_dispatched",
    )
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="transfers_received",
    )
    received_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Stock Transfer"
        verbose_name_plural = "Stock Transfers"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.transfer_number}: {self.source_branch.code} -> {self.target_branch.code} ({self.get_status_display()})"

    def clean(self):
        super().clean()
        if self.source_branch_id and self.target_branch_id and self.source_branch_id == self.target_branch_id:
            raise ValidationError({"target_branch": _("Target branch cannot be the same as source branch.")})


class StockTransferItem(UUIDTimeStampedModel):
    transfer = models.ForeignKey(
        StockTransfer,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="transfer_items",
    )
    quantity_sent = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_quantity],
    )
    quantity_received = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_quantity],
    )

    class Meta:
        verbose_name = "Stock Transfer Item"
        verbose_name_plural = "Stock Transfer Items"

    def __str__(self):
        return f"{self.product.name} ({self.quantity_sent} sent)"


class StockAdjustment(UUIDTimeStampedModel):
    """Write-offs, spoilage, shrinkage, and corrections."""
    class Reason(models.TextChoices):
        DAMAGE = "DAMAGE", "Damage / Wastage"
        EXPIRY = "EXPIRY", "Expired Stock Write-off"
        THEFT_LOSS = "THEFT_LOSS", "Theft or Shrinkage"
        CORRECTION = "CORRECTION", "Inventory Audit Correction"
        INTERNAL_USE = "INTERNAL_USE", "Gym Internal Use"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        APPROVED = "APPROVED", "Approved & Applied"
        CANCELLED = "CANCELLED", "Cancelled"

    adjustment_number = models.CharField(max_length=30, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_adjustments",
    )
    reason = models.CharField(max_length=30, choices=Reason.choices)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_stock_adjustments",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_stock_adjustments",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Stock Adjustment"
        verbose_name_plural = "Stock Adjustments"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.adjustment_number} @ {self.branch.code} ({self.get_reason_display()})"


class StockAdjustmentItem(UUIDTimeStampedModel):
    adjustment = models.ForeignKey(
        StockAdjustment,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="adjustment_items",
    )
    quantity_change = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Negative for deductions (spoilage/damage), positive for found items.",
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
    )
    reason_detail = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Stock Adjustment Item"
        verbose_name_plural = "Stock Adjustment Items"

    def __str__(self):
        return f"{self.product.name} ({self.quantity_change})"


class StockCount(UUIDTimeStampedModel):
    """Physical cycle count or stocktake."""
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "In Progress"
        COMPLETED = "COMPLETED", "Count Completed"
        RECONCILED = "RECONCILED", "Reconciled & Adjusted"
        CANCELLED = "CANCELLED", "Cancelled"

    count_number = models.CharField(max_length=30, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_counts",
    )
    count_date = models.DateField(default=timezone.localdate)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    counted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="performed_stock_counts",
    )
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reconciled_stock_counts",
    )
    reconciled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Stock Count"
        verbose_name_plural = "Stock Counts"
        ordering = ("-count_date", "-created_at")

    def __str__(self):
        return f"{self.count_number} @ {self.branch.code} ({self.count_date})"


class StockCountItem(UUIDTimeStampedModel):
    stock_count = models.ForeignKey(
        StockCount,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="count_items",
    )
    system_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    counted_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    variance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Counted quantity minus system quantity.",
    )
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    variance_cost = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Stock Count Item"
        verbose_name_plural = "Stock Count Items"

    def __str__(self):
        return f"{self.product.name}: System={self.system_quantity}, Counted={self.counted_quantity}"


class CashierShift(UUIDTimeStampedModel):
    """POS shift tracking cash drawer opening, closing, and reconciliation."""
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    shift_number = models.CharField(max_length=30, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="cashier_shifts",
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cashier_shifts",
    )
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    opening_float = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
        help_text="Starting cash in drawer in ETB.",
    )
    expected_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
        help_text="Opening float + Cash sales - Cash refunds.",
    )
    declared_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
        help_text="Cash counted by cashier at shift close.",
    )
    cash_variance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Declared cash minus expected cash.",
    )
    total_sales_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_refunds_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "Cashier Shift"
        verbose_name_plural = "Cashier Shifts"
        ordering = ("-opened_at",)

    def __str__(self):
        cashier_label = self.cashier.get_full_name() or getattr(self.cashier, "email", str(self.cashier))
        return f"{self.shift_number} - {cashier_label} ({self.get_status_display()})"


class Sale(UUIDTimeStampedModel):
    """POS transaction recording retail purchase."""
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        PARTIALLY_RETURNED = "PARTIALLY_RETURNED", "Partially Returned"
        FULLY_RETURNED = "FULLY_RETURNED", "Fully Returned"
        VOIDED = "VOIDED", "Voided"

    class PaymentStatus(models.TextChoices):
        PAID = "PAID", "Paid in Full"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        UNPAID = "UNPAID", "Unpaid"

    sale_number = models.CharField(max_length=30, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="pos_sales",
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pos_sales",
    )
    shift = models.ForeignKey(
        CashierShift,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sales",
    )
    member = models.ForeignKey(
        "members.Member",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pos_sales",
        help_text="Optional link to gym member profile.",
    )
    customer_name = models.CharField(max_length=150, blank=True, default="Walk-in Customer")
    customer_phone = models.CharField(max_length=30, blank=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.COMPLETED,
        db_index=True,
    )
    payment_status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
        db_index=True,
    )
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
    discount_reason = models.CharField(max_length=150, blank=True)
    discount_authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authorized_discounts",
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
    change_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    notes = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=120, blank=True, default="", db_index=True)
    completed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "POS Sale"
        verbose_name_plural = "Sales History"
        ordering = ("-completed_at", "-created_at")

    def __str__(self):
        return f"{self.sale_number} ({self.total_amount} ETB) - {self.customer_name}"


class SaleItem(UUIDTimeStampedModel):
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="sale_items",
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_quantity],
    )
    returned_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_quantity],
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_non_negative_amount],
        help_text="Historical cost price snapshot.",
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
        help_text="Historical selling price snapshot.",
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    tax_rate = models.DecimalField(
        max_digits=5,
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
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )

    class Meta:
        verbose_name = "Sale Item"
        verbose_name_plural = "Sale Items"

    def __str__(self):
        return f"{self.product.name} x {self.quantity} @ {self.unit_price} ETB"

    @property
    def remaining_returnable_quantity(self):
        return max(Decimal("0.00"), self.quantity - self.returned_quantity)


class SalePayment(UUIDTimeStampedModel):
    """Payment tendered for a POS sale."""
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    payment_method = models.ForeignKey(
        "billing.PaymentMethod",
        on_delete=models.PROTECT,
        related_name="pos_payments",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    tendered_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    change_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[validate_non_negative_amount],
    )
    reference = models.CharField(max_length=100, blank=True)
    is_verified = models.BooleanField(default=True, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_pos_payments",
    )
    verification_notes = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        verbose_name = "Sale Payment"
        verbose_name_plural = "Sale Payments"

    def __str__(self):
        return f"{self.sale.sale_number} - {self.payment_method.name}: {self.amount} ETB"


class SaleReceipt(UUIDTimeStampedModel):
    """Thermal or digital receipt record."""
    receipt_number = models.CharField(max_length=30, unique=True, db_index=True)
    sale = models.OneToOneField(
        Sale,
        on_delete=models.CASCADE,
        related_name="receipt",
    )
    receipt_data = models.JSONField(default=dict)
    issued_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Sale Receipt"
        verbose_name_plural = "Sale Receipts"

    def __str__(self):
        return f"Receipt {self.receipt_number} for {self.sale.sale_number}"


class SaleReturn(UUIDTimeStampedModel):
    """Customer return and refund transaction."""
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    return_number = models.CharField(max_length=30, unique=True, db_index=True)
    sale = models.ForeignKey(
        Sale,
        on_delete=models.PROTECT,
        related_name="returns",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="pos_returns",
    )
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="processed_pos_returns",
    )
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="authorized_pos_returns",
    )
    reason = models.CharField(max_length=200)
    refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    refund_method = models.ForeignKey(
        "billing.PaymentMethod",
        on_delete=models.PROTECT,
        related_name="pos_refunds",
    )
    restock_items = models.BooleanField(
        default=True,
        help_text="Whether to restore returned resellable items back to branch inventory.",
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.COMPLETED,
        db_index=True,
    )
    returned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Sale Return"
        verbose_name_plural = "Sale Returns"
        ordering = ("-returned_at",)

    def __str__(self):
        return f"{self.return_number} for {self.sale.sale_number} ({self.refund_amount} ETB)"


class SaleReturnItem(UUIDTimeStampedModel):
    class Condition(models.TextChoices):
        RESELLABLE = "RESELLABLE", "Resellable / Good Condition"
        DAMAGED = "DAMAGED", "Damaged / Defective"
        EXPIRED = "EXPIRED", "Expired"

    sale_return = models.ForeignKey(
        SaleReturn,
        on_delete=models.CASCADE,
        related_name="items",
    )
    sale_item = models.ForeignKey(
        SaleItem,
        on_delete=models.PROTECT,
        related_name="return_entries",
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_quantity],
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[validate_positive_amount],
    )
    condition = models.CharField(
        max_length=20,
        choices=Condition.choices,
        default=Condition.RESELLABLE,
    )
    restocked = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Sale Return Item"
        verbose_name_plural = "Sale Return Items"

    def __str__(self):
        return f"{self.sale_item.product.name} x {self.quantity} (Refund: {self.refund_amount} ETB)"


class PosSettlementBatch(UUIDTimeStampedModel):
    """
    Durable settlement batch linking verified POS sale payments and returns
    to Phase 10 FinancialAccount balance adjustments.
    Follows Phase 10 lessons learned: guarantees exactly-once posting via DB unique constraints.
    """
    batch_number = models.CharField(max_length=30, unique=True, db_index=True)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="pos_settlement_batches",
    )
    financial_account = models.ForeignKey(
        "finance.FinancialAccount",
        on_delete=models.PROTECT,
        related_name="pos_settlements",
    )
    payment_method_type = models.CharField(max_length=30)
    period_start = models.DateField()
    period_end = models.DateField()
    sale_payment_count = models.PositiveIntegerField(default=0)
    total_sale_payments = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    return_count = models.PositiveIntegerField(default=0)
    total_returns = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    net_collections = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    settled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="settled_pos_batches",
    )
    settled_at = models.DateTimeField(default=timezone.now)
    account_adjustment = models.ForeignKey(
        "finance.AccountAdjustment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_pos_settlements",
    )
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "POS Settlement Batch"
        verbose_name_plural = "POS Settlement Batches"
        ordering = ("-settled_at",)

    def __str__(self):
        return f"{self.batch_number} - {self.branch.code} [{self.payment_method_type}]: {self.net_collections} ETB"


class PosSettledPayment(UUIDTimeStampedModel):
    """
    Database-level UNIQUE constraint enforcing that a SalePayment can be settled
    into at most one PosSettlementBatch across all accounts, dates, and attempts.
    """
    payment = models.OneToOneField(
        SalePayment,
        on_delete=models.CASCADE,
        related_name="settlement_entry",
    )
    settlement_batch = models.ForeignKey(
        PosSettlementBatch,
        on_delete=models.CASCADE,
        related_name="settled_payments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "POS Settled Payment"
        verbose_name_plural = "POS Settled Payments"

    def __str__(self):
        return f"Settled Payment {self.payment.id} in Batch {self.settlement_batch.batch_number}"


class PosSettledReturn(UUIDTimeStampedModel):
    """
    Database-level UNIQUE constraint enforcing that a SaleReturn refund deduction can be settled
    into at most one PosSettlementBatch.
    """
    sale_return = models.OneToOneField(
        SaleReturn,
        on_delete=models.CASCADE,
        related_name="settlement_entry",
    )
    settlement_batch = models.ForeignKey(
        PosSettlementBatch,
        on_delete=models.CASCADE,
        related_name="settled_returns",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "POS Settled Return"
        verbose_name_plural = "POS Settled Returns"

    def __str__(self):
        return f"Settled Return {self.sale_return.return_number} in Batch {self.settlement_batch.batch_number}"
