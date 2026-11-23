"""Keeps chosen MongoDB collections searchable automatically (no manual import).

How changes are picked up, fastest first:
1. Change streams (MongoDB Atlas or any replica set): every insert/update/delete within ~1 second.
2. Notify webhook: your system calls POST /sync/sources/{name}/notify with the ids it changed.
3. Incremental polling: only records whose `updated_field` (e.g. updatedAt) is newer than the last
   run, plus new records by ObjectId. No full collection scan.
4. A full reconcile scan (to catch deletions when there is no change stream) runs rarely:
   every 24 h with change streams, 6 h with an updated_field, otherwise every hour.
Only new or changed records are embedded again (content hash), deleted ones are removed."""
import asyncio
import logging
import time
from datetime import datetime

from bson import ObjectId
from pymongo.errors import OperationFailure, PyMongoError

from . import knowledge
from .config import cfg
from .db import db, is_internal, source_db, utcnow
from .security import decrypt

log = logging.getLogger("rag.sync")

BATCH = 200
_locks: dict[str, asyncio.Lock] = {}
_watchers: dict[str, asyncio.Task] = {}
_no_change_streams: dict[str, float] = {}   # source -> when we found change streams unsupported
_tasks: set = set()


def source_key(database: str | None, collection: str) -> str:
    return collection if not database or database == cfg.mongodb_db else f"{database}.{collection}"


def _col(src: dict):
    uri = decrypt(src["uri_enc"]) if src.get("uri_enc") else None
    return source_db(src.get("database"), uri)[src["collection"]]


def _projection(src: dict) -> dict | None:
    fields = src.get("text_fields") or []
    if not fields:
        return None
    keep = set(fields) | {f for f in (src.get("title_field"), src.get("updated_field")) if f}
    return {f: 1 for f in keep}


def _item(src: dict, d: dict) -> dict:
    title_field = src.get("title_field")
    text = knowledge.doc_to_text(d, src.get("text_fields") or None)
    title = str(d[title_field]) if title_field and d.get(title_field) else str(d["_id"])
    return {
        "doc_id": f"col:{src['_id']}:{d['_id']}", "title": title, "text": text,
        "source": f"collection:{src['_id']}", "namespace": src.get("namespace") or knowledge.DEFAULT_NS,
        "content_hash": knowledge.content_hash(title, text),
    }


async def _index_changed(src: dict, docs: list[dict], res: dict) -> None:
    """Embeds only the documents whose content changed since the last sync."""
    items = [_item(src, d) for d in docs]
    existing = {d["_id"]: d.get("content_hash") async for d in
                db.documents.find({"_id": {"$in": [i["doc_id"] for i in items]}}, {"content_hash": 1})}
    empty = [i["doc_id"] for i in items if not i["text"].strip() and i["doc_id"] in existing]
    if empty:
        res["removed"] += await knowledge.delete_documents(empty, reset_cache=False)
    todo = [i for i in items if i["text"].strip() and existing.get(i["doc_id"]) != i["content_hash"]]
    res["skipped"] += sum(1 for i in items if not i["text"].strip())
    res["unchanged"] += len(items) - len(todo) - sum(1 for i in items if not i["text"].strip())
    if not todo:
        return
    try:
        await knowledge.ingest_batch(todo, reset_cache=False)
        for i in todo:
            res["updated" if i["doc_id"] in existing else "added"] += 1
    except Exception:
        log.exception("Could not index a batch from %s", src["_id"])
        res["failed"] += len(todo)


def _new_result() -> dict:
    return {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "skipped": 0, "failed": 0}


def _and(q: dict, flt: dict | None) -> dict:
    return {"$and": [q, flt]} if flt else q


async def refresh_ids(src: dict, ids: list) -> dict:
    """Re-reads the given records: changed ones are re-indexed, missing ones (deleted, or no longer
    matching the source filter) are removed. Used by change streams and the notify webhook."""
    res = _new_result()
    if not ids:
        return res
    q_ids = []
    for i in ids:
        q_ids.append(i)
        if isinstance(i, str) and ObjectId.is_valid(i):
            q_ids.append(ObjectId(i))
    found = {}
    async for d in _col(src).find(_and({"_id": {"$in": q_ids}}, src.get("filter")), _projection(src)):
        found[str(d["_id"])] = d
    for s in range(0, len(found), BATCH):
        await _index_changed(src, list(found.values())[s:s + BATCH], res)
    gone = [f"col:{src['_id']}:{i}" for i in {str(x) for x in ids} if i not in found]
    if gone:
        res["removed"] += await knowledge.delete_documents(gone, reset_cache=False)
    if res["added"] or res["updated"] or res["removed"]:
        await knowledge.clear_cache([src.get("namespace") or knowledge.DEFAULT_NS])
    return res


