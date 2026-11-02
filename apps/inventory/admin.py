from decimal import Decimal
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from apps.accounts.roles import is_owner
from .models import (
    CashierShift,
    GoodsReceivedItem,
    GoodsReceivedNote,
    Product,
    ProductCategory,
    PurchaseOrder,
    PurchaseOrderItem,
    Sale,
    SaleItem,
    SalePayment,
    SaleReceipt,
    SaleReturn,
    SaleReturnItem,
    StockAdjustment,
    StockAdjustmentItem,
    StockBatch,
    StockCount,
    StockCountItem,
    StockItem,
    StockMovement,
    StockTransfer,
    StockTransferItem,
    PosSettlementBatch,
    PosSettledPayment,
    PosSettledReturn,
)
from .permissions import accessible_branches_for_inventory
from .services import (
    approve_purchase_order,
    approve_stock_adjustment,
    dispatch_stock_transfer,
    receive_stock_transfer,
    reconcile_stock_count,
    submit_purchase_order,
)


# -----------------------------------------------------------------------------
# Inlines
# -----------------------------------------------------------------------------

class StockBatchInline(TabularInline):
    model = StockBatch
    extra = 0
    classes = ("collapse",)
    collapsible = True
    fields = ("batch_number", "quantity", "expiry_date", "cost_price", "received_date")


class PurchaseOrderItemInline(TabularInline):
    model = PurchaseOrderItem
    extra = 1
    classes = ("collapse",)
    collapsible = True
    fields = ("product", "quantity_ordered", "quantity_received", "unit_cost", "total_cost")


class GoodsReceivedItemInline(TabularInline):
    model = GoodsReceivedItem
    extra = 1
    classes = ("collapse",)
    collapsible = True
    fields = ("product", "po_item", "quantity_received", "unit_cost", "total_cost", "batch_number", "expiry_date")


class StockTransferItemInline(TabularInline):
    model = StockTransferItem
    extra = 1
    classes = ("collapse",)
    collapsible = True
    fields = ("product", "quantity_sent", "quantity_received")


class StockAdjustmentItemInline(TabularInline):
    model = StockAdjustmentItem
    extra = 1
    classes = ("collapse",)
    collapsible = True
    fields = ("product", "quantity_change", "unit_cost", "total_cost", "reason_detail")


class StockCountItemInline(TabularInline):
    model = StockCountItem
    extra = 0
    classes = ("collapse",)
    collapsible = True
    fields = ("product", "system_quantity", "counted_quantity", "variance", "unit_cost", "variance_cost", "notes")


class SaleItemInline(TabularInline):
    model = SaleItem
    extra = 0
    classes = ("collapse",)
    collapsible = True
    readonly_fields = ("product", "quantity", "returned_quantity", "unit_price", "discount_amount", "tax_amount", "total_price")
    can_delete = False


class SalePaymentInline(TabularInline):
    model = SalePayment
    extra = 0
    classes = ("collapse",)
    collapsible = True
    readonly_fields = ("payment_method", "amount", "tendered_amount", "change_amount", "reference", "is_verified", "verified_at")
    can_delete = False


class SaleReturnInline(TabularInline):
    model = SaleReturn
    extra = 0
    classes = ("collapse",)
    collapsible = True
    readonly_fields = ("return_number", "reason", "refund_amount", "refund_method", "restock_items", "status", "returned_at")
    can_delete = False


class PosSettledPaymentInline(TabularInline):
    model = PosSettledPayment
    extra = 0
    classes = ("collapse",)
    collapsible = True
    readonly_fields = ("payment", "amount")
    can_delete = False


class PosSettledReturnInline(TabularInline):
    model = PosSettledReturn
    extra = 0
    classes = ("collapse",)
    collapsible = True
    readonly_fields = ("sale_return", "amount")
    can_delete = False


# -----------------------------------------------------------------------------
# ModelAdmins
# -----------------------------------------------------------------------------

