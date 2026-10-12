import csv
import hashlib
from datetime import date

from django.core.cache import cache
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from . import open_meteo
from .analysis import HAZARD_META, run_analysis
from .throttling import AnalysisThrottle

MAX_SPAN_YEARS = 80
MIN_YEAR = 1940
MAX_BOX_HALF_SIZE_DEG = 2.5


class AnalysisQuerySerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False)
    lon = serializers.FloatField(min_value=-180, max_value=180, required=False)
    box_size = serializers.FloatField(min_value=0.05, max_value=MAX_BOX_HALF_SIZE_DEG, required=False)
    hazard = serializers.ChoiceField(choices=list(HAZARD_META.keys()), default="heatwave")
    start_year = serializers.IntegerField(min_value=MIN_YEAR)
    end_year = serializers.IntegerField()
    percentile = serializers.FloatField(min_value=50, max_value=99.9, default=95.0)
    baseline_start = serializers.IntegerField(min_value=MIN_YEAR, default=1981)
    baseline_end = serializers.IntegerField(default=2010)
    min_days = serializers.IntegerField(min_value=1, max_value=30, required=False)
    label = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def validate(self, data):
        current_year = date.today().year
        if "lat" not in data or "lon" not in data:
            raise ValidationError("lat and lon are required.")
        if data["end_year"] > current_year:
            raise ValidationError("end_year cannot be in the future.")
        if data["start_year"] >= data["end_year"]:
            raise ValidationError("start_year must be before end_year.")
        if data["end_year"] - data["start_year"] + 1 > MAX_SPAN_YEARS:
            raise ValidationError(f"Time range is limited to {MAX_SPAN_YEARS} years.")
        if data["baseline_start"] >= data["baseline_end"]:
            raise ValidationError("baseline_start must be before baseline_end.")
        if data["baseline_end"] > current_year:
            raise ValidationError("baseline_end cannot be in the future.")
        if data["baseline_end"] - data["baseline_start"] + 1 < 10:
            raise ValidationError("The baseline period must span at least 10 years.")
        return data


def _region_from(p):
    """Point, or a square box centred on lat/lon with half-width box_size (degrees)."""
    lat, lon = p["lat"], p["lon"]
    if "box_size" in p:
        half = p["box_size"]
        box = {
            "north": min(lat + half, 90.0),
            "south": max(lat - half, -90.0),
            "east": min(lon + half, 180.0),
            "west": max(lon - half, -180.0),
        }
        return {"box": box, "lat": lat, "lon": lon}
    return {"lat": lat, "lon": lon}


def lat_label(p):
    return f"{p['lat']:.2f}, {p['lon']:.2f}"


def _run(p):
    region = _region_from(p)
    label = p["label"] or lat_label(p)
    return run_analysis(
        region=region,
        hazard=p["hazard"],
        start_year=p["start_year"],
        end_year=p["end_year"],
        percentile=p["percentile"],
        baseline_start=p["baseline_start"],
        baseline_end=p["baseline_end"],
        min_days=p.get("min_days"),
        label=label,
    )


@api_view(["GET"])
def health(request):
    return Response({"status": "ok"})


@api_view(["GET"])
def geocode(request):
    query = (request.query_params.get("q") or "").strip()
    if not 2 <= len(query) <= 100:
        raise ValidationError({"q": "Enter between 2 and 100 characters."})

    cache_key = "geocode:" + hashlib.sha256(query.lower().encode()).hexdigest()
    results = cache.get(cache_key)
    if results is None:
        try:
            results = open_meteo.search_places(query)
        except open_meteo.ExternalAPIError as exc:
            return Response({"error": str(exc)}, status=502)
        cache.set(cache_key, results, timeout=60 * 60 * 24)
    return Response({"results": results})


@api_view(["GET"])
@throttle_classes([AnalysisThrottle, AnonRateThrottle])
def analysis(request):
    serializer = AnalysisQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    try:
        result = _run(serializer.validated_data)
    except open_meteo.ExternalAPIError as exc:
        return Response({"error": str(exc)}, status=502)

    response = Response(result)
    response["Cache-Control"] = "public, max-age=3600"
    return response


CSV_COLUMNS = [
    "year", "event_count", "event_days", "max_duration_days",
    "mean_intensity", "coverage_pct", "included",
]


@api_view(["GET"])
@throttle_classes([AnalysisThrottle, AnonRateThrottle])
def analysis_csv(request):
    """Same parameters as /api/analysis/, returned as a yearly CSV download."""
    serializer = AnalysisQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    p = serializer.validated_data
    try:
        result = _run(p)
    except open_meteo.ExternalAPIError as exc:
        return Response({"error": str(exc)}, status=502)

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    slug = f"{result['hazard']}_{p['start_year']}-{p['end_year']}.csv"
    response["Content-Disposition"] = f'attachment; filename="{slug}"'
    writer = csv.writer(response)
    writer.writerow([f"# {result['hazard_label']} · {result['location']['label']} · {result['definition']['description']}"])
    writer.writerow(CSV_COLUMNS)
    for row in result["yearly"]:
        writer.writerow([row[c] if row[c] is not None else "" for c in CSV_COLUMNS])
    return response
