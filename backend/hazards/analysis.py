"""Orchestrates one hazard analysis: cache check -> ingest -> detect -> trends -> store.

Read path: if a fresh result for the same parameters is already stored in MySQL, it is
returned directly. Otherwise the daily data is loaded (fetching only uncached days),
the hazard is computed, and the result document is saved for next time.

Regions:
  * point: one grid location.
  * box:   a 3x3 grid of points inside the box; the daily series is the mean over the grid.
"""
import hashlib
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
from django.utils import timezone

from . import heatwave, trends
from .ingest import clamp_end_to_available, ensure_daily_weather, load_daily_series, location_key
from .models import AnalysisResult

RESULT_TTL = timedelta(hours=24)
ALGORITHM_VERSION = "hazards-v2"
TREND_WINDOW_YEARS = 5
BOX_GRID_SIZE = 3

HAZARD_META = {
    "heatwave": {
        "label": "Heatwave",
        "noun": "Heatwave events",
        "variable": "tmax_c",
        "units": "°C",
        "intensity": "Mean exceedance above threshold (°C)",
        "min_days": 3,
    },
    "heavy_rain": {
        "label": "Heavy rainfall",
        "noun": "Heavy-rain days",
        "variable": "precip_mm",
        "units": "mm",
        "intensity": "Mean rainfall above threshold (mm)",
        "min_days": 1,
    },
    "drought": {
        "label": "Drought",
        "noun": "Drought spells",
        "variable": "precip_mm",
        "units": "days",
        "intensity": None,
        "min_days": 10,
    },
}


def _cache_key(params):
    canonical = json.dumps(params, sort_keys=True)
    return hashlib.sha256(f"{ALGORITHM_VERSION}|{canonical}".encode()).hexdigest()


def _jsonable(value):
    """Convert numpy scalars and NaN into plain JSON-safe values."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value


def box_points(box):
    lats = np.linspace(box["south"], box["north"], BOX_GRID_SIZE)
    lons = np.linspace(box["west"], box["east"], BOX_GRID_SIZE)
    return [(round(float(a), 2), round(float(b), 2)) for a in lats for b in lons]


def region_key(region):
    if region.get("box"):
        b = region["box"]
        return f"box:{b['south']:.2f},{b['west']:.2f},{b['north']:.2f},{b['east']:.2f}"
    return location_key(region["lat"], region["lon"])


def _region_series(region, fetch_start, fetch_end):
    """Cached daily series for a point, or the mean over a box's grid points."""
    points = box_points(region["box"]) if region.get("box") else [(region["lat"], region["lon"])]
    frames = []
    for lat, lon in points:
        ensure_daily_weather(lat, lon, fetch_start, fetch_end)
        frames.append(load_daily_series(location_key(lat, lon), fetch_start, fetch_end))
    if len(frames) == 1:
        return frames[0]
    # Mean across grid points per day; a point with a missing day does not drag the mean to NaN.
    return pd.concat(frames).groupby(level=0).mean()


def _insights(label, noun, included, trend_block, pct, params, meta, data_quality, thresholds_note):
    lines = []
    first_years = included[:TREND_WINDOW_YEARS]
    last_years = included[-TREND_WINDOW_YEARS:]
    first_span = f"{first_years[0]['year']}–{first_years[-1]['year']}" if first_years else ""
    last_span = f"{last_years[0]['year']}–{last_years[-1]['year']}" if last_years else ""
    first_avg = float(np.mean([y["event_count"] for y in first_years])) if first_years else 0.0
    last_avg = float(np.mean([y["event_count"] for y in last_years])) if last_years else 0.0

    if pct is None:
        lines.append(
            f"The percentage change in {noun.lower()} for {label} could not be computed because "
            "the early-period average is zero or there is no valid data."
        )
    elif pct == 0:
        lines.append(
            f"{noun} in {label} are unchanged between {first_span} and {last_span} "
            f"(about {first_avg:.1f} per year)."
        )
    else:
        direction = "increased" if pct > 0 else "decreased"
        lines.append(
            f"{noun} have {direction} by {abs(pct):.0f}% in {label} since {params['start_year']}: "
            f"about {first_avg:.1f} per year in {first_span} vs {last_avg:.1f} per year in {last_span}."
        )

    slope_dec = trend_block["slope_per_decade"]
    if slope_dec is not None:
        mk = trend_block["mann_kendall"]
        if mk["p_value"] is None:
            significance = "too few years for a significance test"
        elif mk["trend"] in ("increasing", "decreasing"):
            significance = f"statistically significant (Mann–Kendall p = {mk['p_value']:.3f})"
        else:
            significance = f"not statistically significant (Mann–Kendall p = {mk['p_value']:.3f})"
        lines.append(
            f"The linear trend is {slope_dec:+.2f} per decade (R² = {trend_block['r_squared']:.2f}), {significance}."
        )

    if data_quality["years_excluded"]:
        lines.append(
            f"{len(data_quality['years_excluded'])} year(s) with less than "
            f"{data_quality['min_coverage_pct']:.0f}% data coverage were excluded from trends."
        )
    if thresholds_note:
        lines.append(thresholds_note)
    lines.append(
        "Year-to-year counts are small and noisy. Read single-year spikes with care and focus on "
        "multi-year trends."
    )
    return lines


