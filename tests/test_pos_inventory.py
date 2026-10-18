from decimal import Decimal
import threading
import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import has_role, seed_roles
from apps.analytics.dates import resolve_date_filter
from apps.billing.models import PaymentMethod
from apps.branches.models import Branch, UserBranchAccess
from apps.core.models import GymProfile
from apps.finance.models import (
    AccountAdjustment,
    Expense,
    ExpenseCategory,
    FinancialAccount,
    FinancialReconciliation,
    Supplier,
)
from apps.finance.services import close_reconciliation
from apps.inventory.models import (
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
    StockBatch,
    StockCount,
    StockItem,
    StockMovement,
    StockTransfer,
    PosSettlementBatch,
    PosSettledPayment,
    PosSettledReturn,
)
from apps.inventory.permissions import (
    can_manage_inventory,
    can_operate_pos,
    can_authorize_refund,
    can_authorize_discount,
)
from apps.inventory.reports import (
    extract_cashier_performance_report,
    extract_daily_sales_report,
    extract_expiring_products_report,
    extract_inventory_valuation_report,
    extract_low_stock_report,
    extract_sales_by_product_report,
    extract_stock_movement_report,
    export_report_to_csv,
)
from apps.inventory.services import (
    adjust_stock,
    approve_purchase_order,
    approve_stock_adjustment,
    close_cashier_shift,
    create_product,
    create_purchase_order,
    create_stock_adjustment,
    create_stock_transfer,
    dispatch_stock_transfer,
    get_or_create_stock_item,
    open_cashier_shift,
    post_verified_pos_settlement_to_account,
    process_pos_checkout,
    process_sale_return,
    receive_goods_note,
    receive_stock_transfer,
    reconcile_stock_count,
    record_stock_count,
    submit_purchase_order,
)
from apps.members.models import Member

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_pos_prerequisites(db):
    seed_roles()
    GymProfile.objects.update_or_create(
        singleton=1,
        defaults={
            "name": "AGMS Gym",
            "legal_name": "Antigravity Fitness PLC",
            "email": "support@agms.test",
            "phone": "+251911000000",
            "default_timezone": "Africa/Addis_Ababa",
        },
    )


@pytest.fixture
def test_branches():
    b1 = Branch.objects.create(code="BOLE", name="Bole Branch", address="Bole Road", is_active=True)
    b2 = Branch.objects.create(code="MEG", name="Megenagna Branch", address="Megenagna Square", is_active=True)
    return b1, b2


@pytest.fixture
def test_users(test_branches):
    b1, b2 = test_branches

    owner = User.objects.create_user("owner_pos@agms.test", "OwnerPass12345!", is_staff=True)
    owner.groups.add(Group.objects.get(name="Gym Owner"))

    mgr_bole = User.objects.create_user("mgr_bole_pos@agms.test", "ManagerPass123!", is_staff=True)
    mgr_bole.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_bole, branch=b1, is_active=True, is_primary=True)

    mgr_meg = User.objects.create_user("mgr_meg_pos@agms.test", "ManagerPass123!", is_staff=True)
    mgr_meg.groups.add(Group.objects.get(name="Branch Manager"))
    UserBranchAccess.objects.create(user=mgr_meg, branch=b2, is_active=True, is_primary=True)

    pos_staff = User.objects.create_user("cashier@agms.test", "CashierPass123!", is_staff=True)
    pos_staff.groups.add(Group.objects.get(name="POS Staff"))
    UserBranchAccess.objects.create(user=pos_staff, branch=b1, is_active=True, is_primary=True)

    accountant = User.objects.create_user("accountant_pos@agms.test", "AccountPass123!", is_staff=True)
    accountant.groups.add(Group.objects.get(name="Accountant"))
    UserBranchAccess.objects.create(user=accountant, branch=b1, is_active=True, is_primary=True)
    UserBranchAccess.objects.create(user=accountant, branch=b2, is_active=True, is_primary=False)

    trainer = User.objects.create_user("trainer_pos@agms.test", "TrainerPass123!", is_staff=True)
    trainer.groups.add(Group.objects.get(name="Trainer"))
    UserBranchAccess.objects.create(user=trainer, branch=b1, is_active=True, is_primary=True)

    return {
        "owner": owner,
        "mgr_bole": mgr_bole,
        "mgr_meg": mgr_meg,
        "pos_staff": pos_staff,
        "accountant": accountant,
        "trainer": trainer,
    }


