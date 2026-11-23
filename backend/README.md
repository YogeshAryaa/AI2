# Backend: private RAG API with free-AI fallback
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU build, much smaller
pip install -r requirements.txt
cp environment.example .env # set MONGODB_URI, ADMIN_TOKEN, APP_SECRET_KEY
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000              # production: --workers 2, no --reload
```
Interactive docs: http://localhost:8000/docs (click **Authorize**, paste a key).

## How a question is answered

1. A follow-up is rewritten into a standalone question (only when there is history).
2. **Saved answer** (cache): only if the question means the same (≥ 0.97 similar *and* differs by filler words
   only, so "phone of Ravi" never returns Rahul's answer). Scoped per data group.
3. **Your data**: hybrid search (vectors + keywords, fused with RRF), optional re-ranker. IDs, phone numbers and
   emails in the question count as an exact match. A Hindi/Hinglish miss is retried once with an English rewrite.
4. If it matches well enough, an AI writes the answer **from those passages only**, streamed. It replies
   `NOT_FOUND` when the passages are irrelevant (never shown to the user) and we go to step 5.
5. **General AI** (if allowed): only the question is sent. If no AI is reachable at all, the best matching passages
   are returned instead of an error.

The match level is **measured automatically** from your data (`min_score_mode=auto`): unrelated questions in
English/Hindi/Hinglish set the noise floor, real passages set the signal, and the cut-off sits between. It is
re-measured when the data grows by 20%.

## Who can call what

| Caller | How | Can |
|---|---|---|
| Admin (this UI) | `ADMIN_TOKEN` | everything |
| App key `rk_...` | created in Connect apps / `POST /keys` | its scopes, its data groups (namespaces), its rate limit |

Scopes: `chat` (chat + history), `search`, `ingest` (add/update/delete data). Wrong-key attempts are blocked per IP
after `AUTH_FAIL_LIMIT`/minute. Limits and provider cool-downs live in MongoDB, so they hold across workers.

## Endpoints

| Purpose | Endpoint | Who |
|---|---|---|
| Chat (JSON / SSE stream) | `POST /chat`, `POST /chat/stream` | chat |
| Chat history | `GET /conversations`, `GET/PATCH/DELETE /conversations/{id}` | chat |
| Search only | `POST /search` | search |
| Push records from your systems | `POST /records`, `POST /records/delete`, `DELETE /records/{collection}/{id}` | ingest |
| Text / file (pdf docx txt md html csv tsv xlsx json jsonl) | `POST /ingest/text`, `POST /ingest/file` | ingest |
| Job progress | `GET /jobs/{id}` | ingest |
| Browse / delete | `GET /documents`, `DELETE /documents/{id}`, `POST /documents/delete-source` | ingest |
| Data groups | `GET /namespaces` | search |
| Instant sync ping | `POST /sync/sources/{name}/notify` | ingest |
| MongoDB sources | `GET/POST /sync/sources`, `PATCH/DELETE /sync/sources/{name}`, `POST .../run` | admin |
| One-time collection import | `GET /databases`, `GET /collections`, `GET /collections/{name}/sample`, `POST /ingest/collection` | admin |
| AI providers | `/providers...` | admin |
| API keys | `GET/POST /keys`, `PATCH/DELETE /keys/{id}`, `POST /keys/{id}/rotate` | admin |
| Settings / calibration / status | `GET/PUT /settings`, `POST /settings/calibrate`, `GET /status` | admin |

Collection filters accept plain query operators only (`$eq $gt $in $regex $and $or` ...); `$where`, `$expr`,
`$function` and anything else that could run code are rejected.

## Scaling notes

- **Embedding** is batched (hundreds of chunks per call) and runs off the event loop, so questions stay fast during
  a bulk import. `EMBEDDING_BACKEND=onnx` is about 2x faster on CPU.
- **Local vector index** is updated incrementally (no full reload after each import), stores vectors as compact
  binary in MongoDB, and filters by data group in memory. Worker processes stay in step through MongoDB.
  RAM ≈ chunks × 384 × 4 bytes (≈ 1.5 GB per million; `VECTOR_DTYPE=float16` halves it).
  Beyond a few million chunks use `VECTOR_BACKEND=atlas`.
- **Sync** prefers change streams (instant), then the `updated_field` (reads only changes), and runs a full
  reconcile scan only every 1-24 h to catch deletions. No record limit (`SYNC_MAX_RECORDS=0`).
- Background work (sync, re-embedding, calibration) runs on one worker at a time (a MongoDB lease lock).
- App collections use the `rag_` prefix so they never clash with your own collections (e.g. a `jobs` collection).