def _threshold_note(hazard, params, threshold_info):
    if hazard == "heatwave":
        return (
            f"Hot days are defined against the {params['baseline_start']}–{params['baseline_end']} "
            f"{params['percentile']:g}th percentile for each time of year. Baseline years are included "
            "in the analysis, so their exceedance rate is close to, but not exactly, "
            f"{100 - params['percentile']:g}% by design."
        )
    if hazard == "heavy_rain":
        value = threshold_info.get("value_mm")
        if value is None:
            return "Too few wet days in the baseline to set a heavy-rain threshold."
        return (
            f"Heavy rain means a day above {value:.1f} mm, the {params['percentile']:g}th percentile of "
            f"wet days (≥ 1 mm) in {params['baseline_start']}–{params['baseline_end']}."
        )
    return "A dry day has less than 1 mm of precipitation. Spells are runs of consecutive dry days."


def run_analysis(region, hazard, start_year, end_year, percentile, baseline_start, baseline_end,
                 min_days, label, today=None):
    if hazard not in HAZARD_META:
        raise ValueError(f"Unknown hazard: {hazard}")
    meta = HAZARD_META[hazard]
    min_days = min_days or meta["min_days"]
    today = today or date.today()

    params = {
        "location_key": region_key(region),
        "hazard": hazard,
        "start_year": start_year,
        "end_year": end_year,
        "percentile": percentile,
        "baseline_start": baseline_start,
        "baseline_end": baseline_end,
        "min_days": min_days,
    }
    cache_key = _cache_key(params)

    stored = AnalysisResult.objects.filter(cache_key=cache_key).first()
    if stored and timezone.now() - stored.updated_at < RESULT_TTL:
        result = dict(stored.result)
        result["cached"] = True
        return result

    # 1) Make sure the needed days are in MySQL (only missing ranges are fetched).
    fetch_start = date(min(start_year, baseline_start), 1, 1)
    fetch_end = clamp_end_to_available(date(max(end_year, baseline_end), 12, 31), today)
    series = _region_series(region, fetch_start, fetch_end)

    # 2) Hazard definition and event detection.
    analysis_series = series[(series.index.year >= start_year) & (series.index.year <= end_year)]
    years = list(range(start_year, end_year + 1))
    threshold_info = {}
    if hazard == "heatwave":
        thresholds = heatwave.calendar_day_thresholds(
            series, baseline_start, baseline_end, percentile=percentile, window_days=7
        )
        events = heatwave.detect_heatwaves(analysis_series, thresholds, min_days=min_days)
        threshold_info = {"valid_days": int(thresholds.notna().sum())}
    elif hazard == "heavy_rain":
        value = heatwave.wet_day_threshold(series, baseline_start, baseline_end, percentile=percentile)
        events = heatwave.detect_heavy_rain(analysis_series, value, min_days=min_days)
        threshold_info = {"value_mm": None if np.isnan(value) else round(value, 2)}
    else:
        events = heatwave.detect_drought(analysis_series, min_days=min_days)

    yearly = heatwave.yearly_summary(analysis_series, events, years, variable=meta["variable"])

    # 3) Trend statistics over valid years only.
    included = [row for row in yearly if row["included"]]
    excluded_years = [row["year"] for row in yearly if not row["included"]]
    counts = [row["event_count"] for row in included]
    valid_years = [row["year"] for row in included]

    ols = trends.linear_regression(valid_years, counts)
    mk = trends.mann_kendall(counts)
    sen = trends.sen_slope(valid_years, counts)
    trend_block = {
        "slope_per_year": ols["slope_per_year"],
        "slope_per_decade": round(ols["slope_per_year"] * 10, 3) if ols["slope_per_year"] is not None else None,
        "intercept": ols["intercept"],
        "r_squared": ols["r_squared"],
        "mann_kendall": {**mk, "sen_slope_per_decade": round(sen * 10, 3) if sen is not None else None},
    }
    pct = trends.percent_change(
        [row["event_count"] for row in included[:TREND_WINDOW_YEARS]],
        [row["event_count"] for row in included[-TREND_WINDOW_YEARS:]],
    )

    total_days = len(analysis_series)
    available_days = int(analysis_series[meta["variable"]].notna().sum())
    data_quality = {
        "days_expected": total_days,
        "days_available": available_days,
        "coverage_pct": round(100.0 * available_days / total_days, 1) if total_days else 0.0,
        "years_excluded": excluded_years,
        "min_coverage_pct": 80.0,
        **threshold_info,
    }

    label = label or params["location_key"]
    note = _threshold_note(hazard, params, threshold_info)
    result = {
        "location": _location_block(region, label),
        "hazard": hazard,
        "hazard_label": meta["label"],
        "units": {"variable": meta["units"], "intensity": meta["intensity"]},
        "definition": {
            "description": _definition_text(hazard, min_days, percentile),
            "baseline": f"{baseline_start}–{baseline_end}" if hazard != "drought" else "fixed rule",
            "window": "±7 calendar days pooled per threshold" if hazard == "heatwave" else None,
        },
        "parameters": params,
        "data_quality": data_quality,
        "yearly": _yearly_rows(yearly),
        "plot": {
            "years": valid_years,
            "event_count": counts,
            "rolling_mean_5yr": trends.rolling_mean(counts, window=TREND_WINDOW_YEARS),
            "event_days": [row["event_days"] for row in included],
            "mean_intensity": [row["mean_intensity"] for row in included],
        },
        "trend": trend_block,
        "percent_change_first_last_5yr": pct,
        "events_total": len(events),
        "insights": _insights(label, meta["noun"], included, trend_block, pct, params, meta, data_quality, note),
        "cached": False,
        "computed_at": timezone.now().isoformat(),
    }
    result = _jsonable(result)

    AnalysisResult.objects.update_or_create(
        cache_key=cache_key,
        defaults={
            "location_key": params["location_key"],
            "hazard": hazard,
            "params": params,
            "result": result,
        },
    )
    return result


def _location_block(region, label):
    if region.get("box"):
        b = region["box"]
        return {"label": label, "box": {k: round(float(v), 2) for k, v in b.items()}, "points": BOX_GRID_SIZE ** 2}
    return {"label": label, "lat": round(region["lat"], 2), "lon": round(region["lon"], 2)}


def _definition_text(hazard, min_days, percentile):
    if hazard == "heatwave":
        return (
            f"Run of at least {min_days} consecutive days with daily max temperature above the "
            f"{percentile:g}th percentile for the same time of year."
        )
    if hazard == "heavy_rain":
        return f"Days with precipitation above the {percentile:g}th percentile of wet days in the baseline."
    return f"Spells of at least {min_days} consecutive dry days (precipitation below 1 mm)."


def _yearly_rows(yearly):
    return [
        {
            "year": row["year"],
            "event_count": row["event_count"],
            "event_days": row["event_days"],
            "max_duration_days": row["max_duration_days"],
            "mean_intensity": row["mean_intensity"],
            "coverage_pct": row["coverage_pct"],
            "included": row["included"],
        }
        for row in yearly
    ]
