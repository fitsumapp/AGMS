from decimal import Decimal
import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_positive_amount(value):
    if value is None or value <= Decimal("0.00"):
        raise ValidationError(_("Amount must be strictly greater than zero."))


def validate_non_negative_amount(value):
    if value is not None and value < Decimal("0.00"):
        raise ValidationError(_("Amount cannot be negative."))


def validate_receipt_document(file_obj):
    if not file_obj:
        return
    max_size = 10 * 1024 * 1024  # 10 MB
    if file_obj.size > max_size:
        raise ValidationError(_("Receipt attachment file size must not exceed 10 MB."))
    ext = file_obj.name.split(".")[-1].lower() if "." in file_obj.name else ""
    allowed_extensions = {"pdf", "png", "jpg", "jpeg", "webp"}
    if ext not in allowed_extensions:
        raise ValidationError(_(f"Unsupported document format .{ext}. Allowed: {', '.join(allowed_extensions)}."))


def mask_account_number(account_number: str) -> str:
    """Mask account number to protect banking details (e.g., '****1234')."""
    if not account_number:
        return ""
    clean = account_number.strip()
    if len(clean) <= 4:
        return "****"
    return f"{'*' * (len(clean) - 4)}{clean[-4:]}"


def mask_tax_id(tax_id: str) -> str:
    """Mask tax identification number (e.g., 'TIN-*****678')."""
    if not tax_id:
        return ""
    clean = tax_id.strip()
    if len(clean) <= 4:
        return "****"
    return f"{clean[:3]}-{ '*' * (len(clean) - 6) }{clean[-3:]}" if len(clean) >= 6 else f"****{clean[-3:]}"


def sanitize_csv_cell(value: str) -> str:
    """Sanitize CSV cell content to mitigate CSV formula injection vulnerabilities."""
    if not value:
        return ""
    val_str = str(value)
    if val_str.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{val_str}"
    return val_str
