import asyncio
import hashlib
import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import background, jobs, knowledge, vectors
from .auth import Principal, require, require_admin
from .config import cfg
from .db import db, is_internal, list_databases, source_db, utcnow
from .embeddings import embed_query, fingerprint, reranker_enabled
from .extract import SUPPORTED, document_text, kind_of, row_title, table_rows
from .filters import FilterError, validate_filter
from .runtime_settings import get_runtime, update_runtime

router = APIRouter(tags=["knowledge"])
BATCH = 200


class TextIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=2_000_000)
    source: str = Field(default="manual", max_length=100)
    tags: list[str] = []
    namespace: str | None = None


class CollectionIn(BaseModel):
    collection: str
    database: str | None = None
    text_fields: list[str] = []  # empty = all fields (except secret-looking ones)
    title_field: str | None = None
    filter: dict = {}
    limit: int = Field(default=0, ge=0)  # 0 = everything
    namespace: str | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)
    namespaces: list[str] | None = None


class QAIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=50000)
    namespace: str | None = None


class SettingsPatch(BaseModel):
    min_score_mode: str | None = Field(default=None, pattern="^(auto|manual)$")
    min_score: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=20)
    cache_enabled: bool | None = None
    cache_threshold: float | None = Field(default=None, ge=0.8, le=1)
    send_context_to_external: bool | None = None
    max_context_chars: int | None = Field(default=None, ge=1000, le=50000)
    query_rewrite: bool | None = None
    ai_fallback: bool | None = None


def source_key(database: str | None, collection: str) -> str:
    return collection if not database or database == cfg.mongodb_db else f"{database}.{collection}"


async def check_collection(name: str, database: str | None) -> None:
    if is_internal(name, database):
        raise HTTPException(400, "That is an internal collection")
    if name not in await source_db(database).list_collection_names():
        raise HTTPException(404, f"Collection '{name}' not found")


# ---------------------------------------------------------------- ingestion
@router.post("/ingest/text")
async def ingest_text(body: TextIn, p: Principal = Depends(require("ingest"))):
    ns = p.write_namespace(body.namespace)
    doc_id = f"text:{hashlib.sha1(f'{ns}|{body.title}|{body.text}'.encode()).hexdigest()[:16]}"
    try:
        n = await knowledge.ingest_document(doc_id, body.title, body.text, body.source, body.tags, namespace=ns)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"doc_id": doc_id, "chunks": n, "namespace": ns}


async def _file_job(job_id: str, name: str, data: bytes, ns: str, tags: list[str], title_field: str | None):
    started = utcnow()
    fid = hashlib.sha1(f"{ns}|{name}".encode()).hexdigest()[:12]  # same file name again = update, not duplicate
    tag = f"file:{name}"
    if kind_of(name) == "document":
        text = await asyncio.to_thread(document_text, name, data)
        await jobs.update(job_id, total=1)
        res = await knowledge.ingest_batch([{"doc_id": f"file:{fid}", "title": name, "text": text, "source": tag,
                                             "tags": tags, "namespace": ns, "metadata": {"file": name}}])
        if not res["indexed"]:
            raise ValueError("No extractable text found in the file (a scanned PDF needs OCR first)")
        await jobs.progress(job_id, processed=res["indexed"], chunks=res["chunks"])
        return
    rows = await asyncio.to_thread(lambda: list(table_rows(name, data)))
    await jobs.update(job_id, total=len(rows))
    batch: list[dict] = []

    async def flush():
        res = await knowledge.ingest_batch(batch, reset_cache=False)
        await jobs.progress(job_id, processed=res["indexed"], skipped=res["skipped"], chunks=res["chunks"])
        batch.clear()

    for i, row in enumerate(rows, 1):
        batch.append({
            "doc_id": f"file:{fid}:{i}", "title": row_title(row, title_field, f"{name} #{i}"),
            "text": knowledge.doc_to_text(row, None), "source": tag, "tags": tags, "namespace": ns,
            "metadata": {"file": name, "row": i},
        })
        if len(batch) >= BATCH:
            await flush()
    if batch:
        await flush()
    # rows that were in an older upload of this file but are gone now
    old = [d["_id"] async for d in db.documents.find(
        {"source": tag, "namespace": ns, "updated_at": {"$lt": started}}, {"_id": 1})]
    for s in range(0, len(old), 1000):
        await knowledge.delete_documents(old[s:s + 1000], reset_cache=False)
    await knowledge.clear_cache([ns])


