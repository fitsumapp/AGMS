import csv
from decimal import Decimal
import io
from typing import Any, Dict, List
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse
from django.utils import timezone

from apps.analytics.dates import DateFilterRange, get_gym_timezone
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
from .validators import sanitize_csv_cell


def extract_daily_sales_report(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Daily sales aggregation: transactions, revenue, discounts, refunds, and net collections."""
    sales = (
        Sale.objects.filter(
            branch__in=branch_qs,
            completed_at__date__gte=date_range.start_date,
            completed_at__date__lte=date_range.end_date,
            status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
        )
        .values("completed_at__date")
        .annotate(
            tx_count=Count("id"),
            gross=Sum("subtotal"),
            discounts=Sum("discount_amount"),
            tax=Sum("tax_amount"),
            total=Sum("total_amount"),
        )
        .order_by("-completed_at__date")
    )

    returns = (
        SaleReturn.objects.filter(
            branch__in=branch_qs,
            returned_at__date__gte=date_range.start_date,
            returned_at__date__lte=date_range.end_date,
            status=SaleReturn.Status.COMPLETED,
        )
        .values("returned_at__date")
        .annotate(
            ret_count=Count("id"),
            refunds=Sum("refund_amount"),
        )
    )
    ret_map = {r["returned_at__date"]: r for r in returns}

    rows = []
    for s in sales:
        d = s["completed_at__date"]
        r_info = ret_map.get(d, {"ret_count": 0, "refunds": Decimal("0.00")})
        gross = s["gross"] or Decimal("0.00")
        disc = s["discounts"] or Decimal("0.00")
        tax = s["tax"] or Decimal("0.00")
        tot = s["total"] or Decimal("0.00")
        ref = r_info["refunds"] or Decimal("0.00")
        net = tot - ref

        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "transactions": s["tx_count"],
            "gross_sales": float(gross),
            "discounts": float(disc),
            "taxes": float(tax),
            "net_sales": float(tot),
            "returns_count": r_info["ret_count"],
            "refunds": float(ref),
            "net_collections": float(net),
        })
    return rows


def extract_sales_by_product_report(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Product performance: quantity sold, total revenue, estimated cost, and gross profit margin."""
    items = (
        SaleItem.objects.filter(
            sale__branch__in=branch_qs,
            sale__completed_at__date__gte=date_range.start_date,
            sale__completed_at__date__lte=date_range.end_date,
            sale__status__in=[Sale.Status.COMPLETED, Sale.Status.PARTIALLY_RETURNED],
        )
        .values(
            "product__name",
            "product__sku",
            "product__category__name",
            "product__unit_of_measurement",
        )
        .annotate(
            total_qty=Sum("quantity"),
            returned_qty=Sum("returned_quantity"),
            revenue=Sum("total_price"),
            cost=Sum(F("quantity") * F("unit_cost")),
        )
        .order_by("-revenue")
    )

    rows = []
    for it in items:
        tot_qty = it["total_qty"] or Decimal("0.00")
        ret_qty = it["returned_qty"] or Decimal("0.00")
        net_qty = max(Decimal("0.00"), tot_qty - ret_qty)
        rev = it["revenue"] or Decimal("0.00")
        cost = it["cost"] or Decimal("0.00")
        profit = rev - cost
        margin_pct = (profit / rev * 100) if rev > Decimal("0.00") else Decimal("0.00")

        rows.append({
            "product": sanitize_csv_cell(it["product__name"]),
            "sku": sanitize_csv_cell(it["product__sku"]),
            "category": sanitize_csv_cell(it["product__category__name"]),
            "unit": it["product__unit_of_measurement"],
            "quantity_sold": float(net_qty),
            "revenue": float(rev),
            "cogs": float(cost),
            "gross_profit": float(profit),
            "margin_percent": round(float(margin_pct), 2),
        })
    return rows


def extract_inventory_valuation_report(branch_qs) -> List[Dict[str, Any]]:
    """
    Inventory valuation breakdown:
    Costing Method: Weighted Moving Average Costing (per Ethiopian and IFRS accounting standards).
    """
    stock_items = (
        StockItem.objects.filter(branch__in=branch_qs)
        .select_related("branch", "product", "product__category")
        .order_by("branch__name", "product__name")
    )
    rows = []
    for si in stock_items:
        val = si.total_inventory_value
        retail_val = (si.quantity_on_hand * si.product.selling_price).quantize(Decimal("0.01"))
        rows.append({
            "branch": sanitize_csv_cell(si.branch.name),
            "product": sanitize_csv_cell(si.product.name),
            "sku": sanitize_csv_cell(si.product.sku),
            "category": sanitize_csv_cell(si.product.category.name),
            "quantity_on_hand": float(si.quantity_on_hand),
            "unit": si.product.unit_of_measurement,
            "average_unit_cost": float(si.average_cost_price),
            "total_inventory_cost": float(val),
            "retail_selling_price": float(si.product.selling_price),
            "potential_retail_value": float(retail_val),
            "is_low_stock": "YES" if si.is_low_stock else "NO",
        })
    return rows


def extract_low_stock_report(branch_qs) -> List[Dict[str, Any]]:
    """Stock items currently below the reorder threshold."""
    items = (
        StockItem.objects.filter(branch__in=branch_qs)
        .select_related("branch", "product", "product__category")
        .order_by("branch__name", "product__name")
    )
    rows = []
    for it in items:
        if it.is_low_stock:
            deficit = it.product.low_stock_threshold - it.quantity_on_hand
            rows.append({
                "branch": sanitize_csv_cell(it.branch.name),
                "product": sanitize_csv_cell(it.product.name),
                "sku": sanitize_csv_cell(it.product.sku),
                "category": sanitize_csv_cell(it.product.category.name),
                "quantity_on_hand": float(it.quantity_on_hand),
                "threshold": it.product.low_stock_threshold,
                "reorder_deficit": float(deficit),
                "unit": it.product.unit_of_measurement,
                "unit_cost": float(it.product.cost_price),
                "estimated_reorder_cost": float(deficit * it.product.cost_price) if deficit > 0 else 0.0,
            })
    return rows


def extract_expiring_products_report(branch_qs, days: int = 60) -> List[Dict[str, Any]]:
    """Batches nearing or past expiration date."""
    today = timezone.localdate()
    cutoff = today + timezone.timedelta(days=days)
    batches = (
        StockBatch.objects.filter(
            stock_item__branch__in=branch_qs,
            expiry_date__lte=cutoff,
            quantity__gt=Decimal("0.00"),
        )
        .select_related("stock_item", "stock_item__branch", "stock_item__product")
        .order_by("expiry_date")
    )
    rows = []
    for b in batches:
        days_left = (b.expiry_date - today).days if b.expiry_date else 999
        status_lbl = "EXPIRED" if days_left < 0 else (f"{days_left} days left" if days_left <= 30 else "Expiring Soon")
        rows.append({
            "branch": sanitize_csv_cell(b.stock_item.branch.name),
            "product": sanitize_csv_cell(b.stock_item.product.name),
            "sku": sanitize_csv_cell(b.stock_item.product.sku),
            "batch_number": sanitize_csv_cell(b.batch_number),
            "quantity": float(b.quantity),
            "expiry_date": b.expiry_date.strftime("%Y-%m-%d") if b.expiry_date else "N/A",
            "days_remaining": days_left,
            "status": status_lbl,
            "unit_cost": float(b.cost_price),
            "at_risk_value": float((b.quantity * b.cost_price).quantize(Decimal("0.01"))),
        })
    return rows


def extract_stock_movement_report(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Audit ledger of all stock quantity changes."""
    movements = (
        StockMovement.objects.filter(
            stock_item__branch__in=branch_qs,
            timestamp__date__gte=date_range.start_date,
            timestamp__date__lte=date_range.end_date,
        )
        .select_related("stock_item", "stock_item__branch", "stock_item__product", "actor")
        .order_by("-timestamp")
    )
    rows = []
    for m in movements:
        rows.append({
            "timestamp": m.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "branch": sanitize_csv_cell(m.stock_item.branch.name),
            "product": sanitize_csv_cell(m.stock_item.product.name),
            "sku": sanitize_csv_cell(m.stock_item.product.sku),
            "movement_type": m.get_movement_type_display(),
            "quantity_change": float(m.quantity_change),
            "balance_before": float(m.balance_before),
            "balance_after": float(m.balance_after),
            "reference": sanitize_csv_cell(m.reference),
            "actor": sanitize_csv_cell(m.actor.get_full_name() or getattr(m.actor, "email", str(m.actor))),
            "notes": sanitize_csv_cell(m.notes),
        })
    return rows


def extract_cashier_performance_report(branch_qs, date_range: DateFilterRange) -> List[Dict[str, Any]]:
    """Performance metrics and cash discrepancies by cashier."""
    shifts = (
        CashierShift.objects.filter(
            branch__in=branch_qs,
            opened_at__date__gte=date_range.start_date,
            opened_at__date__lte=date_range.end_date,
        )
        .values("cashier__email", "cashier__first_name", "cashier__last_name", "branch__name")
        .annotate(
            shift_count=Count("id"),
            total_sales=Sum("total_sales_amount"),
            total_refunds=Sum("total_refunds_amount"),
            net_variance=Sum("cash_variance"),
        )
        .order_by("-total_sales")
    )
    rows = []
    for s in shifts:
        fn = f"{s['cashier__first_name']} {s['cashier__last_name']}".strip() or s["cashier__email"]
        sales = s["total_sales"] or Decimal("0.00")
        refs = s["total_refunds"] or Decimal("0.00")
        var = s["net_variance"] or Decimal("0.00")
        rows.append({
            "cashier": sanitize_csv_cell(fn),
            "branch": sanitize_csv_cell(s["branch__name"]),
            "shifts_worked": s["shift_count"],
            "total_sales": float(sales),
            "total_refunds": float(refs),
            "net_collected": float(sales - refs),
            "cash_variance": float(var),
        })
    return rows


def export_report_to_csv(rows: List[Dict[str, Any]], filename_prefix: str) -> HttpResponse:
    """Generate sanitized CSV file response."""
    output = io.StringIO()
    if rows:
        headers = list(rows[0].keys())
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    else:
        output.write("No data available for the selected criteria.\n")

    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    filename = f"{filename_prefix}_{timezone.localdate().strftime('%Y%m%d')}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
