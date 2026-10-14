from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

phone_validator = RegexValidator(r"^\+?[0-9 ()-]{7,25}$", "Enter a valid phone number.")
currency_validator = RegexValidator(r"^[A-Z]{3}$", "Use a three-letter uppercase currency code.")

def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Choose a valid IANA timezone.")

def validate_logo(value):
    if value.size > 2 * 1024 * 1024:
        raise ValidationError("Logo must be at most 2 MB.")
