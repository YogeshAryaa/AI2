from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import knowledge, sync
from .auth import Principal, require, require_admin
from .db import db, is_internal, source_db
from .filters import FilterError, validate_filter
from .security import encrypt

router = APIRouter(prefix="/sync", tags=["sync"])


class SourceIn(BaseModel):
    collection: str
    database: str | None = None            # another database on the same MongoDB server
    connection_uri: str | None = None      # or a different MongoDB server (stored encrypted)
    text_fields: list[str] = []            # empty = all fields (secret-looking fields are skipped)
    title_field: str | None = None
    updated_field: str | None = None       # e.g. updatedAt: lets sync read only changed records
    filter: dict = {}
    namespace: str | None = None
    interval_minutes: int = Field(default=5, ge=1, le=1440)
    full_scan_minutes: int = Field(default=0, ge=0, le=10080)  # 0 = automatic
    live_sync: bool = True                 # use change streams when the server supports them


class SourcePatch(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    full_scan_minutes: int | None = Field(default=None, ge=0, le=10080)
    updated_field: str | None = None
    live_sync: bool | None = None


class NotifyIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


def _public(s: dict) -> dict:
    return {
        "id": s["_id"], "collection": s.get("collection", s["_id"]), "database": s.get("database"),
        "external_server": bool(s.get("uri_enc")), "text_fields": s.get("text_fields", []),
        "title_field": s.get("title_field"), "updated_field": s.get("updated_field"),
        "namespace": s.get("namespace") or "default", "interval_minutes": s.get("interval_minutes", 5),
        "full_scan_minutes": s.get("full_scan_minutes", 0), "enabled": s.get("enabled", True),
        "auto": s.get("auto", False), "running": s.get("running", False), "live": s.get("live", False),
        "live_error": s.get("live_error"), "last_event_at": s.get("last_event_at"),
        "last_sync_ts": s.get("last_sync_ts"), "last_result": s.get("last_result"), "last_error": s.get("last_error"),
    }


async def _get(name: str) -> dict:
    src = await db.sync_sources.find_one({"_id": name})
    if not src:
        raise HTTPException(404, "Source not found")
    return src


@router.get("/sources")
async def list_sources(_: Principal = Depends(require_admin)):
    return [_public(s) for s in await db.sync_sources.find().sort("_id", 1).to_list(500)]


@router.post("/sources", status_code=201)
async def add_source(body: SourceIn, p: Principal = Depends(require_admin)):
    if is_internal(body.collection, body.database) and not body.connection_uri:
        raise HTTPException(400, "Cannot connect an internal collection")
    try:
        validate_filter(body.filter)
    except FilterError as e:
        raise HTTPException(400, str(e))
    uri = (body.connection_uri or "").strip() or None
    if uri and not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise HTTPException(400, "connection_uri must start with mongodb:// or mongodb+srv://")
    try:
        names = await source_db(body.database, uri).list_collection_names()
    except Exception as e:
        raise HTTPException(400, f"Could not open that database: {type(e).__name__}")
    if body.collection not in names:
        raise HTTPException(404, f"Collection '{body.collection}' not found")
    key = sync.source_key(body.database, body.collection) if not uri else f"ext:{body.database or 'db'}.{body.collection}"
    ns = p.write_namespace(body.namespace)
    await db.sync_sources.update_one(
        {"_id": key},
        {"$set": {
            "collection": body.collection, "database": body.database, "uri_enc": encrypt(uri) if uri else None,
            "text_fields": body.text_fields, "title_field": body.title_field or None,
            "updated_field": body.updated_field or None, "filter": body.filter, "namespace": ns,
            "interval_minutes": body.interval_minutes, "full_scan_minutes": body.full_scan_minutes,
            "live_sync": body.live_sync, "enabled": True, "last_sync_ts": 0, "last_full_scan_ts": 0,
        }, "$setOnInsert": {"created_at": datetime.now(timezone.utc), "auto": False}},
        upsert=True,
    )
    sync.spawn_run(key, full=True)
    return _public(await _get(key))


@router.patch("/sources/{name}")
async def patch_source(name: str, body: SourcePatch, _: Principal = Depends(require_admin)):
    await _get(name)
    patch = body.model_dump(exclude_unset=True)
    if patch:
        await db.sync_sources.update_one({"_id": name}, {"$set": patch})
    return _public(await _get(name))


@router.post("/sources/{name}/run", status_code=202)
async def run_now(name: str, full: bool = True, _: Principal = Depends(require_admin)):
    await _get(name)
    sync.spawn_run(name, full=full)
    return {"started": True}


@router.post("/sources/{name}/notify")
async def notify(name: str, body: NotifyIn, p: Principal = Depends(require("ingest"))):
    """Webhook for instant sync without change streams: call it right after your system saves or
    deletes records, with their _id values. Records that no longer exist are removed."""
    src = await _get(name)
    if not p.is_admin and (src.get("namespace") or "default") not in (p.namespaces or ()):
        raise HTTPException(403, "This API key cannot write to that source's namespace")
    return await sync.refresh_ids(src, body.ids)


@router.delete("/sources/{name}")
async def remove_source(name: str, _: Principal = Depends(require_admin)):
    src = await _get(name)
    if src.get("auto"):  # listed in AUTO_SYNC_COLLECTIONS: would be re-created, so pause it instead
        await db.sync_sources.update_one({"_id": name}, {"$set": {"enabled": False}})
        return {"paused": True}
    await db.sync_sources.delete_one({"_id": name})
    removed = await knowledge.delete_by_source(f"collection:{name}")
    return {"deleted": True, "documents_removed": removed}
