import logging

from . import knowledge, llm, offline
from .embeddings import embed_query
from .runtime_settings import get_runtime

log = logging.getLogger("rag.chat")

GROUNDED_SYSTEM = (
    "You are the assistant for a company's internal data. Answer the QUESTION using ONLY the numbered "
    "CONTEXT passages.\n\n"
    "FORMATTING RULES — always follow these:\n"
    "- Structure your answer with markdown: use **bold** for key names/values, `code` for IDs/emails/numbers, "
    "bullet lists (- item) for multiple items, and ## headings only when the answer has 3+ distinct sections.\n"
    "- Start directly with the answer. No preamble like 'Based on the context...' or 'According to passage...'.\n"
    "- For a single-fact answer (name, phone, date, amount): one short sentence or value, then the citation.\n"
    "- For a list of items: use a clean bullet list, one item per line.\n"
    "- For a comparison or multi-topic answer: use ## subheadings, then bullets under each.\n"
    "- Cite the passages you used inline like [1] or [2] after the relevant sentence.\n"
    "- Copy names, numbers, dates, amounts, emails and IDs exactly as written in the context.\n"
    "- Reply in the same language and style as the question (English, Hindi or Hinglish).\n"
    "- Answer exactly what was asked, nothing more. Ignore passages about other topics.\n"
    "- If the context answers only part of the question, give that part and clearly state what is missing.\n"
    "- Never use outside knowledge and never guess.\n"
    "- If the context has nothing relevant to the question, reply with exactly: NOT_FOUND"
)
FALLBACK_SYSTEM = (
    "You are a helpful, accurate assistant for a staffing and IT recruitment company (developers, QA, DevOps, "
    "data and other tech roles), also covering marketing, sales and HR.\n\n"
    "FORMATTING RULES — always follow these:\n"
    "- Structure your answer with markdown: use **bold** for key terms/names, bullet lists for multiple items, "
    "numbered lists for steps/sequences, and ## headings only when the answer has 3+ distinct sections.\n"
    "- Start directly with the answer. No preamble like 'Great question!' or 'Certainly!'.\n"
    "- For a single-fact or short answer: 1–2 sentences, plain and direct.\n"
    "- For a list of items or options: clean bullet list (- item), one per line.\n"
    "- For step-by-step answers: numbered list (1. step).\n"
    "- For detailed multi-topic answers: ## subheadings, then bullets under each.\n"
    "- Keep the same language and style as the user (English, Hindi or Hinglish).\n"
    "- If the question is about specific private records (a particular employee, candidate, client, order, "
    "invoice or internal document) you have no data for, say it was not found in the company data instead of inventing details."
)
CHAT_SYSTEM = (
    "You are a friendly assistant inside a staffing & IT recruitment company's internal tool (it also knows "
    "the company's marketing, sales and HR data). The user is making small talk (a greeting, thanks, bye...). "
    "Reply like a warm, natural human colleague in 1-2 short sentences, in the same language and style as the "
    "user (English, Hindi or Hinglish), matching their tone. Do not list your features unless asked; at most, "
    "ask casually what they need."
)
CONDENSE_SYSTEM = (
    "Rewrite the user's last message as a single standalone question, using the conversation "
    "for missing context. Keep the user's language. If it is already standalone, return it unchanged. "
    "Output only the question, nothing else."
)
REWRITE_SYSTEM = (
    "Turn the user's question (it may be Hindi, Hinglish or English) into a short English search query "
    "for a company database. Keep names, numbers and IDs exactly as written. Output only the query."
)

DOC_STYLE = (
    "\n\nThe user will download this answer as a PDF document. Write it as a complete, well-organised document: "
    "start with a '# ' title line, then short '## ' sections, bullet points and, where it helps, markdown tables. "
    "Use **bold** for key terms, `code` for IDs/emails/technical values. "
    "Be thorough but do not pad. Do not say that you cannot create files."
)

SEARCH_MAX_RESULTS = 3    # max chunks passed to AI as context (keep tight)
SEARCH_DISPLAY_MAX = 15   # fast search: max unique records to show to the user


def _citations(chunks: list[dict]) -> list[dict]:
    return [
        {
            "index": i + 1, "doc_id": c["doc_id"], "title": c.get("title"), "source": c.get("source"),
            "namespace": c.get("namespace"), "chunk_index": c.get("chunk_index"),
            "score": round(c["cosine"], 3), "exact_match": c.get("exact", False), "snippet": c["text"][:300],
        }
        for i, c in enumerate(chunks)
    ]


def _context(chunks: list[dict], max_chars: int) -> str:
    parts, used = [], 0
    for i, c in enumerate(chunks):
        piece = f"[{i + 1}] ({c.get('title')}) {c['text']}"
        if parts and used + len(piece) > max_chars:
            break
        parts.append(piece[: max_chars])
        used += len(piece)
    return "\n\n".join(parts)


