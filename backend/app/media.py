"""Files the AI makes on request inside the chat: PDFs and images.

The user just asks in the chat ("iski PDF bana do", "make a PDF on React roles", "ek office poster ki image
banao") and the answer comes back with the file attached. No separate button or API is needed.

* PDF: written from your data first (same rules as a normal answer, AI fallback if allowed), or from the
  previous answer / the whole chat when the user says "this", "iski", "poori chat"... Rendered locally
  (fpdf2 + bundled Noto fonts, so Hindi works). Nothing extra leaves your server.
* Image: the request is turned into an English prompt and sent to an image API: Pollinations (free, no key)
  by default, or any OpenAI-compatible /images/generations endpoint (IMAGE_PROVIDER=openai).
  Only the prompt leaves your server, never your data.

Files are stored in MongoDB (rag_files) and belong to the API key (and user_id) that asked for them.
"""
import asyncio
import base64
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from bson import Binary

from . import llm, local_image, offline, pdfdesign, styles
from .chat import run_chat
from .config import cfg
from .db import db, utcnow

log = logging.getLogger("rag.media")

FONTS = Path(__file__).resolve().parent / "fonts"
MAX_FILE_BYTES = 12 * 1024 * 1024  # MongoDB documents are limited to 16 MB

# ------------------------------------------------------------------ what does the user want?
_PDF = re.compile(r"\bpdf\b|पीडीएफ", re.I)
_IMG = re.compile(
    r"\b(image|images|img|photo|photos|pic|pics|picture|poster|banner|logo|illustration|drawing|wallpaper|"
    r"thumbnail|artwork|tasveer|tasvir|tasveeren|chitra)\b|तस्वीर|फोटो|फ़ोटो|इमेज|चित्र|पोस्टर|लोगो|बैनर",
    re.I,
)
_MAKE = re.compile(
    r"\b(bana\w*|bna\w*|banaa\w*|generate\w*|create\w*|make|making|draw\w*|design\w*|export\w*|convert\w*|"
    r"download\w*|de\s*do|dedo|dijiye|dijie|chahiye|chahie|chaiye|chahiye|render\w*|produce|give|send|bhej\w*|"
    r"nikal\w*|save)\b|बना|दो|दीजिए|चाहिए|भेज",
    re.I,
)
# "how do I make a PDF?" / "image kaise banate hain" is a question, not a request.
_HOW = re.compile(r"\b(how\s+(do|to|can|does)|kaise|kese|kaisey|kyse|what\s+is|kya\s+hota|kya\s+hai)\b|कैसे", re.I)
_REFER = re.compile(
    r"\b(is|iss|isk[aeio]|isko|isse|ise|ye|yeh|ya|this|that|it|above|upar|upar\s*wal[aei]|previous|last|pichl[aei]|"
    r"same|answer|jawab|reply|response|wahi|vahi)\b|इसक|इसे|इसको|ऊपर|यह",
    re.I,
)
_WHOLE_CHAT = re.compile(r"\b(poori|puri|pura|poora|full|whole|entire|complete|sari|saari)\b.*\b(chat|conversation|baat\w*)\b|"
                         r"\b(chat|conversation)\b", re.I)
_FILLER = {
    "a", "an", "the", "of", "on", "about", "for", "please", "pls", "plz", "me", "mein", "mai", "main", "mujhe", "hume",
    "humein", "ek", "ki", "ka", "ke", "ko", "se", "do", "de", "kar", "karo", "karke", "kr", "krdo", "kardo", "karde",
    "file", "format", "form", "in", "into", "as", "to", "and", "with", "jaldi", "zara", "bhi", "baare", "bare",
    "ko", "wali", "wala", "sakte", "sakta", "ho", "hai", "can", "you", "could", "would", "kindly", "me", "my", "us",
    "dijiye", "do", "na", "ab", "now", "isme", "usme", "ek", "acchi", "achhi", "achi", "nice", "good", "sundar",
    "में", "मे", "की", "का", "के", "को", "से", "एक", "मुझे", "हमें", "दो", "दीजिए", "पर", "बारे", "और", "भी",
}


def detect(question: str) -> str | None:
    """'pdf', 'image' or None."""
    q = question.strip()
    if len(q) > 1500 or _HOW.search(q):
        return None
    if _PDF.search(q) and _MAKE.search(q):
        return "pdf"
    if _IMG.search(q) and _MAKE.search(q):
        return "image"
    # "cartoon style me bank banao": an art style + "make" is an image request even without the word "image"
    if styles.find_style(q) in _ART_STYLES and _MAKE.search(q):
        return "image"
    return None


