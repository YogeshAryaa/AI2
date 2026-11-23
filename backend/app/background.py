"""Background work: MongoDB sync, re-embedding after a model change, automatic match-level calibration.

With several worker processes (or servers) only one of them, the "leader", does this work.
Leadership is a lock in MongoDB that expires after 60 s, so if the leader dies another takes over."""
import asyncio
import logging
import os
import socket
import time
import uuid

import numpy as np
from pymongo import UpdateOne

from . import embeddings, knowledge, sync, vectors
from .config import cfg
from .db import acquire_lock, db, ensure_atlas_index, release_lock, utcnow
from .runtime_settings import get_runtime

log = logging.getLogger("rag.background")

OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
LEGACY_FINGERPRINT = "BAAI/bge-small-en-v1.5|"   # what versions before this one used by default
_state = {"leader": False, "reindex": None, "last_calibration_try": 0.0}


def is_leader() -> bool:
    return _state["leader"]


# ---------------------------------------------------------------- one-time data migrations
async def migrate() -> None:
    """Fills fields older versions did not store. Fast when there is nothing to do."""
    await db.chunks.update_many({"namespace": {"$exists": False}}, {"$set": {"namespace": knowledge.DEFAULT_NS}})
    await db.documents.update_many({"namespace": {"$exists": False}}, {"$set": {"namespace": knowledge.DEFAULT_NS}})
    await db.documents.update_many({"updated_at": {"$exists": False}}, [{"$set": {"updated_at": "$created_at"}}])
    await db.cache.delete_many({"ns_key": {"$exists": False}})
    await db.sync_sources.update_many({"collection": {"$exists": False}}, [{"$set": {"collection": "$_id"}}])


# ---------------------------------------------------------------- re-embedding after a model change
async def _index_meta() -> dict:
    meta = await db.settings.find_one({"_id": "index_meta"})
    if meta:
        return meta
    sample = await db.chunks.find_one({}, {"embedding": 1})
    if not sample:
        meta = {"fingerprint": embeddings.fingerprint(), "dim": embeddings.dim(),
                "storage": "list" if cfg.vector_backend == "atlas" else "bin", "epoch": 1}
    else:
        d = len(vectors.from_bson(sample.get("embedding")))
        meta = {"fingerprint": LEGACY_FINGERPRINT if d == 384 else "unknown", "dim": d, "storage": "list", "epoch": 1}
    await db.settings.update_one({"_id": "index_meta"}, {"$setOnInsert": meta}, upsert=True)
    return await db.settings.find_one({"_id": "index_meta"})


async def _reindex(meta: dict) -> None:
    fp, d = embeddings.fingerprint(), embeddings.dim()
    total = await db.chunks.estimated_document_count()
    prog = meta.get("reindex") or {}
    if prog.get("target") != fp:
        prog = {"target": fp, "done": 0, "total": total, "last_id": None, "started_at": utcnow()}
    log.warning("Embedding model changed (%s -> %s): re-embedding %d chunks in the background",
                meta.get("fingerprint"), fp, total)
    while True:
        q = {"_id": {"$gt": prog["last_id"]}} if prog["last_id"] else {}
        docs = await db.chunks.find(q, {"text": 1, "namespace": 1}).sort("_id", 1).limit(256).to_list(256)
        if not docs:
            break
        vecs = await embeddings.embed_documents([x.get("text", "") for x in docs])
        await db.chunks.bulk_write([UpdateOne({"_id": x["_id"]}, {"$set": {"embedding": vectors.to_bson(v)}})
                                    for x, v in zip(docs, vecs)], ordered=False)
        vectors.on_insert([x["_id"] for x in docs], np.asarray(vecs), [x.get("namespace") or "default" for x in docs])
        prog.update(done=prog["done"] + len(docs), last_id=docs[-1]["_id"])
        await db.settings.update_one({"_id": "index_meta"}, {"$set": {"reindex": prog}})
    await db.settings.update_one({"_id": "index_meta"}, {
        "$set": {"fingerprint": fp, "dim": d, "storage": "list" if cfg.vector_backend == "atlas" else "bin", "reindex": None},
        "$inc": {"epoch": 1}})
    await db.cache.delete_many({})
    await db.settings.delete_one({"_id": "calibration"})
    vectors.index.mark_stale()
    if cfg.vector_backend == "atlas":
        await ensure_atlas_index(d)
    log.warning("Re-embedding finished")


async def _maybe_reindex() -> None:
    task = _state["reindex"]
    if task and not task.done():
        return
    meta = await _index_meta()
    need = meta.get("fingerprint") != embeddings.fingerprint() or meta.get("dim") != embeddings.dim()
    need = need or (cfg.vector_backend == "atlas" and meta.get("storage") != "list")
    if need:
        _state["reindex"] = asyncio.create_task(_reindex(meta))


def reindexing() -> bool:
    t = _state["reindex"]
    return bool(t and not t.done())


# ---------------------------------------------------------------- automatic calibration
async def _maybe_calibrate() -> None:
    if reindexing() or time.time() - _state["last_calibration_try"] < 600:
        return
    s = await get_runtime()
    if s["min_score_mode"] != "auto":
        return
    cal = s.get("calibration") or {}
    total = await db.chunks.estimated_document_count()
    stale = not cal.get("ok") or abs(total - (cal.get("chunks") or 0)) > max(50, 0.2 * (cal.get("chunks") or 0))
    if stale and total >= 20:
        _state["last_calibration_try"] = time.time()
        try:
            await knowledge.calibrate()
        except Exception:
            log.exception("Automatic calibration failed")


# ---------------------------------------------------------------- the loop
async def loop() -> None:
    await asyncio.sleep(2)
    try:
        while True:
            try:
                got = await acquire_lock("leader", OWNER, 60)
                if got and not _state["leader"]:
                    log.info("This worker (%s) now runs the background tasks", OWNER)
                    await db.sync_sources.update_many({}, {"$set": {"running": False, "live": False}})
                if not got and _state["leader"]:
                    sync.stop_all()
                _state["leader"] = got
                if got:
                    await _maybe_reindex()
                    await sync.tick()
                    await _maybe_calibrate()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Background loop error")
            await asyncio.sleep(10)
    finally:
        sync.stop_all()
        if _state["leader"]:
            try:
                await release_lock("leader", OWNER)
            except Exception:
                pass