@router.post("/ingest/file")
async def ingest_file(
    file: UploadFile = File(...), tags: str = Form(""), namespace: str = Form(""),
    title_field: str = Form(""), wait: bool = Form(False), p: Principal = Depends(require("ingest")),
):
    """Upload a document (pdf, docx, txt, md, html) or a table for bulk data (csv, tsv, xlsx, json, jsonl).
    Tables become one searchable record per row. Uploading a file with the same name again updates it.
    Returns a job; poll GET /jobs/{id}. Send wait=true to get the result in the same call (small files)."""
    name = (file.filename or "upload.txt").replace("\\", "/").split("/")[-1][:200]
    if not kind_of(name):
        raise HTTPException(400, f"Unsupported file type. Supported: {', '.join(SUPPORTED)}")
    data = await file.read(cfg.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > cfg.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {cfg.max_upload_mb} MB)")
    ns = p.write_namespace(namespace or None)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    job = await jobs.create("file", p.key_id, file=name, namespace=ns)
    coro = _file_job(job["id"], name, data, ns, tag_list, title_field.strip() or None)
    if wait:
        try:
            await coro
            await jobs.update(job["id"], status="done")
        except Exception as e:
            await jobs.update(job["id"], status="failed", error=f"{type(e).__name__}: {e}"[:500])
        return await jobs.get(job["id"])
    jobs.spawn(job["id"], coro)
    return job


@router.post("/knowledge/qa")
async def save_qa(body: QAIn, p: Principal = Depends(require("ingest"))):
    """Add an approved question/answer pair to your knowledge base."""
    ns = p.write_namespace(body.namespace)
    doc_id = f"qa:{hashlib.sha1(f'{ns}|{body.question}'.encode()).hexdigest()[:16]}"
    n = await knowledge.ingest_document(doc_id, body.question[:80], f"Q: {body.question}\nA: {body.answer}",
                                        "approved-qa", ["qa"], namespace=ns)
    return {"doc_id": doc_id, "chunks": n, "namespace": ns}


# ---------------------------------------------------------------- MongoDB collections (admin)
@router.get("/databases")
async def databases(_: Principal = Depends(require_admin)):
    return {"default": cfg.mongodb_db, "databases": await list_databases()}


@router.get("/collections")
async def list_collections(database: str | None = None, _: Principal = Depends(require_admin)):
    names = await source_db(database).list_collection_names()
    return sorted(n for n in names if not is_internal(n, database))


@router.get("/collections/{name}/sample")
async def collection_sample(name: str, database: str | None = None, _: Principal = Depends(require_admin)):
    await check_collection(name, database)
    col = source_db(database)[name]
    doc = await col.find_one(sort=[("_id", -1)])
    count = await col.estimated_document_count()
    fields = list(doc.keys()) if doc else []
    guess = next((f for f in fields if f.lower() in ("updatedat", "updated_at", "modifiedat", "modified_at",
                                                      "lastmodified", "last_modified", "updated", "modified")), None)
    return {
        "count": count, "fields": fields, "updated_field_guess": guess,
        "sensitive_fields": [f for f in fields if knowledge.SENSITIVE_FIELD.search(f)],
        "sample": json.loads(json.dumps(doc, default=str)) if doc else None,
    }


async def _collection_job(job_id: str, req: CollectionIn, ns: str):
    col = source_db(req.database)[req.collection]
    key = source_key(req.database, req.collection)
    await jobs.update(job_id, total=await col.count_documents(req.filter) if req.filter else await col.estimated_document_count())
    cur = col.find(req.filter).batch_size(500)
    if req.limit:
        cur = cur.limit(req.limit)
    batch: list[dict] = []

    async def flush():
        res = await knowledge.ingest_batch(batch, reset_cache=False)
        await jobs.progress(job_id, processed=res["indexed"], skipped=res["skipped"], chunks=res["chunks"])
        batch.clear()

    async for d in cur:
        text = knowledge.doc_to_text(d, req.text_fields or None)
        title = str(d.get(req.title_field)) if req.title_field and d.get(req.title_field) else str(d["_id"])
        batch.append({"doc_id": f"col:{key}:{d['_id']}", "title": title, "text": text,
                      "source": f"collection:{key}", "namespace": ns,
                      "content_hash": knowledge.content_hash(title, text)})
        if len(batch) >= BATCH:
            await flush()
    if batch:
        await flush()
    await knowledge.clear_cache([ns])


@router.post("/ingest/collection", status_code=202)
async def ingest_collection(body: CollectionIn, p: Principal = Depends(require_admin)):
    """One-time import. For data that keeps changing, connect it with POST /sync/sources instead."""
    await check_collection(body.collection, body.database)
    try:
        validate_filter(body.filter)
    except FilterError as e:
        raise HTTPException(400, str(e))
    ns = p.write_namespace(body.namespace)
    job = await jobs.create("collection", p.key_id, collection=body.collection, namespace=ns)
    jobs.spawn(job["id"], _collection_job(job["id"], body, ns))
    return job


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, p: Principal = Depends(require("ingest"))):
    job = await jobs.get(job_id)
    if not job or (not p.is_admin and job.get("owner") != p.key_id):
        raise HTTPException(404, "Job not found")
    return job