@admin.register(ProductCategory)
class ProductCategoryAdmin(ModelAdmin):
    list_display = ("name", "code", "parent", "is_active_badge")
    list_filter = ("is_active",)
    search_fields = ("name", "code")

    fieldsets = (
        ("Category Details / የምድብ መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("name", "code"),
                ("parent", "is_active"),
                "description",
            ),
        }),
    )

    @display(description="Status", boolean=True)
    def is_active_badge(self, obj):
        return obj.is_active


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    list_display = (
        "name",
        "sku",
        "barcode",
        "category",
        "selling_price_display",
        "cost_price_display",
        "unit_of_measurement",
        "low_stock_threshold",
        "tracks_expiry",
        "is_active_badge",
    )
    list_filter = ("category", "unit_of_measurement", "is_active", "tracks_expiry")
    search_fields = ("name", "sku", "barcode")
    filter_horizontal = ("branches",)

    fieldsets = (
        ("Product Identity / የዕቃው መለያ", {
            "classes": ("collapse",),
            "fields": (
                ("name", "product_number"),
                ("sku", "barcode"),
                ("category", "unit_of_measurement"),
                "description",
                "image",
            ),
        }),
        ("Pricing & Taxation / ዋጋ እና ታክስ", {
            "classes": ("collapse",),
            "fields": (
                ("selling_price", "cost_price"),
                ("tax_rate", "is_tax_inclusive"),
            ),
        }),
        ("Inventory & Branch Controls / የክምችት እና ቅርንጫፍ ቁጥጥር", {
            "classes": ("collapse",),
            "fields": (
                ("low_stock_threshold", "tracks_expiry"),
                "is_active",
                "branches",
            ),
        }),
    )

    @display(description="Selling Price")
    def selling_price_display(self, obj):
        return f"{obj.selling_price:,.2f} ETB"

    @display(description="Cost Price")
    def cost_price_display(self, obj):
        return f"{obj.cost_price:,.2f} ETB"

    @display(description="Active", boolean=True)
    def is_active_badge(self, obj):
        return obj.is_active


@admin.register(StockItem)
class StockItemAdmin(ModelAdmin):
    list_display = (
        "product",
        "branch",
        "quantity_on_hand_display",
        "quantity_reserved",
        "average_cost_display",
        "total_value_display",
        "stock_status_badge",
    )
    list_filter = ("branch", "product__category")
    search_fields = ("product__name", "product__sku", "branch__name")
    inlines = [StockBatchInline]

    fieldsets = (
        ("Stock Summary / የክምችት አጠቃላይ መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("product", "branch"),
                ("quantity_on_hand", "quantity_reserved"),
                ("average_cost_price", "last_cost_price"),
                "reorder_level",
            ),
        }),
    )

    @display(description="On Hand")
    def quantity_on_hand_display(self, obj):
        return f"{obj.quantity_on_hand} {obj.product.unit_of_measurement}"

    @display(description="Avg Cost")
    def average_cost_display(self, obj):
        return f"{obj.average_cost_price:,.2f} ETB"

    @display(description="Inventory Value")
    def total_value_display(self, obj):
        return f"{obj.total_inventory_value:,.2f} ETB"

    @display(description="Stock Health")
    def stock_status_badge(self, obj):
        if obj.is_low_stock:
            return format_html(
                '<span class="px-2 py-0.5 rounded text-xs font-bold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">LOW STOCK</span>'
            )
        return format_html(
            '<span class="px-2 py-0.5 rounded text-xs font-bold bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">IN STOCK</span>'
        )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser or is_owner(request.user):
            return qs
        return qs.filter(branch__in=accessible_branches_for_inventory(request.user))