@pytest.fixture
def test_category():
    cat = ProductCategory.objects.create(name="Supplements", code="SUPP", is_active=True)
    return cat


@pytest.fixture
def test_payment_methods():
    pm_cash, _ = PaymentMethod.objects.get_or_create(
        code="CASH_POS",
        defaults={"name": "Cash", "method_type": PaymentMethod.MethodType.CASH, "is_active": True},
    )
    pm_telebirr, _ = PaymentMethod.objects.get_or_create(
        code="TELEBIRR_POS",
        defaults={"name": "Telebirr", "method_type": PaymentMethod.MethodType.TELEBIRR, "is_active": True},
    )
    pm_bank, _ = PaymentMethod.objects.get_or_create(
        code="BANK_POS",
        defaults={"name": "Bank Transfer", "method_type": PaymentMethod.MethodType.BANK_TRANSFER, "is_active": True},
    )
    return {"cash": pm_cash, "telebirr": pm_telebirr, "bank": pm_bank}


@pytest.fixture
def test_supplier(test_branches):
    b1, _ = test_branches
    sup = Supplier.objects.create(
        name="Optimum Nutrition Ethiopia",
        supplier_type=Supplier.SupplierType.VENDOR,
        branch=b1,
        is_active=True,
    )
    return sup


@pytest.fixture
def test_finance_account(test_branches):
    b1, _ = test_branches
    acc = FinancialAccount.objects.create(
        name="Main POS Drawer - Bole",
        account_type=FinancialAccount.AccountType.CASH_ON_HAND,
        branch=b1,
        opening_balance=Decimal("1000.00"),
        current_balance=Decimal("1000.00"),
        is_active=True,
    )
    return acc


# =============================================================================
# 1. Product Lifecycle & Barcode Uniqueness
# =============================================================================

def test_product_lifecycle_and_barcode_uniqueness(test_users, test_category):
    owner = test_users["owner"]

    p1 = create_product(
        actor=owner,
        name="Gold Standard Whey 5lb",
        sku="WHEY-5LB-VAN",
        barcode="123456789012",
        category=test_category,
        unit_of_measurement=Product.Unit.PCS,
        cost_price=Decimal("4500.00"),
        selling_price=Decimal("6000.00"),
        tax_rate=Decimal("15.00"),
        low_stock_threshold=3,
        tracks_expiry=True,
    )
    assert p1.product_number.startswith("PRD-")
    assert p1.selling_price == Decimal("6000.00")
    assert p1.tracks_expiry is True

    # Duplicate barcode must fail
    with pytest.raises(Exception):
        create_product(
            actor=owner,
            name="Different Product",
            sku="DIFF-SKU-01",
            barcode="123456789012",
            category=test_category,
            unit_of_measurement=Product.Unit.PCS,
            cost_price=Decimal("100.00"),
            selling_price=Decimal("200.00"),
        )


# =============================================================================
# 2. Stock Intake & Moving Weighted Average Cost
# =============================================================================

def test_stock_intake_and_weighted_average_cost(test_branches, test_users, test_category):
    b1, _ = test_branches
    owner = test_users["owner"]

    prod = create_product(
        actor=owner,
        name="Electrolyte Drink 500ml",
        sku="ELECTRO-500",
        category=test_category,
        unit_of_measurement=Product.Unit.BOTTLE,
        cost_price=Decimal("50.00"),
        selling_price=Decimal("100.00"),
    )

    # Batch 1: 10 bottles @ 50 ETB
    m1 = adjust_stock(
        actor=owner,
        branch=b1,
        product=prod,
        quantity_change=Decimal("10.00"),
        movement_type=StockMovement.MovementType.OPENING_STOCK,
        reference="OPENING-01",
        unit_cost=Decimal("50.00"),
    )
    stock_item = m1.stock_item
    stock_item.refresh_from_db()
    assert stock_item.quantity_on_hand == Decimal("10.00")
    assert stock_item.average_cost_price == Decimal("50.00")

    # Batch 2: 10 bottles @ 70 ETB
    # Weighted avg = ((10 * 50) + (10 * 70)) / 20 = 1200 / 20 = 60 ETB
    adjust_stock(
        actor=owner,
        branch=b1,
        product=prod,
        quantity_change=Decimal("10.00"),
        movement_type=StockMovement.MovementType.GOODS_RECEIVED,
        reference="GRN-01",
        unit_cost=Decimal("70.00"),
    )
    stock_item.refresh_from_db()
    assert stock_item.quantity_on_hand == Decimal("20.00")
    assert stock_item.average_cost_price == Decimal("60.00")
    assert stock_item.total_inventory_value == Decimal("1200.00")


