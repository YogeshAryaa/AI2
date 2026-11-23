"""Background jobs (bulk imports), with progress stored in MongoDB so any worker can report it."""
import asyncio
import logging
import uuid
from datetime import timedelta

from .db import db, utcnow

log = logging.getLogger("rag.jobs")
_tasks: set = set()
STALE_AFTER = timedelta(minutes=10)


async def create(kind: str, owner: str, **info) -> dict:
    now = utcnow()
    job = {"_id": uuid.uuid4().hex[:16], "kind": kind, "owner": owner, "status": "running",
           "processed": 0, "skipped": 0, "failed": 0, "chunks": 0, "total": None, "error": None,
           "created_at": now, "updated_at": now, **info}
    await db.rag_jobs.insert_one(job)
    return public(job)


async def progress(job_id: str, **inc) -> None:
    await db.rag_jobs.update_one({"_id": job_id}, {"$inc": inc, "$set": {"updated_at": utcnow()}})


async def update(job_id: str, **fields) -> None:
    await db.rag_jobs.update_one({"_id": job_id}, {"$set": {**fields, "updated_at": utcnow()}})


def public(job: dict) -> dict:
    j = {**job, "id": job["_id"]}
    j.pop("_id", None)
    if j["status"] == "running" and j.get("updated_at") and utcnow() - j["updated_at"] > STALE_AFTER:
        j["status"], j["error"] = "failed", "Interrupted (the server restarted while it was running)"
    return j


async def get(job_id: str) -> dict | None:
    job = await db.rag_jobs.find_one({"_id": job_id})
    return public(job) if job else None


def spawn(job_id: str, coro) -> None:
    async def runner():
        try:
            await coro
            await update(job_id, status="done")
        except Exception as e:
            log.exception("Job %s failed", job_id)
            await update(job_id, status="failed", error=f"{type(e).__name__}: {e}"[:500])

    task = asyncio.create_task(runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
