import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import conversations as conv
from .auth import Principal, require
from . import media
from .db import db

log = logging.getLogger("rag.routes.chat")
router = APIRouter(tags=["chat"])


class Msg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20000)


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Msg] = Field(default=[], max_length=50)  # optional; ignored when conversation_id has history
    mode: Literal["ai", "search"] = "ai"      # "search" = answer from your data only, no AI (fast)
    conversation_id: str | None = Field(default=None, max_length=64)
    user_id: str | None = Field(default=None, max_length=128)   # your system's end-user id
    save_history: bool = True
    namespaces: list[str] | None = None      # narrow the search (only within what the key allows)
    allow_ai_fallback: bool | None = None     # false = never answer from general AI knowledge


async def _prepare(p: Principal, body: ChatIn):
    """Checks permissions and opens the conversation before any answer is streamed."""
    namespaces = p.read_namespaces(body.namespaces)
    history = [m.model_dump() for m in body.history]
    c = None
    if body.save_history or body.conversation_id:
        c = await conv.open_conversation(p, body.user_id, body.conversation_id, body.question)
        if not history and body.conversation_id:
            history = await conv.recent_history(c["_id"])
    return namespaces, history, c


async def _events(p: Principal, body: ChatIn, namespaces, history, c):
    """Runs the chat and (optionally) stores the turn in the conversation history.
    Asking for a PDF or an image in the question adds a `file` event (see media.py)."""
    meta, parts, error, files = {}, [], None, []
    try:
        async for ev in media.run(body.question, history, body.mode, namespaces, body.allow_ai_fallback,
                                  p.key_id, body.user_id, c["_id"] if c else None):
            if ev["type"] == "file":
                files.append(ev["file"])
            elif ev["type"] == "meta":
                if c:
                    ev = {**ev, "conversation_id": c["_id"]}
                meta = ev
            elif ev["type"] == "token":
                parts.append(ev["text"])
            elif ev["type"] == "error":
                error = ev["message"]
            yield ev
    finally:
        if c:
            try:
                await conv.add_turn(c["_id"], body.question, "".join(parts), {**meta, "files": files}, error)
            except Exception:
                log.exception("Could not save the conversation turn")
    if c and not meta:
        yield {"type": "meta", "conversation_id": c["_id"]}


@router.post("/chat")
async def chat(body: ChatIn, p: Principal = Depends(require("chat"))):
    """Non-streaming: returns the complete answer as JSON."""
    meta: dict = {}
    parts: list[str] = []
    files: list[dict] = []
    error = None
    async for ev in _events(p, body, *await _prepare(p, body)):
        if ev["type"] == "file":
            files.append(ev["file"])
        elif ev["type"] == "meta":
            meta.update({k: v for k, v in ev.items() if k != "type"})
        elif ev["type"] == "token":
            parts.append(ev["text"])
        elif ev["type"] == "error":
            error = ev
    if error and not parts:
        return {"answer": None, "error": error["message"], "details": error.get("details", []), "files": files, **meta}
    return {"answer": "".join(parts), "files": files, **meta}


@router.post("/chat/stream")
async def chat_stream(body: ChatIn, p: Principal = Depends(require("chat"))):
    """Server-Sent Events. Events: meta, token, error, done (each a JSON line prefixed by 'data:')."""
    prepared = await _prepare(p, body)  # permission errors become proper HTTP errors, not stream events

    async def gen():
        try:
            async for ev in _events(p, body, *prepared):
                yield f"data: {json.dumps(ev, default=str, ensure_ascii=False)}\n\n"
        except HTTPException as e:
            yield f"data: {json.dumps({'type': 'error', 'message': e.detail})}\n\n"
        except Exception as e:  # never leave the client hanging
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- files made in the chat (PDF, images)
@router.get("/files/{fid}")
async def get_file(fid: str, download: bool = False, user_id: str | None = None,
                   p: Principal = Depends(require("chat"))):
    """The file bytes. Only the API key (and user_id, if one was given) that asked for it can read it."""
    q: dict = {"_id": fid} if p.is_admin else {"_id": fid, "owner": p.key_id}
    f = await db.rag_files.find_one(q)
    if not f or (f.get("user_id") and f["user_id"] != user_id and not p.is_admin):
        raise HTTPException(404, "File not found")
    name = f["name"].encode("ascii", "ignore").decode() or "file"
    disp = "attachment" if download else "inline"
    return Response(bytes(f["data"]), media_type=f["mime"], headers={
        "Content-Disposition": f'{disp}; filename="{name}"', "Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- history
class RenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.get("/conversations")
async def list_conversations(user_id: str | None = None, limit: int = 30, skip: int = 0,
                             p: Principal = Depends(require("chat"))):
    return await conv.list_conversations(p, user_id, max(1, min(limit, 100)), max(0, skip))


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, user_id: str | None = None, p: Principal = Depends(require("chat"))):
    return await conv.get_conversation(p, cid, user_id)


@router.patch("/conversations/{cid}")
async def rename_conversation(cid: str, body: RenameIn, user_id: str | None = None,
                              p: Principal = Depends(require("chat"))):
    if not await conv.rename_conversation(p, cid, body.title, user_id):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: str, user_id: str | None = None, p: Principal = Depends(require("chat"))):
    if not await conv.delete_conversation(p, cid, user_id):
        raise HTTPException(404, "Conversation not found")
    return {"deleted": True}