@admin.register(StockMovement)
class StockMovementAdmin(ModelAdmin):
    list_display = (
        "timestamp",
        "product_name",
        "branch_code",
        "movement_type_display",
        "quantity_change_display",
        "balance_after_display",
        "reference",
        "actor",
    )
    list_filter = ("movement_type", "stock_item__branch", "timestamp")
    search_fields = ("reference", "stock_item__product__name", "stock_item__product__sku")
    readonly_fields = [f.name for f in StockMovement._meta.fields]

    @display(description="Product")
    def product_name(self, obj):
        return obj.stock_item.product.name

    @display(description="Branch")
    def branch_code(self, obj):
        return obj.stock_item.branch.code

    @display(description="Type")
    def movement_type_display(self, obj):
        return obj.get_movement_type_display()

    @display(description="Change")
    def quantity_change_display(self, obj):
        sign = "+" if obj.quantity_change > 0 else ""
        color = "text-emerald-600" if obj.quantity_change > 0 else "text-red-600"
        return format_html(f'<span class="font-bold {color}">{sign}{obj.quantity_change}</span>')

    @display(description="Balance After")
    def balance_after_display(self, obj):
        return f"{obj.balance_after} {obj.stock_item.product.unit_of_measurement}"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ModelAdmin):
    list_display = (
        "po_number",
        "branch",
        "supplier",
        "status_badge",
        "order_date",
        "total_amount_display",
        "created_by",
    )
    list_filter = ("status", "branch", "order_date")
    search_fields = ("po_number", "supplier__name")
    inlines = [PurchaseOrderItemInline]
    actions = ["action_submit", "action_approve"]

    fieldsets = (
        ("Purchase Order / የግዢ ትዕዛዝ", {
            "classes": ("collapse",),
            "fields": (
                ("po_number", "branch"),
                ("supplier", "status"),
                ("order_date", "expected_delivery_date"),
                "notes",
            ),
        }),
        ("Financial Totals / የክፍያ ድምር", {
            "classes": ("collapse",),
            "fields": (
                ("subtotal", "tax_amount"),
                "total_amount",
            ),
        }),
        ("Approval Audit / ማጽደቂያ መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("created_by", "approved_by"),
                "approved_at",
            ),
        }),
    )

    @display(description="Total")
    def total_amount_display(self, obj):
        return f"{obj.total_amount:,.2f} ETB"

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            PurchaseOrder.Status.DRAFT: "bg-base-200 text-base-800",
            PurchaseOrder.Status.SUBMITTED: "bg-blue-100 text-blue-800",
            PurchaseOrder.Status.APPROVED: "bg-emerald-100 text-emerald-800",
            PurchaseOrder.Status.PARTIALLY_RECEIVED: "bg-amber-100 text-amber-800",
            PurchaseOrder.Status.RECEIVED: "bg-emerald-200 text-emerald-900",
            PurchaseOrder.Status.CANCELLED: "bg-red-100 text-red-800",
        }
        color = colors.get(obj.status, "bg-base-100 text-base-700")
        return format_html(f'<span class="px-2 py-0.5 rounded text-xs font-bold {color}">{obj.get_status_display()}</span>')

    @action(description="Submit Selected Draft POs")
    def action_submit(self, request, queryset):
        for po in queryset:
            try:
                submit_purchase_order(request.user, po)
            except ValidationError as e:
                self.message_user(request, str(e), level=messages.ERROR)
        self.message_user(request, _("POs submitted successfully."))

    @action(description="Approve Selected POs")
    def action_approve(self, request, queryset):
        for po in queryset:
            try:
                approve_purchase_order(request.user, po)
            except ValidationError as e:
                self.message_user(request, str(e), level=messages.ERROR)
        self.message_user(request, _("POs approved successfully."))


@admin.register(GoodsReceivedNote)
class GoodsReceivedNoteAdmin(ModelAdmin):
    list_display = (
        "grn_number",
        "branch",
        "supplier",
        "received_date",
        "delivery_note_reference",
        "received_by",
        "expense_record",
    )
    list_filter = ("branch", "received_date")
    search_fields = ("grn_number", "supplier__name", "delivery_note_reference")
    inlines = [GoodsReceivedItemInline]