async def _full_scan(src: dict) -> dict:
    tag = f"collection:{src['_id']}"
    existing = {d["_id"]: d.get("content_hash") async for d in db.documents.find({"source": tag}, {"content_hash": 1})}
    res = _new_result()
    seen: set[str] = set()
    batch: list[dict] = []
    max_oid, max_upd = None, None
    upd_field = src.get("updated_field")
    cur = _col(src).find(src.get("filter") or {}, _projection(src)).batch_size(1000)
    if cfg.sync_max_records:
        cur = cur.limit(cfg.sync_max_records)
    async for d in cur:
        doc_id = f"col:{src['_id']}:{d['_id']}"
        seen.add(doc_id)
        if isinstance(d["_id"], ObjectId) and (max_oid is None or d["_id"] > max_oid):
            max_oid = d["_id"]
        v = d.get(upd_field) if upd_field else None
        if isinstance(v, (datetime, int, float, str)) and (max_upd is None or (type(v) is type(max_upd) and v > max_upd)):
            max_upd = v
        item = _item(src, d)
        if item["text"].strip() and existing.get(doc_id) == item["content_hash"]:
            res["unchanged"] += 1
            continue
        batch.append(d)
        if len(batch) >= BATCH:
            await _index_changed(src, batch, res)
            batch = []
    if batch:
        await _index_changed(src, batch, res)
    gone = [i for i in existing if i not in seen]
    for s in range(0, len(gone), 1000):
        res["removed"] += await knowledge.delete_documents(gone[s:s + 1000], reset_cache=False)
    upd = {"last_full_scan_ts": time.time()}
    if max_oid:
        upd["last_oid"] = max_oid
    if max_upd:
        upd["watermark"] = max_upd
    await db.sync_sources.update_one({"_id": src["_id"]}, {"$set": upd})
    return res


async def _incremental(src: dict) -> dict:
    """Reads only what is new or changed since the last run."""
    res = _new_result()
    col, flt = _col(src), src.get("filter")
    upd_field, mark, last_oid = src.get("updated_field"), src.get("watermark"), src.get("last_oid")
    if upd_field and mark is not None:
        while True:
            docs = await col.find(_and({upd_field: {"$gt": mark}}, flt), _projection(src)).sort(upd_field, 1).limit(BATCH).to_list(BATCH)
            if not docs:
                break
            await _index_changed(src, docs, res)
            mark = docs[-1].get(upd_field, mark)
            await db.sync_sources.update_one({"_id": src["_id"]}, {"$set": {"watermark": mark}})
            if len(docs) < BATCH:
                break
    if isinstance(last_oid, ObjectId):
        while True:
            docs = await col.find(_and({"_id": {"$gt": last_oid}}, flt), _projection(src)).sort("_id", 1).limit(BATCH).to_list(BATCH)
            if not docs:
                break
            await _index_changed(src, docs, res)
            last_oid = docs[-1]["_id"]
            await db.sync_sources.update_one({"_id": src["_id"]}, {"$set": {"last_oid": last_oid}})
            if len(docs) < BATCH:
                break
    return res


def _full_every(src: dict) -> float:
    if src.get("full_scan_minutes"):
        return src["full_scan_minutes"] * 60
    if src["_id"] in _watchers and not _watchers[src["_id"]].done():
        return 24 * 3600
    if src.get("updated_field"):
        return 6 * 3600
    return 3600


async def run_source(name: str, full: bool = False) -> None:
    lock = _locks.setdefault(name, asyncio.Lock())
    if lock.locked():
        return
    async with lock:
        src = await db.sync_sources.find_one({"_id": name})
        if not src:
            return
        await db.sync_sources.update_one({"_id": name}, {"$set": {"running": True, "running_since": time.time()}})
        kind = "full"
        try:
            names = await _col(src).database.list_collection_names()
            if src["collection"] not in names:
                raise RuntimeError(f"Collection '{src['collection']}' not found")
            due_full = full or not src.get("last_full_scan_ts") or time.time() - src["last_full_scan_ts"] >= _full_every(src)
            if due_full:
                res = await _full_scan(src)
            else:
                kind = "incremental"
                res = await _incremental(src)
            if res["added"] or res["updated"] or res["removed"]:
                await knowledge.clear_cache([src.get("namespace") or knowledge.DEFAULT_NS])
                log.info("Synced '%s' (%s): %s", name, kind, res)
            upd: dict = {"last_result": {**res, "kind": kind}, "last_error": None}
        except Exception as e:
            log.exception("Sync failed for %s", name)
            upd = {"last_error": f"{type(e).__name__}: {e}"[:500]}
        upd.update({"running": False, "last_sync_ts": time.time(), "last_sync": utcnow()})
        await db.sync_sources.update_one({"_id": name}, {"$set": upd})


