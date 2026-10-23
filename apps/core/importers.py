import csv
from decimal import Decimal
import io
import logging
from typing import Optional, Any, Dict, List, Tuple
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone

from apps.accounts.models import User
from apps.branches.models import Branch
from apps.branches.audit import record_event
from apps.members.models import Member
from apps.inventory.models import (
    Product,
    ProductCategory,
    StockItem,
    generate_next_inventory_number,
)
from apps.inventory.services import get_or_create_stock_item
from apps.facilities.models import Equipment, EquipmentCategory, FacilityArea

logger = logging.getLogger("gym.importers")

MAX_IMPORT_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMPORT_ROWS = 1000

# Formula injection prefixes
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value: Any) -> str:
    """Sanitize cell content to mitigate CSV formula injection in spreadsheet software."""
    if value is None:
        return ""
    str_val = str(value)
    if str_val.startswith(FORMULA_PREFIXES):
        return f"'{str_val}"
    return str_val


def parse_csv_input(file_obj, max_rows: int = MAX_IMPORT_ROWS) -> list[dict]:
    """
    Safely parse an uploaded CSV file with UTF-8 decoding, BOM handling,
    and row-count boundary enforcement.
    """
    if hasattr(file_obj, "size") and file_obj.size > MAX_IMPORT_FILE_SIZE_BYTES:
        raise ValidationError(f"File size exceeds maximum allowed limit of {MAX_IMPORT_FILE_SIZE_BYTES // (1024 * 1024)} MB.")

    if hasattr(file_obj, "read"):
        content = file_obj.read()
        if isinstance(content, bytes):
            # Try utf-8-sig first to remove BOM
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = content.decode("latin-1")
        else:
            text = content
    else:
        text = str(file_obj)

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValidationError("Uploaded CSV file contains no headers or is empty.")

    rows = []
    for idx, row in enumerate(reader, start=2):
        if idx - 1 > max_rows:
            raise ValidationError(f"Import row limit exceeded. Maximum allowed rows per batch is {max_rows}.")
        # Strip whitespace from keys and values
        clean_row = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}
        rows.append((idx, clean_row))

    return rows


# ==============================================================================
# 1. Member Import Engine
# ==============================================================================

def import_members(
    *,
    actor: User,
    branch: Branch,
    file_obj,
    dry_run: bool = False,
) -> dict:
    """
    Import gym members from CSV with duplicate phone check, schema validation,
    and dry-run preview support.
    Expected headers: first_name, last_name, phone, email (optional), gender (optional), date_of_birth (optional)
    """
    rows = parse_csv_input(file_obj)
    created_count = 0
    errors = []
    duplicates = []
    valid_members = []

    for line_num, data in rows:
        fn = data.get("first_name", "")
        ln = data.get("last_name", "")
        phone = data.get("phone", "")
        email = data.get("email", "")
        gender = data.get("gender", "OTHER").upper()
        dob_str = data.get("date_of_birth", "")

        if not fn or not ln or not phone:
            errors.append(f"Line {line_num}: Missing required fields (first_name, last_name, phone).")
            continue

        # Check existing duplicate phone
        if Member.objects.filter(phone=phone).exists():
            duplicates.append(f"Line {line_num}: Member with phone '{phone}' already exists.")
            continue

        dob = None
        if dob_str:
            try:
                dob = timezone.datetime.strptime(dob_str, "%Y-%m-%d").date()
            except ValueError:
                errors.append(f"Line {line_num}: Invalid date_of_birth format '{dob_str}'. Use YYYY-MM-DD.")
                continue

        valid_members.append({
            "first_name": fn,
            "last_name": ln,
            "phone": phone,
            "email": email,
            "gender": gender if gender in ("MALE", "FEMALE", "OTHER") else "OTHER",
            "date_of_birth": dob,
        })

    if not dry_run and not errors and valid_members:
        with transaction.atomic():
            for m_data in valid_members:
                m = Member.objects.create(
                    home_branch=branch,
                    first_name=m_data["first_name"],
                    last_name=m_data["last_name"],
                    phone=m_data["phone"],
                    email=m_data["email"],
                    gender=m_data["gender"],
                    date_of_birth=m_data["date_of_birth"],
                )
                created_count += 1

            record_event(
                actor=actor,
                action="bulk_members_imported",
                target=branch,
                branch=branch,
                metadata={
                    "branch_code": branch.code,
                    "import_type": "members",
                    "records_imported": str(created_count),
                },
            )

    return {
        "dry_run": dry_run,
        "total_rows": len(rows),
        "valid_count": len(valid_members),
        "created_count": created_count,
        "errors": errors,
        "duplicates": duplicates,
    }


