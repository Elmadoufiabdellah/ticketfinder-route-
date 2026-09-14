# ticketfinder — deterministic core, minimal LLM

## The idea

The LLM does two things: **classify** intent and **extract** a human-readable
value. It never calculates a date, never invents an IATA code, never holds
state between turns, never serialises a payload.

```
customer says something
        │
        ▼
  LLM  ──emits──►  {"action": "remove_city", "side": "destination",
        │                    "value": "Venice"}
        │          (~30 tokens, six possible verbs)
        ▼
  patches.apply()  ── resolves "Venice" → VCE against real data
        │           ── mutates ONE field
        │           ── bumps version, appends to history
        ▼
  store.save()     ── atomic write + snapshot
        │
        ▼
  everything downstream RECOMPUTED from scratch
  (date pairs, routes, payload — none of it stored)
```

## Project structure

```
llm/
├── core.py            # date math, Cartesian products — zero dependencies
├── models.py          # pydantic TripState schema, pool − excluded
├── patches.py         # the six verbs — the only LLM entry point
├── store.py           # atomic save, snapshots, undo
├── tool1_dates.py     # adapter: state → date pairs JSON
├── tool2_airports.py  # adapter: country → top N airports (airports.csv)
├── tool3_routes.py    # adapter: state → route list JSON
├── tool4_payload.py   # adapter: state → provider-shaped search payload
├── test_core.py       # 25 tests, no network, no API key
└── airports.csv       # IATA data used by tool2
```

## Three rules that make the JSON safely mutable

**1. Never delete — mark excluded.** Cities live in `pool`; rejections live
in `excluded`. Active list = `pool − excluded`.

| customer says | what happens | lines |
|---|---|---|
| "drop Venice" | `excluded.add("VCE")` | 1 |
| "put Venice back" | `excluded.discard("VCE")` | 1 |
| "add Naples" | `pool.append(NAP)` | 1 |
| "find another one" | `get_top_airports(country, 1, exclude=pool)` | 1 |

All order-independent, all reversible, all idempotent.

**2. Derived data is recomputed, never patched.** `date_pairs()` and
`routes()` are methods, not fields. Drop a city and the route count falls
from 9 to 6 by itself. Nothing can drift out of sync because nothing
downstream is stored.

**3. Patches are atomic.** `apply()` works on a deep copy. A `PatchError`
leaves the file untouched, so on-disk state is always a version that
actually succeeded.

## Layers

| file | depends on | what it does |
|---|---|---|
| `core.py` | *nothing* | date math, Cartesian products. Fully tested. |
| `models.py` | pydantic | state schema, `pool − excluded`, IATA resolution |
| `patches.py` | models | the six verbs; the only LLM entry point |
| `store.py` | models, patches | atomic save, snapshots, undo |
| `tool2_airports.py` | csv | country → top N airports |
| `tool{1,3,4}_*.py` | core, models | thin adapters that emit JSON |

Arithmetic lives in `core.py` with zero dependencies, so it is testable
without constructing a `TripState`. Keep it that way.

## The system prompt

Give the model *only* this, plus the current state slice:

```
You help a customer plan a flight search. Reply with exactly two blocks:

<say>one or two sentences to the customer</say>
<do>{"action": "...", ...}</do>

Allowed actions — nothing else is valid:

  {"action":"set_route","departure_country":"UAE","destination_country":"Italy"}
  {"action":"set_window","holiday_start":"2026-03-03",
                         "holiday_end":"2026-03-31","duration_days":7}
  {"action":"remove_city","side":"destination","value":"Venice"}
  {"action":"restore_city","side":"destination","value":"Venice"}
  {"action":"add_city","side":"departure","value":"Ras Al Khaimah"}
  {"action":"exclude_dates","values":["2026-03-05"]}
  {"action":"confirm"}
  {"action":"wait"}

Rules:
- Use city NAMES, never airport codes. The system resolves codes.
- Never compute dates. State the window; the system computes departures.
- Never list routes or payloads.
- If unsure what the customer meant, use "wait" and ask.
```

Two blocks, eight verbs, no arithmetic. That is the whole contract, and it is
why the model stays sane across a twenty-turn conversation.

## Round trip vs open jaw — read before you build

Your note about "another 9 combinations for the return direction" needs care:

- **Round trip** (`open_jaw=False`, the default): you fly home from where you
  flew into. Dubai→Rome returns Rome→Dubai. **9 itineraries.** This is what a
  normal return ticket is.
- **Open jaw** (`open_jaw=True`): fly into Rome, fly home out of Milan,
  landing in Abu Dhabi. Outbound and inbound vary independently, so it is
  9 × 9 = **81 itineraries** — the directions *multiply*, they do not add.
  It also needs a multi-city endpoint, not the plain round-trip search.

Default to round trip. Turn on open jaw only if a customer asks for it.

## Fan-out

22 date pairs × 9 routes = **198 requests** if you send one call per
combination. But `fly_from`/`fly_to` accept comma-separated lists and
`date_from`/`date_to` are a *range*, with `nights_in_dst_from/to` pinning the
trip length — so the whole matrix collapses into **one call**:

```
fly_from=DXB,AUH,SHJ  fly_to=FCO,MXP,VCE
date_from=03/03/2026  date_to=24/03/2026
nights_in_dst_from=7  nights_in_dst_to=7  flight_type=round
```

`build_payload()` emits both shapes so the cost is visible at build time
rather than at rate-limit time. Send `collapsed`; keep `expanded` for
debugging and offline replay.

## Setup

```bash
pip install -r requirements.txt
python -m pytest test_core.py -v   # 25 tests, no network, no API key
```

`core.py` and its tests have no third-party dependencies at all.

## Still to decide

- **"7 days" is ambiguous.** Nights (depart 3rd → return 10th, 22 options) or
  inclusive days (depart 3rd → return 9th, 23 options)? `duration_mode`
  handles both; ask the customer which they mean.
- **Provider.** Kiwi's Tequila is no longer open self-service for new
  developers — new partnerships are invitation-only. Confirm you have a key
  before building around it. `build_payload()` output is provider-shaped, so
  swapping to Amadeus or Duffel means rewriting one function.