def spawn_run(name: str, full: bool = False) -> None:
    task = asyncio.create_task(run_source(name, full))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---------------------------------------------------------------- change streams
def _unsupported(e: Exception) -> bool:
    code = getattr(e, "code", None)
    return code in (40573, 40324, 13, 8000) or "replica set" in str(e).lower() or "changestream" in str(e).lower()


async def _watch(name: str) -> None:
    src = await db.sync_sources.find_one({"_id": name})
    if not src:
        return
    kwargs = {"max_await_time_ms": 1000}
    if src.get("resume_token"):
        kwargs["resume_after"] = src["resume_token"]
    pipeline = [{"$match": {"operationType": {"$in": ["insert", "update", "replace", "delete"]}}}]
    try:
        async with _col(src).watch(pipeline, **kwargs) as stream:
            await db.sync_sources.update_one({"_id": name}, {"$set": {"live": True, "live_error": None}})
            log.info("Live sync (change stream) active for '%s'", name)
            pending: dict[str, object] = {}
            last_flush = time.time()
            while stream.alive:
                ch = await stream.try_next()
                if ch is not None:
                    _id = ch["documentKey"]["_id"]
                    pending[repr(_id)] = _id
                if pending and (len(pending) >= BATCH or ch is None or time.time() - last_flush > 1.0):
                    src = await db.sync_sources.find_one({"_id": name}) or src
                    if not src.get("enabled", True):
                        return
                    res = await refresh_ids(src, list(pending.values()))
                    pending.clear()
                    last_flush = time.time()
                    await db.sync_sources.update_one({"_id": name}, {"$set": {
                        "resume_token": stream.resume_token, "last_event_at": utcnow(),
                        "last_live_result": res}})
                elif ch is None and stream.resume_token:
                    await db.sync_sources.update_one({"_id": name}, {"$set": {"resume_token": stream.resume_token}})
    except OperationFailure as e:
        if _unsupported(e):
            _no_change_streams[name] = time.time()
            await db.sync_sources.update_one({"_id": name}, {"$set": {
                "live": False, "live_error": "Change streams need MongoDB Atlas or a replica set; using polling"}})
            return
        if e.code in (280, 286, 260):  # resume point lost: start fresh and reconcile
            await db.sync_sources.update_one({"_id": name}, {"$unset": {"resume_token": 1}})
            spawn_run(name, full=True)
        raise
    finally:
        await db.sync_sources.update_one({"_id": name}, {"$set": {"live": False}})


def _start_watcher(name: str) -> None:
    async def runner():
        try:
            await _watch(name)
        except asyncio.CancelledError:
            raise
        except PyMongoError as e:
            log.warning("Change stream for '%s' stopped: %s (will retry)", name, e)
            await db.sync_sources.update_one({"_id": name}, {"$set": {"live_error": str(e)[:300]}})
        except Exception:
            log.exception("Change stream for '%s' crashed (will retry)", name)

    _watchers[name] = asyncio.create_task(runner())


def stop_all() -> None:
    for t in _watchers.values():
        t.cancel()
    _watchers.clear()


# ---------------------------------------------------------------- auto sources & scheduler
async def _ensure_auto_sources() -> None:
    spec = cfg.auto_sync_collections.strip()
    if not spec:
        return
    if spec == "*":
        names = [n for n in await db.list_collection_names() if not is_internal(n)]
    else:
        names = [n.strip() for n in spec.split(",") if n.strip()]
    for n in names:
        if is_internal(n):
            continue
        await db.sync_sources.update_one(
            {"_id": n},
            {"$setOnInsert": {
                "collection": n, "text_fields": [], "title_field": None, "filter": {}, "namespace": knowledge.DEFAULT_NS,
                "interval_minutes": cfg.auto_sync_minutes, "enabled": True, "auto": True, "created_at": utcnow(),
            }},
            upsert=True,
        )


async def tick() -> None:
    """Called by the background leader every few seconds."""
    await _ensure_auto_sources()
    now = time.time()
    sources = await db.sync_sources.find({}).to_list(500)
    for src in sources:
        name = src["_id"]
        w = _watchers.get(name)
        if not src.get("enabled", True):
            if w and not w.done():
                w.cancel()
            continue
        # live sync: (re)start the change stream unless this server cannot do it (re-checked every 30 min)
        if (not w or w.done()) and now - _no_change_streams.get(name, 0) > 1800 and src.get("live_sync", True):
            _start_watcher(name)
        every = max(1, int(src.get("interval_minutes", 5))) * 60
        busy = (_locks.get(name) and _locks[name].locked()) or (src.get("running") and now - src.get("running_since", 0) < 7200)
        if now - src.get("last_sync_ts", 0) >= every and not busy:
            spawn_run(name)
    for name in [n for n in _watchers if n not in {s["_id"] for s in sources}]:
        _watchers.pop(name).cancel()
