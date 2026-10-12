from django.db import models


class DailyWeather(models.Model):
    """One cleaned day of observations for a grid location (the raw data cache)."""

    location_key = models.CharField(max_length=32)
    date = models.DateField()
    tmax_c = models.FloatField(null=True, blank=True)
    tmin_c = models.FloatField(null=True, blank=True)
    precip_mm = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["location_key", "date"], name="uniq_location_date"),
        ]
        indexes = [models.Index(fields=["location_key", "date"])]


class FetchedRange(models.Model):
    """Records which date ranges were already pulled from Open-Meteo, so we never re-fetch them."""

    location_key = models.CharField(max_length=32)
    start_date = models.DateField()
    end_date = models.DateField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["location_key", "start_date", "end_date"])]


class AnalysisResult(models.Model):
    """Document store for computed hazard analyses, keyed by a hash of the request parameters."""

    cache_key = models.CharField(max_length=64, unique=True)
    location_key = models.CharField(max_length=32)
    hazard = models.CharField(max_length=32)
    params = models.JSONField()
    result = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)
