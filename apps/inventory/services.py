from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.roles import is_owner
from apps.branches.models import Branch
from apps.billing.models import PaymentMethod
from apps.finance.models import (
    FinancialAccount,
    AccountAdjustment,
    Expense,
    ExpenseCategory,
    FinanceAuditEvent,
    Supplier,
)
from apps.finance.services import check_closed_reconciliation, perform_account_adjustment
from .models import (
    CashierShift,
    GoodsReceivedItem,
    GoodsReceivedNote,
    InventorySequence,
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
    generate_next_inventory_number,
)
from .permissions import (
    can_authorize_discount,
    can_authorize_refund,
    can_manage_inventory,
    can_operate_pos,
    can_approve_purchase_order,
    can_reconcile_stock_count,
    can_settle_pos_to_finance,
)


# -----------------------------------------------------------------------------
# 1. Product & Category Management
# -----------------------------------------------------------------------------

def create_product(
    actor,
    name: str,
    sku: str,
    category: ProductCategory,
    unit_of_measurement: str,
    cost_price: Decimal,
    selling_price: Decimal,
    tax_rate: Decimal = Decimal("0.00"),
    is_tax_inclusive: bool = True,
    barcode: Optional[str] = None,
    low_stock_threshold: int = 5,
    tracks_expiry: bool = False,
    description: str = "",
    image=None,
    branches=None,
) -> Product:
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to create products."))

    prd_number = generate_next_inventory_number("product", "PRD")
    product = Product.objects.create(
        product_number=prd_number,
        name=name.strip(),
        sku=sku.strip(),
        barcode=barcode.strip() if barcode else None,
        category=category,
        unit_of_measurement=unit_of_measurement,
        cost_price=cost_price,
        selling_price=selling_price,
        tax_rate=tax_rate,
        is_tax_inclusive=is_tax_inclusive,
        low_stock_threshold=low_stock_threshold,
        tracks_expiry=tracks_expiry,
        description=description.strip(),
        image=image,
    )
    if branches:
        product.branches.set(branches)
    return product


# -----------------------------------------------------------------------------
# 2. Stock Item & Movement Management
# -----------------------------------------------------------------------------

def get_or_create_stock_item(branch: Branch, product: Product) -> StockItem:
    """Safely fetch or create branch StockItem record."""
    stock_item, _created = StockItem.objects.get_or_create(
        branch=branch,
        product=product,
        defaults={
            "quantity_on_hand": Decimal("0.00"),
            "quantity_reserved": Decimal("0.00"),
            "reorder_level": product.low_stock_threshold,
            "last_cost_price": product.cost_price,
            "average_cost_price": product.cost_price,
        },
    )
    return stock_item


def adjust_stock(
    actor,
    branch: Branch,
    product: Product,
    quantity_change: Decimal,
    movement_type: str,
    reference: str,
    notes: str = "",
    unit_cost: Optional[Decimal] = None,
) -> StockMovement:
    """
    Atomic, concurrency-safe inventory adjustment with strict negative stock prevention
    and weighted average cost calculation.
    """
    with transaction.atomic():
        # Row lock StockItem to serialize all concurrent updates on this branch & product
        stock_item, _created = StockItem.objects.select_for_update().get_or_create(
            branch=branch,
            product=product,
            defaults={
                "quantity_on_hand": Decimal("0.00"),
                "quantity_reserved": Decimal("0.00"),
                "reorder_level": product.low_stock_threshold,
                "last_cost_price": product.cost_price,
                "average_cost_price": product.cost_price,
            },
        )

        bal_before = stock_item.quantity_on_hand
        bal_after = bal_before + quantity_change

        if bal_after < Decimal("0.00"):
            raise ValidationError(
                _(f"Insufficient stock for '{product.name}' at branch '{branch.name}'. "
                  f"Available: {bal_before} {product.unit_of_measurement}, requested: {abs(quantity_change)}.")
            )

        # Cost recalculation for positive inventory intake
        cost = unit_cost if unit_cost is not None else stock_item.average_cost_price
        if quantity_change > Decimal("0.00") and cost > Decimal("0.00"):
            current_total_value = bal_before * stock_item.average_cost_price
            intake_value = quantity_change * cost
            new_qty = bal_after
            if new_qty > Decimal("0.00"):
                new_avg = (current_total_value + intake_value) / new_qty
                stock_item.average_cost_price = new_avg.quantize(Decimal("0.01"))
            stock_item.last_cost_price = cost

        stock_item.quantity_on_hand = bal_after
        stock_item.save(update_fields=["quantity_on_hand", "last_cost_price", "average_cost_price", "updated_at"])

        movement = StockMovement.objects.create(
            stock_item=stock_item,
            movement_type=movement_type,
            quantity_change=quantity_change,
            balance_before=bal_before,
            balance_after=bal_after,
            unit_cost=cost,
            reference=reference,
            notes=notes,
            actor=actor,
        )
        return movement


# -----------------------------------------------------------------------------
# 3. Purchasing & Goods Received Notes (GRN)
# -----------------------------------------------------------------------------

