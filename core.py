"""Pure logic. No pydantic, no pandas, no I/O, no LLM.

Everything here is a deterministic function of its arguments. This is the
layer that must never surprise you, so it has zero dependencies and is
trivially unit-testable.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import product
from typing import Iterable, Literal, NamedTuple

DurationMode = Literal["nights", "days"]


class DatePair(NamedTuple):
    depart: date
    back: date

    def as_dict(self) -> dict[str, str]:
        return {"depart": self.depart.isoformat(), "return": self.back.isoformat()}


class Route(NamedTuple):
    fly_from: str
    fly_to: str


# --------------------------------------------------------------------------
# Tool 1 logic — the departure window
# --------------------------------------------------------------------------

def return_offset(duration: int, mode: DurationMode) -> int:
    """Days to add to a departure date to get the return date.

    'nights' : depart 3 Mar, duration 7 -> return 10 Mar  (7 nights away)
    'days'   : depart 3 Mar, duration 7 -> return  9 Mar  (7 calendar days
               inclusive of both travel days)

    This distinction is the single most common off-by-one in trip planners.
    Make the customer's meaning explicit rather than guessing.
    """
    if duration < 1:
        raise ValueError("duration must be at least 1")
    return duration if mode == "nights" else duration - 1


def last_possible_departure(
    holiday_end: date, duration: int, mode: DurationMode = "nights"
) -> date:
    """holiday_end - duration. For 31 Mar and 7 nights, this is 24 Mar."""
    return holiday_end - timedelta(days=return_offset(duration, mode))


def departure_window(
    holiday_start: date,
    holiday_end: date,
    duration: int,
    mode: DurationMode = "nights",
    excluded: Iterable[date] = (),
) -> list[DatePair]:
    """Every (depart, return) pair that fits entirely inside the holiday.

    Guarantees, by construction:
      - depart >= holiday_start
      - return <= holiday_end
      - return == depart + duration

    Excluded departure dates are filtered out at the end, so exclusions are
    applied to a freshly computed window every time. That means an exclusion
    can be undone by simply dropping it from the set and recomputing --
    no need to reconstruct anything.
    """
    if holiday_end < holiday_start:
        raise ValueError("holiday_end is before holiday_start")

    offset = return_offset(duration, mode)
    last = holiday_end - timedelta(days=offset)

    if last < holiday_start:
        window = (holiday_end - holiday_start).days + 1
        raise ValueError(
            f"A {duration}-{mode[:-1]} trip does not fit in a {window}-day "
            f"holiday ({holiday_start} to {holiday_end}). "
            f"Shorten the trip or widen the window."
        )

    blocked = set(excluded)
    pairs: list[DatePair] = []
    cursor = holiday_start
    while cursor <= last:
        if cursor not in blocked:
            pairs.append(DatePair(cursor, cursor + timedelta(days=offset)))
        cursor += timedelta(days=1)
    return pairs


# --------------------------------------------------------------------------
# Tool 3 logic — route combinations
# --------------------------------------------------------------------------

def outbound_routes(departure: Iterable[str], destination: Iterable[str]) -> list[Route]:
    """Cartesian product. 3 x 3 -> 9 routes, in stable order."""
    return [Route(a, b) for a, b in product(departure, destination)]


def inbound_routes(departure: Iterable[str], destination: Iterable[str]) -> list[Route]:
    """The reverse direction: destination -> departure. Also 3 x 3 -> 9."""
    return [Route(a, b) for a, b in product(destination, departure)]


def round_trip_routes(
    departure: Iterable[str], destination: Iterable[str]
) -> list[dict[str, str]]:
    """Simple round trips: you come home from where you flew into.

    9 itineraries, not 81. This is what a normal return ticket is, and it is
    what Kiwi's standard /search endpoint models with a single fly_from /
    fly_to pair plus return dates.
    """
    return [
        {"out_from": a, "out_to": b, "back_from": b, "back_to": a}
        for a, b in outbound_routes(departure, destination)
    ]


def open_jaw_routes(
    departure: Iterable[str], destination: Iterable[str]
) -> list[dict[str, str]]:
    """Open-jaw: fly into Rome, fly home out of Milan, land in Abu Dhabi.

    9 outbound x 9 inbound = 81 itineraries. This is the "another 9
    combinations for the return direction" case -- but note that combining
    the two directions independently multiplies, it does not add.

    Only use this if you genuinely want open-jaw. It needs a multi-city
    endpoint, not the plain round-trip search, and it multiplies your API
    calls by 9.
    """
    out = outbound_routes(departure, destination)
    back = inbound_routes(departure, destination)
    return [
        {"out_from": o.fly_from, "out_to": o.fly_to,
         "back_from": i.fly_from, "back_to": i.fly_to}
        for o, i in product(out, back)
    ]


# --------------------------------------------------------------------------
# Tool 4 logic — the search matrix
# --------------------------------------------------------------------------

def search_requests(
    pairs: list[DatePair], routes: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Every date pair x every route. This is your fan-out, so count it."""
    out = []
    for p in pairs:
        for r in routes:
            out.append({
                "fly_from": r["out_from"],
                "fly_to": r["out_to"],
                "return_from_airport": r["back_from"],
                "return_to_airport": r["back_to"],
                "date_from": p.depart.strftime("%d/%m/%Y"),
                "date_to": p.depart.strftime("%d/%m/%Y"),
                "return_from": p.back.strftime("%d/%m/%Y"),
                "return_to": p.back.strftime("%d/%m/%Y"),
            })
    return out


def collapsed_search_requests(
    pairs: list[DatePair],
    departure: Iterable[str],
    destination: Iterable[str],
    duration: int,
    mode: DurationMode = "nights",
) -> list[dict[str, str]]:
    """The same search, as ONE request instead of N x 9.

    Kiwi's fly_from and fly_to accept comma-separated lists, and date_from /
    date_to are a range rather than a single day. nights_in_dst_from/to pins
    the trip length. So the entire matrix collapses to a single call.

    Keep search_requests() for debugging and offline replay; use this one in
    production.
    """
    if not pairs:
        return []
    offset = return_offset(duration, mode)
    first = min(p.depart for p in pairs)
    last = max(p.depart for p in pairs)
    return [{
        "fly_from": ",".join(departure),
        "fly_to": ",".join(destination),
        "date_from": first.strftime("%d/%m/%Y"),
        "date_to": last.strftime("%d/%m/%Y"),
        "nights_in_dst_from": str(offset),
        "nights_in_dst_to": str(offset),
        "flight_type": "round",
    }]
