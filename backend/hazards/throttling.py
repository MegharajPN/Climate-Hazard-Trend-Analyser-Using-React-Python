from rest_framework.throttling import AnonRateThrottle


class AnalysisThrottle(AnonRateThrottle):
    """Stricter limit for the expensive analysis endpoint (rate set via RATE_LIMIT_ANALYSIS)."""

    scope = "analysis"
