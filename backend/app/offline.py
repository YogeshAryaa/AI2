"""Built-in answers that need no AI provider at all.

1. Small talk (hi / hello / thanks / bye / who are you ...) is answered instantly and locally, so it
   never uses up a free provider's quota.
2. When no AI provider is set up, or every provider is down / out of quota, the answer is written
   directly from your data: a saved Q&A pair returns its answer, any other document returns its most
   relevant sentences.
"""
import random
import re

_WORD = re.compile(r"[a-z0-9\u0900-\u097f']+")
_HINGLISH = set("""
kya kaise kaisa kaisi kaun kon kab kahan kaha kyun kyu hai hain ho hota hoti tha thi mujhe hume humein
mera meri mere apna apni batao bataiye btao bata chahiye karna karo kare krna kro kr karein milega milegi
nahi nhi haan ji aap tum hum yeh ye woh wo kitna kitni kitne kaunsa konsa liye wala wali waale
""".split())


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def is_hinglish(text: str) -> bool:
    if re.search(r"[\u0900-\u097f]", text):
        return True
    w = _words(text)
    return bool(w) and sum(x in _HINGLISH for x in w) >= max(1, len(w) // 5)


# ---------------------------------------------------------------- small talk
# Short, human replies. Several variants each, so it does not sound like a robot repeating itself.
# (When an AI provider is available, chat.py lets the AI reply to small talk instead; these are the fallback.)
_GREET = re.compile(r"^(hi+|hey+|hello+|helo+|hlo+|hy+|namaste|namaskar|salaam|assalam\w*|good (morning|afternoon|evening)|gm|yo|hola)( (there|ji|sir|madam|mam|bhai|team|bot|dost))?$")
_SMALL_TALK: list[tuple[str, re.Pattern, list[str], list[str]]] = [
    ("greet", _GREET,
     ["{g}! 😊 How can I help you today?", "{g}! What can I do for you?", "{g}! 👋 Tell me, what do you need?",
      "{g}! Looking for something specific? Just ask."],
     ["{g}! 😊 Bataiye, kya madad karoon?", "{g}! Kaise help kar sakta hoon aapki?", "{g} ji! 👋 Bataiye, kya chahiye?",
      "{g}! Kuch poochna hai? Bindaas poochiye."]),
    ("how", re.compile(r"^(how are you|how r u|how are u|how is it going|kaise ho|kaise ho aap|kaise hain( aap)?|kya haal( hai| chaal)?|sab theek|sab badhiya|whats up|what's up|wassup|sup)$"),
     ["I'm good, thanks! How about you? What can I help with?", "All good here 😊 What's on your mind?"],
     ["Main badhiya hoon, aap sunaiye? 😊 Kya help chahiye?", "Sab badhiya! Aap bataiye, kya kaam hai?"]),
    ("thanks", re.compile(r"^(thanks?|thank you( so much| very much)?|thx|thnx|ty|shukriya|dhanyavaad|dhanyawad|thanks a lot|thanks bro|ok thanks|okay thanks)$"),
     ["You're welcome! 😊", "Happy to help! Anything else?", "Anytime! 👍"],
     ["Koi baat nahi! 😊", "Khushi hui madad karke! Aur kuch?", "Welcome ji! 👍"]),
    ("bye", re.compile(r"^(bye+|goodbye|good night|gn|see you|see ya|tata|alvida|chalo bye|ok bye|milte hai)$"),
     ["Bye! Have a great day 👋", "See you! 👋"],
     ["Bye! Aapka din achha rahe 👋", "Chaliye, milte hain! 👋"]),
    ("who", re.compile(r"^(who are you|what are you|what can you do|help|tum kaun ho|aap kaun ho|kaun ho|kya kar sakte ho|tum kya kar sakte ho|aap kya kar sakte ho|tum kya karte ho)$"),
     ["I'm your company's assistant. I answer from your own data: candidates, jobs, tech roles, hiring process, "
      "marketing, sales and HR. Ask me anything, in English or Hinglish."],
     ["Main aapki company ka assistant hoon. Aapke data se jawab deta hoon: candidates, jobs, developer/QA roles, "
      "hiring process, marketing, sales aur HR. Kuch bhi poochiye, English ya Hinglish me."]),
    ("ok", re.compile(r"^(ok+|okay|k|kk|hmm+|accha|acha|achha|theek hai|thik hai|fine|cool|nice|great|good|done)$"),
     ["👍", "Okay! Let me know if you need anything."],
     ["👍", "Theek hai! Kuch aur chahiye to bataiye."]),
]


def small_talk_kind(question: str) -> str | None:
    q = " ".join(_words(question.replace("’", "'")))
    if not q or len(q) > 40:
        return None
    for kind, pat, _, _ in _SMALL_TALK:
        if pat.match(q):
            return kind
    return None


def small_talk(question: str) -> str | None:
    """A ready reply for greetings / thanks / bye..., or None if the message is a real question."""
    kind = small_talk_kind(question)
    if not kind:
        return None
    _, _, en, hi = next(x for x in _SMALL_TALK if x[0] == kind)
    q = " ".join(_words(question))
    first = (q.split() or ["hello"])[0]
    hinglish = is_hinglish(question) or first in ("namaste", "namaskar", "hii", "hiii", "hy", "hlo")
    m = _GREET.match(q) if kind == "greet" else None
    g = m.group(1).capitalize() if m and len(m.group(1)) <= 16 else "Hello"
    return random.choice(hi if hinglish else en).format(g=g)


# ---------------------------------------------------------------- answers from your data
_QA_ANSWER = re.compile(r"(?:^|\s)A:\s*(.+)", re.S)
_QA_QUESTIONS = re.compile(r"Q:\s*(.*?)\s*(?:Also asked as:\s*(.*?)\s*)?(?:^|\s)A:\s", re.S)
_STOP = set("""
a an the is are was were be of to in on at for and or what which who how when where why do does did can
could would should please tell me give list about my our your i you we it this that kya hai hain ka ki ke
ko se me mein aur ya bhi to batao kaise kaun kab mujhe
""".split())


def _relines(text: str) -> str:
    """Chunking joins lines with spaces; put list items back on their own lines."""
    text = re.sub(r"\s+(?=(?:•|- |\d{1,2}\. )\S)", "\n", text)
    return re.sub(r":[ \t]+(?=\n)", ":", text).strip()


def _qa_answer(text: str) -> str | None:
    m = _QA_ANSWER.search(text)
    return _relines(m.group(1)) if m else None


def _best_sentences(text: str, question: str, n: int = 3) -> str:
    q = {w for w in _words(question) if w not in _STOP}
    sents = [s.strip() for s in re.split(r"(?<=[.!?।])\s+|\n+", text) if len(s.strip()) > 2]
    if len(sents) <= n:
        return "\n".join(sents)
    scored = sorted(range(len(sents)), key=lambda i: -len(q & set(_words(sents[i]))))[:n]
    return "\n".join(sents[i] for i in sorted(scored))


# words that say nothing about the topic ("what is react" -> {"react"})
_Q_STOP = _STOP | set("""
whats what's explain meaning mean means define definition matlab hota hoti hote hai hain kya kyaa tell about
something thing please plz pls ek koi briefly short simple simply basic basics
""".split())


def _terms(text: str) -> set[str]:
    return {w for w in _words(text) if w not in _Q_STOP and len(w) > 1}


def _stored_questions(c: dict) -> list[str]:
    m = _QA_QUESTIONS.search(c["text"])
    if not m:
        return [str(c.get("title") or "")]
    return [m.group(1)] + [v for v in (m.group(2) or "").split("|") if v.strip()]


def _topic_match(c: dict, q_terms: set[str]) -> float:
    """How exactly a stored question asks the same thing (F1 of topic words, best variant).
    "what is react" vs "What is React?" = 1.0, vs "What is Flutter and React Native?" = 0.5."""
    best = 0.0
    for v in _stored_questions(c):
        vt = _terms(v)
        hit = len(q_terms & vt)
        if hit:
            p, r = hit / len(vt), hit / len(q_terms)
            best = max(best, 2 * p * r / (p + r))
    return best


def pick_best(chunks: list[dict], question: str) -> dict:
    """Meaning (cosine) decides most, but the stored question that asks exactly the same thing wins ties
    over one that only mentions the same word next to other topics."""
    q_terms = _terms(question)
    if not q_terms:
        return chunks[0]
    return max(chunks, key=lambda c: (c.get("exact", False), c["cosine"] + 0.15 * _topic_match(c, q_terms)))


def answer_from_data(chunks: list[dict], question: str) -> str:
    """Writes a readable reply from the single best chunk, without any AI. Only what was asked."""
    best = pick_best(chunks, question)
    ans = _qa_answer(best["text"])
    if ans:
        return ans
    out = _best_sentences(best["text"], question)
    title = best.get("title")
    return f"**{title}**\n{out}" if title and not out.startswith(str(title)) else out


def not_found(question: str) -> str:
    if is_hinglish(question):
        return ("Maaf kijiye, iska jawab abhi mere data me nahi hai. 🙏\n"
                "• Apna sawaal thoda alag shabdon me poochiye, ya\n"
                "• “My data” tab me iska jawab add kar dijiye (Title = sawaal, Text = jawab), ya\n"
                "• “AI providers” tab me ek free AI (jaise Groq ya Gemini) jod dijiye, phir main general sawaalon ka bhi jawab de paunga.")
    return ("Sorry, I don't have an answer for that in my data yet. 🙏\n"
            "• Try asking in different words, or\n"
            "• Add the answer in the “My data” tab (Title = question, Text = answer), or\n"
            "• Add a free AI in the “AI providers” tab (e.g. Groq or Gemini) so I can answer general questions too.")
