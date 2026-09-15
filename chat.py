"""chat.py -- the turn machinery, shared by every frontend.

Both frontends are thin shells around this module:
    agent.py  -- the terminal CLI
    server.py -- the FastAPI web backend

Each turn:
    customer message
        -> send system prompt + <state> slice + conversation to the LLM
        -> parse <say>...</say> and <do>{...}</do> from the reply
        -> commit the <do> patch via store.commit()  (the ONLY write path)
        -> return the <say> text plus system notices to the frontend

The model never calculates, never holds state, never touches the disk.
A failed patch is fed back to the model so it can correct itself; the
on-disk state is untouched by failures (atomic apply in patches.py).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

import store
from models import Step, TripState
from patches import PatchError

load_dotenv()

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
SYSTEM = Path(__file__).with_name("system_prompt.md").read_text(encoding="utf-8")

SAY_RE = re.compile(r"<say>(.*?)</say>", re.S)
DO_RE = re.compile(r"<do>(.*?)</do>", re.S)

MAX_PATCH_ATTEMPTS = 3


# ---------------------------------------------------------------- state slice

def state_slice(state: TripState) -> dict:
    """The compact JSON the model is handed each turn. Names, never codes."""
    return {
        "step": state.step.value,
        "departure": {
            "country": state.departure.country,
            "cities": [a.label() for a in state.departure.active],
            "excluded": sorted(state.departure.excluded),
        },
        "destination": {
            "country": state.destination.country,
            "cities": [a.label() for a in state.destination.active],
            "excluded": sorted(state.destination.excluded),
        },
        "window": {
            "holiday_start": str(state.window.holiday_start),
            "holiday_end": str(state.window.holiday_end),
            "duration_days": state.window.duration_days,
            "duration_mode": state.window.duration_mode,
            "excluded_departures": sorted(
                str(d) for d in state.window.excluded_departures
            ),
        },
        "date_options": len(state.date_pairs()),
        "route_count": len(state.routes()),
        "ready": state.ready(),
    }


# --------------------------------------------------------------------- LLM

def call_llm(messages: list[dict]) -> str:
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 1024,
            "system": SYSTEM,
            "messages": messages,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


def parse_reply(text: str) -> tuple[str, dict | None]:
    """Split the reply into (say, patch). Raises PatchError on bad JSON."""
    say_m = SAY_RE.search(text)
    do_m = DO_RE.search(text)
    say = say_m.group(1).strip() if say_m else text.strip()
    if not do_m:
        return say, None
    raw = do_m.group(1).strip()
    try:
        patch = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PatchError(f"<do> block is not valid JSON: {exc}") from exc
    if not isinstance(patch, dict) or "action" not in patch:
        raise PatchError("<do> block must be a JSON object with an 'action' key")
    return say, patch


# ------------------------------------------------------------------ one turn

def take_turn(messages: list[dict], user_text: str) -> tuple[str, list[str]]:
    """One customer message in, one agent reply out. May retry bad patches.

    Returns (say, notices). `notices` are system events worth showing to the
    user -- "patch applied", "handoff payload written" -- that the CLI used
    to print as a side effect. Frontends decide how to display them.
    """
    notices: list[str] = []
    state = store.load()
    messages.append({
        "role": "user",
        "content": f"<state>{json.dumps(state_slice(state))}</state>\n"
                   f"customer: {user_text}",
    })

    say = "Sorry, I could not apply that. Could you rephrase?"
    for _attempt in range(MAX_PATCH_ATTEMPTS):
        reply = call_llm(messages)
        messages.append({"role": "assistant", "content": reply})

        try:
            say, patch = parse_reply(reply)
        except PatchError as exc:
            messages.append({"role": "user", "content": f"<system>{exc} "
                             "Reply again with a corrected <do> block.</system>"})
            continue

        if patch is None or patch.get("action") == "wait":
            break  # the model just wants to talk

        try:
            state, msg = store.commit(patch)
        except PatchError as exc:
            messages.append({"role": "user", "content":
                             f"<system>That patch failed: {exc} Apologise in "
                             "<say> and emit a corrected <do>, or use wait to "
                             "ask the customer.</system>"})
            continue

        notices.append(f"[state v{state.version}] {msg}")
        messages.append({"role": "user", "content":
                         f"<system>patch applied: {msg}</system>"})
        handoff = maybe_hand_off(state)
        if handoff:
            notices.append(handoff)
        break

    return say, notices


def maybe_hand_off(state: TripState) -> str | None:
    """Once the customer confirms dates, emit the provider-shaped payload."""
    if state.step == Step.READY and state.ready():
        from tool4_payload import build_payload
        path = store.write_artifact("payload.json", build_payload(state))
        return (f"[ready] {len(state.routes())} routes x "
                f"{len(state.date_pairs())} date pairs -> {path}")
    return None