# styles that only make sense for pictures ("realistic", "painting" can appear in normal questions)
_ART_STYLES = {"cartoon", "anime", "watercolor", "sketch", "3d", "pixel", "cinematic", "logo", "illustration", "oil"}


def _strip(question: str, *patterns: re.Pattern) -> str:
    """The request without the 'make a PDF' words, e.g. 'React roles ki PDF bana do' -> 'React roles'."""
    s = question
    for p in patterns + (_MAKE,):
        s = p.sub(" ", s)
    words = re.sub(r"[?!.,:;]+", " ", s).split()
    while words and words[0].lower() in _FILLER:
        words.pop(0)
    while words and words[-1].lower() in _FILLER:
        words.pop()
    return " ".join(words)


_HI_WORDS = re.compile(r"\b(bana\w*|bna\w*|kar\w*|ki|ka|ke|ko|hai|hain|ek|mujhe|iski|isko|iska|de\s*do|dedo|chahiye|koi|aur|dikhao|dikha|dusra|doosra|karo|mein|rang|wala|wali|naya|badlo)\b",
                       re.I)


def _reply(question: str, en: str, hi: str) -> str:
    hindi = offline.is_hinglish(question) or re.search(r"[ऀ-ॿ]", question) or _HI_WORDS.search(question)
    return hi if hindi else en


