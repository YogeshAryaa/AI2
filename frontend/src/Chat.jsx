import { useEffect, useRef, useState } from "react";
import { api, streamChat } from "./api.js";
import { FileCard } from "./Files.jsx";

// ---------------------------------------------------------------------------
// Lightweight markdown renderer — no external deps.
// Handles: # headings, **bold**, *italic*, `code`, [n] citations,
//          - bullet lists, 1. numbered lists, --- hr, blank-line paragraphs.
// ---------------------------------------------------------------------------
function renderMarkdown(text) {
  if (!text) return [];

  // Escape HTML to prevent XSS
  const esc = (s) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // Inline: bold, italic, inline-code, citation refs [1], [2]…
  const inline = (s) =>
    esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/\[(\d+)\]/g, '<sup class="cite">[$1]</sup>');

  const lines = text.split("\n");
  const nodes = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Heading
    const hm = line.match(/^(#{1,3})\s+(.+)/);
    if (hm) {
      const level = hm[1].length;
      nodes.push({ tag: `h${level}`, html: inline(hm[2]) });
      i++;
      continue;
    }

    // Horizontal rule
    if (/^---+$/.test(line.trim())) {
      nodes.push({ tag: "hr" });
      i++;
      continue;
    }

    // Bullet list block
    if (/^[-*]\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) {
        items.push(inline(lines[i].replace(/^[-*]\s+/, "")));
        i++;
      }
      nodes.push({ tag: "ul", items });
      continue;
    }

    // Numbered list block
    if (/^\d+\.\s/.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(inline(lines[i].replace(/^\d+\.\s+/, "")));
        i++;
      }
      nodes.push({ tag: "ol", items });
      continue;
    }

    // Blank line → skip
    if (line.trim() === "") {
      i++;
      continue;
    }

    // Paragraph — collect consecutive non-special lines
    const pLines = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,3}|[-*]|\d+\.|---+)\s/.test(lines[i])
    ) {
      pLines.push(inline(lines[i]));
      i++;
    }
    if (pLines.length) nodes.push({ tag: "p", html: pLines.join("<br/>") });
  }

  return nodes;
}

function MarkdownBody({ text, streaming }) {
  if (!text && streaming)
    return <span className="dots"><i /><i /><i /></span>;

  const nodes = renderMarkdown(text || "");

  return (
    <div className="md-body">
      {nodes.map((n, idx) => {
        if (n.tag === "hr") return <hr key={idx} />;
        if (n.tag === "ul")
          return (
            <ul key={idx}>
              {n.items.map((it, j) => (
                <li key={j} dangerouslySetInnerHTML={{ __html: it }} />
              ))}
            </ul>
          );
        if (n.tag === "ol")
          return (
            <ol key={idx}>
              {n.items.map((it, j) => (
                <li key={j} dangerouslySetInnerHTML={{ __html: it }} />
              ))}
            </ol>
          );
        const Tag = n.tag;
        return <Tag key={idx} dangerouslySetInnerHTML={{ __html: n.html }} />;
      })}
      {streaming && <span className="caret" />}
    </div>
  );
}

// The signature moment: the pulse waits on "Your data", then travels to "Free AI".
function Route({ phase }) {
  const label = {
    idle: "Ready",
    searching: "Looking in your data first…",
    database: "Answered from your data",
    cache: "Answered from a saved answer",
    ai: "Not in your data, answered by AI",
    local: "Instant reply (no AI needed)",
    file: "File created",
  }[phase];
  return (
    <div className={`route ${phase}`} role="status" aria-live="polite">
      <span className="node data"><i />Your data</span>
      <span className="wire"><b className="pulse" /></span>
      <span className="node ai"><i />Free AI</span>
      <span className="route-label">{label}</span>
    </div>
  );
}

function Sources({ citations }) {
  const [open, setOpen] = useState(null);
  if (!citations?.length) return null;
  return (
    <div className="sources">
      {citations.map((c) => (
        <div key={c.index} className="source">
          <button className="chip" onClick={() => setOpen(open === c.index ? null : c.index)} aria-expanded={open === c.index}>
            [{c.index}] {c.title || c.doc_id}
          </button>
          {open === c.index && <p className="snippet">{c.snippet}</p>}
        </div>
      ))}
    </div>
  );
}

function Message({ m }) {
  const [saved, setSaved] = useState(false);
  if (m.role === "user") return <div className="msg user">{m.content}</div>;
  const src = m.meta?.source;
  const badge =
    src === "database" ? "From your data" : src === "search" ? "Fast search · your data" : src === "cache" ? "Saved answer" : src === "ai" ? `AI · ${m.meta.provider}` : src === "local" ? "Instant reply" : src === "file" ? (m.meta.provider === "pdf" ? "PDF" : `Image · ${m.meta.provider === "local" ? "own AI" : m.meta.provider}${m.meta.style ? ` · ${m.meta.style}` : ""}`) : src === "none" ? "Not found" : null;

  const kind = src === "file" ? (m.meta.provider === "pdf" ? "kind-pdf" : "kind-image") : "";

  async function save() {
    await api.saveQA({ question: m.question, answer: m.content });
    setSaved(true);
  }

  return (
    <div className={`msg bot ${src || ""} ${kind}`}>
      {badge && (
        <span className={`badge ${src === "search" || src === "local" ? "database" : src} ${kind}`}>
          {badge}{m.meta?.note?.startsWith("answered offline") ? " · offline" : ""}
        </span>
      )}
      <MarkdownBody text={m.content} streaming={m.streaming} />
      {m.error && <p className="error">{m.error}</p>}
      {(m.files || m.meta?.files || []).map((f) => <FileCard key={f.id} f={f} />)}
      <Sources citations={m.meta?.citations} />
      {src === "ai" && !m.streaming && m.content && m.question && (
        <button className="link" onClick={save} disabled={saved}>
          {saved ? "Saved to my data" : "Save to my data"}
        </button>
      )}
    </div>
  );
}

