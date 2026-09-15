"""agent.py -- terminal frontend for the trip-planning agent.

All turn machinery lives in chat.py; this file is just the CLI shell
(input/print) around it. server.py is the web frontend for the same core.

Setup:
    pip install -r requirements.txt
    copy .env.example to .env and set ANTHROPIC_API_KEY

Run:
    python agent.py

Commands while chatting:
    /state    print the current state slice the model sees
    /payload  write outputs/payload.json (needs a ready state)
    /undo     restore the previous snapshot
    /quit
"""

from __future__ import annotations

import json
import sys

import httpx

import chat
import store
from patches import PatchError


def main() -> None:
    if not chat.API_KEY:
        sys.exit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env "
                 "and fill it in.")

    print(f"ticketfinder agent ({chat.MODEL}). /state /payload /undo /quit")
    messages: list[dict] = []

    while True:
        try:
            user = input("\ncustomer: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue

        if user == "/quit":
            break
        if user == "/state":
            print(json.dumps(chat.state_slice(store.load()), indent=2))
            continue
        if user == "/undo":
            try:
                prev = store.undo()
                print(f"  [undo] back to version {prev.version}")
            except PatchError as exc:
                print(f"  [undo] {exc}")
            continue
        if user == "/payload":
            state = store.load()
            if not state.ready():
                print("  [payload] state is not ready yet "
                      "(need cities + window).")
                continue
            from tool4_payload import build_payload
            print(f"  [payload] {store.write_artifact('payload.json', build_payload(state))}")
            continue

        try:
            say, notices = chat.take_turn(messages, user)
        except httpx.HTTPStatusError as exc:
            print(f"  [api error] {exc.response.status_code}: "
                  f"{exc.response.text[:300]}")
            continue
        for note in notices:
            print(f"  {note}")
        print(f"agent: {say}")


if __name__ == "__main__":
    main()
