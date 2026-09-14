"""Persistence for trip_state.json.

Two things matter here and nothing else does:

1. ATOMIC WRITES. Write to a temp file, then os.replace. If the process dies
   mid-write you keep the last good state instead of a truncated file that
   fails to parse and takes the whole conversation with it.

2. SNAPSHOTS. Every successful patch writes a copy to history/. Undo is then
   "load the previous snapshot", not "figure out how to reverse an edit".
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from models import TripState
from patches import PatchError
from patches import apply as apply_patch

ROOT = Path(os.environ.get("TF_STATE_DIR", "outputs"))
STATE = ROOT / "trip_state.json"
SNAPSHOTS = ROOT / "history"


def load() -> TripState:
    if not STATE.exists():
        return TripState()
    return TripState.model_validate_json(STATE.read_text(encoding="utf-8"))


def save(state: TripState) -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    blob = state.model_dump_json(indent=2)

    fd, tmp = tempfile.mkstemp(dir=ROOT, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(blob)
    os.replace(tmp, STATE)  # atomic on POSIX and Windows

    (SNAPSHOTS / f"v{state.version:04d}.json").write_text(blob, encoding="utf-8")
    return STATE


def commit(patch: dict) -> tuple[TripState, str]:
    """Load -> apply -> save. The only write path in the system.

    On PatchError nothing is written, so the on-disk state is always one of
    the versions that actually succeeded.
    """
    state = load()
    new_state, message = apply_patch(state, patch)
    save(new_state)
    return new_state, message


def undo() -> TripState:
    snaps = sorted(SNAPSHOTS.glob("v*.json"))
    if len(snaps) < 2:
        raise PatchError("Nothing to undo.")
    previous = TripState.model_validate_json(snaps[-2].read_text(encoding="utf-8"))
    snaps[-1].unlink()
    STATE.write_text(previous.model_dump_json(indent=2), encoding="utf-8")
    return previous


def write_artifact(name: str, data: dict) -> Path:
    """Dump a derived file (dates, routes, payload) for inspection."""
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / name
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
