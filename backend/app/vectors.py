"""Vector search.

local: vectors live in this process's memory and are kept up to date incrementally
       (new chunks are appended, deleted ones are masked; nothing is reloaded from scratch on every
       import). Several worker processes stay in step through MongoDB: each one picks up new chunks
       by ObjectId time and deletions through the `rag_tombstones` collection.
atlas: MongoDB Atlas $vectorSearch (scales to millions; nothing held in RAM).
"""
import asyncio
import logging
import threading
import time
from datetime import timedelta

import numpy as np
from bson import Binary, ObjectId

from .config import cfg
from .db import db, utcnow

log = logging.getLogger("rag.vectors")

REFRESH_SECONDS = 2.0                 # how often a process checks MongoDB for changes made by others
OVERLAP = timedelta(seconds=120)      # tolerance for clock differences between processes
FULL_RELOAD_SECONDS = 6 * 3600


def to_bson(v) -> object:
    v = np.asarray(v, dtype=np.float32)
    if cfg.vector_backend == "atlas":
        return v.tolist()
    return Binary(v.tobytes())  # 4x smaller in MongoDB than a list of doubles


def from_bson(x) -> np.ndarray:
    if isinstance(x, (bytes, bytearray)):
        return np.frombuffer(bytes(x), dtype=np.float32)
    if x is None:
        return np.zeros(0, np.float32)
    return np.asarray(x, dtype=np.float32)


class LocalIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._alock = asyncio.Lock()
        self.dim: int | None = None
        self.epoch = None
        self._reset()

    def _reset(self) -> None:
        with self._lock:
            self.ids: list = []
            self.pos: dict = {}
            self.mat: np.ndarray | None = None
            self.ns = np.zeros(0, np.int32)
            self.alive = np.zeros(0, bool)
            self.n = 0
            self.dead = 0
            self.ns_codes: dict[str, int] = {}
            self.loaded = False
            self.watermark = None
            self.last_check = 0.0
            self.loaded_at = 0.0

    # ---------------------------------------------------------- mutation (cheap, in memory)
    def _code(self, ns: str) -> int:
        c = self.ns_codes.get(ns)
        if c is None:
            c = self.ns_codes[ns] = len(self.ns_codes)
        return c

    def _grow(self, extra: int, d: int) -> None:
        dtype = np.float16 if cfg.vector_dtype == "float16" else np.float32
        if self.mat is None:
            cap = max(1024, extra)
            self.mat = np.zeros((cap, d), dtype)
            self.ns = np.zeros(cap, np.int32)
            self.alive = np.zeros(cap, bool)
        elif self.n + extra > self.mat.shape[0]:
            cap = max(self.mat.shape[0] * 2, self.n + extra)
            mat = np.zeros((cap, d), dtype)
            mat[: self.n] = self.mat[: self.n]
            ns = np.zeros(cap, np.int32)
            ns[: self.n] = self.ns[: self.n]
            alive = np.zeros(cap, bool)
            alive[: self.n] = self.alive[: self.n]
            self.mat, self.ns, self.alive = mat, ns, alive

    def _put(self, ids: list, vecs: np.ndarray, namespaces: list[str]) -> None:
        if not ids:
            return
        vecs = np.asarray(vecs, np.float32)
        d = vecs.shape[1]
        with self._lock:
            if self.dim is None:
                self.dim = d
            if d != self.dim:
                return
            new = [i for i, c in enumerate(ids) if c not in self.pos]
            old = [i for i, c in enumerate(ids) if c in self.pos]
            if old:
                rows = np.asarray([self.pos[ids[i]] for i in old])
                self.dead -= int((~self.alive[rows]).sum())
                self.mat[rows] = vecs[old]
                self.ns[rows] = [self._code(namespaces[i]) for i in old]
                self.alive[rows] = True
            if new:
                self._grow(len(new), d)
                s, e = self.n, self.n + len(new)
                self.mat[s:e] = vecs[new]
                self.ns[s:e] = [self._code(namespaces[i]) for i in new]
                self.alive[s:e] = True
                for j, i in enumerate(new):
                    self.ids.append(ids[i])
                    self.pos[ids[i]] = s + j
                self.n = e

    def add(self, ids: list, vecs: np.ndarray, namespaces: list[str]) -> None:
        if self.loaded:
            self._put(ids, vecs, namespaces)

    def remove(self, ids: list) -> None:
        with self._lock:
            rows = [self.pos[i] for i in ids if i in self.pos]
            if not rows:
                return
            rows = np.asarray(rows)
            self.dead += int(self.alive[rows].sum())
            self.alive[rows] = False

    def _maybe_compact(self) -> None:
        with self._lock:
            if self.n < 1000 or self.dead < self.n * 0.25:
                return
            keep = np.nonzero(self.alive[: self.n])[0]
            self.mat = np.ascontiguousarray(self.mat[keep])
            self.ns = self.ns[keep].copy()
            self.alive = np.ones(len(keep), bool)
            self.ids = [self.ids[i] for i in keep]
            self.pos = {c: i for i, c in enumerate(self.ids)}
            self.n, self.dead = len(keep), 0

    # ---------------------------------------------------------- sync with MongoDB
    async def _full_load(self, dim: int) -> None:
        started = utcnow()
        t0 = time.time()
        self._reset()
        self.dim = dim
        meta = await db.settings.find_one({"_id": "index_meta"}, {"epoch": 1}) or {}
        ids, vecs, nss = [], [], []
        async for d in db.chunks.find({}, {"embedding": 1, "namespace": 1}).batch_size(5000):
            v = from_bson(d.get("embedding"))
            if v.shape[0] != dim:  # produced by another model (re-index in progress)
                continue
            ids.append(d["_id"])
            vecs.append(v)
            nss.append(d.get("namespace") or "default")
            if len(ids) >= 5000:
                self._put(ids, np.vstack(vecs), nss)
                ids, vecs, nss = [], [], []
        if ids:
            self._put(ids, np.vstack(vecs), nss)
        self.loaded = True
        self.epoch = meta.get("epoch")
        self.watermark = started
        self.last_check = self.loaded_at = time.time()
        log.info("Vector index loaded: %d chunks in %.1fs", self.n, time.time() - t0)

    async def _catch_up(self) -> None:
        meta = await db.settings.find_one({"_id": "index_meta"}, {"epoch": 1}) or {}
        if meta.get("epoch") != self.epoch or time.time() - self.loaded_at > FULL_RELOAD_SECONDS:
            await self._full_load(self.dim)
            return
        since = self.watermark - OVERLAP
        started = utcnow()
        recent = [d["_id"] async for d in db.chunks.find({"_id": {"$gte": ObjectId.from_datetime(since)}}, {"_id": 1})]
        missing = [i for i in recent if i not in self.pos]
        for s in range(0, len(missing), 2000):
            ids, vecs, nss = [], [], []
            async for d in db.chunks.find({"_id": {"$in": missing[s:s + 2000]}}, {"embedding": 1, "namespace": 1}):
                v = from_bson(d.get("embedding"))
                if v.shape[0] == self.dim:
                    ids.append(d["_id"])
                    vecs.append(v)
                    nss.append(d.get("namespace") or "default")
            if ids:
                self._put(ids, np.vstack(vecs), nss)
        gone = [d["_id"] async for d in db.rag_tombstones.find({"at": {"$gte": since}}, {"_id": 1})]
        self.remove(gone)
        self._maybe_compact()
        self.watermark = started
        self.last_check = time.time()

    async def ensure_fresh(self, dim: int) -> None:
        if self.loaded and time.time() - self.last_check < REFRESH_SECONDS:
            return
        async with self._alock:
            if not self.loaded or self.dim != dim:
                await self._full_load(dim)
            elif time.time() - self.last_check >= REFRESH_SECONDS:
                await self._catch_up()

    def mark_stale(self) -> None:
        self.loaded = False

    # ---------------------------------------------------------- search (runs in a worker thread)
    def _search(self, qv: np.ndarray, k: int, allowed: list[str] | None) -> list[tuple]:
        with self._lock:  # snapshot only; the maths runs without the lock
            n, mat, ids = self.n, self.mat, self.ids
            if n == 0 or mat is None:
                return []
            mask = self.alive[:n].copy()
            if allowed is not None:
                codes = [self.ns_codes[a] for a in allowed if a in self.ns_codes]
                if not codes:
                    return []
                mask &= np.isin(self.ns[:n], codes)
        qv = np.asarray(qv, np.float32)
        if mat.dtype == np.float32:
            sims = mat[:n] @ qv
        else:
            sims = np.empty(n, np.float32)
            for s in range(0, n, 65536):
                e = min(n, s + 65536)
                sims[s:e] = mat[s:e].astype(np.float32) @ qv
        sims[~mask] = -np.inf
        k = min(k, int(mask.sum()))
        if k <= 0:
            return []
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [(ids[i], float(sims[i])) for i in top]

    def stats(self) -> dict:
        return {"loaded": self.loaded, "rows": self.n, "deleted_rows": self.dead,
                "ram_mb": round(self.mat.nbytes / 1e6, 1) if self.mat is not None else 0}


