import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime

import numpy as np
from bson import ObjectId
from pymongo import UpdateOne

from . import vectors
from .config import cfg
from .db import db, utcnow
from .embeddings import embed_documents, embed_queries, rerank, reranker_enabled, split_text

log = logging.getLogger("rag.knowledge")

DEFAULT_NS = "default"
NS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

# Fields that are never indexed unless you pick them explicitly (keeps secrets out of answers).
SENSITIVE_FIELD = re.compile(
    r"(pass(word)?|pwd|secret|token|api[_-]?key|otp|hash|salt|cvv|card_?num|aadhaa?r|pan_?(no|card)|ssn)",
    re.IGNORECASE,
)


def clean_ns(ns: str | None) -> str:
    ns = (ns or DEFAULT_NS).strip().lower()
    if not NS_RE.match(ns):
        raise ValueError("namespace must be 1-40 characters: a-z, 0-9, - or _")
    return ns


def ns_key(namespaces: list[str] | None) -> str:
    return "*" if namespaces is None else ",".join(sorted(namespaces))


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()


def _fmt(v) -> str:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str, ensure_ascii=False)
    if isinstance(v, (bytes, bytearray)):
        return ""
    return str(v)


def doc_to_text(doc: dict, fields: list[str] | None) -> str:
    keys = fields or [k for k in doc.keys() if k not in ("_id", "embedding") and not SENSITIVE_FIELD.search(k)]
    lines = []
    for k in keys:
        v = doc.get(k)
        if v is None or v == "" or v == [] or v == {}:
            continue
        s = _fmt(v).strip()
        if s:
            lines.append(f"{k}: {s[:5000]}")
    return "\n".join(lines)


# ---------------------------------------------------------------- cache
async def clear_cache(namespaces: list[str] | None = None) -> None:
    if namespaces is None:
        await db.cache.delete_many({})
    else:
        await db.cache.delete_many({"namespaces": {"$in": list(set(namespaces)) + ["*"]}})


# ---------------------------------------------------------------- ingestion
async def ingest_batch(items: list[dict], reset_cache: bool = True) -> dict:
    """Index many documents at once (embedding is batched, writes are bulk).

    item: {doc_id, title, text, source, tags?, namespace?, metadata?, content_hash?}
    A document that already exists is replaced. New chunks are written before old ones are removed,
    so a document never disappears from search while it is being updated."""
    prepared, skipped = [], 0
    for it in items:
        chunks = split_text(it.get("text") or "")
        if not chunks:
            skipped += 1
            continue
        prepared.append((it, chunks))
    if not prepared:
        return {"indexed": 0, "chunks": 0, "skipped": skipped}

    vecs = await embed_documents([c for _, ch in prepared for c in ch])
    doc_ids = [it["doc_id"] for it, _ in prepared]
    old_ids = [d["_id"] async for d in db.chunks.find({"doc_id": {"$in": doc_ids}}, {"_id": 1})]
    now = utcnow()
    rows, ops, k = [], [], 0
    namespaces = set()
    for it, chunks in prepared:
        ns = clean_ns(it.get("namespace"))
        namespaces.add(ns)
        for i, c in enumerate(chunks):
            rows.append({
                "_id": ObjectId(), "doc_id": it["doc_id"], "chunk_index": i, "title": it.get("title") or it["doc_id"],
                "source": it.get("source", "manual"), "tags": it.get("tags") or [], "namespace": ns,
                "text": c, "embedding": vectors.to_bson(vecs[k]), "created_at": now,
            })
            k += 1
        ops.append(UpdateOne(
            {"_id": it["doc_id"]},
            {"$set": {
                "title": it.get("title") or it["doc_id"], "source": it.get("source", "manual"),
                "tags": it.get("tags") or [], "namespace": ns, "chunk_count": len(chunks),
                "metadata": it.get("metadata") or {}, "updated_at": now,
                "content_hash": it.get("content_hash") or content_hash(it.get("title") or "", it["text"]),
            }, "$setOnInsert": {"created_at": now}},
            upsert=True,
        ))
    for s in range(0, len(rows), 1000):
        await db.chunks.insert_many(rows[s:s + 1000], ordered=False)
    if old_ids:
        await db.chunks.delete_many({"_id": {"$in": old_ids}})
        await vectors.on_delete(old_ids)
    vectors.on_insert([r["_id"] for r in rows], vecs, [r["namespace"] for r in rows])
    await db.documents.bulk_write(ops, ordered=False)
    if reset_cache:
        await clear_cache(list(namespaces))
    return {"indexed": len(prepared), "chunks": len(rows), "skipped": skipped}