# ---------------------------------------------------------------- browse / delete
def _doc_scope(p: Principal, namespace: str | None) -> dict:
    nss = p.read_namespaces([namespace] if namespace else None)
    return {} if nss is None else {"namespace": {"$in": nss}}


@router.get("/namespaces")
async def namespaces(p: Principal = Depends(require("search"))):
    pipe = [{"$match": _doc_scope(p, None)}, {"$group": {"_id": "$namespace", "documents": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    return [{"namespace": d["_id"] or "default", "documents": d["documents"]} async for d in db.documents.aggregate(pipe)]


@router.get("/documents")
async def list_documents(skip: int = 0, limit: int = 50, namespace: str | None = None, q: str | None = None,
                         source: str | None = None, p: Principal = Depends(require("ingest"))):
    query = _doc_scope(p, namespace)
    if q:
        query["title"] = {"$regex": re.escape(q[:100]), "$options": "i"}
    if source:
        query["source"] = source
    limit = max(1, min(limit, 200))
    docs = await db.documents.find(query, {"content_hash": 0}).sort("updated_at", -1).skip(max(0, skip)).limit(limit).to_list(limit)
    total = await db.documents.count_documents(query) if query else await db.documents.estimated_document_count()
    return {"total": total, "items": [{**d, "id": d.pop("_id")} for d in docs]}


@router.delete("/documents/{doc_id:path}")
async def remove_document(doc_id: str, p: Principal = Depends(require("ingest"))):
    doc = await db.documents.find_one({"_id": doc_id, **_doc_scope(p, None)}, {"_id": 1})
    if not doc or not await knowledge.delete_document(doc_id):
        raise HTTPException(404, "Document not found")
    return {"deleted": True}


@router.post("/documents/delete-source")
async def remove_source(source: str, namespace: str | None = None, p: Principal = Depends(require("ingest"))):
    """Removes every document of one source, e.g. all rows of an uploaded file ("file:staff.csv")."""
    ids = [d["_id"] async for d in db.documents.find({"source": source, **_doc_scope(p, namespace)}, {"_id": 1})]
    n = 0
    for s in range(0, len(ids), 1000):
        n += await knowledge.delete_documents(ids[s:s + 1000])
    return {"deleted": n}


@router.post("/search")
async def search(body: SearchIn, p: Principal = Depends(require("search"))):
    """Retrieval only (no AI): the passages that best match the query, with scores."""
    nss = p.read_namespaces(body.namespaces)
    qv = await embed_query(body.query)
    chunks, confidence, exact = await knowledge.retrieve(body.query, qv, body.top_k, nss)
    s = await get_runtime()
    return {
        "confidence": round(confidence, 3), "exact_match": exact, "min_score": s["effective_min_score"],
        "found": bool(chunks) and (exact or confidence >= s["effective_min_score"]),
        "results": [
            {"doc_id": c["doc_id"], "title": c.get("title"), "namespace": c.get("namespace"), "source": c.get("source"),
             "text": c["text"], "cosine": round(c["cosine"], 3), "exact_match": c["exact"]}
            for c in chunks
        ],
    }


# ---------------------------------------------------------------- settings & status (admin)
@router.get("/settings")
async def read_settings(_: Principal = Depends(require_admin)):
    return await get_runtime()


@router.put("/settings")
async def write_settings(body: SettingsPatch, _: Principal = Depends(require_admin)):
    return await update_runtime(body.model_dump(exclude_none=True))


@router.post("/settings/calibrate")
async def calibrate(_: Principal = Depends(require_admin)):
    res = await knowledge.calibrate()
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason", "Could not calibrate"))
    return await get_runtime()


@router.get("/status")
async def status(_: Principal = Depends(require_admin)):
    meta = await db.settings.find_one({"_id": "index_meta"}) or {}
    return {
        "embedding_model": cfg.embedding_model, "fingerprint": fingerprint(), "reranker": cfg.reranker_model if reranker_enabled() else None,
        "vector_backend": cfg.vector_backend, "vector_index": vectors.index.stats() if cfg.vector_backend != "atlas" else None,
        "chunks": await db.chunks.estimated_document_count(), "documents": await db.documents.estimated_document_count(),
        "reindex": meta.get("reindex"), "this_worker_runs_background_tasks": background.is_leader(),
        "providers_enabled": await db.providers.count_documents({"enabled": True}),
    }