def create_purchase_order(
    actor,
    branch: Branch,
    supplier: Supplier,
    items_data: List[Dict[str, Any]],
    expected_delivery_date=None,
    notes: str = "",
) -> PurchaseOrder:
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to create purchase orders."))

    if not items_data:
        raise ValidationError(_("Purchase order must contain at least one item."))

    with transaction.atomic():
        po_num = generate_next_inventory_number("purchase_order", "PO")
        po = PurchaseOrder.objects.create(
            po_number=po_num,
            branch=branch,
            supplier=supplier,
            status=PurchaseOrder.Status.DRAFT,
            order_date=timezone.localdate(),
            expected_delivery_date=expected_delivery_date,
            created_by=actor,
            notes=notes,
        )

        subtotal = Decimal("0.00")
        for item in items_data:
            product = item["product"]
            qty = Decimal(str(item["quantity"]))
            unit_cost = Decimal(str(item.get("unit_cost", product.cost_price)))
            total_cost = (qty * unit_cost).quantize(Decimal("0.01"))

            PurchaseOrderItem.objects.create(
                purchase_order=po,
                product=product,
                quantity_ordered=qty,
                quantity_received=Decimal("0.00"),
                unit_cost=unit_cost,
                total_cost=total_cost,
            )
            subtotal += total_cost

        po.subtotal = subtotal
        po.total_amount = subtotal
        po.save(update_fields=["subtotal", "total_amount"])
        return po


def submit_purchase_order(actor, po: PurchaseOrder) -> PurchaseOrder:
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(_("Only draft purchase orders can be submitted."))
    po.status = PurchaseOrder.Status.SUBMITTED
    po.save(update_fields=["status", "updated_at"])
    return po


def approve_purchase_order(actor, po: PurchaseOrder) -> PurchaseOrder:
    if not can_approve_purchase_order(actor):
        raise ValidationError(_("You do not have permission to approve purchase orders."))
    if po.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.SUBMITTED):
        raise ValidationError(_("Only draft or submitted purchase orders can be approved."))

    po.status = PurchaseOrder.Status.APPROVED
    po.approved_by = actor
    po.approved_at = timezone.now()
    po.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    return po


def receive_goods_note(
    actor,
    branch: Branch,
    supplier: Supplier,
    items_data: List[Dict[str, Any]],
    purchase_order: Optional[PurchaseOrder] = None,
    delivery_note_reference: str = "",
    create_expense_liability: bool = True,
    expense_category: Optional[ExpenseCategory] = None,
    notes: str = "",
) -> GoodsReceivedNote:
    """
    Intake physical inventory, adjust stock counts, update PO fulfillment,
    and create integrated Phase 10 unpaid Expense liability for the supplier.
    """
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to receive inventory."))

    if not items_data:
        raise ValidationError(_("Goods Received Note must have at least one item."))

    with transaction.atomic():
        grn_num = generate_next_inventory_number("goods_received_note", "GRN")
        grn = GoodsReceivedNote.objects.create(
            grn_number=grn_num,
            purchase_order=purchase_order,
            branch=branch,
            supplier=supplier,
            received_date=timezone.localdate(),
            delivery_note_reference=delivery_note_reference,
            received_by=actor,
            notes=notes,
        )

        total_intake_cost = Decimal("0.00")

        for item in items_data:
            product = item["product"]
            qty = Decimal(str(item["quantity"]))
            unit_cost = Decimal(str(item.get("unit_cost", product.cost_price)))
            total_cost = (qty * unit_cost).quantize(Decimal("0.01"))
            batch_num = item.get("batch_number", "")
            exp_date = item.get("expiry_date")
            po_item = item.get("po_item")

            GoodsReceivedItem.objects.create(
                grn=grn,
                product=product,
                po_item=po_item,
                quantity_received=qty,
                unit_cost=unit_cost,
                total_cost=total_cost,
                batch_number=batch_num,
                expiry_date=exp_date,
            )

            # Atomic stock increment
            adjust_stock(
                actor=actor,
                branch=branch,
                product=product,
                quantity_change=qty,
                movement_type=StockMovement.MovementType.GOODS_RECEIVED,
                reference=grn_num,
                notes=f"GRN intake from {supplier.name}",
                unit_cost=unit_cost,
            )

            # Update PO item if linked
            if po_item:
                po_item.quantity_received += qty
                po_item.save(update_fields=["quantity_received", "updated_at"])

            total_intake_cost += total_cost

        # Update PO overall status if linked
        if purchase_order:
            all_items = purchase_order.items.all()
            all_fulfilled = all(it.quantity_received >= it.quantity_ordered for it in all_items)
            any_fulfilled = any(it.quantity_received > Decimal("0.00") for it in all_items)
            if all_fulfilled:
                purchase_order.status = PurchaseOrder.Status.RECEIVED
            elif any_fulfilled:
                purchase_order.status = PurchaseOrder.Status.PARTIALLY_RECEIVED
            purchase_order.save(update_fields=["status", "updated_at"])

        # Create integrated Phase 10 Expense liability (WITHOUT automatically marking paid)
        if create_expense_liability and total_intake_cost > Decimal("0.00"):
            category = expense_category or ExpenseCategory.objects.filter(is_active=True).first()
            if category:
                exp_ref = f"EXP-GRN-{grn_num}"
                expense = Expense.objects.create(
                    reference=exp_ref,
                    branch=branch,
                    category=category,
                    supplier=supplier,
                    description=f"Inventory procurement: {grn_num} ({delivery_note_reference})",
                    amount=total_intake_cost,
                    tax_amount=Decimal("0.00"),
                    total_amount=total_intake_cost,
                    balance_due=total_intake_cost,
                    paid_amount=Decimal("0.00"),
                    expense_date=timezone.localdate(),
                    approval_status=Expense.ApprovalStatus.SUBMITTED,
                    payment_status=Expense.PaymentStatus.UNPAID,
                    created_by=actor,
                )
                grn.expense_record = expense
                grn.save(update_fields=["expense_record"])

        return grn


