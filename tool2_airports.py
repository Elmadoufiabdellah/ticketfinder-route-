"""Tool 2 -- country -> top N airports.

This is your "small RAG", and it should stay small. For a few thousand rows
keyed by country, a CSV plus a dict beats a vector store on every axis that
matters here: it is exact, it is instant, it is diffable in git, and it
returns the SAME three cities every single time. Semantic search would give
you none of that and would occasionally hand the customer an airport in the
wrong country.

The only genuinely hard part is that customers do not type canonical country
names. "UAE", "U.A.E.", "the Emirates", "united arab emirates" must all land
on one key. That is what ALIASES is for -- and it is the one place worth
adding fuzzy matching later.
"""

from __future__ import annotations

import csv
import functools
from pathlib import Path

from models import Airport

DATA = Path(__file__).resolve().parent / "airports.csv"

ALIASES = {
    "uae": "United Arab Emirates",
    "u.a.e.": "United Arab Emirates",
    "u.a.e": "United Arab Emirates",
    "emirates": "United Arab Emirates",
    "the emirates": "United Arab Emirates",
    "dubai": "United Arab Emirates",
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "britain": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    "usa": "United States",
    "us": "United States",
    "u.s.a.": "United States",
    "america": "United States",
    "holland": "Netherlands",
    "the netherlands": "Netherlands",
    "deutschland": "Germany",
    "italia": "Italy",
    "espana": "Spain",
    "türkiye": "Turkey",
    "turkiye": "Turkey",
    "maroc": "Morocco",
}


def normalise_country(raw: str) -> str:
    """'uae' -> 'United Arab Emirates'. Falls back to title-casing."""
    key = raw.strip().casefold()
    if key in ALIASES:
        return ALIASES[key]
    for country in _by_country():
        if key == country.casefold():
            return country
    return raw.strip().title()


@functools.lru_cache(maxsize=1)
def _by_country() -> dict[str, list[Airport]]:
    """Load once, index by country, pre-sorted by rank."""
    index: dict[str, list[Airport]] = {}
    with DATA.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            airport = Airport(
                country=row["country"].strip(),
                city=row["city"].strip(),
                airport_name=row["airport_name"].strip(),
                iata=row["iata"].strip().upper(),
                rank=int(row["rank"]),
            )
            index.setdefault(airport.country, []).append(airport)
    for airports in index.values():
        airports.sort(key=lambda a: a.rank)
    return index


def get_top_airports(
    country: str, count: int = 3, exclude: set[str] | None = None
) -> list[Airport]:
    """The big N airports of a country, by traffic rank.

    `exclude` takes IATA codes already shown, so "find me another one" is
    just get_top_airports(country, count=1, exclude=already_shown) -- it
    returns the next one down the ranking, and an empty list when the
    country is exhausted. The caller must handle empty; see patches.add_city.
    """
    skip = {c.upper() for c in (exclude or set())}
    pool = _by_country().get(normalise_country(country), [])
    return [a for a in pool if a.iata not in skip][:count]


def lookup_city(name: str, country: str | None = None) -> Airport | None:
    """Resolve a city the customer named to a real airport.

    Scoped to their country when we know it, so 'Birmingham' does not jump
    continents. Returns None rather than guessing -- a wrong IATA code here
    silently poisons every downstream search.
    """
    needle = name.strip().casefold()
    index = _by_country()
    pools = (
        [index.get(normalise_country(country), [])]
        if country else list(index.values())
    )
    for pool in pools:
        for a in pool:
            if needle in (a.city.casefold(), a.iata.casefold()):
                return a
    for pool in pools:  # loose second pass
        for a in pool:
            if needle in a.city.casefold() or needle in a.airport_name.casefold():
                return a
    return None


def countries() -> list[str]:
    return sorted(_by_country())
