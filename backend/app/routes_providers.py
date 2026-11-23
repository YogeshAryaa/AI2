from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import llm
from .db import db
from .auth import require_admin
from .security import decrypt, encrypt, mask

router = APIRouter(prefix="/providers", tags=["providers"], dependencies=[Depends(require_admin)])

# Starting points only: model names change often. `model` can hold several models separated by commas;
# they are tried in order, so a retired model never breaks the provider. Use GET /providers/{id}/models to see what is live.
PRESETS = [
    {"key": "gemini", "name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model_hint": "gemini-3.8-flash, gemini-3.5-flash, gemini-3.1-flash-lite", "trusted": False, "key_url": "https://aistudio.google.com/apikey"},
    {"key": "groq", "name": "Groq", "base_url": "https://api.groq.com/openai/v1",
     "model_hint": "llama-3.3-70b-versatile", "trusted": False, "key_url": "https://console.groq.com/keys"},
    {"key": "openrouter", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "model_hint": "meta-llama/llama-3.3-70b-instruct:free", "trusted": False, "key_url": "https://openrouter.ai/keys"},
    {"key": "mistral", "name": "Mistral", "base_url": "https://api.mistral.ai/v1",
     "model_hint": "mistral-small-latest", "trusted": False, "key_url": "https://console.mistral.ai/api-keys"},
    {"key": "cerebras", "name": "Cerebras", "base_url": "https://api.cerebras.ai/v1",
     "model_hint": "llama3.1-8b", "trusted": False, "key_url": "https://cloud.cerebras.ai"},
    # Local = private and free, but slow on CPU. qwen2.5:3b handles Hindi/Hinglish well; keep it small.
    {"key": "ollama", "name": "Ollama (local)", "base_url": "http://localhost:11434/v1",
     "model_hint": "qwen2.5:3b, llama3.2:3b", "trusted": True, "key_url": None, "timeout_seconds": 300},
    {"key": "custom", "name": "Custom OpenAI-compatible", "base_url": "", "model_hint": "", "trusted": False, "key_url": None},
]


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    base_url: str
    model: str = Field(min_length=1)
    api_key: str = ""
    priority: int = 100
    enabled: bool = True
    trusted: bool = False
    rpm_limit: int = Field(default=0, ge=0)
    rpd_limit: int = Field(default=0, ge=0)
    timeout_seconds: int = Field(default=0, ge=0, le=900)  # 0 = default (60 s); local models need more
    extra_headers: dict[str, str] = {}


class ProviderPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # None = keep the existing key
    priority: int | None = None
    enabled: bool | None = None
    trusted: bool | None = None
    rpm_limit: int | None = Field(default=None, ge=0)
    rpd_limit: int | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, ge=0, le=900)
    extra_headers: dict[str, str] | None = None


def _clean_url(url: str) -> str:
    url = url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "base_url must start with http:// or https://")
    return url


def _oid(pid: str) -> ObjectId:
    try:
        return ObjectId(pid)
    except InvalidId:
        raise HTTPException(404, "Provider not found")


def _public(p: dict, states: dict) -> dict:
    pid = str(p["_id"])
    key = decrypt(p.get("api_key_enc", "")) if p.get("api_key_enc") else ""
    return {
        "id": pid, "name": p["name"], "base_url": p["base_url"], "model": p["model"],
        "api_key_masked": mask(key), "has_key": bool(key), "priority": p.get("priority", 100),
        "enabled": p.get("enabled", True), "trusted": p.get("trusted", False),
        "rpm_limit": p.get("rpm_limit", 0), "rpd_limit": p.get("rpd_limit", 0),
        "timeout_seconds": p.get("timeout_seconds", 0),
        "extra_headers": p.get("extra_headers", {}), "stats": p.get("stats", {}),
        "status": llm.provider_status(p, states), "created_at": p.get("created_at"),
    }


async def _get(pid: str) -> dict:
    p = await db.providers.find_one({"_id": _oid(pid)})
    if not p:
        raise HTTPException(404, "Provider not found")
    p["api_key"] = decrypt(p.get("api_key_enc", "")) if p.get("api_key_enc") else ""
    return p


@router.get("/presets")
async def presets():
    return PRESETS


@router.get("")
async def list_providers():
    docs = await db.providers.find().sort("priority", 1).to_list(100)
    states = await llm.load_states()
    return [_public(p, states) for p in docs]


@router.post("", status_code=201)
async def add_provider(body: ProviderIn):
    doc = {
        "name": body.name, "base_url": _clean_url(body.base_url), "model": body.model.strip(),
        "api_key_enc": encrypt(body.api_key.strip()) if body.api_key.strip() else "",
        "priority": body.priority, "enabled": body.enabled, "trusted": body.trusted,
        "rpm_limit": body.rpm_limit, "rpd_limit": body.rpd_limit, "timeout_seconds": body.timeout_seconds,
        "extra_headers": body.extra_headers, "stats": {"success": 0, "failure": 0},
        "created_at": datetime.now(timezone.utc),
    }
    res = await db.providers.insert_one(doc)
    doc["_id"] = res.inserted_id
    return _public(doc, {})


@router.patch("/{pid}")
async def update_provider(pid: str, body: ProviderPatch):
    await _get(pid)
    patch = body.model_dump(exclude_none=True)
    if "base_url" in patch:
        patch["base_url"] = _clean_url(patch["base_url"])
    if "api_key" in patch:
        key = patch.pop("api_key").strip()
        if key:
            patch["api_key_enc"] = encrypt(key)
    if patch:
        await db.providers.update_one({"_id": _oid(pid)}, {"$set": patch})
    return _public(await _get(pid), await llm.load_states([pid]))


@router.delete("/{pid}")
async def delete_provider(pid: str):
    res = await db.providers.delete_one({"_id": _oid(pid)})
    if not res.deleted_count:
        raise HTTPException(404, "Provider not found")
    await db.rag_provider_state.delete_many({"pid": pid})
    return {"deleted": True}


@router.post("/{pid}/test")
async def test(pid: str):
    return await llm.test_provider(await _get(pid))


@router.get("/{pid}/models")
async def models(pid: str):
    try:
        return {"models": await llm.list_models(await _get(pid))}
    except Exception as e:
        raise HTTPException(502, f"Could not list models: {type(e).__name__}")
