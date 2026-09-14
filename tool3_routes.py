"""Tool 3 -- the Cartesian product of city lists.

Always recomputed from the CURRENT active lists. Never patched incrementally.
Remove a city and this drops from 9 routes to 6 automatically, with no
bookkeeping anywhere else.
"""
from __future__ import annotations

from models import TripState


def build_routes(state: TripState) -> dict:
    routes = state.routes()
    dep, dest = state.departure.active, state.destination.active
    names = {a.iata: a.city for a in dep + dest}
    return {
        "mode": "open_jaw" if state.open_jaw else "round_trip",
        "departure_cities": [a.label() for a in dep],
        "destination_cities": [a.label() for a in dest],
        "count": len(routes),
        "routes": [
            {
                "id": i,
                "label": f"{names.get(r['out_from'], r['out_from'])} -> "
                         f"{names.get(r['out_to'], r['out_to'])}",
                **r,
            }
            for i, r in enumerate(routes, start=1)
        ],
    }
