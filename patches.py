"""The ONLY interface between the LLM and the state file.

The model emits one small object per turn. Six verbs, nothing else:

    {"action": "set_route",      "departure_country": "UAE",
                                 "destination_country": "Italy"}
    {"action": "set_window",     "holiday_start": "2026-03-03",
                                 "holiday_end": "2026-03-31",
                                 "duration_days": 7}
    {"action": "remove_city",    "side": "destination", "value": "Venice"}
    {"action": "add_city",       "side": "departure",   "value": "Ras Al Khaimah"}
    {"action": "exclude_dates",  "values": ["2026-03-05", "2026-03-06"]}
    {"action": "confirm"}

Notice what is NOT in that list: no computed dates, no IATA codes, no route
lists, no payloads. The model works in human words; Python resolves them.

Every apply() returns (new_state, message). If the model says something that
cannot be resolved -- "remove Naples" when Naples was never offered -- the
patch FAILS LOUDLY and the harness asks the customer again. A failed patch
leaves state untouched, so there is no half-applied corruption to recover
from.
"""

from __future__ import annotations

from datetime import date

from models import Airport, Step, TripState


class PatchError(ValueError):
    """Raised when a patch cannot be applied. State is left unchanged."""


def _side(state: TripState, name: str):
    if name not in ("departure", "destination"):
        raise PatchError(f"unknown side {name!r}; expected departure/destination")
    return getattr(state, name)


def _as_date(raw) -> date:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise PatchError(f"{raw!r} is not an ISO date (YYYY-MM-DD)") from exc


# --------------------------------------------------------------------------

def set_route(state: TripState, departure_country: str, destination_country: str) -> str:
    from tool2_airports import get_top_airports

    state.departure.country = departure_country
    state.destination.country = destination_country
    state.departure.pool = get_top_airports(departure_country, count=3)
    state.destination.pool = get_top_airports(destination_country, count=3)

    if not state.departure.pool:
        raise PatchError(f"No airports on file for {departure_country!r}")
    if not state.destination.pool:
        raise PatchError(f"No airports on file for {destination_country!r}")

    state.step = Step.REVIEW_CITIES
    state.log("set_route", f"{departure_country} -> {destination_country}")
    return (f"{departure_country} -> {destination_country}, "
            f"{len(state.departure.pool)} x {len(state.destination.pool)} cities")


def remove_city(state: TripState, side: str, value: str) -> str:
    s = _side(state, side)
    airport = s.find(value)
    if airport is None:
        offered = ", ".join(a.city for a in s.active)
        raise PatchError(f"{value!r} is not on the {side} list. Currently: {offered}")
    if len(s.active) <= 1:
        raise PatchError(
            f"{airport.city} is the only {side} city left. "
            f"Add another before removing this one."
        )
    s.excluded.add(airport.iata)
    state.log("remove_city", f"{side}: -{airport.label()}")
    return f"Dropped {airport.label()}. {len(s.active)} {side} cities left."


def restore_city(state: TripState, side: str, value: str) -> str:
    """The undo half of remove_city. Costs one line because we never deleted."""
    s = _side(state, side)
    airport = s.find(value)
    if airport is None:
        raise PatchError(f"{value!r} was never on the {side} list")
    s.excluded.discard(airport.iata)
    state.log("restore_city", f"{side}: +{airport.label()}")
    return f"{airport.label()} is back on the list."


def add_city(state: TripState, side: str, value: str, offer_next: bool = True) -> str:
    """Add a city the customer named, or -- if they just said 'find another' --
    pull the next-ranked airport from the RAG file.
    """
    from tool2_airports import get_top_airports, lookup_city

    s = _side(state, side)

    if value.strip().casefold() in {"another", "next", "one more", ""}:
        already = {a.iata for a in s.pool}
        more = get_top_airports(s.country, count=1, exclude=already)
        if not more:
            raise PatchError(
                f"No more airports on file for {s.country} -- "
                f"all {len(already)} are already on the list."
            )
        airport = more[0]
    else:
        existing = s.find(value)
        if existing is not None:
            return restore_city(state, side, value)
        found = lookup_city(value, country=s.country)
        if found is None:
            raise PatchError(f"I could not find an airport for {value!r}")
        airport = found.model_copy(update={"source": "customer"})

    s.pool.append(airport)
    s.excluded.discard(airport.iata)
    state.log("add_city", f"{side}: +{airport.label()}")
    return f"Added {airport.label()}. {len(s.active)} {side} cities now."


def set_window(state: TripState, holiday_start, holiday_end,
               duration_days: int, duration_mode: str = "nights") -> str:
    from core import departure_window

    start, end = _as_date(holiday_start), _as_date(holiday_end)
    if end < start:
        raise PatchError("The end of the holiday is before the start.")

    try:  # validate before committing -- never leave an unusable window on disk
        pairs = departure_window(start, end, duration_days, duration_mode)
    except ValueError as exc:
        raise PatchError(str(exc)) from exc

    state.window.holiday_start = start
    state.window.holiday_end = end
    state.window.duration_days = duration_days
    state.window.duration_mode = duration_mode
    state.window.excluded_departures.clear()
    state.step = Step.REVIEW_DATES
    state.log("set_window", f"{start}..{end}, {duration_days} {duration_mode}")
    return (f"Departures from {pairs[0].depart} to {pairs[-1].depart} "
            f"({len(pairs)} options).")


def exclude_dates(state: TripState, values) -> str:
    if not state.window.complete:
        raise PatchError("No holiday window set yet.")
    wanted = {_as_date(v) for v in values}
    available = {p.depart for p in state.date_pairs()}
    unknown = wanted - available
    if unknown:
        raise PatchError(
            "Not currently on offer: " + ", ".join(str(d) for d in sorted(unknown))
        )
    if not available - wanted:
        raise PatchError("That would remove every remaining date.")

    state.window.excluded_departures |= wanted
    state.log("exclude_dates", ", ".join(str(d) for d in sorted(wanted)))
    return f"Removed {len(wanted)}. {len(state.date_pairs())} date options left."


def confirm(state: TripState) -> str:
    order = [Step.REVIEW_CITIES, Step.COLLECT_WINDOW, Step.REVIEW_DATES, Step.READY]
    if state.step == Step.REVIEW_CITIES:
        state.step = Step.COLLECT_WINDOW
    elif state.step == Step.REVIEW_DATES:
        if not state.ready():
            raise PatchError("Not ready: missing cities or dates.")
        state.step = Step.READY
    state.log("confirm", state.step.value)
    return f"Confirmed. Now at {state.step.value}."


HANDLERS = {
    "set_route": set_route,
    "remove_city": remove_city,
    "restore_city": restore_city,
    "add_city": add_city,
    "set_window": set_window,
    "exclude_dates": exclude_dates,
    "confirm": confirm,
}


def apply(state: TripState, patch: dict) -> tuple[TripState, str]:
    """Apply one LLM patch to a COPY of state. Atomic: all or nothing."""
    action = patch.get("action")
    handler = HANDLERS.get(action)
    if handler is None:
        raise PatchError(
            f"unknown action {action!r}; expected one of {sorted(HANDLERS)}"
        )
    draft = state.model_copy(deep=True)
    kwargs = {k: v for k, v in patch.items() if k != "action"}
    try:
        message = handler(draft, **kwargs)
    except TypeError as exc:
        raise PatchError(f"bad arguments for {action}: {exc}") from exc
    return draft, message
