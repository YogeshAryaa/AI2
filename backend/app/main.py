import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import background, embeddings, vectors
from .config import cfg
from .db import init_indexes
from .routes_chat import router as chat_router
from .routes_keys import router as keys_router
from .routes_knowledge import router as knowledge_router
from .routes_providers import router as providers_router
from .routes_records import router as records_router
from .routes_studio import router as studio_router
from .routes_sync import router as sync_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rag")
VERSION = "2.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if len(cfg.admin_token) < 24 or cfg.admin_token.startswith("change-me"):
        log.warning("ADMIN_TOKEN is short or the example value. Use a long random string before going live.")
    dim = await asyncio.to_thread(embeddings.dim)  # loads the embedding model once
    await init_indexes(dim)
    await background.migrate()
    if cfg.vector_backend != "atlas":
        asyncio.create_task(vectors.index.ensure_fresh(dim))  # warm the vector index in the background
    bg = asyncio.create_task(background.loop())
    yield
    bg.cancel()
    try:
        await bg
    except (asyncio.CancelledError, Exception):
        pass


app = FastAPI(
    title="Ask my data: private RAG API", version=VERSION, lifespan=lifespan,
    docs_url="/docs" if cfg.docs_enabled else None, redoc_url=None,
    openapi_url="/openapi.json" if cfg.docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cfg.cors_origins.split(",") if o.strip()],
    # the dev UI may start on 5174, 5175... when 5173 is busy; any port on this machine is fine
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)


@app.middleware("http")
async def guard(request: Request, call_next):
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > (cfg.max_upload_mb + 2) * 1024 * 1024:
        return JSONResponse({"detail": f"Request too large (max {cfg.max_upload_mb} MB)"}, status_code=413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "version": VERSION, "reindexing": background.reindexing()}


app.include_router(providers_router)
app.include_router(knowledge_router)
app.include_router(sync_router)
app.include_router(records_router)
app.include_router(keys_router)
app.include_router(chat_router)
app.include_router(studio_router)

# Serve the built frontend (npm run build) from the same address, so one server is all you deploy.
_dist = (Path(__file__).resolve().parent.parent / cfg.frontend_dist).resolve()
if cfg.serve_frontend and (_dist / "index.html").exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