@admin.register(StockTransfer)
class StockTransferAdmin(ModelAdmin):
    list_display = (
        "transfer_number",
        "source_branch",
        "target_branch",
        "status_badge",
        "dispatched_by",
        "received_by",
        "created_at",
    )
    list_filter = ("status", "source_branch", "target_branch")
    search_fields = ("transfer_number",)
    inlines = [StockTransferItemInline]
    actions = ["action_dispatch", "action_receive"]

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            StockTransfer.Status.PENDING: "bg-amber-100 text-amber-800",
            StockTransfer.Status.DISPATCHED: "bg-blue-100 text-blue-800",
            StockTransfer.Status.COMPLETED: "bg-emerald-100 text-emerald-800",
            StockTransfer.Status.CANCELLED: "bg-red-100 text-red-800",
        }
        color = colors.get(obj.status, "bg-base-100 text-base-700")
        return format_html(f'<span class="px-2 py-0.5 rounded text-xs font-bold {color}">{obj.get_status_display()}</span>')

    @action(description="Dispatch Selected Transfers (Deduct Source Stock)")
    def action_dispatch(self, request, queryset):
        for tr in queryset:
            try:
                dispatch_stock_transfer(request.user, tr)
            except ValidationError as e:
                self.message_user(request, str(e), level=messages.ERROR)
        self.message_user(request, _("Transfers dispatched."))

    @action(description="Receive Selected Transfers (Intake Target Stock)")
    def action_receive(self, request, queryset):
        for tr in queryset:
            try:
                receive_stock_transfer(request.user, tr)
            except ValidationError as e:
                self.message_user(request, str(e), level=messages.ERROR)
        self.message_user(request, _("Transfers received into target inventory."))


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(ModelAdmin):
    list_display = (
        "adjustment_number",
        "branch",
        "reason",
        "status_badge",
        "created_by",
        "approved_by",
        "created_at",
    )
    list_filter = ("reason", "status", "branch")
    search_fields = ("adjustment_number",)
    inlines = [StockAdjustmentItemInline]
    actions = ["action_approve"]

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            StockAdjustment.Status.DRAFT: "bg-amber-100 text-amber-800",
            StockAdjustment.Status.APPROVED: "bg-emerald-100 text-emerald-800",
            StockAdjustment.Status.CANCELLED: "bg-red-100 text-red-800",
        }
        color = colors.get(obj.status, "bg-base-100 text-base-700")
        return format_html(f'<span class="px-2 py-0.5 rounded text-xs font-bold {color}">{obj.get_status_display()}</span>')

    @action(description="Approve & Apply Selected Adjustments")
    def action_approve(self, request, queryset):
        for adj in queryset:
            try:
                approve_stock_adjustment(request.user, adj)
            except ValidationError as e:
                self.message_user(request, str(e), level=messages.ERROR)
        self.message_user(request, _("Stock adjustments approved."))


@admin.register(StockCount)
class StockCountAdmin(ModelAdmin):
    list_display = (
        "count_number",
        "branch",
        "count_date",
        "status_badge",
        "counted_by",
        "reconciled_by",
    )
    list_filter = ("status", "branch", "count_date")
    search_fields = ("count_number",)
    inlines = [StockCountItemInline]
    actions = ["action_reconcile"]

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            StockCount.Status.DRAFT: "bg-amber-100 text-amber-800",
            StockCount.Status.COMPLETED: "bg-blue-100 text-blue-800",
            StockCount.Status.RECONCILED: "bg-emerald-100 text-emerald-800",
            StockCount.Status.CANCELLED: "bg-red-100 text-red-800",
        }
        color = colors.get(obj.status, "bg-base-100 text-base-700")
        return format_html(f'<span class="px-2 py-0.5 rounded text-xs font-bold {color}">{obj.get_status_display()}</span>')

    @action(description="Reconcile & Apply Physical Count Differences")
    def action_reconcile(self, request, queryset):
        for sc in queryset:
            try:
                reconcile_stock_count(request.user, sc)
            except ValidationError as e:
                self.message_user(request, str(e), level=messages.ERROR)
        self.message_user(request, _("Physical counts reconciled."))


