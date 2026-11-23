"""Push API for your other systems (staffing, marketing, ...).

Whenever a record is created, changed or deleted in your system, send it here. It becomes searchable
within a second, without any polling, and works whatever database your system uses."""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import jobs, knowledge
from .auth import Principal, require
from .db import db

router = APIRouter(prefix="/records", tags=["records (push API)"])
INLINE_LIMIT = 100


class RecordIn(BaseModel):
    id: str = Field(min_length=1, max_length=200, description="Your system's id for this record")
    title: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, max_length=2_000_000, description="Free text (or send `fields`)")
    fields: dict | None = Field(default=None, description="Structured fields; turned into 'key: value' lines")
    tags: list[str] = []
    metadata: dict = Field(default={}, description="Small extra info returned with search results")


class RecordsIn(BaseModel):
    records: list[RecordIn] = Field(min_length=1, max_length=1000)
    collection: str = Field(default="records", pattern=r"^[A-Za-z0-9_.-]{1,60}$",
                            description="Kind of record, e.g. candidates, jobs, clients, campaigns")
    namespace: str | None = None
    wait: bool = False


class DeleteIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)
    collection: str = Field(default="records", pattern=r"^[A-Za-z0-9_.-]{1,60}$")
    namespace: str | None = None


def _doc_id(ns: str, collection: str, rid: str) -> str:
    return f"rec:{ns}:{collection}:{rid}"


def _item(r: RecordIn, ns: str, collection: str) -> dict:
    parts = [r.text or ""]
    if r.fields:
        parts.append(knowledge.doc_to_text(r.fields, None))
    text = "\n".join(p for p in parts if p).strip()
    title = r.title or (r.fields or {}).get("name") or (r.fields or {}).get("title") or r.id
    return {
        "doc_id": _doc_id(ns, collection, r.id), "title": str(title)[:300], "text": text,
        "source": f"api:{collection}", "tags": r.tags, "namespace": ns,
        "metadata": {**r.metadata, "record_id": r.id, "collection": collection},
        "content_hash": hashlib.sha256(f"{title}\n{text}\n{sorted(r.tags)}".encode()).hexdigest(),
    }


async def _index(job_id: str | None, items: list[dict], ns: str) -> dict:
    total = {"indexed": 0, "chunks": 0, "skipped": 0}
    for s in range(0, len(items), 200):
        res = await knowledge.ingest_batch(items[s:s + 200], reset_cache=False)
        for k in total:
            total[k] += res[k]
        if job_id:
            await jobs.progress(job_id, processed=res["indexed"], skipped=res["skipped"], chunks=res["chunks"])
    await knowledge.clear_cache([ns])
    return total


@router.post("")
async def upsert_records(body: RecordsIn, p: Principal = Depends(require("ingest"))):
    """Create or update up to 1000 records. Unchanged records are skipped (no re-embedding).
    Up to 100 changed records are indexed before the call returns; more run as a job (poll /jobs/{id})."""
    ns = p.write_namespace(body.namespace)
    if len({r.id for r in body.records}) != len(body.records):
        raise HTTPException(400, "Duplicate ids in one request")
    items = [_item(r, ns, body.collection) for r in body.records]
    existing = {d["_id"]: d.get("content_hash") async for d in
                db.documents.find({"_id": {"$in": [i["doc_id"] for i in items]}}, {"content_hash": 1})}
    changed = [i for i in items if existing.get(i["doc_id"]) != i["content_hash"]]
    empty = [i for i in changed if not i["text"]]
    changed = [i for i in changed if i["text"]]
    if empty:  # a record that now has no text: remove it from search
        await knowledge.delete_documents([i["doc_id"] for i in empty if i["doc_id"] in existing])
    base = {"namespace": ns, "received": len(items), "unchanged": len(items) - len(changed) - len(empty), "empty": len(empty)}
    if not changed:
        return {**base, "status": "done", "indexed": 0}
    if body.wait or len(changed) <= INLINE_LIMIT:
        return {**base, "status": "done", **await _index(None, changed, ns)}
    job = await jobs.create("records", p.key_id, namespace=ns, total=len(changed))
    jobs.spawn(job["id"], _index(job["id"], changed, ns))
    return {**base, "status": "running", "job_id": job["id"]}


@router.post("/delete")
async def delete_records(body: DeleteIn, p: Principal = Depends(require("ingest"))):
    ns = p.write_namespace(body.namespace)
    n = await knowledge.delete_documents([_doc_id(ns, body.collection, i) for i in body.ids])
    return {"deleted": n, "namespace": ns}


@router.delete("/{collection}/{rid}")
async def delete_record(collection: str, rid: str, namespace: str | None = None,
                        p: Principal = Depends(require("ingest"))):
    ns = p.write_namespace(namespace)
    n = await knowledge.delete_documents([_doc_id(ns, collection, rid)])
    if not n:
        raise HTTPException(404, "Record not found")
    return {"deleted": True}