# -----------------------------------------------------------------------------
# 4. Stock Transfers, Adjustments & Physical Counts
# -----------------------------------------------------------------------------

def create_stock_transfer(
    actor,
    source_branch: Branch,
    target_branch: Branch,
    items_data: List[Dict[str, Any]],
    notes: str = "",
) -> StockTransfer:
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to create stock transfers."))

    if source_branch == target_branch:
        raise ValidationError(_("Source and target branch cannot be the same."))

    if not items_data:
        raise ValidationError(_("Transfer must contain at least one item."))

    with transaction.atomic():
        tr_num = generate_next_inventory_number("stock_transfer", "ST")
        transfer = StockTransfer.objects.create(
            transfer_number=tr_num,
            source_branch=source_branch,
            target_branch=target_branch,
            status=StockTransfer.Status.PENDING,
            notes=notes,
        )

        for item in items_data:
            StockTransferItem.objects.create(
                transfer=transfer,
                product=item["product"],
                quantity_sent=Decimal(str(item["quantity"])),
                quantity_received=Decimal("0.00"),
            )
        return transfer


def dispatch_stock_transfer(actor, transfer: StockTransfer) -> StockTransfer:
    """Dispatches transfer, deducting inventory from source branch."""
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to dispatch stock transfers."))

    if transfer.status != StockTransfer.Status.PENDING:
        raise ValidationError(_("Only pending transfers can be dispatched."))

    with transaction.atomic():
        for item in transfer.items.all():
            adjust_stock(
                actor=actor,
                branch=transfer.source_branch,
                product=item.product,
                quantity_change=-item.quantity_sent,
                movement_type=StockMovement.MovementType.TRANSFER_OUT,
                reference=transfer.transfer_number,
                notes=f"Transfer to {transfer.target_branch.name}",
            )

        transfer.status = StockTransfer.Status.DISPATCHED
        transfer.dispatched_by = actor
        transfer.dispatched_at = timezone.now()
        transfer.save(update_fields=["status", "dispatched_by", "dispatched_at", "updated_at"])
        return transfer


def receive_stock_transfer(
    actor,
    transfer: StockTransfer,
    received_items_data: Optional[List[Dict[str, Any]]] = None,
) -> StockTransfer:
    """Receives transfer into target branch inventory."""
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to receive stock transfers."))

    if transfer.status != StockTransfer.Status.DISPATCHED:
        raise ValidationError(_("Only dispatched transfers in transit can be received."))

    with transaction.atomic():
        items_map = {item.id: item for item in transfer.items.all()}

        if received_items_data:
            for rec in received_items_data:
                item_id = rec.get("item_id")
                qty = Decimal(str(rec["quantity_received"]))
                transfer_item = items_map.get(item_id)
                if transfer_item:
                    transfer_item.quantity_received = qty
                    transfer_item.save(update_fields=["quantity_received", "updated_at"])
                    adjust_stock(
                        actor=actor,
                        branch=transfer.target_branch,
                        product=transfer_item.product,
                        quantity_change=qty,
                        movement_type=StockMovement.MovementType.TRANSFER_IN,
                        reference=transfer.transfer_number,
                        notes=f"Transfer received from {transfer.source_branch.name}",
                    )
        else:
            # Default receive full dispatched quantity
            for transfer_item in transfer.items.all():
                transfer_item.quantity_received = transfer_item.quantity_sent
                transfer_item.save(update_fields=["quantity_received", "updated_at"])
                adjust_stock(
                    actor=actor,
                    branch=transfer.target_branch,
                    product=transfer_item.product,
                    quantity_change=transfer_item.quantity_sent,
                    movement_type=StockMovement.MovementType.TRANSFER_IN,
                    reference=transfer.transfer_number,
                    notes=f"Transfer received from {transfer.source_branch.name}",
                )

        transfer.status = StockTransfer.Status.COMPLETED
        transfer.received_by = actor
        transfer.received_at = timezone.now()
        transfer.save(update_fields=["status", "received_by", "received_at", "updated_at"])
        return transfer


