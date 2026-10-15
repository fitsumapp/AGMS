import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from django.conf import settings


def get_gym_today() -> date:
    """Return the current date in the gym's configured timezone (Africa/Addis_Ababa)."""
    tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Africa/Addis_Ababa")
    return datetime.now(tz).date()


def add_calendar_months(orig_date: date, months: int) -> date:
    """
    Add calendar months to a date without 30-day approximations.
    Clamps day of month if the target month has fewer days
    (e.g., Jan 31 + 1 month = Feb 28 in common years, Feb 29 in leap years).
    """
    total_months = orig_date.month + months - 1
    year = orig_date.year + total_months // 12
    month = total_months % 12 + 1
    max_days = calendar.monthrange(year, month)[1]
    day = min(orig_date.day, max_days)
    return date(year, month, day)


def add_calendar_years(orig_date: date, years: int) -> date:
    """
    Add calendar years to a date without 365-day approximations.
    Handles leap-year edge cases (Feb 29 -> Feb 28 in non-leap year).
    """
    year = orig_date.year + years
    max_days = calendar.monthrange(year, orig_date.month)[1]
    day = min(orig_date.day, max_days)
    return date(year, orig_date.month, day)


def compute_membership_end_date(start_date: date, duration_unit: str, duration_value: int) -> date:
    """
    Calculate the exclusive membership end date from an inclusive start date.
    Rules:
    - DAY: start_date + timedelta(days=duration_value)
    - WEEK: start_date + timedelta(weeks=duration_value)
    - MONTH: add_calendar_months(start_date, duration_value)
    - YEAR: add_calendar_years(start_date, duration_value)
    Example:
    Start: 2026-10-14, 1 MONTH -> Exclusive End: 2026-11-14.
    """
    unit = (duration_unit or "").upper().strip()
    val = int(duration_value)
    if val <= 0:
        raise ValueError("Duration value must be positive.")

    if unit == "DAY":
        return start_date + timedelta(days=val)
    elif unit == "WEEK":
        return start_date + timedelta(weeks=val)
    elif unit == "MONTH":
        return add_calendar_months(start_date, val)
    elif unit == "YEAR":
        return add_calendar_years(start_date, val)
    else:
        raise ValueError(f"Unsupported duration unit: {duration_unit}")


def extend_end_date(current_end_date: date, duration_unit: str, duration_value: int) -> date:
    """
    Extend an existing membership exclusive end date by one package duration.
    Used for early renewal.
    """
    return compute_membership_end_date(current_end_date, duration_unit, duration_value)
