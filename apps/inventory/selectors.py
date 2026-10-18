from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.branches.models import Branch
from .models import (
    CashierShift,
    GoodsReceivedNote,
    Product,
    PurchaseOrder,
    Sale,
    SaleItem,
    SalePayment,
    SaleReturn,
    StockBatch,
    StockItem,
    StockMovement,
)


def get_low_stock_items(branch_qs=None) -> List[StockItem]:
    """Retrieve stock items at or below reorder threshold."""
    qs = StockItem.objects.select_related("branch", "product", "product__category")
    if branch_qs is not None:
        qs = qs.filter(branch__in=branch_qs)
    return [item for item in qs if item.is_low_stock]


def get_expiring_stock_batches(branch_qs=None, days: int = 30) -> List[StockBatch]:
    """Retrieve stock batches expiring within the specified number of days."""
    today = timezone.localdate()
    cutoff = today + timezone.timedelta(days=days)
    qs = StockBatch.objects.filter(
        expiry_date__lte=cutoff,
        quantity__gt=Decimal("0.00"),
    ).select_related("stock_item", "stock_item__branch", "stock_item__product")

    if branch_qs is not None:
        qs = qs.filter(stock_item__branch__in=branch_qs)
    return list(qs.order_by("expiry_date"))


def get_pos_kpis(branch_qs, start_date=None, end_date=None) -> Dict[str, Any]:
    """Calculate core POS and inventory summary KPIs."""
    today = timezone.localdate()
    s_date = start_date or today
    e_date = end_date or today

    sales_qs = Sale.objects.filter(
        branch__in=branch_qs,
        completed_at__date__gte=s_date,
        completed_at__date__lte=e_date,
        status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
    )

    gross_sales = sales_qs.aggregate(tot=Sum("subtotal"))["tot"] or Decimal("0.00")
    discounts = sales_qs.aggregate(tot=Sum("discount_amount"))["tot"] or Decimal("0.00")
    net_sales = sales_qs.aggregate(tot=Sum("total_amount"))["tot"] or Decimal("0.00")
    sales_count = sales_qs.count()

    returns_qs = SaleReturn.objects.filter(
        branch__in=branch_qs,
        returned_at__date__gte=s_date,
        returned_at__date__lte=e_date,
        status=SaleReturn.Status.COMPLETED,
    )
    refunds_total = returns_qs.aggregate(tot=Sum("refund_amount"))["tot"] or Decimal("0.00")
    returns_count = returns_qs.count()

    # Total inventory valuation across accessible branches
    stock_qs = StockItem.objects.filter(branch__in=branch_qs)
    total_val = sum((item.total_inventory_value for item in stock_qs), Decimal("0.00"))

    low_stock_count = sum(1 for item in stock_qs if item.is_low_stock)

    return {
        "gross_sales": gross_sales,
        "discounts": discounts,
        "net_sales": net_sales,
        "sales_count": sales_count,
        "refunds_total": refunds_total,
        "returns_count": returns_count,
        "net_collections": net_sales - refunds_total,
        "inventory_valuation": total_val,
        "low_stock_count": low_stock_count,
    }


def search_products_for_pos(branch: Branch, query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """Fast product search by barcode, SKU, or name for POS interface."""
    q_str = query.strip()
    qs = Product.objects.filter(is_active=True).select_related("category")

    # Filter by branch availability if specified
    branch_available = Q(branches=branch) | Q(branches__isnull=True)
    qs = qs.filter(branch_available)

    if q_str:
        qs = qs.filter(
            Q(barcode__iexact=q_str)
            | Q(sku__icontains=q_str)
            | Q(name__icontains=q_str)
        )

    results = []
    product_list = list(qs[:limit])
    product_ids = [p.id for p in product_list]
    stocks = {
        si.product_id: si.quantity_on_hand
        for si in StockItem.objects.filter(branch=branch, product_id__in=product_ids)
    }

    for p in product_list:
        qty = stocks.get(p.id, Decimal("0.00"))
        results.append({
            "id": str(p.id),
            "name": p.name,
            "sku": p.sku,
            "barcode": p.barcode or "",
            "category": p.category.name,
            "price": float(p.selling_price),
            "stock": float(qty),
            "unit": p.get_unit_of_measurement_display(),
            "in_stock": qty > Decimal("0.00"),
        })
    return results
