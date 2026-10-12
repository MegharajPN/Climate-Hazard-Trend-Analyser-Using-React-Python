"""Hazard definitions for temperature and precipitation extremes.

Heatwave (default):
  A day is "hot" when its daily maximum temperature exceeds the 95th percentile of maxima
  for the same time of year. The threshold is computed per calendar day from a baseline
  period, pooling every baseline year's values within +/- 7 days of that day.
  A heatwave is a run of at least `min_days` (default 3) consecutive hot days.

Heavy rain:
  A day is "heavy rain" when precipitation exceeds the 95th percentile of wet days
  (>= 1 mm) in the baseline period. Each such day is an event (min_days = 1).

Drought:
  A dry day has < 1 mm precipitation. A drought spell is a run of at least `min_days`
  (default 10) consecutive dry days.

Missing days are never hot, heavy-rain or dry, so they break a run instead of bridging it.
"""
import numpy as np
import pandas as pd

from .detection import find_events

MIN_BASELINE_SAMPLES = 30
WET_DAY_MM = 1.0
DRY_DAY_MM = 1.0


def _day_of_year(index):
    # Leap days map to 366 so the seasonal lookup always has a slot for every date.
    return np.minimum(index.dayofyear.values, 366)


def _baseline(series, column, baseline_start, baseline_end):
    base = series[(series.index.year >= baseline_start) & (series.index.year <= baseline_end)]
    return base[column].dropna()


def calendar_day_thresholds(series, baseline_start, baseline_end, percentile=95.0, window_days=7):
    """Return a Series indexed by day-of-year (1..366) holding the temperature percentile.

    Days with fewer than MIN_BASELINE_SAMPLES baseline observations get NaN, meaning they
    cannot be classified as hot.
    """
    base = _baseline(series, "tmax_c", baseline_start, baseline_end)
    base_doy = _day_of_year(base.index)
    base_vals = base.values

    thresholds = np.full(366, np.nan)
    for doy in range(1, 367):
        distance = np.abs(base_doy - doy)
        distance = np.minimum(distance, 366 - distance)  # wrap around the year end
        sample = base_vals[distance <= window_days]
        if sample.size >= MIN_BASELINE_SAMPLES:
            thresholds[doy - 1] = np.percentile(sample, percentile)

    return pd.Series(thresholds, index=np.arange(1, 367), name="threshold_c")


def wet_day_threshold(series, baseline_start, baseline_end, percentile=95.0):
    """Single heavy-rain threshold (mm/day) from baseline wet days. NaN if too few samples."""
    base = _baseline(series, "precip_mm", baseline_start, baseline_end)
    wet = base[base >= WET_DAY_MM]
    if wet.size < MIN_BASELINE_SAMPLES:
        return float("nan")
    return float(np.percentile(wet.values, percentile))


def detect_heatwaves(series, thresholds, min_days=3):
    """Heatwave events: consecutive days above the calendar-day temperature threshold."""
    tmax = series["tmax_c"].values
    threshold = thresholds.reindex(_day_of_year(series.index)).values
    with np.errstate(invalid="ignore"):
        hot = tmax > threshold  # NaN comparisons are False, so gaps break runs
    return find_events(series.index, tmax, hot, reference=threshold, min_days=min_days)


def detect_heavy_rain(series, threshold_mm, min_days=1):
    precip = series["precip_mm"].values
    if np.isnan(threshold_mm):
        return []
    with np.errstate(invalid="ignore"):
        heavy = precip > threshold_mm
    return find_events(series.index, precip, heavy, reference=threshold_mm, min_days=min_days)


def detect_drought(series, min_days=10):
    precip = series["precip_mm"].values
    with np.errstate(invalid="ignore"):
        dry = precip < DRY_DAY_MM
    return find_events(series.index, precip, dry, reference=None, min_days=min_days)


def yearly_summary(series, events, years, variable="tmax_c", min_coverage_pct=80.0):
    """Aggregate events per year. Years below the coverage threshold are flagged, not dropped."""
    rows = []
    for year in years:
        year_slice = series[series.index.year == year]
        total_days = len(year_slice)
        available = int(year_slice[variable].notna().sum())
        coverage = round(100.0 * available / total_days, 1) if total_days else 0.0

        year_events = [e for e in events if int(e["start"][:4]) == year]
        margins = [e["mean_exceedance"] for e in year_events if e["mean_exceedance"] is not None]
        rows.append(
            {
                "year": year,
                "event_count": len(year_events),
                "event_days": sum(e["duration_days"] for e in year_events),
                "max_duration_days": max((e["duration_days"] for e in year_events), default=0),
                "mean_intensity": round(float(np.mean(margins)), 2) if margins else None,
                "coverage_pct": coverage,
                "included": coverage >= min_coverage_pct,
            }
        )
    return rows