index = LocalIndex()


async def search(qv: np.ndarray, n: int, namespaces: list[str] | None) -> list[tuple]:
    """Returns [(chunk_id, cosine)] best first. namespaces=None searches everything."""
    if cfg.vector_backend == "atlas":
        stage = {
            "index": cfg.vector_index_name, "path": "embedding", "queryVector": qv.tolist(),
            "numCandidates": max(100, n * 10), "limit": n,
        }
        if namespaces is not None:
            stage["filter"] = {"namespace": {"$in": namespaces}}
        pipe = [{"$vectorSearch": stage}, {"$project": {"_id": 1, "score": {"$meta": "vectorSearchScore"}}}]
        return [(d["_id"], 2 * d["score"] - 1) async for d in db.chunks.aggregate(pipe)]
    await index.ensure_fresh(len(qv))
    return await asyncio.to_thread(index._search, qv, n, namespaces)


def on_insert(ids: list, vecs: np.ndarray, namespaces: list[str]) -> None:
    if cfg.vector_backend != "atlas":
        index.add(ids, vecs, namespaces)


async def on_delete(chunk_ids: list) -> None:
    if not chunk_ids:
        return
    if cfg.vector_backend != "atlas":
        index.remove(chunk_ids)
        now = utcnow()
        for s in range(0, len(chunk_ids), 5000):
            try:
                await db.rag_tombstones.insert_many(
                    [{"_id": i, "at": now} for i in chunk_ids[s:s + 5000]], ordered=False
                )
            except Exception:  # duplicates are fine
                pass
