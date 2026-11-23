"""API keys for your other systems. Admin only. The full key is shown once, at creation."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import SCOPES, forget_cached_keys, hash_key, new_key, require_admin
from .config import cfg
from .db import db, utcnow
from .knowledge import clean_ns

router = APIRouter(prefix="/keys", tags=["api keys"], dependencies=[Depends(require_admin)])


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scopes: list[str] = ["chat", "search", "history"]
    namespaces: list[str] = ["default"]
    rpm: int | None = Field(default=None, ge=0, le=100000)  # 0 = no limit
    expires_days: int | None = Field(default=None, ge=1, le=3650)


class KeyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    scopes: list[str] | None = None
    namespaces: list[str] | None = None
    rpm: int | None = Field(default=None, ge=0, le=100000)
    enabled: bool | None = None


def _check(scopes: list[str] | None, namespaces: list[str] | None) -> tuple[list[str] | None, list[str] | None]:
    if scopes is not None:
        bad = [s for s in scopes if s not in SCOPES]
        if bad:
            raise HTTPException(400, f"Unknown scope(s): {bad}. Allowed: {list(SCOPES)}")
        scopes = sorted(set(scopes))
    if namespaces is not None:
        try:
            namespaces = list(dict.fromkeys(clean_ns(n) for n in namespaces))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not namespaces:
            raise HTTPException(400, "Give the key at least one namespace")
    return scopes, namespaces


def _public(d: dict) -> dict:
    return {
        "id": d["_id"], "name": d["name"], "prefix": d.get("prefix"), "scopes": d.get("scopes", []),
        "namespaces": d.get("namespaces", []), "rpm": d.get("rpm", 0), "enabled": d.get("enabled", True),
        "created_at": d.get("created_at"), "last_used": d.get("last_used"), "expires_at": d.get("expires_at"),
    }


@router.get("")
async def list_keys():
    return [_public(d) async for d in db.rag_api_keys.find().sort("created_at", -1)]


@router.post("", status_code=201)
async def create_key(body: KeyIn):
    scopes, namespaces = _check(body.scopes, body.namespaces)
    kid, token = new_key()
    doc = {
        "_id": kid, "name": body.name, "prefix": token[:11], "hash": hash_key(token), "scopes": scopes,
        "namespaces": namespaces, "rpm": cfg.default_key_rpm if body.rpm is None else body.rpm, "enabled": True,
        "created_at": utcnow(),
        "expires_at": utcnow() + timedelta(days=body.expires_days) if body.expires_days else None,
    }
    await db.rag_api_keys.insert_one(doc)
    return {**_public(doc), "key": token, "note": "Copy this key now. It is not shown again."}


@router.patch("/{kid}")
async def update_key(kid: str, body: KeyPatch):
    patch = body.model_dump(exclude_none=True)
    scopes, namespaces = _check(patch.get("scopes"), patch.get("namespaces"))
    if scopes is not None:
        patch["scopes"] = scopes
    if namespaces is not None:
        patch["namespaces"] = namespaces
    if patch:
        await db.rag_api_keys.update_one({"_id": kid}, {"$set": patch})
    doc = await db.rag_api_keys.find_one({"_id": kid})
    if not doc:
        raise HTTPException(404, "Key not found")
    forget_cached_keys()
    return _public(doc)


@router.post("/{kid}/rotate")
async def rotate_key(kid: str):
    """Issues a new secret for the same key (same permissions). The old secret stops working."""
    doc = await db.rag_api_keys.find_one({"_id": kid})
    if not doc:
        raise HTTPException(404, "Key not found")
    token = f"rk_{kid}_{new_key()[1].split('_', 2)[2]}"
    await db.rag_api_keys.update_one({"_id": kid}, {"$set": {"hash": hash_key(token), "prefix": token[:11]}})
    forget_cached_keys()
    return {**_public({**doc, "prefix": token[:11]}), "key": token, "note": "Copy this key now. It is not shown again."}


@router.delete("/{kid}")
async def delete_key(kid: str):
    res = await db.rag_api_keys.delete_one({"_id": kid})
    if not res.deleted_count:
        raise HTTPException(404, "Key not found")
    forget_cached_keys()
    return {"deleted": True}