async def ingest_document(doc_id: str, title: str, text: str, source: str = "manual",
                          tags: list[str] | None = None, reset_cache: bool = True,
                          namespace: str | None = None, metadata: dict | None = None) -> int:
    res = await ingest_batch([{"doc_id": doc_id, "title": title, "text": text, "source": source,
                               "tags": tags, "namespace": namespace, "metadata": metadata}], reset_cache)
    if not res["indexed"]:
        raise ValueError("No text to index")
    return res["chunks"]


async def delete_documents(doc_ids: list[str], reset_cache: bool = True) -> int:
    if not doc_ids:
        return 0
    namespaces = await db.documents.distinct("namespace", {"_id": {"$in": doc_ids}})
    chunk_ids = [d["_id"] async for d in db.chunks.find({"doc_id": {"$in": doc_ids}}, {"_id": 1})]
    res = await db.documents.delete_many({"_id": {"$in": doc_ids}})
    if chunk_ids:
        await db.chunks.delete_many({"_id": {"$in": chunk_ids}})
        await vectors.on_delete(chunk_ids)
    if reset_cache and (res.deleted_count or chunk_ids):
        await clear_cache(namespaces or None)
    return res.deleted_count


async def delete_document(doc_id: str) -> bool:
    return await delete_documents([doc_id]) > 0


async def delete_by_source(source: str) -> int:
    ids = [d["_id"] async for d in db.documents.find({"source": source}, {"_id": 1})]
    total = 0
    for s in range(0, len(ids), 1000):
        total += await delete_documents(ids[s:s + 1000])
    return total


# ---------------------------------------------------------------- retrieval
def _exact_tokens(query: str) -> list[str]:
    """IDs, phone numbers, emails, codes: things that must match letter for letter."""
    out = []
    for t in re.findall(r"[\w@.+-]+", query.lower()):
        t = t.strip(".-+")
        has_d, has_a = any(c.isdigit() for c in t), any(c.isalpha() for c in t)
        if "@" in t and len(t) >= 5:
            out.append(t)
        elif len(t) >= 4 and has_d and has_a:
            out.append(t)
        elif sum(c.isdigit() for c in t) >= 6:
            out.append(t)
    return out


async def _text_ids(query: str, n: int, namespaces: list[str] | None) -> list:
    q: dict = {"$text": {"$search": query}}
    if namespaces is not None:
        q["namespace"] = {"$in": namespaces}
    try:
        cur = db.chunks.find(q, {"score": {"$meta": "textScore"}}).sort([("score", {"$meta": "textScore"})]).limit(n)
        return [d["_id"] async for d in cur]
    except Exception:
        return []


async def retrieve(query: str, qv: np.ndarray, top_k: int, namespaces: list[str] | None = None):
    """Hybrid search (vectors + keywords, fused with Reciprocal Rank Fusion), optional re-ranking.

    Returns (chunks, confidence, exact). confidence = best cosine similarity of the returned chunks;
    exact = a returned chunk contains an ID / number / email from the question letter for letter."""
    n = max(top_k * 4, 20)
    vec, txt_ids = await asyncio.gather(vectors.search(qv, n, namespaces), _text_ids(query, n, namespaces))
    rrf: dict = {}
    for ranking in ([i for i, _ in vec], txt_ids):
        for rank, i in enumerate(ranking):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (60 + rank)
    if not rrf:
        return [], 0.0, False
    q: dict = {"_id": {"$in": list(rrf)}}
    if namespaces is not None:
        q["namespace"] = {"$in": namespaces}
    docs = await db.chunks.find(q).to_list(None)
    tokens = _exact_tokens(query)
    for d in docs:
        v = vectors.from_bson(d.pop("embedding", None))
        d["cosine"] = float(np.dot(qv, v)) if v.shape[0] == qv.shape[0] else 0.0
        d["rrf"] = rrf[d["_id"]]
        low = (d.get("text", "") + " " + str(d.get("title", ""))).lower()
        d["exact"] = bool(tokens) and any(t in low for t in tokens)
    if not docs:
        return [], 0.0, False
    docs.sort(key=lambda d: (d["exact"], d["rrf"]), reverse=True)
    if reranker_enabled():
        pool = docs[: max(top_k * 3, 15)]
        try:
            scores = await rerank(query, [d["text"] for d in pool])
            for d, s in zip(pool, scores):
                d["rerank"] = s
            pool.sort(key=lambda d: (d["exact"], d["rerank"]), reverse=True)
            docs = pool
        except Exception:
            log.exception("Re-ranker failed; using fused order")
    top = docs[:top_k]
    return top, max(d["cosine"] for d in top), any(d["exact"] for d in top)


# ---------------------------------------------------------------- semantic cache
_STOP = set("""
a an the is are was were be been of to in on at for and or what which who whom whose how when where why do does did
can could would should please tell me show give list about my our your i you we it its this that these those any all
kya kaun kon kis kisi kab kahan kaha kaise kyun kyu hai hain ho hota hoti tha thi the ka ki ke ko se me mein mai main
mujhe hume humein par pe aur ya bhi to toh batao bataiye bataye btao bata dikhao do de dena na ne sab koi kuch zara
क्या है हैं का की के को से में और या भी तो बताओ बताइए बताएं कौन कब कहाँ कहां कैसे क्यों मुझे हमें
""".split())