# ------------------------------------------------------------------ storage
async def save_file(owner: str, user_id: str | None, conversation_id: str | None,
                    kind: str, name: str, mime: str, data: bytes, recipe: dict | None = None,
                    label: str | None = None) -> dict:
    """recipe: how the file was made (content, colours, design), so "koi aur design" can remake it."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({len(data) // 1024} KB)")
    fid = secrets.token_urlsafe(18)
    await db.rag_files.insert_one({
        "_id": fid, "owner": owner, "user_id": user_id, "conversation_id": conversation_id, "kind": kind,
        "name": name, "mime": mime, "size": len(data), "data": Binary(data), "created_at": utcnow(),
        "recipe": recipe, "label": label,
    })
    out = {"id": fid, "kind": kind, "name": name, "mime": mime, "size": len(data), "url": f"/files/{fid}"}
    if label:
        out["label"] = label
    return out


def _slug(text: str, ext: str) -> str:
    s = re.sub(r"[^\wऀ-ॿ]+", "-", text.lower()).strip("-")[:60] or "file"
    return f"{s}.{ext}"


# ------------------------------------------------------------------ PDF
_DASHES = str.maketrans({"‑": "-", " ": " ", "​": ""})


def _pdf_fonts(pdf) -> None:
    pdf.add_font("Noto", "", str(FONTS / "NotoSans-Regular.ttf"))
    pdf.add_font("Noto", "B", str(FONTS / "NotoSans-Bold.ttf"))
    pdf.add_font("Deva", "", str(FONTS / "NotoSansDevanagari-Regular.ttf"))
    pdf.add_font("Deva", "B", str(FONTS / "NotoSansDevanagari-Bold.ttf"))
    pdf.set_fallback_fonts(["Deva"], exact_match=False)
    try:
        import uharfbuzz  # noqa: F401  (proper Hindi letter joining)
        pdf.set_text_shaping(True)
    except ImportError:
        log.info("uharfbuzz not installed: Hindi text in PDFs may look unjoined")


def _clean_inline(text: str) -> str:
    """Markdown the PDF engine does not know: links, code ticks, single-* italics, stray '--' / '__'."""
    t = text.translate(_DASHES)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", t)
    t = t.replace("`", "").replace("--", "–").replace("__", "")
    t = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)(?<!\*)\*(?!\*)", r"\1", t)
    t = re.sub(r"^\s*\*\*\s*$", "", t)
    return t


def _table_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for ln in lines:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # | --- | --- |
        rows.append([_clean_inline(c).replace("**", "") for c in cells])
    width = max(len(r) for r in rows) if rows else 0
    return [r + [""] * (width - len(r)) for r in rows]


def _tint(rgb, amount: float) -> tuple[int, int, int]:
    return tuple(round(c * amount + 255 * (1 - amount)) for c in rgb)


def build_pdf(title: str, body: str, citations: list[dict] | None = None, subtitle: str = "",
              accent: str = "#2563eb", secondary: str | None = None, template: str = "classic") -> bytes:
    """See pdfdesign.py for the designs."""
    return pdfdesign.build(title, body, citations, subtitle, accent, secondary, template)


def _title_of(body: str, fallback: str) -> tuple[str, str]:
    """Uses the first '# heading' as the PDF title (and removes it from the body)."""
    lines = body.strip().split("\n")
    if lines and re.match(r"^#{1,2}\s+\S", lines[0].strip()):
        return re.sub(r"^#+\s+", "", lines[0].strip()).replace("**", "")[:150], "\n".join(lines[1:]).strip()
    t = fallback.strip().rstrip("?.!") or "Document"
    return (t[:1].upper() + t[1:])[:150], body.strip()


async def _whole_chat(conversation_id: str | None, history: list[dict]) -> list[dict]:
    if conversation_id:
        docs = await db.rag_messages.find({"conversation_id": conversation_id}, {"role": 1, "content": 1}) \
            .sort("created_at", 1).to_list(500)
        msgs = [{"role": d["role"], "content": d.get("content") or ""} for d in docs]
        if msgs:
            return msgs
    return history


def _transcript(msgs: list[dict]) -> str:
    out = []
    for m in msgs:
        if not m.get("content"):
            continue
        out.append(f"### {'You' if m['role'] == 'user' else 'Assistant'}")
        out.append(m["content"].strip())
        out.append("")
    return "\n".join(out)


_NUM = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "che": 6, "chhe": 6,
        "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6}
_COUNT = re.compile(r"(?<![\wऀ-ॿ])(\d|two|three|four|five|six|teen|tin|chaar|char|paanch|panch|chhe|che|दो|तीन|चार|पांच|पाँच|छह)"
                    r"\s*(design\w*|option\w*|version\w*|variation\w*|types?|alag|different|image\w*|pics?|photo\w*|"
                    r"pdf\w*|logo\w*|poster\w*|tarah|tarike|style\w*|colou?r\w*|templates?|sample\w*|डिज़ाइन|डिजाइन|तरह|इमेज)", re.I)
_MANY = re.compile(r"\b(multiple|kai|kayi|bahut\s*sar[ei]|bohot\s*sar[ei]|bahut|bohot|several|many|options|variations|"
                   r"designs|samples|sab\s*design)\b|कई|बहुत", re.I)
_AGAIN = re.compile(
    r"\b(koi\s*(aur|or|dusr\w*|doosr\w*)|dusr[aei]|doosr[aei]|ek\s*(aur|or)|aur\s*ek|or\s*ek|another|different|alag|"
    r"change|badal\w*|bdl\w*|new|naya|nayi|naye|phir\s*se|fir\s*se|again|retry|regenerate|redo|remake|more|"
    r"aur\s*design|or\s*design|variation\w*|option\w*|pasand\s*nahi|psnd\s*nahi|acch?a\s*nahi|acha\s*nahi|"
    r"not\s*good|don'?t\s*like|dislike|better|behtar|aur\s*accha|aur\s*acha|try)\b|दूसर|और\s*एक|एक\s*और|बदल|नया|नई",
    re.I)


def _count(question: str, cap: int) -> int:
    m = _COUNT.search(question)
    if m:
        return max(1, min(cap, _NUM.get(m.group(1).lower(), 1)))
    return min(cap, 3) if _MANY.search(question) else 1


def _pdf_colors(question: str, default_accent: str, default_secondary: str | None = None) -> tuple[str, str | None]:
    """'red aur blue' -> red main colour, blue headings. One colour -> both the same."""
    cols = styles.find_colors(question, 2)
    if not cols:
        return default_accent, default_secondary
    return styles.color_hex(cols[0]), (styles.color_hex(cols[1]) if len(cols) > 1 else None)


async def _render_pdfs(recipe: dict, templates: list[str], owner, user_id, conversation_id) -> list[dict]:
    files = []
    for tpl in templates:
        r = {**recipe, "template": tpl}
        data = await asyncio.to_thread(build_pdf, r["title"], r["body"], r.get("citations"), "",
                                       r["accent"], r.get("secondary"), tpl)
        label = pdfdesign.TEMPLATES[tpl][0]
        name = _slug(f"{r['title']} {tpl}" if len(templates) > 1 else r["title"], "pdf")
        files.append(await save_file(owner, user_id, conversation_id, "pdf", name, "application/pdf", data, r, label))
    return files


def _labels(files: list[dict]) -> str:
    return ", ".join(f.get("label") or f["name"] for f in files)


_PDF_HINT = ("\n\nDon't like it? Say “another design”, “3 designs”, “red and blue colour” or “minimal / banner / "
             "boxed / sidebar / elegant design”.")
_PDF_HINT_HI = ("\n\nPasand nahi aaya? Likhiye “koi aur design”, “3 design dikhao”, “laal aur neela rang” ya "
                "“minimal / banner / boxed / sidebar / elegant design”.")


async def _pdf_events(question, history, mode, namespaces, allow_ai, owner, user_id, conversation_id):
    # "green aur gold mein, banner design, 3 options" -> colours, design and how many
    accent, secondary = _pdf_colors(question, styles.color_hex(None, cfg.pdf_color))
    first = pdfdesign.find_template(question) or "classic"
    n = _count(question, len(pdfdesign.ORDER))
    templates = [first] + [t for t in pdfdesign.next_templates(first, n) if t != first][:n - 1]
    clean = pdfdesign._TPL_RE[first].sub(" ", styles.strip_color_words(question)) if first else question
    clean = _COUNT.sub(" ", clean)
    topic = _strip(clean, _PDF)
    last_answer = next((m["content"] for m in reversed(history) if m["role"] == "assistant" and m.get("content")), None)
    referential = bool(_REFER.search(question)) and len(topic.split()) <= 6

    # a) the whole chat / b) the previous answer: nothing new to write, just render it
    if history and (_WHOLE_CHAT.search(question) and len(topic.split()) <= 6):
        msgs = await _whole_chat(conversation_id, history)
        title, body, cites = "Chat", _transcript(msgs), None
    elif last_answer and (referential or not topic):
        prev_q = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        title, body = _title_of(last_answer, prev_q)
        cites = None
    else:
        title = body = cites = None

    if body is not None:
        recipe = {"title": title, "body": body, "citations": cites, "accent": accent, "secondary": secondary}
        try:
            files = await _render_pdfs(recipe, templates, owner, user_id, conversation_id)
        except Exception as e:
            log.exception("PDF failed")
            yield {"type": "meta", "source": "none", "citations": [], "standalone_question": question, "confidence": 1.0}
            yield {"type": "error", "message": f"Could not create the PDF: {type(e).__name__}: {e}"}
            yield {"type": "done"}
            return
        yield {"type": "meta", "source": "file", "provider": "pdf", "model": None, "citations": [],
               "standalone_question": question, "confidence": 1.0}
        many = len(files) > 1
        yield {"type": "token", "text": _reply(
            question, (f"Here are {len(files)} designs: {_labels(files)}." if many else "Here is your PDF.") + _PDF_HINT,
            (f"Ye rahe {len(files)} designs: {_labels(files)}." if many else "Ye rahi aapki PDF.") + _PDF_HINT_HI)}
        for f in files:
            yield {"type": "file", "file": f}
        yield {"type": "done"}
        return

    # c) new content: answer the topic like any other question (your data first), as a document
    if not topic:
        yield {"type": "meta", "source": "none", "citations": [], "standalone_question": question, "confidence": 0}
        yield {"type": "token", "text": _reply(
            question, "What should the PDF be about? For example: “Make a PDF on our leave policy”.",
            "PDF kis topic par chahiye? Jaise: “leave policy ki PDF bana do”.")}
        yield {"type": "done"}
        return
    meta, parts, failed = {}, [], False
    async for ev in run_chat(topic, history, mode, namespaces, allow_ai, doc=True):
        if ev["type"] == "done":
            break
        if ev["type"] == "meta":
            meta = ev
        elif ev["type"] == "token":
            parts.append(ev["text"])
        elif ev["type"] == "error":
            failed = True
        yield ev
    body = "".join(parts).strip()
    if body and not failed and meta.get("source") not in ("none", None):
        try:
            title, rest = _title_of(body, meta.get("standalone_question") or topic)
            recipe = {"title": title, "body": rest, "citations": meta.get("citations"), "accent": accent,
                      "secondary": secondary}
            files = await _render_pdfs(recipe, templates, owner, user_id, conversation_id)
            yield {"type": "token", "text": _reply(question, _PDF_HINT, _PDF_HINT_HI)}
            for f in files:
                yield {"type": "file", "file": f}
        except Exception as e:
            log.exception("PDF failed")
            yield {"type": "error", "message": f"Could not create the PDF: {type(e).__name__}: {e}"}
    yield {"type": "done"}


# ------------------------------------------------------------------ image
IMAGE_PROMPT_SYSTEM = (
    "You write prompts for an image generator. Turn the user's request (it may be Hindi, Hinglish or English) "
    "into ONE detailed English prompt about exactly the subject the user named: subject, setting, lighting, "
    "composition. Do not turn it into a logo or poster unless asked. Do not invent slogans or any text: only if the "
    "user gave exact words to show, put those words in double quotes. Do not name real artists. "
    "Do not add colours of your own: colours and art style are added separately. "
    "Output only the prompt, at most 55 words."
)


def _size() -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{3,4})\s*[xX*]\s*(\d{3,4})\s*", cfg.image_size or "")
    return (int(m.group(1)), int(m.group(2))) if m else (1024, 1024)


def _shape(question: str) -> tuple[int, int]:
    w, h = _size()
    q = question.lower()
    if re.search(r"\b(banner|wallpaper|landscape|wide|youtube|thumbnail|cover|linkedin|horizontal|16:9)\b", q):
        return max(w, h), round(max(w, h) * 9 / 16 / 8) * 8
    if re.search(r"\b(poster|portrait|story|reel|vertical|flyer|9:16)\b", q):
        return round(max(w, h) * 3 / 4 / 8) * 8, max(w, h)
    return w, h


async def _image_prompt(question: str, history: list[dict]) -> str:
    topic = _strip(question) or question  # used when no AI can polish the prompt
    msgs = [{"role": "system", "content": IMAGE_PROMPT_SYSTEM}]
    if history and _REFER.search(question):  # "iski image bana do": use what we were talking about
        ctx = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in history[-4:])
        msgs.append({"role": "user", "content": f"Conversation so far:\n{ctx}\n\nRequest: {question}"})
    else:
        msgs.append({"role": "user", "content": question})
    try:
        res = await llm.chat(msgs, temperature=0.6, max_tokens=200)
        p = res.text.strip().strip('"').strip()
        if 5 < len(p) < 1500:
            return p
    except llm.AllProvidersFailed:
        pass
    return topic


async def _one_image(provider: str, prompt: str, width: int, height: int) -> tuple[bytes, str, str]:
    if provider == "local":
        return await asyncio.to_thread(local_image.generate, prompt, width, height), "image/png", "local"
    timeout = httpx.Timeout(cfg.image_timeout_seconds, connect=15)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if provider == "pollinations":
            params = {"width": width, "height": height, "nologo": "true", "seed": secrets.randbelow(10**9)}
            if cfg.image_model:
                params["model"] = cfg.image_model
            headers = {"Authorization": f"Bearer {cfg.image_api_key}"} if cfg.image_api_key else {}
            base = (cfg.image_api_url or "https://image.pollinations.ai/prompt").rstrip("/")
            r = await client.get(f"{base}/{quote(prompt[:1500], safe='')}", params=params, headers=headers)
            if r.status_code in (402, 429) and "limit" in r.text.lower():
                wait = re.search(r"retry after (\d+)", r.text, re.I)
                raise RuntimeError("the free image service allows 1 image every 45 seconds"
                                   + (f": try again in {wait.group(1)} seconds" if wait else "")
                                   + ". For no limit, set IMAGE_API_KEY (a Pollinations key) in .env.")
            if r.status_code in (401, 402, 403):
                raise RuntimeError(f"Pollinations refused (HTTP {r.status_code}): this model or size needs an "
                                   "API key (IMAGE_API_KEY) or a paid plan. Leave IMAGE_MODEL empty for the free one.")
            if r.status_code == 429:
                raise RuntimeError("the free image service is busy (rate limit). Try again in a minute.")
            r.raise_for_status()
            mime = r.headers.get("content-type", "").split(";")[0]
            if not mime.startswith("image/"):
                raise RuntimeError(f"the image service returned {mime or 'no content type'}, not an image")
            return r.content, mime, "pollinations"
        if provider == "openai":
            if not cfg.image_api_url:
                raise RuntimeError("IMAGE_API_URL is not set")
            body = {"model": cfg.image_model or "gpt-image-1", "prompt": prompt, "n": 1, "size": f"{width}x{height}"}
            if not body["model"].startswith("gpt-image"):
                body["response_format"] = "b64_json"
            r = await client.post(cfg.image_api_url.rstrip("/") + "/images/generations", json=body,
                                  headers={"Authorization": f"Bearer {cfg.image_api_key}"} if cfg.image_api_key else {})
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            item = (r.json().get("data") or [{}])[0]
            if item.get("b64_json"):
                return base64.b64decode(item["b64_json"]), "image/png", body["model"]
            if item.get("url"):
                img = await client.get(item["url"])
                img.raise_for_status()
                return img.content, img.headers.get("content-type", "image/png").split(";")[0], body["model"]
            raise RuntimeError("the image API returned no image")
    raise RuntimeError(f"unknown image provider '{provider}'")


ONLINE = ("pollinations", "openai")


def image_chain(prefer: str | None = None) -> list[str]:
    """IMAGE_PROVIDER order, e.g. 'local,pollinations' = own AI first, online as backup.
    prefer: 'local' = only our own, 'online' = only the online ones, None/'auto' = the configured order."""
    chain = [p.strip().lower() for p in (cfg.image_provider or "").split(",")]
    chain = [p for p in chain if p in ("local",) + ONLINE]
    if prefer == "local":
        return ["local"]
    if prefer == "online":
        return [p for p in chain if p in ONLINE] or ["pollinations"]
    return chain


async def generate_image(prompt: str, width: int, height: int, prefer: str | None = None) -> tuple[bytes, str, str]:
    """Tries each provider in order. Returns (bytes, mime, provider name)."""
    chain = image_chain(prefer)
    if not chain:
        raise RuntimeError("image generation is turned off (IMAGE_PROVIDER=off)")
    errors = []
    for prov in chain:
        try:
            try:
                return await _one_image(prov, prompt, width, height)
            except RuntimeError as e:  # free online tier: 1 image / 45 s. When making several, wait once.
                wait = re.search(r"try again in (\d+) seconds", str(e))
                if not wait or int(wait.group(1)) > 60:
                    raise
                await asyncio.sleep(int(wait.group(1)) + 2)
                return await _one_image(prov, prompt, width, height)
        except Exception as e:
            log.warning("image provider %s failed: %s", prov, e)
            errors.append(f"{prov}: {e}")
    raise RuntimeError(" | ".join(errors))


_LOCAL_WORDS = re.compile(r"\b(local|offline|apna|apne|apni|khud|own|private)\b", re.I)
_ONLINE_WORDS = re.compile(r"\b(online|internet|bahar|cloud)\b", re.I)


def _prefer(question: str) -> str | None:
    if _ONLINE_WORDS.search(question):
        return "online"
    if _LOCAL_WORDS.search(question):
        return "local"
    return None


def styled_prompt(prompt: str, style: str | None, colors: list[str] | None) -> str:
    """Style words go first: local models only read the first ~60 words."""
    extra = styles.style_suffix(style, colors)
    return f"{extra}. {prompt}" if extra else prompt


_STYLE_ROTATION = ["illustration", "3d", "realistic", "cartoon", "watercolor", "cinematic", "anime", "sketch"]
_NEW_STYLE = re.compile(r"\b(style|styl|andaz|andaaz|look|type)\b|स्टाइल|अंदाज़", re.I)


def _styles_for(n: int, current: str | None, explicit: str | None, rotate: bool) -> list[str | None]:
    """The style of each of n images: an asked-for style, or a mix when several designs are wanted."""
    if explicit:
        return [explicit] * n
    if not rotate and n == 1:
        return [current]
    # walk the rotation from the style after the current one, so "koi aur" keeps moving to new looks
    start = _STYLE_ROTATION.index(current) + 1 if current in _STYLE_ROTATION else 0
    pool = [x for x in _STYLE_ROTATION[start:] + _STYLE_ROTATION[:start] if x != current]
    first = [current] if (current and not rotate) else []
    return (first + pool)[:n]


async def _make_images(recipe: dict, style_list: list, owner, user_id, conversation_id):
    """Yields ("file", f) or ("error", message) per image, in order."""
    for i, st in enumerate(style_list):
        r = {**recipe, "style": st}
        prompt = styled_prompt(r["base"], st, r.get("colors"))
        try:
            data, mime, provider = await generate_image(prompt, r["w"], r["h"], r.get("prefer"))
        except Exception as e:
            yield "error", f"Design {i + 1}: {e}"[:400]
            continue
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "png")
        label = (styles.IMAGE_STYLES[st][0] if st in styles.IMAGE_STYLES else "Design") + (
            " · own AI" if provider == "local" else f" · {provider}")
        name = _slug(f"{r.get('name') or 'image'} {i + 1}" if len(style_list) > 1 else (r.get("name") or "image"), ext)
        f = await save_file(owner, user_id, conversation_id, "image", name, mime, data,
                            {**r, "prompt": prompt, "provider": provider}, label)
        yield "file", f


_IMG_HINT = ("\n\nWant something else? Say “another one”, “4 designs”, “cartoon / realistic / 3D style” or "
             "“in red and gold”.")
_IMG_HINT_HI = ("\n\nKuch aur chahiye? Likhiye “koi aur”, “4 design dikhao”, “cartoon / realistic / 3D style mein” "
                "ya “laal aur sunehre rang mein”.")


async def _image_run(question, recipe, style_list, owner, user_id, conversation_id):
    n = len(style_list)
    yield {"type": "meta", "source": "file", "provider": recipe.get("prefer") or "auto", "model": None,
           "citations": [], "standalone_question": question, "confidence": 1.0, "image_prompt": recipe["base"],
           "style": style_list[0]}
    yield {"type": "token", "text": _reply(
        question, f"Making {n} designs…" if n > 1 else "Making your image…",
        f"{n} designs bana raha hoon…" if n > 1 else "Image bana raha hoon…")}
    made, errors = 0, []
    async for kind, val in _make_images(recipe, style_list, owner, user_id, conversation_id):
        if kind == "file":
            made += 1
            yield {"type": "file", "file": val}
        else:
            errors.append(val)
    if not made:
        yield {"type": "error", "message": "Could not create the image: " + " | ".join(errors)}
    else:
        if errors:
            yield {"type": "token", "text": "\n\n" + " | ".join(errors)}
        yield {"type": "token", "text": "\n" + _reply(
            question, ("Here they are." if made > 1 else "Here is your image.") + _IMG_HINT,
            ("Ye rahe." if made > 1 else "Ye rahi aapki image.") + _IMG_HINT_HI)}
    yield {"type": "done"}


async def _image_events(question, history, owner, user_id, conversation_id, use_ai: bool = True):
    if not image_chain():
        yield {"type": "meta", "source": "none", "citations": [], "standalone_question": question, "confidence": 0}
        yield {"type": "token", "text": _reply(question, "Image generation is turned off on this server.",
                                               "Is server par image banana band hai.")}
        yield {"type": "done"}
        return
    style, colors = styles.find_style(question), styles.find_colors(question)
    n = _count(question, 4)
    clean = _COUNT.sub(" ", styles.strip_color_words(question) if colors else question)
    base = await _image_prompt(clean, history) if use_ai else (_strip(clean) or clean)
    w, h = _shape(question)
    recipe = {"base": base, "colors": colors, "w": w, "h": h, "prefer": _prefer(question),
              "name": _strip(clean, _IMG)[:50] or "image"}
    async for ev in _image_run(question, recipe, _styles_for(n, style, style, rotate=n > 1),
                               owner, user_id, conversation_id):
        yield ev


# ------------------------------------------------------------------ "koi aur design" / "laal rang mein karo"
async def _last_file_recipe(conversation_id: str | None) -> dict | None:
    """The file made in the previous answer of this conversation, if the previous answer made one."""
    if not conversation_id:
        return None
    last = await db.rag_messages.find_one({"conversation_id": conversation_id, "role": "assistant"},
                                          sort=[("created_at", -1)])
    files = ((last or {}).get("meta") or {}).get("files") or []
    if not files:
        return None
    doc = await db.rag_files.find_one({"_id": files[-1]["id"]}, {"data": 0})
    return doc if doc and doc.get("recipe") else None


_FOLLOW_FILLER = re.compile(
    r"(?<![\wऀ-ॿ])(design\w*|version\w*|option\w*|sample\w*|dikha\w*|show|karo|kar|kr|krdo|kardo|karde|do|de|"
    r"isko|iski|iska|isk[eo]|ise|isse|is|ye|yeh|this|it|that|make|in|mein|me|mai|wala|wali|vala|wale|chahiye|please|pls|"
    r"plz|tarah|types?|ek|aur|or|and|se|ko|ka|ki|ke|hai|hain|ab|now|bhi|abhi|thoda|zara|bas|look|theme|colou?r|rang|"
    r"style|bnao|banao|bana|bnado|banado|image|photo|pdf|wapas|dobara|dubara|one|ones|a|an|the|to|with|please|"
    r"pasand|psnd|nahi|nhi|aaya|aya|ayi|aayi|accha|acha|achha|achhi|good|like|bilkul|yaar|yar|bhai|sir|ji|ok|okay|"
    r"\d{1,2}|two|three|four|five|six|teen|tin|char|chaar|paanch|panch)"
    r"(?![\wऀ-ॿ])|इसे|इसको|में|और|दिखाओ|करो", re.I)


def _is_followup(question: str) -> bool:
    q = question.strip()
    if len(q.split()) > 16:
        return False
    return bool(_AGAIN.search(q) or _COUNT.search(q) or styles.find_colors(q) or styles.find_style(q)
                or pdfdesign.find_template(q))


async def _remake_events(question, prev: dict, owner, user_id, conversation_id):
    r = dict(prev["recipe"])
    n_cap = len(pdfdesign.ORDER) if prev["kind"] == "pdf" else 4
    n = _count(question, n_cap)
    if prev["kind"] == "pdf":
        r["accent"], r["secondary"] = _pdf_colors(question, r["accent"], r.get("secondary"))
        asked = pdfdesign.find_template(question)
        only_colour = bool(styles.find_colors(question)) and not _AGAIN.search(question) and not _COUNT.search(question)
        if asked:
            templates = [asked] + [t for t in pdfdesign.next_templates(asked, n) if t != asked][:n - 1]
        elif only_colour:
            templates = [r.get("template") or "classic"]
        else:
            templates = pdfdesign.next_templates(r.get("template"), n)
        files = await _render_pdfs(r, templates, owner, user_id, conversation_id)
        yield {"type": "meta", "source": "file", "provider": "pdf", "model": None, "citations": [],
               "standalone_question": question, "confidence": 1.0}
        yield {"type": "token", "text": _reply(
            question, f"New design{'s' if len(files) > 1 else ''}: {_labels(files)}." + _PDF_HINT,
            f"Naya design: {_labels(files)}." + _PDF_HINT_HI)}
        for f in files:
            yield {"type": "file", "file": f}
        yield {"type": "done"}
        return
    # image
    colors = styles.find_colors(question)
    if colors:
        r["colors"] = colors
    if re.search(r"\b(banner|wallpaper|landscape|wide|poster|portrait|story|vertical|square)\b", question, re.I):
        r["w"], r["h"] = _shape(question)
    r["prefer"] = _prefer(question) or r.get("prefer")
    explicit = styles.find_style(question)
    # "koi aur" / "dusra design" = a really different design: move to the next style, not just another seed.
    # Only a colour or size change ("laal rang mein karo") keeps the style.
    rotate = not explicit and bool(_NEW_STYLE.search(question) or _AGAIN.search(question))
    style_list = _styles_for(n, r.get("style"), explicit, rotate or n > 1)
    async for ev in _image_run(question, r, style_list, owner, user_id, conversation_id):
        yield ev


# ------------------------------------------------------------------ entry point used by the chat routes
async def run(question: str, history: list[dict], mode: str, namespaces, allow_ai,
              owner: str, user_id: str | None = None, conversation_id: str | None = None):
    """Same events as chat.run_chat, plus {"type": "file", "file": {...}} when a file was made."""
    want = detect(question) if cfg.chat_files else None
    if cfg.chat_files and _is_followup(question):
        # Only when nothing new is asked: "koi aur", "4 design dikhao", "laal rang mein karo", "koi aur image banao".
        # "koi aur candidate hai Pune mein?" has a new subject, so it stays a normal question.
        leftover = _strip(_AGAIN.sub(" ", _COUNT.sub(" ", styles.strip_color_words(question))), _PDF, _IMG)
        leftover = [w for w in _FOLLOW_FILLER.sub(" ", leftover).split()
                    if not (styles.find_style(w) or pdfdesign.find_template(w))]
        # any real word left ("bank" in "cartoon style me bank banao") = a new subject, so make a new file
        prev = await _last_file_recipe(conversation_id) if not leftover else None
        # a design word ("banner", "boxed") after a PDF means the PDF, even though "banner" also sounds like an image
        if prev and (want is None or prev["kind"] == want or (prev["kind"] == "pdf" and pdfdesign.find_template(question))):
            try:
                async for ev in _remake_events(question, prev, owner, user_id, conversation_id):
                    yield ev
                return
            except Exception as e:
                log.exception("remake failed")
                yield {"type": "meta", "source": "none", "citations": [], "standalone_question": question,
                       "confidence": 0}
                yield {"type": "error", "message": f"Could not make a new design: {type(e).__name__}: {e}"}
                yield {"type": "done"}
                return
    if want == "pdf":
        async for ev in _pdf_events(question, history, mode, namespaces, allow_ai, owner, user_id, conversation_id):
            yield ev
    elif want == "image":
        async for ev in _image_events(question, history, owner, user_id, conversation_id, use_ai=mode != "search"):
            yield ev
    else:
        async for ev in run_chat(question, history, mode, namespaces, allow_ai):
            yield ev
