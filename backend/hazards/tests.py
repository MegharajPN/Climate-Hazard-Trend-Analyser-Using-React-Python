from datetime import date
from unittest import mock

import numpy as np
import pandas as pd
from django.test import TestCase
from rest_framework.test import APIClient

from . import heatwave, ingest, trends
from .models import DailyWeather


def _synthetic_series(start="1981-01-01", end="2020-12-31", seed=0):
    """Seasonal Tmax with noise, plus a deliberately hot late summer for the last decade."""
    idx = pd.date_range(start, end, freq="D")
    rng = np.random.default_rng(seed)
    doy = idx.dayofyear.values
    seasonal = 15 + 12 * np.sin(2 * np.pi * (doy - 110) / 365.25)
    tmax = seasonal + rng.normal(0, 2.5, len(idx))
    frame = pd.DataFrame({"tmax_c": tmax, "tmin_c": tmax - 8, "precip_mm": 0.0}, index=idx)
    return frame


class HeatwaveLogicTests(TestCase):
    def test_threshold_uses_tmax_only_not_tmin_or_precip(self):
        # Regression: pooling every column once inflated heatwave counts by ~2.6x.
        series = _synthetic_series()
        tmax_only = series.copy()
        tmax_only["tmin_c"] = np.nan
        tmax_only["precip_mm"] = np.nan
        mixed = series.copy()
        mixed["tmin_c"] = -50.0
        mixed["precip_mm"] = 500.0
        expected = heatwave.calendar_day_thresholds(tmax_only, 1981, 2010, percentile=95)
        actual = heatwave.calendar_day_thresholds(mixed, 1981, 2010, percentile=95)
        pd.testing.assert_series_equal(actual, expected)

    def test_threshold_is_percentile_of_baseline_for_calendar_day(self):
        series = _synthetic_series()
        thresholds = heatwave.calendar_day_thresholds(series, 1981, 2010, percentile=95)
        self.assertEqual(len(thresholds), 366)
        self.assertFalse(thresholds.isna().any())
        # Summer threshold must sit above winter threshold (seasonal cycle respected).
        self.assertGreater(thresholds.loc[200], thresholds.loc[20])

    def test_run_of_three_hot_days_is_a_heatwave_but_two_is_not(self):
        idx = pd.date_range("2000-07-01", periods=10, freq="D")
        series = pd.DataFrame({"tmax_c": [30.0] * 10}, index=idx)
        thresholds = pd.Series(np.full(366, 29.0), index=np.arange(1, 367))

        events = heatwave.detect_heatwaves(series, thresholds, min_days=3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["duration_days"], 10)

        series.iloc[3:5, 0] = 20.0  # cool days 3-4 split the run into 3 hot + 5 hot
        events = heatwave.detect_heatwaves(series, thresholds, min_days=3)
        self.assertEqual([e["duration_days"] for e in events], [3, 5])

        series.iloc[0:9, 0] = 20.0  # only a single hot day remains
        self.assertEqual(heatwave.detect_heatwaves(series, thresholds, min_days=3), [])

    def test_missing_day_breaks_a_run_instead_of_bridging_it(self):
        idx = pd.date_range("2000-07-01", periods=7, freq="D")
        tmax = [30, 30, np.nan, 30, 30, 30, 30]
        series = pd.DataFrame({"tmax_c": tmax}, index=idx)
        thresholds = pd.Series(np.full(366, 29.0), index=np.arange(1, 367))
        events = heatwave.detect_heatwaves(series, thresholds, min_days=3)
        self.assertEqual([e["duration_days"] for e in events], [4])

    def test_yearly_summary_flags_low_coverage_years(self):
        idx = pd.date_range("2001-01-01", "2001-12-31", freq="D")
        tmax = np.full(len(idx), 30.0)
        tmax[:200] = np.nan  # ~55% coverage
        series = pd.DataFrame({"tmax_c": tmax}, index=idx)
        rows = heatwave.yearly_summary(series, [], [2001])
        self.assertFalse(rows[0]["included"])
        self.assertLess(rows[0]["coverage_pct"], 80)


class IngestCleaningTests(TestCase):
    def test_implausible_temperatures_and_negative_rain_become_null(self):
        self.assertIsNone(ingest._clean_temp(99.0))
        self.assertIsNone(ingest._clean_temp(None))
        self.assertEqual(ingest._clean_temp(31.5), 31.5)
        self.assertIsNone(ingest._clean_precip(-1.0))

    def test_location_key_rounds_to_two_decimals(self):
        self.assertEqual(ingest.location_key(23.61234, 58.54321), "23.61,58.54")

    @mock.patch("hazards.ingest.fetch_daily_archive")
    def test_second_request_for_cached_range_does_not_refetch(self, fetch):
        fetch.return_value = [
            {"date": date(2000, 1, d), "tmax_c": 20.0, "tmin_c": 10.0, "precip_mm": 0.0}
            for d in range(1, 11)
        ]
        ingest.ensure_daily_weather(23.6, 58.5, date(2000, 1, 1), date(2000, 1, 10))
        ingest.ensure_daily_weather(23.6, 58.5, date(2000, 1, 2), date(2000, 1, 9))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(DailyWeather.objects.count(), 10)