def create_stock_adjustment(
    actor,
    branch: Branch,
    reason: str,
    items_data: List[Dict[str, Any]],
    notes: str = "",
) -> StockAdjustment:
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to create stock adjustments."))

    with transaction.atomic():
        adj_num = generate_next_inventory_number("stock_adjustment", "SA")
        adj = StockAdjustment.objects.create(
            adjustment_number=adj_num,
            branch=branch,
            reason=reason,
            status=StockAdjustment.Status.DRAFT,
            created_by=actor,
            notes=notes,
        )

        for item in items_data:
            product = item["product"]
            qty_change = Decimal(str(item["quantity_change"]))
            cost = Decimal(str(item.get("unit_cost", product.cost_price)))
            total_cost = (abs(qty_change) * cost).quantize(Decimal("0.01"))
            StockAdjustmentItem.objects.create(
                adjustment=adj,
                product=product,
                quantity_change=qty_change,
                unit_cost=cost,
                total_cost=total_cost,
                reason_detail=item.get("reason_detail", ""),
            )
        return adj


def approve_stock_adjustment(actor, adjustment: StockAdjustment) -> StockAdjustment:
    """Applies stock adjustment to inventory."""
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to approve stock adjustments."))

    if adjustment.status != StockAdjustment.Status.DRAFT:
        raise ValidationError(_("Only draft stock adjustments can be approved."))

    with transaction.atomic():
        m_type = (
            StockMovement.MovementType.DAMAGE_WASTAGE
            if adjustment.reason in (StockAdjustment.Reason.DAMAGE, StockAdjustment.Reason.EXPIRY)
            else StockMovement.MovementType.MANUAL_ADJUSTMENT
        )

        for item in adjustment.items.all():
            adjust_stock(
                actor=actor,
                branch=adjustment.branch,
                product=item.product,
                quantity_change=item.quantity_change,
                movement_type=m_type,
                reference=adjustment.adjustment_number,
                notes=f"Adjustment: {adjustment.get_reason_display()} - {item.reason_detail}",
                unit_cost=item.unit_cost,
            )

        adjustment.status = StockAdjustment.Status.APPROVED
        adjustment.approved_by = actor
        adjustment.approved_at = timezone.now()
        adjustment.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        return adjustment


def record_stock_count(
    actor,
    branch: Branch,
    counted_items_data: List[Dict[str, Any]],
    notes: str = "",
) -> StockCount:
    if not can_manage_inventory(actor):
        raise ValidationError(_("You do not have permission to conduct physical stock counts."))

    with transaction.atomic():
        sc_num = generate_next_inventory_number("stock_count", "SC")
        sc = StockCount.objects.create(
            count_number=sc_num,
            branch=branch,
            count_date=timezone.localdate(),
            status=StockCount.Status.COMPLETED,
            counted_by=actor,
            notes=notes,
        )

        for item in counted_items_data:
            product = item["product"]
            counted_qty = Decimal(str(item["counted_quantity"]))
            stock_item = get_or_create_stock_item(branch, product)
            sys_qty = stock_item.quantity_on_hand
            variance = counted_qty - sys_qty
            cost = stock_item.average_cost_price
            variance_cost = (variance * cost).quantize(Decimal("0.01"))

            StockCountItem.objects.create(
                stock_count=sc,
                product=product,
                system_quantity=sys_qty,
                counted_quantity=counted_qty,
                variance=variance,
                unit_cost=cost,
                variance_cost=variance_cost,
                notes=item.get("notes", ""),
            )
        return sc


def reconcile_stock_count(actor, stock_count: StockCount) -> StockCount:
    """Adjusts physical stock differences to match counted inventory."""
    if not can_reconcile_stock_count(actor):
        raise ValidationError(_("You do not have permission to reconcile stock counts."))

    if stock_count.status != StockCount.Status.COMPLETED:
        raise ValidationError(_("Only completed stock counts can be reconciled."))

    with transaction.atomic():
        for item in stock_count.items.all():
            if item.variance != Decimal("0.00"):
                adjust_stock(
                    actor=actor,
                    branch=stock_count.branch,
                    product=item.product,
                    quantity_change=item.variance,
                    movement_type=StockMovement.MovementType.COUNT_ADJUSTMENT,
                    reference=stock_count.count_number,
                    notes=f"Cycle count variance reconciliation: {item.notes}",
                    unit_cost=item.unit_cost,
                )

        stock_count.status = StockCount.Status.RECONCILED
        stock_count.reconciled_by = actor
        stock_count.reconciled_at = timezone.now()
        stock_count.save(update_fields=["status", "reconciled_by", "reconciled_at", "updated_at"])
        return stock_count


# -----------------------------------------------------------------------------
# 5. Cashier Shifts
# -----------------------------------------------------------------------------

