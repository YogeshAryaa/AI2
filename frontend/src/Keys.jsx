import { useEffect, useState } from "react";
import { api, conn } from "./api.js";

const SCOPES = [
  ["chat", "Ask questions (chat + history)"],
  ["search", "Search records (no AI)"],
  ["ingest", "Add / update / delete data"],
];

function Snippets({ keyText, ns }) {
  const [tab, setTab] = useState("js");
  const base = conn().base;
  const key = keyText || "rk_xxxxxxxx_your_key";
  const code = {
    js: `// Node.js / your system's BACKEND (never put the key in browser code)
const RAG = "${base}";
const KEY = process.env.RAG_API_KEY; // ${key.slice(0, 12)}...

// 1) Ask a question (history is saved per user of your system)
const r = await fetch(RAG + "/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": KEY },
  body: JSON.stringify({
    question: "Pune me Java developers kaun hai?",
    user_id: currentUser.id,           // keeps each user's chats separate
    conversation_id: savedConversationId // optional: continue a chat
  }),
});
const { answer, source, citations, conversation_id } = await r.json();

// 2) Push a record whenever it is created or changed (searchable in ~1 s)
await fetch(RAG + "/records", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": KEY },
  body: JSON.stringify({
    collection: "candidates",
    records: [{ id: String(c._id), title: c.name, fields: { name: c.name, skills: c.skills, city: c.city } }],
  }),
});

// 3) When it is deleted
await fetch(RAG + "/records/candidates/" + id, { method: "DELETE", headers: { "X-API-Key": KEY } });`,
    py: `# Python backend (Django / Flask / FastAPI)
import os, requests
RAG, KEY = "${base}", os.environ["RAG_API_KEY"]
H = {"X-API-Key": KEY}

r = requests.post(f"{RAG}/chat", headers=H, json={
    "question": "Diwali campaign ka budget kitna tha?",
    "user_id": str(user.id),
}, timeout=120)
print(r.json()["answer"])

# push / update records in bulk (up to 1000 per call)
requests.post(f"{RAG}/records", headers=H, json={
    "collection": "campaigns",
    "records": [{"id": str(c.id), "title": c.name, "text": c.description} for c in changed],
}, timeout=300)`,
    curl: `curl -X POST ${base}/chat \\
  -H "X-API-Key: ${key}" -H "Content-Type: application/json" \\
  -d '{"question": "Ravi ka phone number?", "user_id": "42"}'

curl -X POST ${base}/search \\
  -H "X-API-Key: ${key}" -H "Content-Type: application/json" \\
  -d '{"query": "React developer Delhi", "top_k": 5}'`,
  };
  return (
    <div className="snip">
      <div className="tabs" role="tablist">
        {[["js", "JavaScript"], ["py", "Python"], ["curl", "curl"]].map(([id, l]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>{l}</button>
        ))}
      </div>
      <pre className="code">{code[tab]}</pre>
      <p className="hint">This key only sees the data groups: <strong>{ns}</strong>. Full reference: <a href={`${base}/docs`} target="_blank" rel="noreferrer">{base}/docs</a> and docs/INTEGRATION.md.</p>
    </div>
  );
}

function CreateKey({ onCreated }) {
  const [name, setName] = useState("");
  const [ns, setNs] = useState("");
  const [scopes, setScopes] = useState(["chat", "search"]);
  const [rpm, setRpm] = useState(120);
  const [err, setErr] = useState("");
  const toggle = (s) => setScopes((x) => (x.includes(s) ? x.filter((y) => y !== s) : [...x, s]));

  async function submit(e) {
    e.preventDefault();
    setErr("");
    try {
      const sc = scopes.includes("chat") ? [...new Set([...scopes, "history"])] : scopes;
      const k = await api.addKey({ name, namespaces: ns.split(",").map((x) => x.trim()).filter(Boolean), scopes: sc, rpm: Number(rpm) || 0 });
      setName(""); setNs("");
      onCreated(k);
    } catch (e2) { setErr(e2.message); }
  }

  return (
    <form className="add" onSubmit={submit}>
      <h2>New API key for a system</h2>
      <div className="grid2">
        <label>Name<input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Staffing portal" /></label>
        <label>Data groups it can use (comma separated)<input required value={ns} onChange={(e) => setNs(e.target.value.toLowerCase())} placeholder="staffing" /></label>
      </div>
      <div className="chips">
        {SCOPES.map(([s, label]) => (
          <button type="button" key={s} className={`chip ${scopes.includes(s) ? "on" : ""}`} aria-pressed={scopes.includes(s)} onClick={() => toggle(s)}>{label}</button>
        ))}
      </div>
      <label>Requests per minute for this key (0 = no limit)<input type="number" min="0" value={rpm} onChange={(e) => setRpm(e.target.value)} /></label>
      {err && <p className="error">{err}</p>}
      <button className="btn" disabled={!scopes.length}>Create key</button>
    </form>
  );
}

export default function Keys() {
  const [items, setItems] = useState(null);
  const [fresh, setFresh] = useState(null);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const load = () => api.keys().then(setItems).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const act = (fn) => async () => { setErr(""); try { await fn(); load(); } catch (e) { setErr(e.message); } };

  return (
    <section className="page">
      <h1>Connect your systems</h1>
      <p className="lead">Give each system (staffing, marketing, …) its own key. A key only sees its own data groups, has its own rate limit, and can be switched off at any time. Keep keys on your systems' servers, never in browser code.</p>
      {fresh && (
        <div className="new-key" role="alert">
          <strong>Key for “{fresh.name}”. Copy it now, it is not shown again.</strong>
          <div className="key-line">
            <code>{fresh.key}</code>
            <button className="btn small" onClick={async () => { await navigator.clipboard.writeText(fresh.key); setCopied(true); }}>{copied ? "Copied" : "Copy"}</button>
          </div>
          <Snippets keyText={fresh.key} ns={fresh.namespaces.join(", ")} />
          <button className="link" onClick={() => { setFresh(null); setCopied(false); }}>Done, hide the key</button>
        </div>
      )}
      {err && <p className="error">{err}</p>}
      {items && items.length === 0 && !fresh && <p className="empty-note">No keys yet. Create one below for each system that should use this AI.</p>}
      {items && items.length > 0 && (
        <ul className="docs">
          {items.map((k) => (
            <li key={k.id} className={k.enabled ? "" : "off"}>
              <div>
                <strong>{k.name}</strong> <code className="small">{k.prefix}…</code>
                <div className="muted small">
                  groups: {k.namespaces.join(", ")} · can: {k.scopes.join(", ")} · {k.rpm ? `${k.rpm}/min` : "no limit"} · last used {k.last_used ? new Date(k.last_used).toLocaleString() : "never"}
                  {!k.enabled && " · OFF"}
                </div>
              </div>
              <div>
                <button className="link" onClick={act(() => api.patchKey(k.id, { enabled: !k.enabled }))}>{k.enabled ? "Turn off" : "Turn on"}</button>
                <button className="link" onClick={() => confirm(`Issue a new secret for “${k.name}”? The old one stops working.`) && act(async () => { const r = await api.rotateKey(k.id); setFresh(r); setCopied(false); })()}>New secret</button>
                <button className="link danger" onClick={() => confirm(`Delete key “${k.name}”? Systems using it stop working.`) && act(() => api.deleteKey(k.id))()}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <CreateKey onCreated={(k) => { setFresh(k); setCopied(false); load(); }} />
      {!fresh && (
        <>
          <h2>How a system uses its key</h2>
          <Snippets ns="(the key's groups)" />
        </>
      )}
    </section>
  );
}