class TrendTests(TestCase):
    def test_mann_kendall_detects_clear_increase(self):
        values = list(range(30))
        result = trends.mann_kendall(values)
        self.assertEqual(result["trend"], "increasing")
        self.assertLess(result["p_value"], 0.05)

    def test_mann_kendall_flat_series_shows_no_trend(self):
        result = trends.mann_kendall([5, 5, 5, 5, 5, 5])
        self.assertEqual(result["trend"], "no trend")

    def test_sen_slope_and_regression_agree_on_linear_data(self):
        years = list(range(1990, 2021))
        values = [2 * (y - 1990) + 1 for y in years]
        self.assertAlmostEqual(trends.sen_slope(years, values), 2.0, places=3)
        self.assertAlmostEqual(trends.linear_regression(years, values)["slope_per_year"], 2.0, places=3)

    def test_percent_change_handles_zero_baseline(self):
        self.assertIsNone(trends.percent_change([0, 0], [3, 4]))
        self.assertEqual(trends.percent_change([2, 2], [3, 3]), 50.0)


class AnalysisApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_rejects_inverted_range_and_future_years(self):
        resp = self.client.get("/api/analysis/", {"lat": 23.6, "lon": 58.5, "start_year": 2020, "end_year": 1990})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get("/api/analysis/", {"lat": 23.6, "lon": 58.5, "start_year": 1990, "end_year": 2999})
        self.assertEqual(resp.status_code, 400)

    def test_rejects_out_of_range_coordinates_and_unknown_hazard(self):
        resp = self.client.get("/api/analysis/", {"lat": 120, "lon": 58.5, "start_year": 1990, "end_year": 2020})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get("/api/analysis/", {"lat": 23, "lon": 58, "start_year": 1990, "end_year": 2020, "hazard": "flood"})
        self.assertEqual(resp.status_code, 400)

    def test_rejects_spans_over_limit(self):
        resp = self.client.get("/api/analysis/", {"lat": 23, "lon": 58, "start_year": 1900, "end_year": 2020})
        self.assertEqual(resp.status_code, 400)

    @mock.patch("hazards.analysis.ensure_daily_weather")
    def test_full_analysis_returns_summary_and_stores_result(self, _ensure):
        # Pre-load synthetic data straight into MySQL so the analysis runs offline.
        frame = _synthetic_series(start="1981-01-01", end="2020-12-31")
        DailyWeather.objects.bulk_create(
            [
                DailyWeather(location_key="23.60,58.50", date=d.date(), tmax_c=float(v), tmin_c=None, precip_mm=None)
                for d, v in frame["tmax_c"].items()
            ],
            batch_size=2000,
        )

        resp = self.client.get(
            "/api/analysis/",
            {"lat": 23.6, "lon": 58.5, "start_year": 1990, "end_year": 2020, "label": "Muscat"},
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["hazard"], "heatwave")
        self.assertEqual(len(body["plot"]["years"]), 31)
        self.assertIn("insights", body)
        self.assertFalse(body["cached"])

        # Second call is served from MySQL.
        resp2 = self.client.get(
            "/api/analysis/",
            {"lat": 23.6, "lon": 58.5, "start_year": 1990, "end_year": 2020, "label": "Muscat"},
        )
        self.assertTrue(resp2.json()["cached"])
        _ensure.assert_called_once()  # no second ingest call


class NewHazardTests(TestCase):
    def test_heavy_rain_counts_days_above_wet_day_threshold(self):
        idx = pd.date_range("2000-01-01", periods=6, freq="D")
        series = pd.DataFrame({"precip_mm": [0.0, 5.0, 40.0, 2.0, 60.0, np.nan]}, index=idx)
        events = heatwave.detect_heavy_rain(series, threshold_mm=30.0, min_days=1)
        self.assertEqual([e["start"] for e in events], ["2000-01-03", "2000-01-05"])

    def test_drought_spell_needs_min_consecutive_dry_days(self):
        idx = pd.date_range("2000-06-01", periods=15, freq="D")
        precip = [0.0] * 12 + [5.0] + [0.0] * 2
        series = pd.DataFrame({"precip_mm": precip}, index=idx)
        events = heatwave.detect_drought(series, min_days=10)
        self.assertEqual([e["duration_days"] for e in events], [12])

    def test_box_points_form_a_three_by_three_grid(self):
        from .analysis import box_points

        points = box_points({"north": 24.0, "south": 23.0, "east": 59.0, "west": 58.0})
        self.assertEqual(len(points), 9)
        self.assertIn((23.5, 58.5), points)


class ExportAndLimitTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @mock.patch("hazards.analysis.ensure_daily_weather")
    def test_csv_export_returns_yearly_rows(self, _ensure):
        frame = _synthetic_series(start="1981-01-01", end="2020-12-31")
        DailyWeather.objects.bulk_create(
            [
                DailyWeather(location_key="23.60,58.50", date=d.date(), tmax_c=float(v), tmin_c=None, precip_mm=None)
                for d, v in frame["tmax_c"].items()
            ],
            batch_size=2000,
        )
        resp = self.client.get(
            "/api/analysis/export/",
            {"lat": 23.6, "lon": 58.5, "start_year": 2000, "end_year": 2002, "label": "Muscat"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        lines = resp.content.decode().strip().splitlines()
        self.assertEqual(lines[1].split(",")[0], "year")
        self.assertEqual(len(lines), 2 + 3)

    def test_analysis_rate_limit_returns_429_after_quota(self):
        from django.core.cache import cache
        from .throttling import AnalysisThrottle

        cache.clear()
        params = {"lat": 10, "lon": 10, "start_year": 1990, "end_year": 1992}
        rates = {"analysis": "2/hour"}
        with mock.patch.dict(AnalysisThrottle.THROTTLE_RATES, rates), mock.patch(
            "hazards.views.run_analysis", return_value={"cached": True}
        ):
            codes = [self.client.get("/api/analysis/", params).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
