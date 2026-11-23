# Version 2.1: files, Studio and your own AI

**Files from the chat**
- Ask for a PDF in plain words: "green theme mein leave policy ki PDF bana do", "iski PDF bana do", "poori chat ki PDF".
  6 PDF designs (classic, banner, minimal, sidebar, boxed, elegant), 1 or 2 colours named in English, Hinglish or Hindi.
- Ask for an image: "cartoon style mein office ki image banao", "laal aur sunehre rang mein logo". 12 styles, colours,
  shapes (poster, banner, story).
- Not happy? "koi aur", "3 design dikhao", "laal aur neela rang mein karo", "boxed design mein karo" remakes the last
  file. Each file keeps a recipe (content, colours, design) for this. Several designs come back side by side.
- Files are stored in MongoDB (`rag_files`), private to the API key that made them (`GET /files/{id}`).

**Your own AI first, online only as a backup**
- Own image AI on the server (Stable Diffusion Turbo via diffusers, `IMAGE_PROVIDER=local,pollinations`).
  Say "online" in the chat or choose it in Studio to use an online service instead.
- Own text AI: Ollama (Qwen 2.5 3B) as the first provider; Gemini and others stay optional backups.

**Studio (no AI at all)**
- Scan & read: text from PDFs, Word files and photos (OCR, English + Hindi), add it to your data in one click.
- Resume builder: reads an old resume into a form, 2 designs, any colour, optional photo.
- Poster maker: 6 colour themes, 4 sizes, left or centred layout, photo and logo, "Show me 4 designs". PNG + PDF.
- AI image: style, up to 3 colours, size, 1-4 designs, own AI or online.

# Version 2.0

**Connect your other systems**
- API keys per system (Connect apps tab, `/keys`): scopes, data groups (namespaces), own rate limit, rotate, switch off.
  Only hashes are stored. The admin token is now only for this UI.
- Data groups keep each system's data apart: a marketing key never sees staffing records, not even through the cache.
- Push API `/records` for instant updates from any system, plus `/sync/sources/{name}/notify` for MongoDB sources.

**Accuracy**
- Default embedding model is now multilingual (`intfloat/multilingual-e5-small`): Hindi and Hinglish questions find
  English records. Changing the model re-embeds existing data automatically.
- Match level is measured from your data (no more slider guessing). Manual mode still exists.
- Exact matching of IDs, phone numbers and emails; Hindi/Hinglish misses retried with an English rewrite;
  optional cross-encoder re-ranker; answers in the user's language; partial answers instead of guesses.
- Saved-answer cache can no longer return one person's answer for another person's question.

**Never stops**
- Grounded answers now stream (the first words appear at once, also on slow local Ollama).
- If every AI is down, the best matching records are returned instead of an error.
- Per-provider timeout (Ollama preset: 300 s). Cool-downs and rate limits are stored in MongoDB, shared by all workers.

**Big data**
- Bulk upload: several files at once; CSV, Excel, JSON, JSONL become one record per row; Word and HTML supported.
  Re-uploading a file updates it. Progress bars, background jobs stored in MongoDB.
- Batched embedding, incremental in-memory vector index (no full reload after every import), compact binary vectors,
  no 100k record cap.
- Sync: change streams (instant on Atlas/replica sets), `updated_field` incremental reads, other databases/servers,
  rare full scans only for deletions.

**History & security**
- Chat history saved per system and per end user (`user_id`); history sidebar in the UI.
- Safe MongoDB filters (allow-list of operators), secret-looking fields never indexed, upload size limit,
  security headers, brute-force blocking, `/docs` can be hidden, internal collections prefixed `rag_`.

**Deploy**
- `Dockerfile` + `docker-compose.yml` (UI + API + MongoDB replica set). The backend serves the built UI.

Upgrading from 1.x: your data, providers and settings are kept. If your `.env` still says
`EMBEDDING_MODEL=BAAI/bge-small-en-v1.5`, nothing is re-embedded; change it to `intfloat/multilingual-e5-small`
for Hindi/Hinglish and the app re-embeds in the background.
