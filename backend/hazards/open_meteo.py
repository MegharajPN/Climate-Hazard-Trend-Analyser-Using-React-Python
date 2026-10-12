"""Thin client for the Open-Meteo archive (historical) and geocoding APIs.

Both endpoints are free and need no API key. Failures are raised as
ExternalAPIError so views can return a clean 502 instead of a stack trace.
"""
from datetime import date

import time

import requests
from django.conf import settings


class ExternalAPIError(Exception):
    pass


DAILY_VARIABLES = "temperature_2m_max,temperature_2m_min,precipitation_sum"


RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 10


def _get_json(url, params):
    """GET with retries on rate limits (429) and transient server errors, with backoff."""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        delay = 2 ** attempt  # 2 s, then 4 s
        try:
            response = requests.get(url, params=params, timeout=settings.OPEN_METEO_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = exc
        else:
            if response.status_code not in RETRYABLE_STATUS:
                try:
                    response.raise_for_status()
                    return response.json()
                except (requests.RequestException, ValueError) as exc:
                    raise ExternalAPIError(f"Open-Meteo request failed: {exc}") from exc
            last_error = f"HTTP {response.status_code}"
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = int(retry_after)
        if attempt < MAX_ATTEMPTS:
            time.sleep(min(delay, MAX_BACKOFF_SECONDS))
    raise ExternalAPIError(f"Open-Meteo request failed after {MAX_ATTEMPTS} attempts: {last_error}")


def fetch_daily_archive(lat, lon, start, end):
    """Return a list of dicts with date, tmax_c, tmin_c, precip_mm for the inclusive date range.

    Null values from the API are passed through as None so the caller can treat them as gaps.
    """
    payload = _get_json(
        settings.OPEN_METEO_ARCHIVE_URL,
        {
            "latitude": lat,
            "longitude": lon,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": DAILY_VARIABLES,
            "timezone": "GMT",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        },
    )
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_sum") or []

    rows = []
    for i, day in enumerate(times):
        rows.append(
            {
                "date": date.fromisoformat(day),
                "tmax_c": tmax[i] if i < len(tmax) else None,
                "tmin_c": tmin[i] if i < len(tmin) else None,
                "precip_mm": precip[i] if i < len(precip) else None,
            }
        )
    return rows


def search_places(name, count=5):
    payload = _get_json(
        settings.OPEN_METEO_GEOCODING_URL,
        {"name": name, "count": count, "language": "en", "format": "json"},
    )
    results = payload.get("results") or []
    return [
        {
            "name": item.get("name"),
            "admin1": item.get("admin1"),
            "country": item.get("country"),
            "latitude": item.get("latitude"),
            "longitude": item.get("longitude"),
        }
        for item in results
    ]