def _tokens(q: str) -> set[str]:
    return set(re.findall(r"\w+", q.lower()))


def _same_meaning(a: str, b: str) -> bool:
    """Two questions may share a cached answer only if they differ by filler words alone.
    Stops "phone number of Ravi" from returning the cached answer for "phone number of Rahul"."""
    return all(t in _STOP for t in _tokens(a) ^ _tokens(b))


async def cache_lookup(question: str, qv: np.ndarray, threshold: float, namespaces: list[str] | None) -> dict | None:
    docs = await (
        db.cache.find({"ns_key": ns_key(namespaces)},
                      {"embedding": 1, "question": 1, "answer": 1, "citations": 1, "provider": 1, "model": 1, "source": 1})
        .sort("created_at", -1).limit(2000).to_list(2000)
    )
    docs = [d for d in docs if len(d.get("embedding") or []) == qv.shape[0]]
    if not docs:
        return None
    sims = np.asarray([d["embedding"] for d in docs], dtype=np.float32) @ qv
    for i in np.argsort(-sims)[:5]:
        if float(sims[i]) < threshold:
            break
        if _same_meaning(question, docs[i].get("question", "")):
            return docs[i]
    return None


async def cache_store(question, qv, answer, source, citations, provider, model, namespaces: list[str] | None):
    await db.cache.insert_one({
        "question": question, "embedding": qv.tolist(), "answer": answer, "source": source,
        "citations": citations, "provider": provider, "model": model,
        "ns_key": ns_key(namespaces), "namespaces": ["*"] if namespaces is None else namespaces,
        "created_at": utcnow(),
    })


# ---------------------------------------------------------------- automatic match level
OUT_OF_DOMAIN = [
    "What is the weather like today?", "Who won the football world cup?", "How do I cook pasta?",
    "Tell me a joke", "What is the capital of Australia?", "How far is the moon from the earth?",
    "Recommend a good movie to watch", "Explain quantum physics simply", "What is the price of gold today?",
    "Write a poem about the sea", "How can I learn guitar fast?", "Best places to visit in Europe",
    "Who painted the Mona Lisa?", "aaj mausam kaisa hai", "mujhe ek joke sunao", "cricket match ka score kya hai",
    "biryani kaise banate hain", "आज मौसम कैसा है?", "भारत की राजधानी क्या है?", "सबसे अच्छी फिल्म कौन सी है?",
]


def _probe(text: str) -> str:
    words = re.sub(r"^\w+:\s*", "", text).split()
    if len(words) <= 12:
        return " ".join(words)
    mid = len(words) // 2
    return " ".join(words[max(0, mid - 6): mid + 6])


async def calibrate() -> dict:
    """Finds the match level from your own data instead of a hand-set slider.

    It measures how well unrelated questions (weather, jokes, cricket, in English/Hindi/Hinglish)
    match your data (= noise) and how well real passages from your data match (= signal), then
    places the cut-off between the two."""
    total = await db.chunks.estimated_document_count()
    if total < 20:
        return {"ok": False, "reason": "Add at least 20 pieces of data first", "chunks": total}
    ood = []
    for v in await embed_queries(OUT_OF_DOMAIN):
        hits = await vectors.search(v, 1, None)
        if hits:
            ood.append(hits[0][1])
    sample = await db.chunks.aggregate([{"$sample": {"size": 60}}, {"$project": {"text": 1}}]).to_list(60)
    probes = [_probe(d["text"]) for d in sample if len(d.get("text", "").split()) >= 4]
    ind = []
    for v in await embed_queries(probes):
        hits = await vectors.search(v, 1, None)
        if hits:
            ind.append(hits[0][1])
    if len(ood) < 5 or len(ind) < 5:
        return {"ok": False, "reason": "Not enough data to measure", "chunks": total}
    noise = float(np.percentile(ood, 90))
    signal = float(np.median(ind))
    if signal > noise:
        strict = noise + 0.3 * (signal - noise)
        loose = noise + 0.05 * (signal - noise)
    else:
        strict = loose = noise + 0.01
    out = {
        "ok": True, "min_score": round(min(max(strict, 0.2), 0.95), 3),
        "ai_min_score": round(min(max(loose, 0.15), 0.95), 3),
        "noise": round(noise, 3), "signal": round(signal, 3), "chunks": total, "at": utcnow(),
    }
    await db.settings.update_one({"_id": "calibration"}, {"$set": out}, upsert=True)
    log.info("Calibrated match level: %s", out)
    return out