function History({ current, onOpen, onNew, refresh }) {
  const [items, setItems] = useState([]);
  useEffect(() => { api.conversations().then(setItems).catch(() => {}); }, [refresh]);
  return (
    <aside className="history" aria-label="Chat history">
      <button className="btn small" onClick={onNew}>New chat</button>
      <ul>
        {items.map((c) => (
          <li key={c.id} className={c.id === current ? "on" : ""}>
            <button className="hist-item" onClick={() => onOpen(c.id)} title={c.title}>{c.title || "Untitled"}</button>
            <button className="icon tiny" aria-label="Delete chat" onClick={async () => {
              if (!confirm("Delete this chat?")) return;
              await api.deleteConversation(c.id);
              setItems((s) => s.filter((x) => x.id !== c.id));
              if (c.id === current) onNew();
            }}>×</button>
          </li>
        ))}
        {items.length === 0 && <li className="muted small">Your chats appear here.</li>}
      </ul>
    </aside>
  );
}

export default function Chat() {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("idle");
  const [convId, setConvId] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const endRef = useRef(null);
  const [fast, setFast] = useState(() => {
    try { return localStorage.getItem("fastSearch") === "1"; } catch { return false; }
  });
  const toggleFast = (v) => {
    setFast(v);
    try { localStorage.setItem("fastSearch", v ? "1" : "0"); } catch { /* ignore */ }
  };

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs]);

  async function open(id) {
    if (busy) return;
    const c = await api.conversation(id);
    setConvId(id);
    setPhase("idle");
    setMsgs(c.messages.map((m) => ({ role: m.role, content: m.content, meta: m.meta, error: m.error })));
  }

  function newChat() {
    if (busy) return;
    setConvId(null);
    setMsgs([]);
    setPhase("idle");
  }

  async function send() {
    const q = input.trim();
    if (!q || busy) return;
    setMsgs((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "", streaming: true, question: q }]);
    setInput("");
    setBusy(true);
    setPhase("searching");
    const patch = (fn) => setMsgs((m) => [...m.slice(0, -1), fn(m[m.length - 1])]);
    try {
      await streamChat({ question: q, mode: fast ? "search" : "ai", conversation_id: convId }, (ev) => {
        if (ev.type === "meta") {
          if (ev.conversation_id && ev.conversation_id !== convId) { setConvId(ev.conversation_id); setRefresh((x) => x + 1); }
          if (!ev.source) return;
          setPhase(ev.source === "search" ? "database" : ev.source === "none" ? "idle" : ev.source);
          patch((x) => ({ ...x, meta: ev }));
        } else if (ev.type === "token") {
          patch((x) => ({ ...x, content: x.content + ev.text }));
        } else if (ev.type === "file") {
          setPhase("file");
          patch((x) => ({ ...x, files: [...(x.files || []), ev.file] }));
        } else if (ev.type === "error") {
          setPhase("idle");
          patch((x) => ({ ...x, error: ev.message }));
        }
      });
    } catch (e) {
      setPhase("idle");
      patch((x) => ({ ...x, error: `${e.message}. Check the Backend connection panel.` }));
    }
    patch((x) => ({ ...x, streaming: false }));
    setBusy(false);
  }

  return (
    <div className="chat-wrap">
      <History current={convId} onOpen={open} onNew={newChat} refresh={refresh} />
      <section className="chat">
        <header className="chat-head">
          <h1>Ask my data</h1>
          <Route phase={phase} />
        </header>
        <div className="thread">
          {msgs.length === 0 && (
            <div className="empty">
              <h2>Ask anything, in English, Hindi or Hinglish. Your own records are searched first.</h2>
              <p>If the answer isn’t there, your free AI providers take over, and each answer shows where it came from.</p>
              <p className="muted small">It can also make files: try “green theme mein leave policy ki PDF bana do”, “iski PDF bana do” after an answer, or “cartoon style mein ek office ki image banao” (add “online” to use an online service instead of your own AI).</p>
            </div>
          )}
          {msgs.map((m, i) => (
            <Message key={i} m={m} />
          ))}
          <div ref={endRef} />
        </div>
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          <textarea
            rows={1}
            value={input}
            placeholder="Type a question"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button className="btn" disabled={busy || !input.trim()}>Ask</button>
        </form>
        <label className="check" style={{ marginBottom: 12 }}>
          <input type="checkbox" checked={fast} onChange={(e) => toggleFast(e.target.checked)} />
          Fast search: show matching records straight from my data, no AI (instant)
        </label>
      </section>
    </div>
  );
}
