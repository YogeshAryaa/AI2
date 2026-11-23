import logging
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, OperationFailure

from .config import cfg

log = logging.getLogger("rag.db")

_client = AsyncIOMotorClient(cfg.mongodb_uri, tz_aware=True)
db = _client[cfg.mongodb_db]
_other_clients: dict[str, AsyncIOMotorClient] = {}

# Collections owned by this app. New ones use the "rag_" prefix so they never clash with your own data
# (a staffing system may well have its own "jobs" or "messages" collection).
INTERNAL_COLLECTIONS = {
    "chunks", "documents", "providers", "cache", "settings", "sync_sources",
    "rag_api_keys", "rag_conversations", "rag_messages", "rag_jobs", "rag_limits",
    "rag_provider_state", "rag_tombstones", "rag_locks", "rag_files",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_internal(name: str, database: str | None = None) -> bool:
    if database and database != cfg.mongodb_db:
        return name.startswith("system.")
    return name in INTERNAL_COLLECTIONS or name.startswith(("system.", "rag_"))


def source_db(database: str | None = None, uri: str | None = None):
    """Database that holds a data source. Same server by default; `uri` connects to another server."""
    if uri:
        if uri not in _other_clients:
            _other_clients[uri] = AsyncIOMotorClient(uri, tz_aware=True)
        client = _other_clients[uri]
        return client[database or client.get_default_database(cfg.mongodb_db).name]
    return _client[database] if database else db


async def list_databases() -> list[str]:
    try:
        names = await _client.list_database_names()
    except OperationFailure:  # user without listDatabases permission
        return [cfg.mongodb_db]
    return sorted(n for n in names if n not in ("admin", "local", "config"))


async def init_indexes(dim: int) -> None:
    await db.chunks.create_index("doc_id")
    await db.chunks.create_index("namespace")
    await db.documents.create_index([("source", 1)])
    await db.documents.create_index([("namespace", 1), ("created_at", -1)])

    # Keyword index. language "none" = no English stemming, so Hindi/Hinglish words match as written.
    info = await db.chunks.index_information()
    old = info.get("chunks_text")
    if old and old.get("default_language") != "none":
        await db.chunks.drop_index("chunks_text")
    if not old or old.get("default_language") != "none":
        await db.chunks.create_index(
            [("text", "text"), ("title", "text")], name="chunks_text", default_language="none"
        )

    await db.cache.create_index("ns_key")
    try:
        await db.cache.create_index("created_at", expireAfterSeconds=cfg.cache_ttl_days * 86400, name="cache_ttl")
    except OperationFailure as e:  # TTL changed since last run
        log.warning("Cache TTL index not updated: %s", e)

    await db.rag_limits.create_index("expires", expireAfterSeconds=0)
    await db.rag_tombstones.create_index("at", expireAfterSeconds=86400)
    await db.rag_jobs.create_index("created_at", expireAfterSeconds=7 * 86400)
    await db.rag_conversations.create_index([("owner", 1), ("user_id", 1), ("updated_at", -1)])
    await db.rag_messages.create_index([("conversation_id", 1), ("created_at", 1)])
    if cfg.history_days > 0:
        for c in (db.rag_conversations, db.rag_messages):
            try:
                await c.create_index("updated_at" if c is db.rag_conversations else "created_at",
                                     expireAfterSeconds=cfg.history_days * 86400, name="history_ttl")
            except OperationFailure as e:
                log.warning("History TTL index not updated: %s", e)

    await db.rag_files.create_index([("owner", 1), ("created_at", -1)])
    if cfg.files_days > 0:
        try:
            await db.rag_files.create_index("created_at", expireAfterSeconds=cfg.files_days * 86400, name="files_ttl")
        except OperationFailure as e:
            log.warning("Files TTL index not updated: %s", e)

    if cfg.vector_backend == "atlas":
        await ensure_atlas_index(dim)


async def ensure_atlas_index(dim: int) -> None:
    try:
        from pymongo.operations import SearchIndexModel

        definition = {"fields": [
            {"type": "vector", "path": "embedding", "numDimensions": dim, "similarity": "cosine"},
            {"type": "filter", "path": "namespace"},
        ]}
        existing = {i["name"]: i async for i in db.chunks.list_search_indexes()}
        cur = existing.get(cfg.vector_index_name)
        if not cur:
            await db.chunks.create_search_index(
                SearchIndexModel(definition=definition, name=cfg.vector_index_name, type="vectorSearch")
            )
            log.info("Created Atlas vector index '%s'", cfg.vector_index_name)
        else:
            fields = (cur.get("latestDefinition") or {}).get("fields", [])
            if fields != definition["fields"]:
                await db.chunks.update_search_index(cfg.vector_index_name, definition)
                log.info("Updated Atlas vector index '%s'", cfg.vector_index_name)
    except Exception as e:
        log.warning("Could not create/update the Atlas vector index automatically: %s", e)


# ------------------------------------------------------------ cross-process lock (leader election)
async def acquire_lock(name: str, owner: str, ttl_seconds: int) -> bool:
    now = utcnow()
    try:
        await db.rag_locks.find_one_and_update(
            {"_id": name, "$or": [{"owner": owner}, {"until": {"$lt": now}}]},
            {"$set": {"owner": owner, "until": now + timedelta(seconds=ttl_seconds)}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False


async def release_lock(name: str, owner: str) -> None:
    await db.rag_locks.delete_one({"_id": name, "owner": owner})
