"""Construcción del mes sin acceso a datos ni dependencias del template."""
from calendar import Calendar
from collections import defaultdict
from datetime import date, datetime, time

from django.utils import timezone

from .holidays import holiday_for

MIN_YEAR = 1900
MAX_YEAR = 2100


def selected_month(params, *, today=None):
    today = today or timezone.localdate()
    try:
        year = int(params.get("year", today.year))
        month = int(params.get("month", today.month))
        if not MIN_YEAR <= year <= MAX_YEAR or not 1 <= month <= 12:
            raise ValueError
        return date(year, month, 1)
    except (ValueError, TypeError, OverflowError):
        return today.replace(day=1)


def shift_month(first, offset):
    year, month = divmod(first.year * 12 + first.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def month_bounds(first):
    tz = timezone.get_current_timezone()
    return tuple(timezone.make_aware(datetime.combine(day, time.min), tz)
                 for day in (first, shift_month(first, 1)))


def build_month(first, events, *, today=None, selected=None):
    today = today or timezone.localdate()
    by_day = defaultdict(list)
    for event in events:
        day = event["starts_at"].date()
        if (day.year, day.month) == (first.year, first.month):
            by_day[day].append(event)
    weeks = []
    for dates in Calendar(firstweekday=0).monthdatescalendar(first.year, first.month):
        weeks.append([{
            "date": day, "in_month": day.month == first.month,
            "is_today": day == today,
            "is_weekend": day.weekday() >= 5,
            "holiday": holiday_for(day),
            "events": sorted(by_day[day], key=lambda event: (event["starts_at"], event["id"])),
        } for day in dates])
    for week in weeks:
        for day in week:
            day['is_selected'] = day['date'] == selected
            day['css_classes'] = ' '.join(name for name, active in (
                ('is-weekend', day['is_weekend']), ('is-holiday', bool(day['holiday'])),
                ('is-outside', not day['in_month']), ('is-today', day['is_today']),
                ('is-selected', day['is_selected']),
            ) if active)
    previous, following = shift_month(first, -1), shift_month(first, 1)
    return {
        "month_date": first, "weeks": weeks,
        "has_holidays": any(day["holiday"] for week in weeks for day in week if day["in_month"]),
        "previous_month": previous if previous.year >= MIN_YEAR else None,
        "next_month": following if following.year <= MAX_YEAR else None,
        "event_count": sum(len(events) for events in by_day.values()),
        "weekdays": ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"),
    }
