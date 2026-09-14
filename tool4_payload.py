"""Tool 4 -- the final search payload. Dates x routes.

Emits BOTH shapes:
  - "collapsed": one request using comma-separated fly_from/fly_to and a date
    range. This is what you actually send.
  - "expanded": one request per date pair per route. Keep it for debugging,
    offline replay, and for any provider that will not take a range.

Printing both makes the fan-out cost visible at build time instead of at
rate-limit time.
"""
from __future__ import annotations

from core import collapsed_search_requests, search_requests
from models import TripState


def build_payload(state: TripState) -> dict:
    pairs = state.date_pairs()
    routes = state.routes()
    expanded = search_requests(pairs, routes)
    collapsed = collapsed_search_requests(
        pairs,
        state.departure.codes,
        state.destination.codes,
        state.window.duration_days,
        state.window.duration_mode,
    )
    return {
        "trip": {
            "from": state.departure.country,
            "to": state.destination.country,
            "duration_days": state.window.duration_days,
            "duration_mode": state.window.duration_mode,
        },
        "counts": {
            "date_pairs": len(pairs),
            "routes": len(routes),
            "expanded_requests": len(expanded),
            "collapsed_requests": len(collapsed),
        },
        "collapsed": collapsed,
        "expanded": expanded,
    }
