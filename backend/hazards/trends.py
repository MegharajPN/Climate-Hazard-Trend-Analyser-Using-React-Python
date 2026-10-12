"""Trend statistics for yearly hazard counts: OLS slope, Mann-Kendall test, Sen's slope."""
import math

import numpy as np
import pandas as pd


def _normal_sf(z):
    """Survival function of the standard normal, P(Z > z)."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def linear_regression(years, values):
    x = np.asarray(years, dtype=float)
    y = np.asarray(values, dtype=float)
    if x.size < 3:
        return {"slope_per_year": None, "intercept": None, "r_squared": None}

    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        "slope_per_year": round(float(slope), 4),
        "intercept": round(float(intercept), 4),
        "r_squared": round(float(r_squared), 3),
    }


def mann_kendall(values):
    """Two-sided Mann-Kendall trend test with tie correction (normal approximation)."""
    y = np.asarray(values, dtype=float)
    n = y.size
    if n < 4:
        return {"tau": None, "p_value": None, "trend": "insufficient data", "S": None}

    diffs = np.sign(y[None, :] - y[:, None])
    s = float(np.triu(diffs, k=1).sum())

    _, counts = np.unique(y, return_counts=True)
    tie_term = float(np.sum(counts * (counts - 1) * (2 * counts + 5)))
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var_s <= 0:
        return {"tau": 0.0, "p_value": 1.0, "trend": "no trend", "S": s}

    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0

    p_value = min(1.0, 2 * _normal_sf(abs(z)))
    tau = s / (0.5 * n * (n - 1))
    trend = "no trend"
    if p_value < 0.05:
        trend = "increasing" if s > 0 else "decreasing"
    return {"tau": round(tau, 3), "p_value": round(p_value, 4), "trend": trend, "S": s}


def sen_slope(years, values):
    x = np.asarray(years, dtype=float)
    y = np.asarray(values, dtype=float)
    if x.size < 3:
        return None
    i, j = np.triu_indices(x.size, k=1)
    slopes = (y[j] - y[i]) / (x[j] - x[i])
    return round(float(np.median(slopes)), 4)


def rolling_mean(values, window=5):
    series = pd.Series(values, dtype=float)
    return series.rolling(window=window, center=True, min_periods=3).mean().round(2).tolist()


def percent_change(first_values, last_values):
    """Change between the mean of the first and last valid windows, in percent."""
    if not first_values or not last_values:
        return None
    first = float(np.mean(first_values))
    last = float(np.mean(last_values))
    if first == 0:
        return None
    return round(100.0 * (last - first) / first, 1)