def open_cashier_shift(
    actor,
    branch: Branch,
    opening_float: Decimal = Decimal("0.00"),
    notes: str = "",
) -> CashierShift:
    if not can_operate_pos(actor):
        raise ValidationError(_("You do not have permission to open a POS cashier shift."))

    # Check if cashier already has an open shift at this branch
    active_shift = CashierShift.objects.filter(
        cashier=actor,
        branch=branch,
        status=CashierShift.Status.OPEN,
    ).first()
    if active_shift:
        return active_shift

    shift_num = generate_next_inventory_number("cashier_shift", "SHIFT")
    shift = CashierShift.objects.create(
        shift_number=shift_num,
        branch=branch,
        cashier=actor,
        opened_at=timezone.now(),
        status=CashierShift.Status.OPEN,
        opening_float=opening_float,
        expected_cash=opening_float,
        notes=notes,
    )
    return shift


def close_cashier_shift(
    actor,
    shift: CashierShift,
    declared_cash: Decimal,
    notes: str = "",
) -> CashierShift:
    if shift.cashier != actor and not is_owner(actor):
        raise ValidationError(_("Only the shift cashier or Gym Owner can close this shift."))

    if shift.status != CashierShift.Status.OPEN:
        raise ValidationError(_("Shift is already closed."))

    with transaction.atomic():
        shift = CashierShift.objects.select_for_update().get(pk=shift.pk)

        # Calculate cash sales and cash refunds during this shift
        cash_sales = SalePayment.objects.filter(
            sale__shift=shift,
            payment_method__method_type=PaymentMethod.MethodType.CASH,
            is_verified=True,
        ).aggregate(tot=Sum("amount"))["tot"] or Decimal("0.00")

        cash_refunds = SaleReturn.objects.filter(
            sale__shift=shift,
            refund_method__method_type=PaymentMethod.MethodType.CASH,
            status=SaleReturn.Status.COMPLETED,
        ).aggregate(tot=Sum("refund_amount"))["tot"] or Decimal("0.00")

        expected = shift.opening_float + cash_sales - cash_refunds
        variance = declared_cash - expected

        shift.status = CashierShift.Status.CLOSED
        shift.closed_at = timezone.now()
        shift.expected_cash = expected
        shift.declared_cash = declared_cash
        shift.cash_variance = variance
        if notes:
            shift.notes = f"{shift.notes}\nClose notes: {notes}".strip()
        shift.save()
        return shift


# -----------------------------------------------------------------------------
# 6. POS Checkout & Sales Operations
# -----------------------------------------------------------------------------