async def _ask(system: str, user: str, trusted_only: bool, max_tokens: int = 150) -> str | None:
    try:
        res = await llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                             require_trusted=trusted_only, temperature=0, max_tokens=max_tokens)
        return res.text.strip().strip('"').strip() or None
    except llm.AllProvidersFailed:
        return None


async def _condense(question: str, history: list[dict], trusted_only: bool) -> str:
    if not history:
        return question
    transcript = "\n".join(f"{m['role']}: {m['content'][:1000]}" for m in history[-6:])
    out = await _ask(CONDENSE_SYSTEM, f"{transcript}\nuser: {question}", trusted_only)
    return out if out and len(out) < 1000 else question


def _meta(source: str, q: str, confidence: float, citations=None, provider=None, model=None, **extra) -> dict:
    return {"type": "meta", "source": source, "provider": provider, "model": model,
            "citations": citations or [], "standalone_question": q, "confidence": round(confidence, 3), **extra}


def _best_matches(chunks: list[dict], min_score: float) -> list[dict]:
    good = sorted((c for c in chunks if c.get("exact") or c["cosine"] >= min_score),
                  key=lambda c: (c.get("exact", False), c["cosine"]), reverse=True)
    if good and not good[0].get("exact"):
        best = good[0]["cosine"]
        margin = max(0.02, min(0.08, (best - min_score) * 0.5 + 0.02))
        good = [c for c in good if c["cosine"] >= best - margin]
    return good[:SEARCH_MAX_RESULTS]


async def _search_only(q: str, s: dict, namespaces):
    """Fast path: no AI at all. Embed the question locally and return the closest records."""
    qv = await embed_query(q)
    chunks, confidence, _ = await knowledge.retrieve(q, qv, s["top_k"], namespaces)
    good = _best_matches(chunks, s["effective_min_score"])
    if not good:
        yield _meta("none", q, confidence)
        yield {"type": "token", "text": (
            f"No close match in your data (best score {confidence:.2f}, needed {s['effective_min_score']:.2f}). "
            "Turn off “Fast search” to let an AI answer, or add the missing data.")}
        yield {"type": "done"}
        return
    yield _meta("search", q, confidence, _citations(good))
    yield {"type": "token", "text": offline.answer_from_data(good, q)}
    yield {"type": "done"}


def _is_not_found(text: str) -> bool | None:
    """True / False once decided, None while the first characters could still become NOT_FOUND."""
    s = text.strip().lstrip("`*\"' ").upper().replace(" ", "_")
    if not s:
        return None
    if s.startswith("NOT_FOUND"):
        return True
    if "NOT_FOUND".startswith(s):
        return None
    return False


