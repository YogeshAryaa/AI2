"""Chat history, stored in MongoDB. Each conversation belongs to one API key (system) and,
optionally, one end user of that system (`user_id`), so users never see each other's chats."""
import uuid

from fastapi import HTTPException

from .auth import Principal
from .db import db, utcnow


def _scope(p: Principal, user_id: str | None) -> dict:
    q: dict = {} if p.is_admin else {"owner": p.key_id}
    if user_id is not None:
        q["user_id"] = user_id
    return q


async def open_conversation(p: Principal, user_id: str | None, conversation_id: str | None, first_question: str) -> dict:
    if conversation_id:
        conv = await db.rag_conversations.find_one({"_id": conversation_id, **_scope(p, None)})
        if not conv or (conv.get("user_id") and conv.get("user_id") != user_id and not p.is_admin):
            raise HTTPException(404, "Conversation not found")
        return conv
    now = utcnow()
    conv = {
        "_id": uuid.uuid4().hex, "owner": p.key_id, "owner_name": p.name, "user_id": user_id,
        "title": first_question.strip().replace("\n", " ")[:80], "created_at": now, "updated_at": now,
        "message_count": 0,
    }
    await db.rag_conversations.insert_one(conv)
    return conv


async def recent_history(conversation_id: str, n: int = 6) -> list[dict]:
    docs = await db.rag_messages.find(
        {"conversation_id": conversation_id}, {"role": 1, "content": 1}
    ).sort("created_at", -1).limit(n).to_list(n)
    return [{"role": d["role"], "content": d["content"]} for d in reversed(docs) if d.get("content")]


async def add_turn(conversation_id: str, question: str, answer: str, meta: dict, error: str | None) -> None:
    now = utcnow()
    keep = {k: meta.get(k) for k in ("source", "provider", "model", "citations", "confidence", "standalone_question")}
    if meta.get("files"):
        keep["files"] = meta["files"]
    await db.rag_messages.insert_many([
        {"conversation_id": conversation_id, "role": "user", "content": question, "created_at": now},
        {"conversation_id": conversation_id, "role": "assistant", "content": answer, "meta": keep,
         "error": error, "created_at": utcnow()},
    ])
    await db.rag_conversations.update_one(
        {"_id": conversation_id}, {"$set": {"updated_at": utcnow()}, "$inc": {"message_count": 2}}
    )


async def list_conversations(p: Principal, user_id: str | None, limit: int, skip: int) -> list[dict]:
    docs = await db.rag_conversations.find(_scope(p, user_id)).sort("updated_at", -1).skip(skip).limit(limit).to_list(limit)
    return [{**d, "id": d.pop("_id")} for d in docs]


async def get_conversation(p: Principal, cid: str, user_id: str | None) -> dict:
    conv = await db.rag_conversations.find_one({"_id": cid, **_scope(p, user_id)})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    msgs = await db.rag_messages.find({"conversation_id": cid}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    return {**conv, "id": conv.pop("_id"), "messages": msgs}


async def delete_conversation(p: Principal, cid: str, user_id: str | None) -> bool:
    res = await db.rag_conversations.delete_one({"_id": cid, **_scope(p, user_id)})
    if res.deleted_count:
        await db.rag_messages.delete_many({"conversation_id": cid})
        await db.rag_files.delete_many({"conversation_id": cid})
    return bool(res.deleted_count)


async def rename_conversation(p: Principal, cid: str, title: str, user_id: str | None) -> bool:
    res = await db.rag_conversations.update_one({"_id": cid, **_scope(p, user_id)}, {"$set": {"title": title[:120]}})
    return bool(res.matched_count)
