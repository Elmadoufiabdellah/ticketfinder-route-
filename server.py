"""server.py -- FastAPI backend for the trip-planning agent.

The browser UI (static/index.html) talks to these endpoints; all agent
logic lives in chat.py and all persistence in store.py, exactly as the
CLI (agent.py) uses them. Single user, single session: one in-memory
`messages` list and the one outputs/trip_state.json on disk.

Setup:
    pip install -r requirements.txt
    copy .env.example to .env and set ANTHROPIC_API_KEY

Run:
    uvicorn server:app --reload
Then open http://127.0.0.1:8000
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import chat
import store
from patches import PatchError

STATIC = Path(__file__).with_name("static")

app = FastAPI(title="ticketfinder agent")

messages: list[dict] = []  # single user, single session -- same as the CLI


class ChatIn(BaseModel):
    text: str


class ChatOut(BaseModel):
    say: str
    notices: list[str]
    state: dict


@app.post("/api/chat", response_model=ChatOut)
def post_chat(body: ChatIn) -> ChatOut:
    if not chat.API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not set on the server.")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty message.")
    try:
        say, notices = chat.take_turn(messages, text)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502,
            f"LLM API error {exc.response.status_code}: "
            f"{exc.response.text[:300]}",
        )
    return ChatOut(say=say, notices=notices,
                   state=chat.state_slice(store.load()))


@app.get("/api/state")
def get_state() -> dict:
    """The current state slice -- the same JSON the model sees each turn."""
    return chat.state_slice(store.load())


@app.post("/api/undo")
def post_undo() -> dict:
    try:
        prev = store.undo()
    except PatchError as exc:
        raise HTTPException(400, str(exc))
    # Drop the last customer exchange from the in-memory conversation so it
    # matches the reverted on-disk state: trailing <system> notes, the
    # assistant reply, then the customer message.
    while (messages and messages[-1]["role"] == "user"
           and messages[-1]["content"].startswith("<system>")):
        messages.pop()
    if messages and messages[-1]["role"] == "assistant":
        messages.pop()
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    return {"version": prev.version, "state": chat.state_slice(prev)}


@app.post("/api/reset")
def post_reset() -> dict:
    """Fresh conversation: clear memory, state file, and snapshots."""
    messages.clear()
    if store.STATE.exists():
        store.STATE.unlink()
    if store.SNAPSHOTS.exists():
        shutil.rmtree(store.SNAPSHOTS)
    return {"state": chat.state_slice(store.load())}


@app.get("/api/payload")
def get_payload() -> dict:
    state = store.load()
    if not state.ready():
        raise HTTPException(400, "State is not ready yet "
                            "(need cities + window).")
    from tool4_payload import build_payload
    payload = build_payload(state)
    path = store.write_artifact("payload.json", payload)
    return {"path": str(path), "payload": payload}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