# =============================================================================
# 3. Negative Stock Prevention
# =============================================================================

def test_negative_stock_prevention(test_branches, test_users, test_category):
    b1, _ = test_branches
    owner = test_users["owner"]

    prod = create_product(
        actor=owner,
        name="Gym Shaker Bottle",
        sku="SHAKER-01",
        category=test_category,
        unit_of_measurement=Product.Unit.PCS,
        cost_price=Decimal("150.00"),
        selling_price=Decimal("300.00"),
    )

    adjust_stock(
        actor=owner,
        branch=b1,
        product=prod,
        quantity_change=Decimal("5.00"),
        movement_type=StockMovement.MovementType.OPENING_STOCK,
        reference="OPEN-01",
    )

    # Attempting to deduct 6 items when only 5 exist must be rejected
    with pytest.raises(ValidationError) as exc:
        adjust_stock(
            actor=owner,
            branch=b1,
            product=prod,
            quantity_change=Decimal("-6.00"),
            movement_type=StockMovement.MovementType.SALE_DEDUCTION,
            reference="SALE-TEST",
        )
    assert "Insufficient stock" in str(exc.value)


# =============================================================================
# 4. Purchasing Workflow & Partial Goods Receiving
# =============================================================================

def test_purchasing_workflow_and_partial_receiving(test_branches, test_users, test_category, test_supplier):
    b1, _ = test_branches
    owner = test_users["owner"]

    prod = create_product(
        actor=owner,
        name="Creatine Monohydrate 300g",
        sku="CREA-300G",
        category=test_category,
        unit_of_measurement=Product.Unit.PCS,
        cost_price=Decimal("1000.00"),
        selling_price=Decimal("1600.00"),
    )

    # 1. Create Purchase Order (10 units @ 1,000 ETB = 10,000 ETB)
    po = create_purchase_order(
        actor=owner,
        branch=b1,
        supplier=test_supplier,
        items_data=[{"product": prod, "quantity": 10, "unit_cost": Decimal("1000.00")}],
    )
    assert po.status == PurchaseOrder.Status.DRAFT
    assert po.total_amount == Decimal("10000.00")

    # 2. Submit & Approve
    submit_purchase_order(owner, po)
    approve_purchase_order(owner, po)
    assert po.status == PurchaseOrder.Status.APPROVED

    po_item = po.items.first()

    # 3. Partial Delivery: Receive 4 units
    grn1 = receive_goods_note(
        actor=owner,
        branch=b1,
        supplier=test_supplier,
        items_data=[{"product": prod, "quantity": 4, "unit_cost": Decimal("1000.00"), "po_item": po_item}],
        purchase_order=po,
        delivery_note_reference="DN-PART-01",
        create_expense_liability=True,
    )
    po.refresh_from_db()
    po_item.refresh_from_db()
    assert po_item.quantity_received == Decimal("4.00")
    assert po.status == PurchaseOrder.Status.PARTIALLY_RECEIVED

    stock = get_or_create_stock_item(b1, prod)
    assert stock.quantity_on_hand == Decimal("4.00")

    # Verify expense liability was recorded as UNPAID (no financial accounts credited yet)
    assert grn1.expense_record is not None
    assert grn1.expense_record.amount == Decimal("4000.00")
    assert grn1.expense_record.payment_status == Expense.PaymentStatus.UNPAID

    # 4. Final Delivery: Receive remaining 6 units
    receive_goods_note(
        actor=owner,
        branch=b1,
        supplier=test_supplier,
        items_data=[{"product": prod, "quantity": 6, "unit_cost": Decimal("1000.00"), "po_item": po_item}],
        purchase_order=po,
        delivery_note_reference="DN-FINAL-02",
    )
    po.refresh_from_db()
    assert po.status == PurchaseOrder.Status.RECEIVED
    stock.refresh_from_db()
    assert stock.quantity_on_hand == Decimal("10.00")


# =============================================================================
# 5. Inter-Branch Stock Transfers
# =============================================================================

