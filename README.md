# Ask my data: your own free AI over your company data

Answers questions (English, Hindi, Hinglish) from **your data first**: MongoDB collections, files, and records your
other systems push in. If the answer is not there, a chain of **free AI APIs** (Groq, Gemini, OpenRouter, Ollama...)
takes over with automatic failover. Each of your systems (staffing, marketing, ...) connects with its own API key.

```
rag-app/
  backend/    FastAPI + MongoDB + local multilingual embeddings + provider failover router
  frontend/   React UI: chat with history, data import, AI providers, API keys
  docs/INTEGRATION.md   how your other systems call the API
```

## Run on your PC (development)

Needs: Python 3.10+, Node 18+, a MongoDB (local, or a free Atlas cluster).

```bash
./run-backend.sh          # Windows: run-backend.bat   (first run creates backend/.env and stops)
```
Edit `backend/.env`: `MONGODB_URI`, `ADMIN_TOKEN` and `APP_SECRET_KEY` (two different long random strings:
`python -c "import secrets; print(secrets.token_urlsafe(32))"`). Run it again; the first start downloads the
embedding model (~470 MB).

```bash
./run-frontend.sh         # Windows: run-frontend.bat   -> http://localhost:5173
```
In the app: *Backend connection* → `http://localhost:8000` + your `ADMIN_TOKEN` → Connect.

## Ready-made knowledge base (works without any AI)

`backend/seed_data/*.json` holds ~100 ready answers for staffing, recruitment, marketing, sales, HR and the app
itself. Load them (backend running): `cd backend && .venv\Scripts\python seed_data.py`. Edit the answers (especially
`hr_policies.json` and the company templates in `general.json`) or add new files with the same shape, then run it
again: items have fixed ids, so changes update instead of duplicating.

Without any AI provider the app still replies: greetings are answered instantly, questions that clearly match your
data get the stored answer, anything else gets a polite "not in my data" reply. With providers added, they are
tried in order (top first); one that errors or hits its limit is paused and the next is used automatically.

## Deploy on a server (one command)

```bash
cp backend/environment.example backend/.env    # set MONGODB_URI, ADMIN_TOKEN, APP_SECRET_KEY, DOCS_ENABLED=false
docker compose up -d --build            # UI + API on port 8000, MongoDB with live sync included
```
For the bundled MongoDB, set `MONGODB_URI=mongodb://mongo:27017/?replicaSet=rs0` in `backend/.env`.
For MongoDB Atlas, set `MONGODB_URI` there to your Atlas connection string; do not commit `backend/.env`.
The first app start downloads the embedding model (about 470 MB); OCR models download the first time OCR is used.
Docker keeps these downloads in the `model-cache` volume instead of embedding them in the image.
Put HTTPS in front (e.g. Caddy: `ai.yourcompany.com { reverse_proxy localhost:8000 }`). Without Docker:
`cd frontend && npm run build`, then run uvicorn as in `run-backend.sh` (without `--reload`, with `--workers 2`);
the backend serves the built UI itself.

A 2 vCPU / 4 GB RAM VPS handles a few hundred thousand records comfortably. For millions, use MongoDB Atlas and
`VECTOR_BACKEND=atlas`.

## First steps in the app

1. **AI providers**: add free APIs. Put a fast one first (Groq or Gemini), Ollama last as a private backup.
2. **My data**: choose a data group (e.g. `staffing`), then upload files (CSV/Excel for bulk), or connect a MongoDB collection.
3. **Connect apps**: create one API key per system. Copy-paste code is shown. See `docs/INTEGRATION.md`.
4. **Ask**.

## Good to know

- **Privacy**: provider keys are encrypted; app keys are stored only as hashes. Passages from your data go to the
  AI provider that writes the answer. To keep them on your own machine, use Ollama (marked "local") and tick
  *Keep my data on local providers only* in My data → Settings. The general-AI fallback only receives the question.
- **Local Ollama is slow on CPU**: use a small model (`qwen2.5:3b` is good at Hindi), set `OLLAMA_KEEP_ALIVE=24h`
  so it stays loaded, and keep a free cloud provider above it. Answers stream word by word either way.
- **Changing the embedding model** re-embeds everything in the background by itself (progress in My data → Settings).
- More: `backend/README.md` (how answers are chosen, all endpoints), `CHANGES.md`.
