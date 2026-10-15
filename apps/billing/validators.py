from decimal import Decimal
from django.core.exceptions import ValidationError


def validate_non_negative_amount(value: Decimal):
    """Ensure money amounts are zero or positive."""
    if value is not None and value < Decimal("0.00"):
        raise ValidationError("Amount cannot be negative.")


def validate_positive_amount(value: Decimal):
    """Ensure money amounts are strictly positive."""
    if value is not None and value <= Decimal("0.00"):
        raise ValidationError("Amount must be greater than zero.")