def test_inter_branch_stock_transfer(test_branches, test_users, test_category):
    b1, b2 = test_branches
    owner = test_users["owner"]

    prod = create_product(
        actor=owner,
        name="Lifting Straps (Pair)",
        sku="STRAPS-BLK",
        category=test_category,
        unit_of_measurement=Product.Unit.PAIR,
        cost_price=Decimal("200.00"),
        selling_price=Decimal("450.00"),
    )

    # Initial stock at Bole: 15 pairs
    adjust_stock(owner, b1, prod, Decimal("15.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    # Create transfer: Bole -> Megenagna (5 pairs)
    tr = create_stock_transfer(
        actor=owner,
        source_branch=b1,
        target_branch=b2,
        items_data=[{"product": prod, "quantity": 5}],
    )
    assert tr.status == StockTransfer.Status.PENDING

    # Dispatch: deducts from Bole
    dispatch_stock_transfer(owner, tr)
    tr.refresh_from_db()
    assert tr.status == StockTransfer.Status.DISPATCHED
    assert get_or_create_stock_item(b1, prod).quantity_on_hand == Decimal("10.00")
    assert get_or_create_stock_item(b2, prod).quantity_on_hand == Decimal("0.00")

    # Receive: adds to Megenagna
    receive_stock_transfer(owner, tr)
    tr.refresh_from_db()
    assert tr.status == StockTransfer.Status.COMPLETED
    assert get_or_create_stock_item(b2, prod).quantity_on_hand == Decimal("5.00")


# =============================================================================
# 6. Physical Stock Counts & Reconciliation
# =============================================================================

def test_physical_stock_count_and_reconciliation(test_branches, test_users, test_category):
    b1, _ = test_branches
    owner = test_users["owner"]

    prod = create_product(
        actor=owner,
        name="Bottled Water 1L",
        sku="WATER-1L",
        category=test_category,
        unit_of_measurement=Product.Unit.BOTTLE,
        cost_price=Decimal("15.00"),
        selling_price=Decimal("30.00"),
    )

    # System stock = 50 bottles
    adjust_stock(owner, b1, prod, Decimal("50.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    # Stock count: physically found 46 bottles (variance -4)
    sc = record_stock_count(
        actor=owner,
        branch=b1,
        counted_items_data=[{"product": prod, "counted_quantity": Decimal("46.00")}],
        notes="Monthly shelf audit",
    )
    item = sc.items.first()
    assert item.system_quantity == Decimal("50.00")
    assert item.counted_quantity == Decimal("46.00")
    assert item.variance == Decimal("-4.00")

    # Reconcile count: updates stock to exactly 46
    reconcile_stock_count(owner, sc)
    sc.refresh_from_db()
    assert sc.status == StockCount.Status.RECONCILED
    assert get_or_create_stock_item(b1, prod).quantity_on_hand == Decimal("46.00")


# =============================================================================
# 7. Cashier Shift Lifecycle & Cash Variance
# =============================================================================

def test_cashier_shift_lifecycle_and_variance(test_branches, test_users, test_payment_methods, test_category):
    b1, _ = test_branches
    cashier = test_users["pos_staff"]
    pm_cash = test_payment_methods["cash"]

    prod = create_product(
        actor=test_users["owner"],
        name="Energy Bar 60g",
        sku="BAR-60G",
        category=test_category,
        unit_of_measurement=Product.Unit.PCS,
        cost_price=Decimal("60.00"),
        selling_price=Decimal("120.00"),
    )
    adjust_stock(test_users["owner"], b1, prod, Decimal("30.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    # 1. Open Shift with 500 ETB float
    shift = open_cashier_shift(actor=cashier, branch=b1, opening_float=Decimal("500.00"))
    assert shift.status == CashierShift.Status.OPEN
    assert shift.opening_float == Decimal("500.00")

    # 2. Complete Cash Sale of 2 bars (240 ETB)
    process_pos_checkout(
        actor=cashier,
        branch=b1,
        items_data=[{"product": prod, "quantity": 2}],
        payments_data=[{"payment_method": pm_cash, "amount": Decimal("240.00"), "tendered_amount": Decimal("240.00")}],
        shift=shift,
    )

    # 3. Close Shift: Expected cash = 500 (float) + 240 (sale) = 740 ETB
    # Cashier physically declares 750 ETB (10 ETB surplus)
    closed = close_cashier_shift(actor=cashier, shift=shift, declared_cash=Decimal("750.00"))
    assert closed.status == CashierShift.Status.CLOSED
    assert closed.expected_cash == Decimal("740.00")
    assert closed.declared_cash == Decimal("750.00")
    assert closed.cash_variance == Decimal("10.00")


# =============================================================================
# 8. POS Checkout, Split Payments & Receipt Generation
# =============================================================================

def test_pos_checkout_multi_tender_and_receipt(test_branches, test_users, test_payment_methods, test_category):
    b1, _ = test_branches
    cashier = test_users["pos_staff"]
    owner = test_users["owner"]
    pm_cash = test_payment_methods["cash"]
    pm_telebirr = test_payment_methods["telebirr"]

    p_water = create_product(
        actor=owner,
        name="Highland Spring Water 500ml",
        sku="WATER-500",
        category=test_category,
        unit_of_measurement=Product.Unit.BOTTLE,
        cost_price=Decimal("10.00"),
        selling_price=Decimal("20.00"),
    )
    p_towel = create_product(
        actor=owner,
        name="Microfiber Gym Towel",
        sku="TOWEL-01",
        category=test_category,
        unit_of_measurement=Product.Unit.PCS,
        cost_price=Decimal("150.00"),
        selling_price=Decimal("350.00"),
    )

    adjust_stock(owner, b1, p_water, Decimal("20.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")
    adjust_stock(owner, b1, p_towel, Decimal("10.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    shift = open_cashier_shift(actor=cashier, branch=b1, opening_float=Decimal("300.00"))

    # Cart: 2 Waters (40 ETB) + 1 Towel (350 ETB) = 390 ETB
    # Discount: 40 ETB (authorized by branch manager) -> Total Due: 350 ETB
    # Split Tender: 150 ETB Telebirr + 200 ETB Cash (Tendered 250 ETB -> 50 ETB change)
    sale = process_pos_checkout(
        actor=cashier,
        branch=b1,
        items_data=[
            {"product": p_water, "quantity": 2},
            {"product": p_towel, "quantity": 1},
        ],
        payments_data=[
            {"payment_method": pm_telebirr, "amount": Decimal("150.00"), "reference": "TELE-987654"},
            {"payment_method": pm_cash, "amount": Decimal("200.00"), "tendered_amount": Decimal("250.00")},
        ],
        customer_name="Abebe Bikila",
        discount_amount=Decimal("40.00"),
        discount_reason="Weekend Promo",
        discount_authorizer=test_users["mgr_bole"],
        shift=shift,
    )

    assert sale.subtotal == Decimal("390.00")
    assert sale.discount_amount == Decimal("40.00")
    assert sale.total_amount == Decimal("350.00")
    assert sale.paid_amount == Decimal("350.00")
    assert sale.change_amount == Decimal("50.00")

    # Inventory was cleanly deducted
    assert get_or_create_stock_item(b1, p_water).quantity_on_hand == Decimal("18.00")
    assert get_or_create_stock_item(b1, p_towel).quantity_on_hand == Decimal("9.00")

    # Receipt record generated
    assert hasattr(sale, "receipt")
    receipt = sale.receipt
    assert receipt.receipt_number.startswith("REC-POS-")
    assert receipt.receipt_data["customer_name"] == "Abebe Bikila"
    assert len(receipt.receipt_data["items"]) == 2


# =============================================================================
# 9. Concurrent POS Checkout Serialization & Race Safety
# =============================================================================

@pytest.mark.django_db(transaction=True)
def test_concurrent_pos_checkout_serialization(test_branches, test_users, test_payment_methods, test_category):
    """
    Two cashiers concurrently attempt to sell the LAST remaining item (quantity_on_hand = 1).
    Pessimistic row locking ensures exactly one succeeds and the other receives Insufficient stock.
    Stock must never become negative.
    """
    b1, _ = test_branches
    owner = test_users["owner"]
    cashier = test_users["pos_staff"]
    pm_cash = test_payment_methods["cash"]

    prod = create_product(
        actor=owner,
        name="Limited Edition Gym Hoodie",
        sku="HOODIE-LTD",
        category=test_category,
        unit_of_measurement=Product.Unit.PCS,
        cost_price=Decimal("800.00"),
        selling_price=Decimal("1500.00"),
    )
    # Exactly 1 in stock
    adjust_stock(owner, b1, prod, Decimal("1.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    results = []
    errors = []

    def attempt_checkout(worker_id):
        from django.db import connection
        try:
            s = process_pos_checkout(
                actor=cashier,
                branch=b1,
                items_data=[{"product": prod, "quantity": 1}],
                payments_data=[{"payment_method": pm_cash, "amount": Decimal("1500.00")}],
                customer_name=f"Customer {worker_id}",
            )
            results.append(s)
        except Exception as e:
            errors.append(e)
        finally:
            connection.close()

    t1 = threading.Thread(target=attempt_checkout, args=(1,))
    t2 = threading.Thread(target=attempt_checkout, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert "Insufficient stock" in str(errors[0])

    stock = get_or_create_stock_item(b1, prod)
    assert stock.quantity_on_hand == Decimal("0.00")


# =============================================================================
# 10. Customer Sales Returns & Conditional Restocking
# =============================================================================

def test_sales_return_and_conditional_restock(test_branches, test_users, test_payment_methods, test_category):
    b1, _ = test_branches
    cashier = test_users["pos_staff"]
    mgr = test_users["mgr_bole"]
    owner = test_users["owner"]
    pm_cash = test_payment_methods["cash"]

    prod = create_product(
        actor=owner,
        name="Gym Wrist Wraps",
        sku="WRAPS-01",
        category=test_category,
        unit_of_measurement=Product.Unit.PAIR,
        cost_price=Decimal("100.00"),
        selling_price=Decimal("250.00"),
    )
    adjust_stock(owner, b1, prod, Decimal("10.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    sale = process_pos_checkout(
        actor=cashier,
        branch=b1,
        items_data=[{"product": prod, "quantity": 3}],
        payments_data=[{"payment_method": pm_cash, "amount": Decimal("750.00")}],
    )
    assert get_or_create_stock_item(b1, prod).quantity_on_hand == Decimal("7.00")

    sale_item = sale.items.first()

    # Customer returns 1 pair (Resellable condition) -> Restocked into inventory
    ret = process_sale_return(
        actor=cashier,
        authorizer=mgr,
        sale=sale,
        return_items_data=[{
            "sale_item_id": sale_item.id,
            "quantity": 1,
            "condition": SaleReturnItem.Condition.RESELLABLE,
        }],
        refund_method=pm_cash,
        reason="Wrong color preference",
        restock_items=True,
    )
    assert ret.refund_amount == Decimal("250.00")
    assert get_or_create_stock_item(b1, prod).quantity_on_hand == Decimal("8.00")

    sale.refresh_from_db()
    assert sale.status == Sale.Status.PARTIALLY_RETURNED

    # Attempting to return more than remaining 2 items must fail
    with pytest.raises(ValidationError) as exc:
        process_sale_return(
            actor=cashier,
            authorizer=mgr,
            sale=sale,
            return_items_data=[{
                "sale_item_id": sale_item.id,
                "quantity": 3,
            }],
            refund_method=pm_cash,
            reason="Excess return attempt",
        )
    assert "returnable" in str(exc.value)


# =============================================================================
# 11. Financial Integration: Exactly-Once POS Settlement to FinancialAccount
# =============================================================================

def test_pos_settlement_to_finance_exactly_once(test_branches, test_users, test_payment_methods, test_category, test_finance_account):
    b1, _ = test_branches
    accountant = test_users["accountant"]
    cashier = test_users["pos_staff"]
    mgr = test_users["mgr_bole"]
    owner = test_users["owner"]
    pm_cash = test_payment_methods["cash"]

    today = timezone.localdate()
    yesterday = today - timezone.timedelta(days=1)

    prod = create_product(
        actor=owner,
        name="Whey Protein Snack Pack",
        sku="SNACK-01",
        category=test_category,
        unit_of_measurement=Product.Unit.PACK,
        cost_price=Decimal("40.00"),
        selling_price=Decimal("80.00"),
    )
    adjust_stock(owner, b1, prod, Decimal("50.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    # Day 1 Sale: 5 packs = 400 ETB
    sale1 = process_pos_checkout(
        actor=cashier,
        branch=b1,
        items_data=[{"product": prod, "quantity": 5}],
        payments_data=[{"payment_method": pm_cash, "amount": Decimal("400.00")}],
    )
    # Set completed_at to yesterday for date boundary testing
    sale1.completed_at = timezone.make_aware(timezone.datetime.combine(yesterday, timezone.datetime.min.time()))
    sale1.save(update_fields=["completed_at"])

    # Day 2 Sale: 10 packs = 800 ETB
    sale2 = process_pos_checkout(
        actor=cashier,
        branch=b1,
        items_data=[{"product": prod, "quantity": 10}],
        payments_data=[{"payment_method": pm_cash, "amount": Decimal("800.00")}],
    )

    # Return on Day 2: 1 pack = 80 ETB
    process_sale_return(
        actor=cashier,
        authorizer=mgr,
        sale=sale2,
        return_items_data=[{"sale_item_id": sale2.items.first().id, "quantity": 1}],
        refund_method=pm_cash,
        reason="Defective seal",
    )

    init_balance = test_finance_account.current_balance  # 1,000 ETB

    # Batch 1: Settle Yesterday (400 ETB)
    batch1 = post_verified_pos_settlement_to_account(
        actor=accountant,
        branch=b1,
        account=test_finance_account,
        start_date=yesterday,
        end_date=yesterday,
        payment_method_type="CASH",
    )
    test_finance_account.refresh_from_db()
    assert batch1["net_collections"] == Decimal("400.00")
    assert test_finance_account.current_balance == init_balance + Decimal("400.00")

    # Batch 2: Overlapping Date Range (Yesterday to Today)
    # Yesterday is already settled! Must ONLY settle Day 2: 800 (sale) - 80 (return) = 720 ETB
    batch2 = post_verified_pos_settlement_to_account(
        actor=accountant,
        branch=b1,
        account=test_finance_account,
        start_date=yesterday,
        end_date=today,
        payment_method_type="CASH",
    )
    test_finance_account.refresh_from_db()
    assert batch2["sale_payment_count"] == 1
    assert batch2["return_count"] == 1
    assert batch2["net_collections"] == Decimal("720.00")
    # Total account increase is exactly 400 + 720 = 1,120 ETB (no double-counting of yesterday!)
    assert test_finance_account.current_balance == init_balance + Decimal("1120.00")

    # Submitting again must find 0 unsettled payments and abort cleanly
    with pytest.raises(ValidationError) as exc:
        post_verified_pos_settlement_to_account(
            actor=accountant,
            branch=b1,
            account=test_finance_account,
            start_date=yesterday,
            end_date=today,
            payment_method_type="CASH",
        )
    assert "All transactions have already been settled" in str(exc.value)


# =============================================================================
# 12. POS Settlement Closed-Period Protection
# =============================================================================

def test_pos_settlement_closed_period_protection(test_branches, test_users, test_payment_methods, test_category, test_finance_account):
    b1, _ = test_branches
    accountant = test_users["accountant"]
    cashier = test_users["pos_staff"]
    owner = test_users["owner"]
    pm_cash = test_payment_methods["cash"]

    past_date = timezone.localdate() - timezone.timedelta(days=15)

    prod = create_product(
        actor=owner,
        name="Liquid Chalk 250ml",
        sku="CHALK-250",
        category=test_category,
        unit_of_measurement=Product.Unit.BOTTLE,
        cost_price=Decimal("120.00"),
        selling_price=Decimal("250.00"),
    )
    adjust_stock(owner, b1, prod, Decimal("10.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    sale = process_pos_checkout(
        actor=cashier,
        branch=b1,
        items_data=[{"product": prod, "quantity": 1}],
        payments_data=[{"payment_method": pm_cash, "amount": Decimal("250.00")}],
    )
    sale.completed_at = timezone.make_aware(timezone.datetime.combine(past_date, timezone.datetime.min.time()))
    sale.save(update_fields=["completed_at"])

    # Create and close a reconciliation period covering past_date
    recon = FinancialReconciliation.objects.create(
        reconciliation_number="REC-CLOSED-POS",
        branch=b1,
        account=test_finance_account,
        reconciliation_date=past_date,
        period_start=past_date - timezone.timedelta(days=5),
        period_end=past_date,
        opening_balance=Decimal("1000.00"),
        total_inflows=Decimal("0.00"),
        total_outflows=Decimal("0.00"),
        expected_balance=Decimal("1000.00"),
        actual_counted_balance=Decimal("1000.00"),
        variance=Decimal("0.00"),
        status=FinancialReconciliation.Status.APPROVED_CLOSED,
        closed_at=timezone.now(),
        approved_by=owner,
    )

    # Attempting to post POS settlement into this closed period must be blocked
    with pytest.raises(ValidationError) as exc:
        post_verified_pos_settlement_to_account(
            actor=accountant,
            branch=b1,
            account=test_finance_account,
            start_date=past_date - timezone.timedelta(days=2),
            end_date=past_date,
            payment_method_type="CASH",
        )
    assert "closed and locked" in str(exc.value)


# =============================================================================
# 13. Role Permissions Enforcement
# =============================================================================

def test_pos_role_permissions_enforcement(test_users, test_branches, test_category):
    b1, _ = test_branches
    owner = test_users["owner"]
    mgr = test_users["mgr_bole"]
    cashier = test_users["pos_staff"]
    trainer = test_users["trainer"]

    # 1. POS Operation permission
    assert can_operate_pos(cashier) is True
    assert can_operate_pos(mgr) is True
    assert can_operate_pos(owner) is True
    assert can_operate_pos(trainer) is False

    # 2. Inventory Management permission (creating products, transfers, adjustments)
    assert can_manage_inventory(owner) is True
    assert can_manage_inventory(mgr) is True
    assert can_manage_inventory(cashier) is False
    assert can_manage_inventory(trainer) is False

    # 3. Discount & Refund authorization
    assert can_authorize_discount(mgr) is True
    assert can_authorize_discount(cashier) is False
    assert can_authorize_refund(mgr) is True
    assert can_authorize_refund(cashier) is False

    # Cashier cannot create products
    with pytest.raises(ValidationError):
        create_product(
            actor=cashier,
            name="Unauthorized Product",
            sku="UNAUTH-01",
            category=test_category,
            unit_of_measurement=Product.Unit.PCS,
            cost_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
        )


# =============================================================================
# 14. Comprehensive Sales & Inventory Reports Extraction
# =============================================================================

def test_all_pos_and_inventory_reports_extract(test_branches, test_users, test_payment_methods, test_category):
    b1, b2 = test_branches
    owner = test_users["owner"]
    cashier = test_users["pos_staff"]
    pm_cash = test_payment_methods["cash"]

    prod = create_product(
        actor=owner,
        name="BCAA Powder 400g",
        sku="BCAA-400G",
        category=test_category,
        unit_of_measurement=Product.Unit.PCS,
        cost_price=Decimal("900.00"),
        selling_price=Decimal("1400.00"),
        low_stock_threshold=5,
        tracks_expiry=True,
    )
    adjust_stock(owner, b1, prod, Decimal("10.00"), StockMovement.MovementType.OPENING_STOCK, "INIT")

    # Add expiring batch (20 days from now)
    StockBatch.objects.create(
        stock_item=get_or_create_stock_item(b1, prod),
        batch_number="BATCH-EXP-01",
        quantity=Decimal("10.00"),
        expiry_date=timezone.localdate() + timezone.timedelta(days=20),
        cost_price=Decimal("900.00"),
    )

    shift = open_cashier_shift(cashier, b1, Decimal("200.00"))
    process_pos_checkout(
        actor=cashier,
        branch=b1,
        items_data=[{"product": prod, "quantity": 2}],
        payments_data=[{"payment_method": pm_cash, "amount": Decimal("2800.00")}],
        shift=shift,
    )
    close_cashier_shift(cashier, shift, Decimal("3000.00"))

    date_range = resolve_date_filter("today")
    branch_qs = Branch.objects.filter(id=b1.id)

    # 1. Daily Sales
    r1 = extract_daily_sales_report(branch_qs, date_range)
    assert len(r1) >= 1
    assert r1[0]["gross_sales"] == 2800.00

    # 2. Sales by Product
    r2 = extract_sales_by_product_report(branch_qs, date_range)
    assert len(r2) >= 1
    assert r2[0]["product"] == "BCAA Powder 400g"

    # 3. Inventory Valuation
    r3 = extract_inventory_valuation_report(branch_qs)
    assert len(r3) >= 1
    assert r3[0]["quantity_on_hand"] == 8.0  # 10 - 2

    # 4. Low Stock Report
    r4 = extract_low_stock_report(branch_qs)
    # Remaining 8 > threshold 5, so not low stock
    assert isinstance(r4, list)

    # 5. Expiring Products
    r5 = extract_expiring_products_report(branch_qs, days=30)
    assert len(r5) >= 1
    assert r5[0]["batch_number"] == "BATCH-EXP-01"

    # 6. Stock Movements
    r6 = extract_stock_movement_report(branch_qs, date_range)
    assert len(r6) >= 2  # Opening stock + Sale deduction

    # 7. Cashier Performance
    r7 = extract_cashier_performance_report(branch_qs, date_range)
    assert len(r7) >= 1
    assert "cashier" in r7[0]["cashier"]

    # 8. CSV Export
    resp = export_report_to_csv(r1, "pos_daily_sales")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/csv")