async def run_chat(question: str, history: list[dict], mode: str = "ai",
                   namespaces: list[str] | None = None, allow_ai: bool | None = None, doc: bool = False):
    """Async generator of events: meta, token, error, done.
    mode "search" = database only, no AI (fast); mode "ai" = database first, then AI fallback.
    doc=True writes a longer, structured answer for a PDF (and skips the answer cache)."""
    s = await get_runtime()
    style, answer_tokens = (DOC_STYLE, 2500) if doc else ("", 1200)
    if not doc and offline.small_talk_kind(question):  # hi / thanks / bye ...: talk like a person, not a search engine
        msgs = [{"role": "system", "content": CHAT_SYSTEM}] + [
            {"role": m["role"], "content": m["content"][:1000]} for m in history[-6:]
        ] + [{"role": "user", "content": question}]
        try:
            res = await llm.chat(msgs, temperature=0.7, max_tokens=120)
            if res.text:
                yield _meta("ai", question, 1.0, [], res.provider, res.model)
                yield {"type": "token", "text": res.text}
                yield {"type": "done"}
                return
        except llm.AllProvidersFailed:
            pass
        yield _meta("local", question, 1.0)  # no AI available: short built-in reply
        yield {"type": "token", "text": offline.small_talk(question)}
        yield {"type": "done"}
        return
    if mode == "search":
        async for ev in _search_only(question, s, namespaces):
            yield ev
        return
    trusted_only = not s["send_context_to_external"]
    ai_ok = s["ai_fallback"] and allow_ai is not False
    strict, loose = s["effective_min_score"], s["effective_ai_min_score"]

    q = await _condense(question, history, trusted_only)
    qv = await embed_query(q)

    # 1) saved answer
    if s["cache_enabled"] and not doc:
        hit = await knowledge.cache_lookup(q, qv, s["cache_threshold"], namespaces)
        if hit and (ai_ok or hit.get("source") != "ai"):
            yield _meta("cache", q, 1.0, hit.get("citations", []), hit.get("provider"), hit.get("model"))
            yield {"type": "token", "text": hit["answer"]}
            yield {"type": "done"}
            return

    # 2) your data (hybrid search; a Hindi/Hinglish miss is retried with an English rewrite)
    chunks, confidence, exact = await knowledge.retrieve(q, qv, s["top_k"], namespaces)
    if not (exact or confidence >= loose) and s["query_rewrite"]:
        rq = await _ask(REWRITE_SYSTEM, q, trusted_only, 60)
        if rq and rq.lower() != q.lower() and len(rq) < 500:
            c2, conf2, ex2 = await knowledge.retrieve(rq, await embed_query(rq), s["top_k"], namespaces)
            if ex2 or conf2 > confidence:
                chunks, confidence, exact = c2, conf2, ex2
    citations = _citations(chunks)

    if chunks and (exact or confidence >= loose):
        messages = [
            {"role": "system", "content": GROUNDED_SYSTEM + style},
            {"role": "user", "content": f"CONTEXT:\n{_context(chunks, s['max_context_chars'])}\n\nQUESTION: {q}"},
        ]
        buf, parts, pmeta = [], [], {}
        decided, not_found, failed = False, False, False
        agen = llm.stream_chat(messages, require_trusted=trusted_only, temperature=0.1, max_tokens=answer_tokens)
        try:
            async for ev in agen:
                if ev["type"] == "meta":
                    pmeta = ev
                elif ev["type"] == "token":
                    if decided:
                        parts.append(ev["text"])
                        yield ev
                        continue
                    buf.append(ev["text"])
                    verdict = _is_not_found("".join(buf))
                    if verdict is True:
                        not_found = True
                        break
                    if verdict is False:
                        decided = True
                        parts.append("".join(buf))
                        yield _meta("database", q, confidence, citations, pmeta.get("provider"), pmeta.get("model"))
                        yield {"type": "token", "text": "".join(buf)}
                elif ev["type"] == "error":
                    failed = True
                    yield ev
        except llm.AllProvidersFailed:
            # No AI could write the answer: show the best passages instead of failing.
            good = _best_matches(chunks, strict)  # no AI to double-check: only a clear match counts
            if not good:
                yield _meta("none", q, confidence, note="no AI available")
                yield {"type": "token", "text": offline.not_found(question)}
                yield {"type": "done"}
                return
            yield _meta("database", q, confidence, _citations(good), note="answered offline (no AI available)")
            yield {"type": "token", "text": offline.answer_from_data(good, q)}
            yield {"type": "done"}
            return
        finally:
            await agen.aclose()
        if not decided and not not_found and buf:
            not_found = bool(_is_not_found("".join(buf)))
            if not not_found:
                decided = True
                parts.append("".join(buf))
                yield _meta("database", q, confidence, citations, pmeta.get("provider"), pmeta.get("model"))
                yield {"type": "token", "text": "".join(buf)}
        if decided:
            if parts and not failed and not doc:
                await knowledge.cache_store(q, qv, "".join(parts), "database", citations,
                                            pmeta.get("provider"), pmeta.get("model"), namespaces)
            yield {"type": "done"}
            return

    # 3) not in your data
    if not ai_ok:
        yield _meta("none", q, confidence)
        yield {"type": "token", "text": offline.not_found(question)}
        yield {"type": "done"}
        return

    # AI fallback: ONLY the standalone question leaves your system
    messages = [{"role": "system", "content": FALLBACK_SYSTEM + style}, {"role": "user", "content": q}]
    parts, meta = [], {}
    try:
        async for ev in llm.stream_chat(messages, max_tokens=2500 if doc else 1500):
            if ev["type"] == "meta":
                meta = ev
                yield _meta("ai", q, confidence, [], ev["provider"], ev["model"])
            elif ev["type"] == "token":
                parts.append(ev["text"])
                yield ev
            else:
                yield ev
    except llm.AllProvidersFailed as e:
        # Every AI is missing, down or out of quota: answer from your data without AI.
        good = _best_matches(chunks, strict)  # no AI to double-check: only a clear match counts
        if good:
            yield _meta("database", q, confidence, _citations(good), note="answered offline (no AI available)")
            yield {"type": "token", "text": offline.answer_from_data(good, q)}
            yield {"type": "done"}
            return
        yield _meta("none", q, confidence, note=f"no AI available: {e}")
        yield {"type": "token", "text": offline.not_found(question)}
        yield {"type": "done"}
        return
    if parts and meta and not doc:
        await knowledge.cache_store(q, qv, "".join(parts), "ai", [], meta["provider"], meta["model"], namespaces)
    yield {"type": "done"}
