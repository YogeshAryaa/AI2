"""Who is calling.

* ADMIN_TOKEN (from .env): full access. Use it only in this app's own UI.
* App API keys (rk_...): one per system/app (staffing, marketing, ...). Each key has scopes, the data
  namespaces it may read/write, and its own per-minute limit. Only a SHA-256 hash is stored.
"""
import hashlib
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request

from . import limits
from .config import cfg
from .db import db, utcnow
from .knowledge import DEFAULT_NS, clean_ns

SCOPES = ("chat", "search", "ingest", "history")


@dataclass(frozen=True)
class Principal:
    key_id: str
    name: str
    role: str                         # admin | app
    scopes: frozenset
    namespaces: tuple | None          # None = every namespace
    rpm: int = 0

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def can(self, scope: str) -> bool:
        return self.is_admin or scope in self.scopes

    def read_namespaces(self, requested: list[str] | None = None) -> list[str] | None:
        """Namespaces this call may search (None = all). A request can only narrow what the key allows."""
        try:
            req = [clean_ns(n) for n in requested] if requested else None
        except ValueError as e:
            raise HTTPException(400, str(e))
        if self.namespaces is None:
            return req
        if not req:
            return list(self.namespaces)
        allowed = [n for n in req if n in self.namespaces]
        if not allowed:
            raise HTTPException(403, "This API key cannot read those namespaces")
        return allowed

    def write_namespace(self, ns: str | None = None) -> str:
        try:
            ns = clean_ns(ns) if ns else (self.namespaces[0] if self.namespaces else DEFAULT_NS)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if self.namespaces is not None and ns not in self.namespaces:
            raise HTTPException(403, f"This API key cannot write to namespace '{ns}'")
        return ns


ADMIN = Principal("admin", "Admin", "admin", frozenset(SCOPES), None, 0)


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_key() -> tuple[str, str]:
    kid = secrets.token_hex(4)
    return kid, f"rk_{kid}_{secrets.token_urlsafe(30)}"


_cache: dict[str, tuple[Principal | None, float]] = {}


def forget_cached_keys() -> None:
    _cache.clear()


async def _lookup(token: str) -> Principal | None:
    if not token.startswith("rk_") or token.count("_") < 2:
        return None
    h = hash_key(token)
    now = time.time()
    hit = _cache.get(h)
    if hit and hit[1] > now:
        return hit[0]
    kid = token.split("_", 2)[1]
    doc = await db.rag_api_keys.find_one({"_id": kid})
    p = None
    if doc and doc.get("enabled", True) and secrets.compare_digest(doc["hash"], h):
        exp = doc.get("expires_at")
        if not exp or exp > utcnow():
            p = Principal(
                kid, doc["name"], "app", frozenset(doc.get("scopes", [])),
                tuple(doc.get("namespaces") or [DEFAULT_NS]), int(doc.get("rpm", cfg.default_key_rpm)),
            )
            if now - doc.get("last_used_ts", 0) > 60:
                await db.rag_api_keys.update_one({"_id": kid}, {"$set": {"last_used_ts": now, "last_used": utcnow()}})
    if len(_cache) > 5000:
        _cache.clear()
    _cache[h] = (p, now + 30)  # a revoked key stops working within 30 s on every worker
    return p


async def current(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Principal:
    token = x_api_key
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "Missing API key (send header X-API-Key or Authorization: Bearer)")
    ip = request.client.host if request.client else "unknown"
    if await limits.count(f"authfail:{ip}", 60) >= cfg.auth_fail_limit:
        raise HTTPException(429, "Too many wrong keys from your address. Wait a minute.", headers={"Retry-After": "60"})
    if secrets.compare_digest(token.encode(), cfg.admin_token.encode()):
        return ADMIN
    p = await _lookup(token)
    if not p:
        await limits.hit(f"authfail:{ip}", 60, 1 << 30)
        raise HTTPException(401, "Invalid or missing API key")
    if p.rpm > 0:
        ok, reset = await limits.hit(f"key:{p.key_id}", 60, p.rpm)
        if not ok:
            raise HTTPException(429, f"Rate limit of {p.rpm} requests/minute reached", headers={"Retry-After": str(reset)})
    return p


def require(scope: str):
    async def dep(p: Principal = Depends(current)) -> Principal:
        if not p.can(scope):
            raise HTTPException(403, f"This API key does not have the '{scope}' permission")
        return p

    return dep


async def require_admin(p: Principal = Depends(current)) -> Principal:
    if not p.is_admin:
        raise HTTPException(403, "Admin token required")
    return p
