"""Tool 1 -- departure/return date pairs. Adapter over core.departure_window.

Deliberately thin. All arithmetic lives in core.py where it has no
dependencies and can be tested without constructing a TripState.
"""
from __future__ import annotations

from models import TripState


def build_dates(state: TripState) -> dict:
    pairs = state.date_pairs()
    return {
        "holiday_start": state.window.holiday_start.isoformat(),
        "holiday_end": state.window.holiday_end.isoformat(),
        "duration_days": state.window.duration_days,
        "duration_mode": state.window.duration_mode,
        "first_departure": pairs[0].depart.isoformat() if pairs else None,
        "last_departure": pairs[-1].depart.isoformat() if pairs else None,
        "excluded": sorted(d.isoformat() for d in state.window.excluded_departures),
        "count": len(pairs),
        "pairs": [p.as_dict() for p in pairs],
    }