@admin.register(CashierShift)
class CashierShiftAdmin(ModelAdmin):
    list_display = (
        "shift_number",
        "branch",
        "cashier",
        "status_badge",
        "opened_at",
        "closed_at",
        "opening_float_display",
        "expected_cash_display",
        "declared_cash_display",
        "variance_display",
    )
    list_filter = ("status", "branch", "opened_at")
    search_fields = ("shift_number", "cashier__username", "cashier__first_name")

    fieldsets = (
        ("Cashier Shift / የካሼር ፈረቃ", {
            "classes": ("collapse",),
            "fields": (
                ("shift_number", "branch"),
                ("cashier", "status"),
                ("opened_at", "closed_at"),
            ),
        }),
        ("Cash Reconciliation / የገንዘብ ማስታረቂያ", {
            "classes": ("collapse",),
            "fields": (
                ("opening_float", "expected_cash"),
                ("declared_cash", "cash_variance"),
                "closing_notes",
            ),
        }),
    )

    @display(description="Status")
    def status_badge(self, obj):
        color = "bg-emerald-100 text-emerald-800" if obj.status == CashierShift.Status.OPEN else "bg-base-200 text-base-800"
        return format_html(f'<span class="px-2 py-0.5 rounded text-xs font-bold {color}">{obj.get_status_display()}</span>')

    @display(description="Float")
    def opening_float_display(self, obj):
        return f"{obj.opening_float:,.2f} ETB"

    @display(description="Expected Cash")
    def expected_cash_display(self, obj):
        return f"{obj.expected_cash:,.2f} ETB"

    @display(description="Declared Cash")
    def declared_cash_display(self, obj):
        return f"{obj.declared_cash:,.2f} ETB"

    @display(description="Variance")
    def variance_display(self, obj):
        color = "text-emerald-600" if obj.cash_variance >= Decimal("0.00") else "text-red-600"
        return format_html(f'<span class="font-bold {color}">{obj.cash_variance:,.2f} ETB</span>')


@admin.register(Sale)
class SaleAdmin(ModelAdmin):
    list_display = (
        "sale_number",
        "branch",
        "customer_name",
        "status_badge",
        "payment_status",
        "total_amount_display",
        "paid_amount_display",
        "cashier",
        "completed_at",
    )
    list_filter = ("status", "payment_status", "branch", "completed_at")
    search_fields = ("sale_number", "customer_name", "customer_phone")
    inlines = [SaleItemInline, SalePaymentInline, SaleReturnInline]

    fieldsets = (
        ("Sale Header / የሽያጭ መረጃ", {
            "classes": ("collapse",),
            "fields": (
                ("sale_number", "branch"),
                ("cashier", "shift"),
                ("member", "customer_name"),
                ("customer_phone", "status"),
                "payment_status",
            ),
        }),
        ("Financial Totals / የሽያጭ ሂሳብ", {
            "classes": ("collapse",),
            "fields": (
                ("subtotal", "tax_amount"),
                ("discount_amount", "discount_reason"),
                "discount_authorized_by",
                ("total_amount", "paid_amount"),
                "change_amount",
                "notes",
            ),
        }),
    )

    @display(description="Total")
    def total_amount_display(self, obj):
        return f"{obj.total_amount:,.2f} ETB"

    @display(description="Paid")
    def paid_amount_display(self, obj):
        return f"{obj.paid_amount:,.2f} ETB"

    @display(description="Status")
    def status_badge(self, obj):
        colors = {
            Sale.Status.COMPLETED: "bg-emerald-100 text-emerald-800",
            Sale.Status.PARTIALLY_RETURNED: "bg-amber-100 text-amber-800",
            Sale.Status.FULLY_RETURNED: "bg-red-100 text-red-800",
            Sale.Status.VOIDED: "bg-base-200 text-base-700",
        }
        color = colors.get(obj.status, "bg-base-100 text-base-700")
        return format_html(f'<span class="px-2 py-0.5 rounded text-xs font-bold {color}">{obj.get_status_display()}</span>')


@admin.register(PosSettlementBatch)
class PosSettlementBatchAdmin(ModelAdmin):
    list_display = (
        "batch_number",
        "branch",
        "financial_account",
        "payment_method_type",
        "period_display",
        "sale_payment_count",
        "return_count",
        "net_collections_display",
        "settled_by",
        "settled_at",
    )
    list_filter = ("branch", "payment_method_type", "settled_at")
    search_fields = ("batch_number", "financial_account__name")
    inlines = [PosSettledPaymentInline, PosSettledReturnInline]
    readonly_fields = [f.name for f in PosSettlementBatch._meta.fields]

    @display(description="Period")
    def period_display(self, obj):
        return f"{obj.period_start} to {obj.period_end}"

    @display(description="Net Collections")
    def net_collections_display(self, obj):
        return f"{obj.net_collections:,.2f} ETB"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
