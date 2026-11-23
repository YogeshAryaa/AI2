from .config import cfg
from .db import db


def _defaults() -> dict:
    return {
        "min_score_mode": cfg.min_score_mode if cfg.min_score_mode in ("auto", "manual") else "auto",
        "min_score": cfg.min_score,
        "top_k": cfg.top_k,
        "cache_enabled": cfg.cache_enabled,
        "cache_threshold": cfg.cache_threshold,
        "send_context_to_external": cfg.send_context_to_external,
        "max_context_chars": cfg.max_context_chars,
        "query_rewrite": cfg.query_rewrite,
        "ai_fallback": True,
    }


async def get_runtime() -> dict:
    doc = await db.settings.find_one({"_id": "runtime"}) or {}
    s = {k: doc.get(k, v) for k, v in _defaults().items()}
    cal = await db.settings.find_one({"_id": "calibration"}) or {}
    s["calibration"] = {k: cal.get(k) for k in ("ok", "min_score", "ai_min_score", "noise", "signal", "chunks", "at", "reason")} if cal else None
    if s["min_score_mode"] == "auto" and cal.get("ok"):
        s["effective_min_score"] = cal["min_score"]          # no-AI search: strict
        s["effective_ai_min_score"] = cal["ai_min_score"]    # AI double-checks, so it may look a little wider
    else:
        s["effective_min_score"] = s["min_score"]
        s["effective_ai_min_score"] = max(0.0, s["min_score"] - 0.05)
    return s


async def update_runtime(patch: dict) -> dict:
    patch = {k: v for k, v in patch.items() if k in _defaults() and v is not None}
    if patch:
        await db.settings.update_one({"_id": "runtime"}, {"$set": patch}, upsert=True)
    return await get_runtime()
