import json
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.analytics.dates import resolve_date_filter, get_gym_timezone
from apps.billing.models import PaymentMethod
from apps.branches.models import Branch
from apps.branches.services import accessible_branches, active_branch as get_active_branch_from_session
from apps.members.models import Member
from .models import (
    CashierShift,
    Product,
    Sale,
    SaleReceipt,
    StockItem,
)
from .permissions import (
    can_operate_pos,
    can_view_inventory_reports,
    accessible_branches_for_inventory,
)
from .reports import (
    export_report_to_csv,
    extract_cashier_performance_report,
    extract_daily_sales_report,
    extract_expiring_products_report,
    extract_inventory_valuation_report,
    extract_low_stock_report,
    extract_sales_by_product_report,
    extract_stock_movement_report,
)
from .selectors import get_pos_kpis, search_products_for_pos
from .services import close_cashier_shift, open_cashier_shift, process_pos_checkout


@login_required
def pos_checkout_view(request):
    """
    Dedicated POS checkout terminal within Django Unfold:
    - Shift management (open/close cash drawer)
    - Barcode scanner input & instant product search
    - Cart items & quantity modification
    - Member link or walk-in customer
    - Tender split (Cash, Bank Transfer, Telebirr, CBE Birr)
    - Immediate thermal receipt generation
    """
    if not can_operate_pos(request.user):
        messages.error(request, _("You do not have permission to access the POS Terminal."))
        return redirect("admin:index")

    branches = accessible_branches_for_inventory(request.user)
    if not branches.exists():
        messages.error(request, _("You do not have access to any gym branch."))
        return redirect("admin:index")

    branch_id = request.GET.get("branch_id")
    if branch_id:
        active_branch = branches.filter(id=branch_id).first()
    else:
        active_branch = get_active_branch_from_session(request) or branches.first()

    # Active shift check
    active_shift = CashierShift.objects.filter(
        cashier=request.user,
        branch=active_branch,
        status=CashierShift.Status.OPEN,
    ).first()

    # Handle Shift Open / Close POST
    if request.method == "POST" and "action" in request.POST:
        action = request.POST.get("action")

        if action == "open_shift":
            opening_float = Decimal(request.POST.get("opening_float", "0.00") or "0.00")
            notes = request.POST.get("notes", "")
            try:
                active_shift = open_cashier_shift(
                    actor=request.user,
                    branch=active_branch,
                    opening_float=opening_float,
                    notes=notes,
                )
                messages.success(request, _(f"Shift {active_shift.shift_number} opened with float {opening_float} ETB."))
                return redirect(f"{reverse('pos-checkout')}?branch_id={active_branch.id}")
            except ValidationError as e:
                messages.error(request, str(e.message if hasattr(e, "message") else e))

        elif action == "close_shift":
            if not active_shift:
                messages.error(request, _("No active shift found to close."))
                return redirect(reverse("pos-checkout"))
            declared_cash = Decimal(request.POST.get("declared_cash", "0.00") or "0.00")
            notes = request.POST.get("notes", "")
            try:
                closed = close_cashier_shift(
                    actor=request.user,
                    shift=active_shift,
                    declared_cash=declared_cash,
                    notes=notes,
                )
                messages.success(
                    request,
                    _(f"Shift {closed.shift_number} closed. Expected: {closed.expected_cash} ETB, "
                      f"Declared: {closed.declared_cash} ETB, Variance: {closed.cash_variance} ETB.")
                )
                return redirect(f"{reverse('pos-checkout')}?branch_id={active_branch.id}")
            except ValidationError as e:
                messages.error(request, str(e.message if hasattr(e, "message") else e))

        elif action == "process_sale":
            if not active_shift:
                messages.error(request, _("You must open a cashier shift before ringing sales."))
                return redirect(f"{reverse('pos-checkout')}?branch_id={active_branch.id}")

            try:
                cart_json = request.POST.get("cart_data", "[]")
                cart_items = json.loads(cart_json)
                if not cart_items:
                    raise ValidationError(_("Cart cannot be empty."))

                # Build items data
                items_data = []
                for it in cart_items:
                    product = get_object_or_404(Product, id=it["product_id"])
                    qty = Decimal(str(it["quantity"]))
                    items_data.append({
                        "product": product,
                        "quantity": qty,
                        "unit_price": Decimal(str(it.get("unit_price", product.selling_price))),
                    })

                # Payments data
                payment_method_id = request.POST.get("payment_method_id")
                payment_method = get_object_or_404(PaymentMethod, id=payment_method_id)
                amount_tendered = Decimal(request.POST.get("tendered_amount", "0.00") or "0.00")
                amount_paid = Decimal(request.POST.get("paid_amount", "0.00") or "0.00")
                ref = request.POST.get("payment_reference", "")

                payments_data = [{
                    "payment_method": payment_method,
                    "amount": amount_paid,
                    "tendered_amount": amount_tendered if amount_tendered >= amount_paid else amount_paid,
                    "reference": ref,
                }]

                member_id = request.POST.get("member_id")
                member = Member.objects.filter(id=member_id).first() if member_id else None
                cust_name = request.POST.get("customer_name", "Walk-in Customer")
                cust_phone = request.POST.get("customer_phone", "")
                discount_amt = Decimal(request.POST.get("discount_amount", "0.00") or "0.00")
                discount_reason = request.POST.get("discount_reason", "")

                sale = process_pos_checkout(
                    actor=request.user,
                    branch=active_branch,
                    items_data=items_data,
                    payments_data=payments_data,
                    member=member,
                    customer_name=cust_name,
                    customer_phone=cust_phone,
                    discount_amount=discount_amt,
                    discount_reason=discount_reason,
                    shift=active_shift,
                )
                messages.success(request, _(f"Sale {sale.sale_number} completed successfully! ({sale.total_amount} ETB)"))
                return redirect(reverse("pos-receipt", kwargs={"sale_id": sale.id}))
            except (ValidationError, json.JSONDecodeError) as e:
                msg = e.message if hasattr(e, "message") else str(e)
                messages.error(request, msg)

    # Active payment methods for checkout
    payment_methods = PaymentMethod.objects.filter(is_active=True).order_by("name")

    # Fast initial catalog
    popular_products = search_products_for_pos(active_branch, query="", limit=30)

    # Recent sales in shift or today
    recent_sales = Sale.objects.filter(branch=active_branch).order_by("-completed_at")[:10]

    context = {
        "title": f"POS Checkout — {active_branch.name}",
        "active_branch": active_branch,
        "branches": branches,
        "active_shift": active_shift,
        "payment_methods": payment_methods,
        "products": popular_products,
        "recent_sales": recent_sales,
        "today": timezone.localdate(),
    }
    return render(request, "admin/inventory/pos_checkout.html", context)


