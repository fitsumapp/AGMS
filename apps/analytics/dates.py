from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils import timezone


def get_gym_timezone() -> ZoneInfo:
    tz_name = getattr(settings, "TIME_ZONE", "Africa/Addis_Ababa")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Africa/Addis_Ababa")


def get_today_in_gym_tz() -> date:
    return timezone.now().astimezone(get_gym_timezone()).date()


@dataclass(frozen=True)
class DateFilterRange:
    preset: str
    start_date: date
    end_date: date
    start_datetime: datetime
    end_datetime: datetime
    label: str

    @property
    def days_count(self) -> int:
        return (self.end_date - self.start_date).days + 1


def resolve_date_filter(
    preset: Optional[str] = "this_month",
    custom_start: Optional[date] = None,
    custom_end: Optional[date] = None,
) -> DateFilterRange:
    """
    Resolve a date filter preset or custom range into exact date boundaries
    and timezone-aware UTC/Addis_Ababa datetimes.
    """
    tz = get_gym_timezone()
    today = get_today_in_gym_tz()

    preset = (preset or "this_month").strip().lower()

    if preset == "today":
        start_d = today
        end_d = today
        label = f"Today ({today:%b %d, %Y})"
    elif preset == "yesterday":
        start_d = today - timedelta(days=1)
        end_d = today - timedelta(days=1)
        label = f"Yesterday ({start_d:%b %d, %Y})"
    elif preset == "last_7_days":
        start_d = today - timedelta(days=6)
        end_d = today
        label = f"Last 7 Days ({start_d:%b %d} - {end_d:%b %d, %Y})"
    elif preset == "last_30_days":
        start_d = today - timedelta(days=29)
        end_d = today
        label = f"Last 30 Days ({start_d:%b %d} - {end_d:%b %d, %Y})"
    elif preset == "this_month":
        start_d = today.replace(day=1)
        end_d = today
        label = f"This Month ({start_d:%b %d} - {end_d:%b %d, %Y})"
    elif preset == "previous_month":
        first_of_this_month = today.replace(day=1)
        last_day_prev_month = first_of_this_month - timedelta(days=1)
        start_d = last_day_prev_month.replace(day=1)
        end_d = last_day_prev_month
        label = f"Previous Month ({start_d:%b %Y})"
    elif preset == "custom":
        start_d = custom_start or today.replace(day=1)
        end_d = custom_end or today
        if start_d > end_d:
            start_d, end_d = end_d, start_d
        label = f"Custom ({start_d:%b %d, %Y} - {end_d:%b %d, %Y})"
    else:
        # Fallback to this month
        preset = "this_month"
        start_d = today.replace(day=1)
        end_d = today
        label = f"This Month ({start_d:%b %d} - {end_d:%b %d, %Y})"

    # Construct timezone-aware boundary timestamps
    start_dt = datetime.combine(start_d, time.min, tzinfo=tz)
    end_dt = datetime.combine(end_d, time.max, tzinfo=tz)

    return DateFilterRange(
        preset=preset,
        start_date=start_d,
        end_date=end_d,
        start_datetime=start_dt,
        end_datetime=end_dt,
        label=label,
    )
