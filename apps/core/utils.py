from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.utils import timezone

def local_date(zone="Africa/Addis_Ababa"):
    return timezone.localdate(timezone=ZoneInfo(zone))

def format_money(amount: Decimal, currency="ETB"):
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise ValueError("Money must be a finite Decimal.")
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f} {currency}"

def safe_admin_error(request):
    messages.error(request, "The operation could not be completed. Please contact your administrator.")