@login_required
def pos_product_lookup_view(request):
    """AJAX/API endpoint for live product and barcode lookups."""
    if not can_operate_pos(request.user):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    branch_id = request.GET.get("branch_id")
    branches = accessible_branches_for_inventory(request.user)
    branch = branches.filter(id=branch_id).first() if branch_id else branches.first()
    if not branch:
        return JsonResponse({"error": "Invalid branch"}, status=400)

    q = request.GET.get("q", "").strip()
    results = search_products_for_pos(branch, query=q, limit=25)
    return JsonResponse({"products": results})


@login_required
def pos_receipt_view(request, sale_id):
    """View and print receipt for completed sale."""
    if not can_operate_pos(request.user):
        raise Http404

    sale = get_object_or_404(
        Sale.objects.select_related("branch", "cashier", "receipt"),
        id=sale_id,
    )

    receipt = getattr(sale, "receipt", None)
    context = {
        "title": f"Receipt — {sale.sale_number}",
        "sale": sale,
        "receipt": receipt,
        "items": sale.items.select_related("product").all(),
        "payments": sale.payments.select_related("payment_method").all(),
    }
    return render(request, "admin/inventory/pos_receipt.html", context)


@login_required
def pos_reports_view(request):
    """Sales & Inventory Reports Center with CSV export support."""
    if not can_view_inventory_reports(request.user):
        messages.error(request, _("You do not have permission to view inventory reports."))
        return redirect("admin:index")

    branches = accessible_branches_for_inventory(request.user)
    branch_id = request.GET.get("branch_id")
    if branch_id:
        branch_qs = branches.filter(id=branch_id)
        selected_branch = branch_qs.first()
    else:
        branch_qs = branches
        selected_branch = None

    period = request.GET.get("period", "last_30_days")
    preset_map = {
        "today": "today",
        "7d": "last_7_days",
        "30d": "last_30_days",
        "this_month": "this_month",
        "last_month": "last_month",
    }
    date_range = resolve_date_filter(preset=preset_map.get(period, "last_30_days"))
    report_type = request.GET.get("report_type", "daily_sales")
    export_format = request.GET.get("export")

    data_rows = []
    headers = []

    if report_type == "daily_sales":
        data_rows = extract_daily_sales_report(branch_qs, date_range)
    elif report_type == "sales_by_product":
        data_rows = extract_sales_by_product_report(branch_qs, date_range)
    elif report_type == "inventory_valuation":
        data_rows = extract_inventory_valuation_report(branch_qs)
    elif report_type == "low_stock":
        data_rows = extract_low_stock_report(branch_qs)
    elif report_type == "expiring_products":
        data_rows = extract_expiring_products_report(branch_qs)
    elif report_type == "stock_movements":
        data_rows = extract_stock_movement_report(branch_qs, date_range)
    elif report_type == "cashier_performance":
        data_rows = extract_cashier_performance_report(branch_qs, date_range)

    if export_format == "csv":
        return export_report_to_csv(data_rows, filename_prefix=f"agms_pos_{report_type}")

    if data_rows:
        headers = list(data_rows[0].keys())

    kpis = get_pos_kpis(branch_qs, start_date=date_range.start_date, end_date=date_range.end_date)

    context = {
        "title": "Sales & Inventory Reports",
        "branches": branches,
        "selected_branch": selected_branch,
        "period": period,
        "date_range": date_range,
        "report_type": report_type,
        "headers": headers,
        "data_rows": data_rows,
        "kpis": kpis,
    }
    return render(request, "admin/inventory/pos_reports.html", context)
