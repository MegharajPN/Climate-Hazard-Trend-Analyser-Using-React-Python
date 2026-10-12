"""Generic event detection shared by all hazards.

A hazard supplies per-day boolean flags. A run of consecutive True flags of at least
`min_days` becomes one event. Missing days are always False, so they break runs.
"""
from itertools import groupby

import numpy as np


def find_events(index, values, flags, reference=None, min_days=1):
    """Return events as dicts.

    values:    daily values (e.g. Tmax or precipitation), NaN where missing
    flags:     boolean array, True on days that belong to the hazard
    reference: per-day array or scalar threshold; intensity = values - reference (None if unused)
    """
    values = np.asarray(values, dtype=float)
    flags = np.asarray(flags, dtype=bool)
    if reference is not None and np.ndim(reference) == 0:
        reference = np.full(values.shape, float(reference))

    events = []
    for is_on, group in groupby(enumerate(flags), key=lambda pair: pair[1]):
        if not is_on:
            continue
        positions = [pos for pos, _ in group]
        if len(positions) < min_days:
            continue
        seg = slice(positions[0], positions[-1] + 1)
        dates = index[seg]
        event = {
            "start": dates[0].date().isoformat(),
            "end": dates[-1].date().isoformat(),
            "duration_days": len(positions),
            "peak_value": round(float(np.nanmax(values[seg])), 2),
            "mean_exceedance": None,
        }
        if reference is not None:
            margin = values[seg] - reference[seg]
            event["mean_exceedance"] = round(float(np.nanmean(margin)), 2)
        events.append(event)
    return events
