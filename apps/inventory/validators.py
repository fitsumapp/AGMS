from decimal import Decimal
import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_positive_amount(value):
    if value is None or Decimal(str(value)) <= Decimal("0.00"):
        raise ValidationError(_("Amount must be strictly greater than 0.00 ETB."))


def validate_non_negative_amount(value):
    if value is None or Decimal(str(value)) < Decimal("0.00"):
        raise ValidationError(_("Amount cannot be negative."))


def validate_positive_quantity(value):
    if value is None or Decimal(str(value)) <= Decimal("0.00"):
        raise ValidationError(_("Quantity must be strictly greater than zero."))


def validate_non_negative_quantity(value):
    if value is None or Decimal(str(value)) < Decimal("0.00"):
        raise ValidationError(_("Quantity cannot be negative."))


def validate_sku(value):
    if not value or not re.match(r"^[A-Za-z0-9_\-\.]{3,50}$", value):
        raise ValidationError(_("SKU must be between 3 and 50 characters (alphanumeric, dash, underscore, dot)."))


def validate_barcode(value):
    if value and not re.match(r"^[A-Za-z0-9_\-\.]{4,50}$", value):
        raise ValidationError(_("Barcode must be between 4 and 50 characters."))


def sanitize_csv_cell(value: str) -> str:
    """Sanitize CSV cells against CSV Formula Injection."""
    if not value:
        return ""
    str_val = str(value).strip()
    if str_val.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{str_val}"
    return str_val
