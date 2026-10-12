"""Fetch, clean and cache daily weather data in MySQL.

Cleaning rules (documented in the README):
  * Temperatures outside [-60, 60] C are treated as sensor/API errors and set to NULL.
  * Negative precipitation is set to NULL.
  * Missing days (nulls or dates absent from the API response) stay NULL; they are never
    filled, so they can neither create nor extend a heatwave run.
"""
from datetime import date

import pandas as pd
from django.db import transaction

from .models import DailyWeather, FetchedRange
from .open_meteo import fetch_daily_archive

PLAUSIBLE_TEMP_C = (-60.0, 60.0)


def location_key(lat, lon):
    return f"{round(lat, 2):.2f},{round(lon, 2):.2f}"


def _clean_temp(value):
    if value is None:
        return None
    value = float(value)
    low, high = PLAUSIBLE_TEMP_C
    return value if low <= value <= high else None


def _clean_precip(value):
    if value is None:
        return None
    value = float(value)
    return value if value >= 0 else None


def is_covered(key, start, end):
    return FetchedRange.objects.filter(
        location_key=key, start_date__lte=start, end_date__gte=end
    ).exists()


def ensure_daily_weather(lat, lon, start, end):
    """Make sure every day in [start, end] is in the DB, fetching only when not already cached."""
    key = location_key(lat, lon)
    if is_covered(key, start, end):
        return key

    rows = fetch_daily_archive(round(lat, 2), round(lon, 2), start, end)
    objects = [
        DailyWeather(
            location_key=key,
            date=row["date"],
            tmax_c=_clean_temp(row["tmax_c"]),
            tmin_c=_clean_temp(row["tmin_c"]),
            precip_mm=_clean_precip(row["precip_mm"]),
        )
        for row in rows
    ]
    with transaction.atomic():
        if objects:
            DailyWeather.objects.bulk_create(
                objects,
                batch_size=1000,
                update_conflicts=True,  # MySQL: ON DUPLICATE KEY UPDATE on the unique constraint
                update_fields=["tmax_c", "tmin_c", "precip_mm"],
            )
        FetchedRange.objects.create(location_key=key, start_date=start, end_date=end)
    return key


def load_daily_series(key, start, end):
    """Return a DataFrame indexed by every calendar day in [start, end] (gaps as NaN)."""
    index = pd.date_range(start=start, end=end, freq="D")
    records = DailyWeather.objects.filter(
        location_key=key, date__gte=start, date__lte=end
    ).values("date", "tmax_c", "tmin_c", "precip_mm")

    frame = pd.DataFrame.from_records(records, columns=["date", "tmax_c", "tmin_c", "precip_mm"])
    if frame.empty:
        return pd.DataFrame(index=index, columns=["tmax_c", "tmin_c", "precip_mm"], dtype=float)

    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").reindex(index)
    frame.index.name = "date"
    return frame.astype(float)


def clamp_end_to_available(end: date, today: date) -> date:
    """Open-Meteo archive lags a few days behind real time, so never ask for future dates."""
    latest = date.fromordinal(today.toordinal() - 2)
    return min(end, latest)