def process_pos_checkout(
    actor,
    branch: Branch,
    items_data: List[Dict[str, Any]],
    payments_data: List[Dict[str, Any]],
    member=None,
    customer_name: str = "Walk-in Customer",
    customer_phone: str = "",
    discount_amount: Decimal = Decimal("0.00"),
    discount_reason: str = "",
    discount_authorizer=None,
    shift: Optional[CashierShift] = None,
    notes: str = "",
) -> Sale:
    """
    Execute atomic POS checkout:
    1. Lock stock items to serialize concurrent sales of the same items.
    2. Check stock availability (negative stock strictly disallowed).
    3. Apply discount authorization rules.
    4. Compute taxes, item snapshots, and payment tenders.
    5. Deduct inventory via SALE_DEDUCTION.
    6. Generate durable Sale, SaleItems, SalePayments, and SaleReceipt.
    """
    if not can_operate_pos(actor):
        raise ValidationError(_("You do not have permission to operate the POS."))

    if not items_data:
        raise ValidationError(_("Cart cannot be empty."))

    if not payments_data:
        raise ValidationError(_("At least one payment method tender is required."))

    discount = Decimal(str(discount_amount)) if discount_amount else Decimal("0.00")
    if discount > Decimal("0.00"):
        if not discount_authorizer or not can_authorize_discount(discount_authorizer):
            if not can_authorize_discount(actor):
                raise ValidationError(_("Discount requires authorization from a Branch Manager or Owner."))

    with transaction.atomic():
        # Order products by ID to guarantee consistent lock ordering across transactions
        product_ids = sorted([item["product"].id for item in items_data])
        products_dict = {p.id: p for p in Product.objects.filter(id__in=product_ids)}

        # Row lock StockItem records
        stock_items_list = list(
            StockItem.objects.select_for_update()
            .filter(branch=branch, product_id__in=product_ids)
            .order_by("product_id")
        )
        stock_map = {si.product_id: si for si in stock_items_list}

        # Check stock sufficiency and calculate line items
        subtotal = Decimal("0.00")
        total_tax = Decimal("0.00")
        sale_items_to_create = []

        for item in items_data:
            prod_id = item["product"].id
            product = products_dict[prod_id]
            qty = Decimal(str(item["quantity"]))
            unit_price = Decimal(str(item.get("unit_price", product.selling_price)))

            stock_item = stock_map.get(prod_id)
            available = stock_item.quantity_on_hand if stock_item else Decimal("0.00")

            if available < qty:
                raise ValidationError(
                    _(f"Insufficient stock for '{product.name}'. Available: {available}, requested: {qty}.")
                )

            line_gross = (qty * unit_price).quantize(Decimal("0.01"))
            tax_rate = product.tax_rate
            if tax_rate > Decimal("0.00"):
                if product.is_tax_inclusive:
                    line_tax = (line_gross - (line_gross / (Decimal("1.00") + (tax_rate / Decimal("100.00"))))).quantize(Decimal("0.01"))
                else:
                    line_tax = (line_gross * (tax_rate / Decimal("100.00"))).quantize(Decimal("0.01"))
            else:
                line_tax = Decimal("0.00")

            cost = stock_item.average_cost_price if stock_item else product.cost_price

            sale_items_to_create.append({
                "product": product,
                "quantity": qty,
                "unit_cost": cost,
                "unit_price": unit_price,
                "discount_amount": Decimal("0.00"),
                "tax_rate": tax_rate,
                "tax_amount": line_tax,
                "total_price": line_gross,
            })
            subtotal += line_gross
            total_tax += line_tax

        total_due = max(Decimal("0.00"), subtotal - discount)

        # Validate payments
        total_tendered = Decimal("0.00")
        total_paid = Decimal("0.00")
        for pay in payments_data:
            amt = Decimal(str(pay["amount"]))
            tendered = Decimal(str(pay.get("tendered_amount", amt)))
            total_tendered += tendered
            total_paid += amt

        if total_paid < total_due:
            raise ValidationError(
                _(f"Total paid ({total_paid} ETB) is less than amount due ({total_due} ETB).")
            )

        change_amt = max(Decimal("0.00"), total_tendered - total_due)

        # Create Sale
        sale_num = generate_next_inventory_number("sale", "SALE")
        sale = Sale.objects.create(
            sale_number=sale_num,
            branch=branch,
            cashier=actor,
            shift=shift,
            member=member,
            customer_name=customer_name.strip() if customer_name else "Walk-in Customer",
            customer_phone=customer_phone.strip(),
            status=Sale.Status.COMPLETED,
            payment_status=Sale.PaymentStatus.PAID,
            subtotal=subtotal,
            discount_amount=discount,
            discount_reason=discount_reason.strip(),
            discount_authorized_by=discount_authorizer,
            tax_amount=total_tax,
            total_amount=total_due,
            paid_amount=total_due,
            change_amount=change_amt,
            notes=notes,
            completed_at=timezone.now(),
        )

        # Deduct inventory & create SaleItems
        for it in sale_items_to_create:
            SaleItem.objects.create(
                sale=sale,
                product=it["product"],
                quantity=it["quantity"],
                unit_cost=it["unit_cost"],
                unit_price=it["unit_price"],
                discount_amount=it["discount_amount"],
                tax_rate=it["tax_rate"],
                tax_amount=it["tax_amount"],
                total_price=it["total_price"],
            )

            adjust_stock(
                actor=actor,
                branch=branch,
                product=it["product"],
                quantity_change=-it["quantity"],
                movement_type=StockMovement.MovementType.SALE_DEDUCTION,
                reference=sale_num,
                notes=f"POS Sale to {customer_name}",
            )

        # Record Payments
        for pay in payments_data:
            pm = pay["payment_method"]
            amt = Decimal(str(pay["amount"]))
            tendered = Decimal(str(pay.get("tendered_amount", amt)))
            chg = Decimal(str(pay.get("change_amount", Decimal("0.00"))))
            ref = pay.get("reference", "")

            # If payment method is CASH, automatically verified. Electronic methods verified if reference supplied or marked
            is_ver = pay.get("is_verified", True)
            SalePayment.objects.create(
                sale=sale,
                payment_method=pm,
                amount=amt,
                tendered_amount=tendered,
                change_amount=chg,
                reference=ref,
                is_verified=is_ver,
                verified_at=timezone.now() if is_ver else None,
                verified_by=actor if is_ver else None,
            )

        # Generate digital/thermal receipt snapshot
        rec_num = generate_next_inventory_number("sale_receipt", "REC-POS")
        receipt_payload = {
            "receipt_number": rec_num,
            "sale_number": sale.sale_number,
            "branch_name": branch.name,
            "branch_code": branch.code,
            "cashier_name": actor.get_full_name() or getattr(actor, "email", str(actor)),
            "customer_name": sale.customer_name,
            "completed_at": sale.completed_at.strftime("%Y-%m-%d %H:%M:%S"),
            "items": [
                {
                    "name": it["product"].name,
                    "sku": it["product"].sku,
                    "qty": str(it["quantity"]),
                    "unit_price": str(it["unit_price"]),
                    "total": str(it["total_price"]),
                }
                for it in sale_items_to_create
            ],
            "subtotal": str(sale.subtotal),
            "discount": str(sale.discount_amount),
            "tax": str(sale.tax_amount),
            "total": str(sale.total_amount),
            "paid": str(sale.paid_amount),
            "change": str(sale.change_amount),
        }
        SaleReceipt.objects.create(
            receipt_number=rec_num,
            sale=sale,
            receipt_data=receipt_payload,
        )

        # Update shift totals
        if shift:
            shift.total_sales_amount = F("total_sales_amount") + sale.total_amount
            shift.save(update_fields=["total_sales_amount", "updated_at"])

        return sale