# ==============================================================================
# 2. Product Catalog Import Engine
# ==============================================================================

def import_products(
    *,
    actor: User,
    branch: Branch,
    file_obj,
    dry_run: bool = False,
) -> dict:
    """
    Import retail product catalog from CSV.
    Expected headers: name, sku, category_code, unit_price_etb, initial_stock (optional)
    """
    rows = parse_csv_input(file_obj)
    created_count = 0
    errors = []
    duplicates = []
    valid_products = []

    for line_num, data in rows:
        name = data.get("name", "")
        sku = data.get("sku", "").upper()
        cat_code = data.get("category_code", "").upper()
        price_str = data.get("unit_price_etb", "0.00")
        stock_str = data.get("initial_stock", "0")

        if not name or not sku or not cat_code:
            errors.append(f"Line {line_num}: Missing required fields (name, sku, category_code).")
            continue

        if Product.objects.filter(sku=sku).exists():
            duplicates.append(f"Line {line_num}: Product with SKU '{sku}' already exists.")
            continue

        category = ProductCategory.objects.filter(code=cat_code).first()
        if not category:
            errors.append(f"Line {line_num}: Category code '{cat_code}' does not exist.")
            continue

        try:
            price = Decimal(price_str)
            if price < Decimal("0.00"):
                raise ValueError
        except Exception:
            errors.append(f"Line {line_num}: Invalid unit_price_etb '{price_str}'. Must be a non-negative decimal.")
            continue

        try:
            stock = int(stock_str)
            if stock < 0:
                raise ValueError
        except Exception:
            errors.append(f"Line {line_num}: Invalid initial_stock '{stock_str}'. Must be a non-negative integer.")
            continue

        valid_products.append({
            "name": name,
            "sku": sku,
            "category": category,
            "unit_price_etb": price,
            "initial_stock": stock,
        })

    if not dry_run and not errors and valid_products:
        with transaction.atomic():
            for p_data in valid_products:
                prd_number = generate_next_inventory_number("product", "PRD")
                prod = Product.objects.create(
                    product_number=prd_number,
                    category=p_data["category"],
                    name=p_data["name"],
                    sku=p_data["sku"],
                    cost_price=p_data["unit_price_etb"],
                    selling_price=p_data["unit_price_etb"],
                    unit_of_measurement=Product.Unit.PCS,
                )
                if p_data["initial_stock"] > 0:
                    stock_item = get_or_create_stock_item(branch=branch, product=prod)
                    stock_item.quantity_on_hand = Decimal(str(p_data["initial_stock"]))
                    stock_item.save(update_fields=["quantity_on_hand"])
                created_count += 1

            record_event(
                actor=actor,
                action="bulk_products_imported",
                target=branch,
                branch=branch,
                metadata={
                    "branch_code": branch.code,
                    "import_type": "products",
                    "records_imported": str(created_count),
                },
            )

    return {
        "dry_run": dry_run,
        "total_rows": len(rows),
        "valid_count": len(valid_products),
        "created_count": created_count,
        "errors": errors,
        "duplicates": duplicates,
    }


# ==============================================================================
# 3. Equipment Registry Import Engine
# ==============================================================================

def import_equipment(
    *,
    actor: User,
    branch: Branch,
    file_obj,
    dry_run: bool = False,
) -> dict:
    """
    Import heavy gym machinery and equipment assets from CSV.
    Expected headers: asset_code, name, category_code, area_code (optional), manufacturer, model_number
    """
    rows = parse_csv_input(file_obj)
    created_count = 0
    errors = []
    duplicates = []
    valid_equipment = []

    for line_num, data in rows:
        code = data.get("asset_code", "").upper()
        name = data.get("name", "")
        cat_code = data.get("category_code", "").upper()
        area_code = data.get("area_code", "").upper()
        mfg = data.get("manufacturer", "")
        model_no = data.get("model_number", "")

        if not code or not name or not cat_code:
            errors.append(f"Line {line_num}: Missing required fields (asset_code, name, category_code).")
            continue

        if Equipment.objects.filter(asset_code=code).exists():
            duplicates.append(f"Line {line_num}: Asset code '{code}' already exists.")
            continue

        category = EquipmentCategory.objects.filter(code=cat_code).first()
        if not category:
            errors.append(f"Line {line_num}: Category '{cat_code}' does not exist.")
            continue

        area = None
        if area_code:
            area = FacilityArea.objects.filter(branch=branch, code=area_code).first()
            if not area:
                errors.append(f"Line {line_num}: Facility area '{area_code}' does not exist in branch {branch.code}.")
                continue

        valid_equipment.append({
            "asset_code": code,
            "name": name,
            "category": category,
            "facility_area": area,
            "manufacturer": mfg,
            "model_number": model_no,
        })

    if not dry_run and not errors and valid_equipment:
        with transaction.atomic():
            for eq_data in valid_equipment:
                Equipment.objects.create(
                    branch=branch,
                    asset_code=eq_data["asset_code"],
                    name=eq_data["name"],
                    category=eq_data["category"],
                    facility_area=eq_data["facility_area"],
                    manufacturer=eq_data["manufacturer"],
                    model_number=eq_data["model_number"],
                )
                created_count += 1

            record_event(
                actor=actor,
                action="bulk_equipment_imported",
                target=branch,
                branch=branch,
                metadata={
                    "branch_code": branch.code,
                    "import_type": "equipment",
                    "records_imported": str(created_count),
                },
            )

    return {
        "dry_run": dry_run,
        "total_rows": len(rows),
        "valid_count": len(valid_equipment),
        "created_count": created_count,
        "errors": errors,
        "duplicates": duplicates,
    }


