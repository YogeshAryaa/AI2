# Connecting your systems (staffing, marketing, ...) to Ask-my-data

Your AI runs on your own server. Each of your systems talks to it over HTTPS with its **own API key**.
Users of those systems pay nothing: answers come from your data plus the free AI providers you configured.

## 1. Get a key (once per system)

In the Ask-my-data UI: **Connect apps → New API key**

| Field | Example | Meaning |
|---|---|---|
| Name | `Staffing portal` | For you to recognise it |
| Data groups | `staffing` | The only data this key can read and write. Use `staffing,shared` to give it two groups |
| Permissions | Ask, Search, Add data | `chat`, `search`, `ingest` |
| Requests/minute | `120` | Per-key limit, shared across all server workers |

The key (`rk_...`) is shown **once**. Store it as a server environment variable (`RAG_API_KEY`) in that system.

> **Never put the key in browser/JavaScript code that users download.** Call this API from your system's backend,
> and let your frontend talk to your backend. That keeps the key secret and lets you check the user first.

Send it on every request as `X-API-Key: rk_...` (or `Authorization: Bearer rk_...`).

## 2. Ask a question

```http
POST /chat
Content-Type: application/json
X-API-Key: rk_...

{
  "question": "Pune me 5 saal experience wale Java developers kaun hai?",
  "user_id": "42",                 // your end-user's id: their chats stay private to them
  "conversation_id": null,         // send the id you got back to continue the same chat
  "mode": "ai",                    // "search" = no AI, instant list of matching records
  "allow_ai_fallback": true        // false = answer only from your data, never general AI
}
```

Response:

```json
{
  "answer": "Pune me 2 Java developers hai: Ravi Kumar [1] ...",
  "source": "database",            // database | cache | ai | search | none
  "provider": "Groq", "model": "llama-3.3-70b-versatile",
  "citations": [{"index": 1, "doc_id": "rec:staffing:candidates:c1", "title": "Ravi Kumar", "score": 0.88, "snippet": "..."}],
  "conversation_id": "9f2c...",
  "confidence": 0.881
}
```

Streaming (shows words as they arrive): `POST /chat/stream` with the same body. It returns Server-Sent Events,
each line `data: {json}` with `type` = `meta` | `token` | `error` | `done`.

Chat history: `GET /conversations?user_id=42`, `GET /conversations/{id}?user_id=42`, `DELETE /conversations/{id}?user_id=42`.

## 3. Keep the AI's data up to date (pick one)

### A. Push records from your code (recommended, instant, any database)

Call this after you save a record (create or update). Up to 1000 records per call; unchanged ones are skipped.

```http
POST /records
{
  "collection": "candidates",
  "records": [
    {"id": "c1", "title": "Ravi Kumar",
     "fields": {"name": "Ravi Kumar", "skills": "Java, Spring", "city": "Pune", "experience_years": 5}},
    {"id": "c2", "text": "Free text works too: Anita Sharma, React developer in Delhi."}
  ]
}
```

Deleted in your system? `DELETE /records/candidates/c1` (or `POST /records/delete` with `{"collection": "candidates", "ids": [...]}`).

First-time bulk load: loop over your table in pages of 500-1000 and call `POST /records` for each page.
More than 100 changed records in one call run in the background; the response has a `job_id` to poll with `GET /jobs/{id}`.

### B. Your system already uses MongoDB

The admin connects the collection in **My data → MongoDB (auto-sync)**. Then:
- On **MongoDB Atlas or a replica set**, changes arrive within ~1 second (change streams).
- Otherwise it checks every few minutes. Choose the **"Last updated" field** (e.g. `updatedAt`) so only changed
  records are read, not the whole collection.
- For instant updates without a replica set, ping it after saving:
  `POST /sync/sources/{collection}/notify` with `{"ids": ["<_id>", ...]}` (needs the `ingest` permission on that group).

Fields named like `password`, `token`, `secret`, `otp`, `hash`, `aadhaar`, `pan` are never indexed unless picked explicitly.

### C. Files

`POST /ingest/file` (multipart: `file`, optional `namespace`, `title_field`). PDF, Word, text, HTML,
or CSV / Excel / JSON / JSONL for bulk (one record per row). Same file name again = update, not duplicate.

## 4. Search without AI

```http
POST /search
{"query": "React developer Delhi", "top_k": 5}
```
Returns the matching passages with scores and `found: true/false`. Fast and uses no AI quota.

## 5. PDFs and images

### From the chat (just ask in words)

Send a normal `/chat` (or `/chat/stream`) question such as `"Java developers ki list ki PDF bana do"` or
`"cartoon style me bank ki image banao"`. The answer then has a `files` list (in streaming, a `file` event):

```json
{"answer": "...", "conversation_id": "9f2c...",
 "files": [{"id": "ab12...", "kind": "pdf", "name": "java-developers.pdf", "mime": "application/pdf",
            "size": 48211, "url": "/files/ab12...", "label": "Classic"}]}
```

With the same `conversation_id`, follow-ups change the last file: `"koi aur"` (another design / style),
`"4 design dikhao"`, `"laal aur neela rang mein karo"`, `"boxed design mein karo"`.

Download the bytes: `GET /files/{id}?user_id=42` with the same key (add `&download=true` for an attachment).
Only the key and `user_id` that created a file can read it, so **serve it to your users through your backend**
(fetch it there and pass the bytes on), not with a direct link that would need the key.

### Studio endpoints (no chat needed)

| Endpoint | Body | Gives |
|---|---|---|
| `POST /studio/read` | multipart `file` (PDF, Word, image) | Text (OCR for images / scanned PDFs) + guessed resume fields |
| `POST /studio/resume` | multipart `data` = JSON (`name`, `headline`, `email`, `phone`, `location`, `links`, `summary`, `skills`, `sections: [{heading, body}]`, `template`: classic\|modern, `accent`: `#2563eb`), optional `photo` | Resume PDF |
| `POST /studio/poster` | multipart `data` = JSON (`size`: square\|story\|a4\|landscape, `theme`, `headline`, `subheadline`, `details`, `footer`, `tag`, `layout`: left\|center), optional `background`, `logo` | Poster PNG + PDF |
| `POST /studio/image` | JSON `{"prompt": "...", "style": "auto", "colors": ["blue"], "size": "square", "provider": "auto", "count": 1}` | 1-4 AI images |
| `GET /studio/options` | | Allowed themes, styles, colours, sizes |

Every one returns `{"files": [...]}` in the same shape as above. They need the `chat` permission.

**Timeouts:** an image from the server's own AI takes about 1.5-3 minutes, and OCR about 40 seconds per page.
Set your HTTP client timeout to at least 5 minutes for `/studio/image`, `/studio/read` and chat image requests.

## Errors

| Code | Meaning | What to do |
|---|---|---|
| 401 | Missing / wrong / revoked key | Check `RAG_API_KEY` |
| 403 | Key lacks the permission or data group | Edit the key in Connect apps |
| 404 | Conversation or record not found (or belongs to someone else) | |
| 413 | Upload too big | Split it, or raise `MAX_UPLOAD_MB` |
| 429 | Rate limit | Wait `Retry-After` seconds; raise the key's limit if needed |

If every AI provider is down, chat still answers with the best matching records from your data instead of failing.

Interactive reference with every field: `https://YOUR_SERVER/docs` (when `DOCS_ENABLED=true`).