# -----------------------------------------------------------------------------
# 7. Sales Returns & Customer Refunds
# -----------------------------------------------------------------------------

def process_sale_return(
    actor,
    authorizer,
    sale: Sale,
    return_items_data: List[Dict[str, Any]],
    refund_method: PaymentMethod,
    reason: str,
    restock_items: bool = True,
) -> SaleReturn:
    """
    Process return and refund for completed POS sale.
    Validates returnable quantities and optionally restocks inventory.
    """
    if not can_authorize_refund(authorizer):
        raise ValidationError(_("Refunds must be authorized by a Branch Manager or Owner."))

    if sale.status in (Sale.Status.VOIDED, Sale.Status.FULLY_RETURNED):
        raise ValidationError(_("This sale cannot be refunded."))

    if not return_items_data:
        raise ValidationError(_("Return must specify at least one item."))

    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale.pk)
        sale_items_map = {item.id: item for item in sale.items.select_for_update()}

        total_refund = Decimal("0.00")
        items_to_restock = []

        ret_num = generate_next_inventory_number("sale_return", "RET")
        sale_return = SaleReturn.objects.create(
            return_number=ret_num,
            sale=sale,
            branch=sale.branch,
            processed_by=actor,
            authorized_by=authorizer,
            reason=reason.strip(),
            refund_amount=Decimal("0.00"),
            refund_method=refund_method,
            restock_items=restock_items,
            status=SaleReturn.Status.COMPLETED,
            returned_at=timezone.now(),
        )

        for ret_spec in return_items_data:
            s_item_id = ret_spec["sale_item_id"]
            sale_item = sale_items_map.get(s_item_id)
            if not sale_item:
                raise ValidationError(_("Invalid sale item specified for return."))

            qty = Decimal(str(ret_spec["quantity"]))
            if qty <= Decimal("0.00"):
                raise ValidationError(_("Return quantity must be greater than zero."))

            if qty > sale_item.remaining_returnable_quantity:
                raise ValidationError(
                    _(f"Cannot return {qty} of '{sale_item.product.name}'. "
                      f"Only {sale_item.remaining_returnable_quantity} returnable.")
                )

            condition = ret_spec.get("condition", SaleReturnItem.Condition.RESELLABLE)
            should_restock = restock_items and (condition == SaleReturnItem.Condition.RESELLABLE)

            item_refund = (qty * sale_item.unit_price).quantize(Decimal("0.01"))
            total_refund += item_refund

            SaleReturnItem.objects.create(
                sale_return=sale_return,
                sale_item=sale_item,
                quantity=qty,
                unit_price=sale_item.unit_price,
                refund_amount=item_refund,
                condition=condition,
                restocked=should_restock,
            )

            sale_item.returned_quantity += qty
            sale_item.save(update_fields=["returned_quantity", "updated_at"])

            if should_restock:
                items_to_restock.append((sale_item.product, qty))

        sale_return.refund_amount = total_refund
        sale_return.save(update_fields=["refund_amount"])

        # Restock inventory
        for prod, qty in items_to_restock:
            adjust_stock(
                actor=actor,
                branch=sale.branch,
                product=prod,
                quantity_change=qty,
                movement_type=StockMovement.MovementType.SALE_RETURN,
                reference=ret_num,
                notes=f"Restock from return on {sale.sale_number}",
            )

        # Update sale status
        all_returned = all(it.remaining_returnable_quantity == Decimal("0.00") for it in sale.items.all())
        sale.status = Sale.Status.FULLY_RETURNED if all_returned else Sale.Status.PARTIALLY_RETURNED
        sale.save(update_fields=["status", "updated_at"])

        # Update shift refunds if sale linked to shift
        if sale.shift:
            sale.shift.total_refunds_amount = F("total_refunds_amount") + total_refund
            sale.shift.save(update_fields=["total_refunds_amount", "updated_at"])

        return sale_return


# -----------------------------------------------------------------------------
# 8. Financial Integration: Exactly-Once POS Settlement to FinancialAccount
# -----------------------------------------------------------------------------