# ==============================================================================
# 4. Branch Data Export Engine (Sanitized CSV)
# ==============================================================================

def export_branch_data(
    *,
    actor: User,
    branch: Branch,
    data_type: str,
) -> HttpResponse:
    """
    Export branch-specific data as CSV with formula-injection sanitization.
    """
    filename = f"agms_{branch.code.lower()}_{data_type}_{timezone.now().strftime('%Y%m%d%H%M')}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    # Write UTF-8 BOM so Excel opens Amharic and unicode correctly
    response.write("\ufeff")
    writer = csv.writer(response)

    if data_type == "members":
        writer.writerow(["Member Number", "First Name", "Last Name", "Phone", "Email", "Status", "Joined At"])
        for m in Member.objects.filter(home_branch=branch).order_by("-created_at")[:1000]:
            writer.writerow([
                sanitize_csv_cell(m.member_number),
                sanitize_csv_cell(m.first_name),
                sanitize_csv_cell(m.last_name),
                sanitize_csv_cell(m.phone),
                sanitize_csv_cell(m.email),
                sanitize_csv_cell(m.get_status_display()),
                sanitize_csv_cell(m.created_at.strftime("%Y-%m-%d")),
            ])

    elif data_type == "equipment":
        writer.writerow(["Asset Code", "Name", "Category", "Area", "Status", "Condition", "Downtime (Hours)"])
        for eq in Equipment.objects.filter(branch=branch).order_by("asset_code"):
            writer.writerow([
                sanitize_csv_cell(eq.asset_code),
                sanitize_csv_cell(eq.name),
                sanitize_csv_cell(eq.category.name if eq.category else ""),
                sanitize_csv_cell(eq.facility_area.name if eq.facility_area else ""),
                sanitize_csv_cell(eq.get_status_display()),
                sanitize_csv_cell(eq.get_condition_display()),
                sanitize_csv_cell(str(eq.total_downtime_hours)),
            ])

    elif data_type == "products":
        writer.writerow(["SKU", "Product Name", "Category", "Unit Price (ETB)", "Current Stock"])
        for prod in Product.objects.all().order_by("sku"):
            st = StockItem.objects.filter(product=prod, branch=branch).first()
            writer.writerow([
                sanitize_csv_cell(prod.sku),
                sanitize_csv_cell(prod.name),
                sanitize_csv_cell(prod.category.name if prod.category else ""),
                sanitize_csv_cell(str(prod.selling_price)),
                sanitize_csv_cell(str(st.quantity_on_hand if st else 0)),
            ])

    elif data_type == "audit_logs":
        from apps.branches.models import AuditEvent
        writer.writerow(["Timestamp", "Actor", "Action", "Target Type", "Target ID", "Details"])
        qs = AuditEvent.objects.filter(branch=branch).order_by("-occurred_at")[:1000]
        for evt in qs:
            writer.writerow([
                sanitize_csv_cell(evt.occurred_at.strftime("%Y-%m-%d %H:%M:%S")),
                sanitize_csv_cell(evt.actor.email if evt.actor else "System"),
                sanitize_csv_cell(evt.action),
                sanitize_csv_cell(evt.target_type),
                sanitize_csv_cell(str(evt.target_id)),
                sanitize_csv_cell(str(evt.metadata)),
            ])
    else:
        raise ValidationError(f"Unsupported export data type '{data_type}'.")

    return response
