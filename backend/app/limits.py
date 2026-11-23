"""Rate limits kept in MongoDB, so they hold across every worker process and server."""
import time
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .db import db


def _slot(key: str, window: int) -> tuple[str, int]:
    slot = int(time.time() // window)
    return f"{key}:{window}:{slot}", slot


async def hit(key: str, window: int, limit: int) -> tuple[bool, int]:
    """Counts one call. Returns (allowed, seconds_until_the_window_resets)."""
    _id, slot = _slot(key, window)
    reset = int((slot + 1) * window - time.time()) + 1
    for _ in range(2):
        try:
            doc = await db.rag_limits.find_one_and_update(
                {"_id": _id},
                {"$inc": {"n": 1}, "$setOnInsert": {
                    "expires": datetime.fromtimestamp((slot + 1) * window + 60, tz=timezone.utc)}},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
            return doc["n"] <= limit, reset
        except DuplicateKeyError:  # two processes created the counter at the same moment
            continue
    return True, reset


async def count(key: str, window: int) -> int:
    _id, _ = _slot(key, window)
    doc = await db.rag_limits.find_one({"_id": _id}, {"n": 1})
    return doc["n"] if doc else 0
