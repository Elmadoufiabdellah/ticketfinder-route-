"""Pydantic models for the trip state file.

ONE file is the source of truth for a conversation: trip_state.json.
Every tool reads it, mutates one slice, and writes it back. The LLM never
holds state between turns -- it is handed the relevant slice each turn.

Design note: `pool` vs `excluded`.
--------------------------------
Cities and dates are never deleted. The pool holds everything ever offered;
`excluded` holds what the customer rejected. The ACTIVE list is
`pool - excluded`, computed on demand. This is what makes the file "hybrid":

    "remove Venice"        -> excluded.add("VCE")
    "actually keep Venice" -> excluded.discard("VCE")
    "add Naples"           -> pool.append(NAP)

All three are one-line, order-independent, and reversible. Nothing downstream
has to be patched, because routes and date pairs are always recomputed.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field, model_validator


class Step(str, Enum):
    """Where the conversation is. The harness reads this to pick a prompt."""
    COLLECT_ROUTE = "collect_route"
    REVIEW_CITIES = "review_cities"
    COLLECT_WINDOW = "collect_window"
    REVIEW_DATES = "review_dates"
    READY = "ready"
    HANDED_OFF = "handed_off"


class Airport(BaseModel):
    model_config = {"frozen": True}

    city: str
    iata: Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]
    airport_name: str = ""
    country: str = ""
    rank: int = 999
    source: Literal["rag", "customer"] = "rag"

    def label(self) -> str:
        return f"{self.city} ({self.iata})"


class CitySide(BaseModel):
    """One side of the trip -- departure or destination."""

    country: str = ""
    pool: list[Airport] = Field(default_factory=list)
    excluded: set[str] = Field(default_factory=set)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> list[Airport]:
        return [a for a in self.pool if a.iata not in self.excluded]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def codes(self) -> list[str]:
        return [a.iata for a in self.active]

    def find(self, needle: str) -> Airport | None:
        """Resolve a human string ('Venice', 'vce') to an airport in the pool.

        The LLM says 'Venice'. It must NEVER say 'VCE' -- inventing IATA codes
        is exactly the kind of hallucination this design exists to prevent.
        Resolution happens here, deterministically, against real data.
        """
        n = needle.strip().casefold()
        for a in self.pool:
            if n == a.iata.casefold() or n == a.city.casefold():
                return a
        for a in self.pool:  # fuzzy fallback: 'rome fiumicino' -> Rome
            if n in a.city.casefold() or a.city.casefold() in n:
                return a
        return None


class Window(BaseModel):
    """The holiday window and trip length."""

    holiday_start: date | None = None
    holiday_end: date | None = None
    duration_days: int | None = Field(default=None, ge=1, le=365)
    duration_mode: Literal["nights", "days"] = "nights"
    excluded_departures: set[date] = Field(default_factory=set)

    @model_validator(mode="after")
    def _check_order(self) -> "Window":
        if self.holiday_start and self.holiday_end:
            if self.holiday_end < self.holiday_start:
                raise ValueError("holiday_end must be on or after holiday_start")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        return all(
            v is not None
            for v in (self.holiday_start, self.holiday_end, self.duration_days)
        )


class Change(BaseModel):
    """One entry in the audit log."""

    at: datetime = Field(default_factory=datetime.utcnow)
    action: str
    detail: str
    version: int


class TripState(BaseModel):
    """The whole conversation, on disk."""

    version: int = 0
    step: Step = Step.COLLECT_ROUTE
    departure: CitySide = Field(default_factory=CitySide)
    destination: CitySide = Field(default_factory=CitySide)
    window: Window = Field(default_factory=Window)
    open_jaw: bool = False
    history: list[Change] = Field(default_factory=list)

    # -- derived, recomputed on demand; never stored, never patched ---------

    def date_pairs(self):
        from core import departure_window
        if not self.window.complete:
            return []
        return departure_window(
            self.window.holiday_start,
            self.window.holiday_end,
            self.window.duration_days,
            self.window.duration_mode,
            excluded=self.window.excluded_departures,
        )

    def routes(self):
        from core import open_jaw_routes, round_trip_routes
        fn = open_jaw_routes if self.open_jaw else round_trip_routes
        return fn(self.departure.codes, self.destination.codes)

    def ready(self) -> bool:
        return bool(self.departure.codes and self.destination.codes
                    and self.window.complete and self.date_pairs())

    def log(self, action: str, detail: str) -> None:
        self.version += 1
        self.history.append(Change(action=action, detail=detail, version=self.version))