def post_verified_pos_settlement_to_account(
    actor,
    branch: Branch,
    account: FinancialAccount,
    start_date,
    end_date,
    payment_method_type: str,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Formal settlement posting from verified POS sales & completed POS returns
    into a branch FinancialAccount.

    Guarantees:
    1. Closed reconciliation periods cannot be backdated or mutated.
    2. Candidate IDs resolved and row-locked on single tables without outer joins.
    3. Exactly-once posting enforced by PosSettledPayment and PosSettledReturn database UNIQUE constraints.
    4. Safe across overlapping date ranges, multi-accounts, and concurrent workers.
    """
    if not can_settle_pos_to_finance(actor):
        raise ValidationError(_("You do not have permission to post POS settlements."))

    if account.branch and account.branch != branch:
        raise ValidationError({"account": _("Financial account belongs to a different branch.")})

    # Accounting closed period check
    check_closed_reconciliation(branch, account, end_date)

    with transaction.atomic():
        locked_account = FinancialAccount.objects.select_for_update().get(pk=account.pk)

        # 1. Fetch candidate UNSETTLED verified sale payments
        candidate_payment_ids = list(
            SalePayment.objects.filter(
                sale__branch=branch,
                payment_method__method_type=payment_method_type,
                is_verified=True,
                sale__completed_at__date__gte=start_date,
                sale__completed_at__date__lte=end_date,
            ).exclude(
                id__in=PosSettledPayment.objects.values("payment_id")
            ).order_by("id").values_list("id", flat=True)
        )

        unsettled_payments = []
        if candidate_payment_ids:
            locked_payments = list(
                SalePayment.objects.filter(id__in=candidate_payment_ids)
                .select_for_update()
                .order_by("id")
            )
            # Re-verify under row lock in case a concurrent transaction settled them
            already_settled_ids = set(
                PosSettledPayment.objects.filter(
                    payment_id__in=[p.id for p in locked_payments]
                ).values_list("payment_id", flat=True)
            )
            unsettled_payments = [p for p in locked_payments if p.id not in already_settled_ids]

        # 2. Fetch candidate UNSETTLED completed sales returns
        candidate_return_ids = list(
            SaleReturn.objects.filter(
                branch=branch,
                refund_method__method_type=payment_method_type,
                status=SaleReturn.Status.COMPLETED,
                returned_at__date__gte=start_date,
                returned_at__date__lte=end_date,
            ).exclude(
                id__in=PosSettledReturn.objects.values("sale_return_id")
            ).order_by("id").values_list("id", flat=True)
        )

        unsettled_returns = []
        if candidate_return_ids:
            locked_returns = list(
                SaleReturn.objects.filter(id__in=candidate_return_ids)
                .select_for_update()
                .order_by("id")
            )
            already_settled_rids = set(
                PosSettledReturn.objects.filter(
                    sale_return_id__in=[r.id for r in locked_returns]
                ).values_list("sale_return_id", flat=True)
            )
            unsettled_returns = [r for r in locked_returns if r.id not in already_settled_rids]

        p_count = len(unsettled_payments)
        r_count = len(unsettled_returns)

        if p_count == 0 and r_count == 0:
            raise ValidationError(
                _(f"No unsettled verified POS {payment_method_type} transactions found for {branch.name} "
                  f"between {start_date} and {end_date}. All transactions have already been settled.")
            )

        total_payments = sum((p.amount for p in unsettled_payments), Decimal("0.00"))
        total_returns = sum((r.refund_amount for r in unsettled_returns), Decimal("0.00"))
        net_collections = total_payments - total_returns

        batch_num = generate_next_inventory_number("pos_settlement_batch", "SETTLE-POS")
        batch = PosSettlementBatch.objects.create(
            batch_number=batch_num,
            branch=branch,
            financial_account=locked_account,
            payment_method_type=payment_method_type,
            period_start=start_date,
            period_end=end_date,
            sale_payment_count=p_count,
            total_sale_payments=total_payments,
            return_count=r_count,
            total_returns=total_returns,
            net_collections=net_collections,
            settled_by=actor,
            notes=notes,
        )

        # Database UNIQUE constraints on payment_id and sale_return_id prevent any duplicate settlement
        for p in unsettled_payments:
            PosSettledPayment.objects.create(
                payment=p,
                settlement_batch=batch,
                amount=p.amount,
            )

        for r in unsettled_returns:
            PosSettledReturn.objects.create(
                sale_return=r,
                settlement_batch=batch,
                amount=r.refund_amount,
            )

        # Post net adjustment to FinancialAccount balance
        adjustment = None
        if net_collections != Decimal("0.00"):
            adj_type = (
                AccountAdjustment.AdjustmentType.CREDIT
                if net_collections > Decimal("0.00")
                else AccountAdjustment.AdjustmentType.DEBIT
            )
            reason_str = f"POS Settlement Batch {batch_num} ({p_count} sales, {r_count} returns) [{start_date} to {end_date}]"
            adjustment = perform_account_adjustment(
                actor=actor,
                account=locked_account,
                adjustment_type=adj_type,
                amount=abs(net_collections),
                reason=reason_str,
            )
            batch.account_adjustment = adjustment
            batch.save(update_fields=["account_adjustment"])

        FinanceAuditEvent.objects.create(
            event_type=FinanceAuditEvent.EventType.ADJUSTMENT_APPLIED,
            actor=actor,
            branch=branch,
            target_type="PosSettlementBatch",
            target_id=str(batch.id),
            action_summary=(
                f"Settled POS batch {batch_num} to {locked_account.name}: {p_count} payments, "
                f"{r_count} returns, net {net_collections} ETB."
            ),
        )

        return {
            "batch_number": batch_num,
            "sale_payment_count": p_count,
            "total_sale_payments": total_payments,
            "return_count": r_count,
            "total_returns": total_returns,
            "net_collections": net_collections,
            "account_new_balance": locked_account.current_balance,
            "adjustment": adjustment,
        }
