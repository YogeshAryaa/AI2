import asyncio
import logging
import re
import threading

import numpy as np

from .config import cfg

log = logging.getLogger("rag.embeddings")

_model = None
_reranker = None
_load_lock = threading.Lock()

# Prefixes the model family was trained with. Using the wrong ones costs a lot of accuracy.
_KNOWN_PREFIXES = [
    ("e5", "query: ", "passage: "),
    ("bge-small-en", "Represent this sentence for searching relevant passages: ", ""),
    ("bge-base-en", "Represent this sentence for searching relevant passages: ", ""),
    ("bge-large-en", "Represent this sentence for searching relevant passages: ", ""),
]


def _prefix(value: str, kind: int) -> str:
    if value.lower() == "none":
        return ""
    if value:
        return value
    name = cfg.embedding_model.lower()
    for key, q, d in _KNOWN_PREFIXES:
        if key in name:
            return q if kind == 0 else d
    return ""


QUERY_PREFIX = _prefix(cfg.query_prefix, 0)
DOC_PREFIX = _prefix(cfg.doc_prefix, 1)


def fingerprint() -> str:
    """Identifies how stored vectors were produced. If it changes, everything is re-embedded."""
    return f"{cfg.embedding_model}|{DOC_PREFIX}"


def _load():
    global _model
    if _model is None:
        with _load_lock:
            if _model is None:
                if cfg.embedding_threads > 0:
                    import torch

                    torch.set_num_threads(cfg.embedding_threads)
                from sentence_transformers import SentenceTransformer

                if cfg.embedding_backend == "onnx":
                    try:
                        _model = SentenceTransformer(cfg.embedding_model, backend="onnx")
                        log.info("Embedding model loaded with the ONNX backend")
                    except Exception as e:  # optimum not installed or model cannot be exported
                        log.warning("ONNX backend unavailable (%s); using torch", e)
                if _model is None:
                    _model = SentenceTransformer(cfg.embedding_model)
    return _model


def dim() -> int:
    m = _load()
    get = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
    return int(get())


def _encode(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, dim()), np.float32)
    return _load().encode(
        texts, normalize_embeddings=True, batch_size=cfg.embedding_batch_size, show_progress_bar=False
    ).astype(np.float32)


async def embed_documents(texts: list[str]) -> np.ndarray:
    """Embeds in slices so a big import never blocks questions for long."""
    out = []
    step = max(cfg.embedding_batch_size * 4, 64)
    for i in range(0, len(texts), step):
        out.append(await asyncio.to_thread(_encode, [DOC_PREFIX + t for t in texts[i:i + step]]))
    return np.vstack(out) if out else np.zeros((0, dim()), np.float32)


async def embed_query(text: str) -> np.ndarray:
    out = await asyncio.to_thread(_encode, [QUERY_PREFIX + text])
    return out[0]


async def embed_queries(texts: list[str]) -> np.ndarray:
    return await asyncio.to_thread(_encode, [QUERY_PREFIX + t for t in texts])


# ------------------------------------------------------------ optional re-ranker
def _load_reranker():
    global _reranker
    if _reranker is None and cfg.reranker_model:
        with _load_lock:
            if _reranker is None:
                from sentence_transformers import CrossEncoder

                _reranker = CrossEncoder(cfg.reranker_model, max_length=512)
    return _reranker


def reranker_enabled() -> bool:
    return bool(cfg.reranker_model)


async def rerank(query: str, texts: list[str]) -> list[float]:
    model = _load_reranker()
    if model is None or not texts:
        return []
    scores = await asyncio.to_thread(model.predict, [(query, t) for t in texts], show_progress_bar=False)
    return [float(s) for s in scores]


# ------------------------------------------------------------ chunking
def split_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    size = size or cfg.chunk_chars
    overlap = min(overlap if overlap is not None else cfg.chunk_overlap, size // 2)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    # "।" is the Hindi full stop
    sentences = re.split(r"(?<=[.!?।])\s+|\n{2,}", text)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        while len(s) > size:  # very long sentence: hard split
            if cur:
                chunks.append(" ".join(cur))
                cur, cur_len = [], 0
            chunks.append(s[:size])
            s = s[size - overlap:]
        if cur and cur_len + len(s) + 1 > size:
            chunks.append(" ".join(cur))
            tail: list[str] = []
            length = 0
            for prev in reversed(cur):
                if length + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                length += len(prev)
            cur = tail
            cur_len = sum(len(x) + 1 for x in cur)
        cur.append(s)
        cur_len += len(s) + 1
    if cur:
        chunks.append(" ".join(cur))
    return chunks
