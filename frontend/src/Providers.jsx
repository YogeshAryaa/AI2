import { useEffect, useState } from "react";
import { api } from "./api.js";

const EMPTY = { name: "", base_url: "", model: "", api_key: "", trusted: false, rpm_limit: 0, timeout_seconds: 0 };

function AddForm({ presets, onAdded, count }) {
  const [f, setF] = useState(EMPTY);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((s) => ({ ...s, [k]: v }));

  function pick(key) {
    const p = presets.find((x) => x.key === key);
    if (!p) return;
    setF({ ...EMPTY, name: p.name, base_url: p.base_url, model: p.model_hint, trusted: p.trusted, timeout_seconds: p.timeout_seconds || 0 });
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api.addProvider({ ...f, rpm_limit: Number(f.rpm_limit) || 0, timeout_seconds: Number(f.timeout_seconds) || 0, priority: (count + 1) * 10 });
      setF(EMPTY);
      onAdded();
    } catch (e2) {
      setErr(e2.message);
    }
    setBusy(false);
  }

  return (
    <form className="add" onSubmit={submit}>
      <h2>Add an AI provider</h2>
      <label>
        Start from
        <select onChange={(e) => pick(e.target.value)} defaultValue="">
          <option value="" disabled>Choose a provider…</option>
          {presets.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
        </select>
      </label>
      <div className="grid2">
        <label>Name<input required value={f.name} onChange={(e) => set("name", e.target.value)} /></label>
        <label>Model<input required value={f.model} onChange={(e) => set("model", e.target.value)} /></label>
      </div>
      <label>Base URL<input required value={f.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="https://api.example.com/v1" /></label>
      <label>API key<input type="password" value={f.api_key} onChange={(e) => set("api_key", e.target.value)} autoComplete="off" /></label>
      <div className="grid2">
        <label>Requests per minute (0 = no limit)<input type="number" min="0" value={f.rpm_limit} onChange={(e) => set("rpm_limit", e.target.value)} /></label>
        <label>Wait at most (seconds, 0 = 60; local models need ~300)<input type="number" min="0" max="900" value={f.timeout_seconds} onChange={(e) => set("timeout_seconds", e.target.value)} /></label>
      </div>
      <div className="grid2">
        <label className="check">
          <input type="checkbox" checked={f.trusted} onChange={(e) => set("trusted", e.target.checked)} />
          Runs on my own machine (safe for private data)
        </label>
      </div>
      {err && <p className="error">{err}</p>}
      <button className="btn" disabled={busy}>{busy ? "Adding…" : "Add provider"}</button>
    </form>
  );
}

function Row({ p, i, last, reload, move }) {
  const [test, setTest] = useState(null);
  const [models, setModels] = useState(null);
  const [msg, setMsg] = useState("");
  const cooling = p.status.state === "cooling_down";
  const demoted = p.status.state === "demoted";

  const run = async (fn) => {
    setMsg("");
    try { await fn(); } catch (e) { setMsg(e.message); }
  };

  const dotClass = !p.enabled ? "off" : demoted ? "demoted" : cooling ? "cool" : "ready";
  const dotTitle = demoted
    ? `Auto-demoted after 3 failures — paused ${p.status.retry_in_seconds}s, next question uses another provider`
    : cooling
    ? `Cooling down ${p.status.retry_in_seconds}s`
    : "Ready";

  return (
    <li className={`prov ${p.enabled ? "" : "off"}`}>
      <span className={`dot ${dotClass}`} title={dotTitle} />
      <div className="prov-body">
        <div className="prov-top">
          <strong>{p.name}</strong>
          <span className="muted">{p.model}</span>
          {p.trusted && <span className="tag">local</span>}
          {demoted && <span className="tag warn-tag">⚠ auto-demoted</span>}
        </div>
        <div className="muted small">
          {p.has_key ? `Key ${p.api_key_masked}` : "No key"} · {p.stats.success || 0} answered · {p.stats.failure || 0} failed
          {demoted && ` · paused ${p.status.retry_in_seconds}s (3 consecutive failures)`}
          {cooling && !demoted && ` · paused ${p.status.retry_in_seconds}s`}
        </div>
        {p.stats.last_error && <div className="small warn">Last error: {p.stats.last_error}</div>}
        {test && <div className={`small ${test.ok ? "okc" : "error"}`}>{test.ok ? `Works (${test.latency_ms} ms). ${test.note || ""}` : test.error}</div>}
        {models && (
          <select className="models" defaultValue="" onChange={(e) => run(async () => {
            const cur = p.model.split(",").map((x) => x.trim()).filter(Boolean);
            await api.patchProvider(p.id, { model: [e.target.value, ...cur.filter((x) => x !== e.target.value)].join(", ") });
            setModels(null); reload();
          })}>
            <option value="" disabled>Use this model first ({models.length})</option>
            {models.map((m) => <option key={m}>{m}</option>)}
          </select>
        )}
        {msg && <div className="small error">{msg}</div>}
      </div>
      <div className="prov-actions">
        <button className="icon" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Try earlier">↑</button>
        <button className="icon" disabled={last} onClick={() => move(i, 1)} aria-label="Try later">↓</button>
        <button className="link" onClick={() => run(async () => setTest(await api.testProvider(p.id)))}>Test</button>
        <button className="link" onClick={() => run(async () => setModels((await api.models(p.id)).models))}>Models</button>
        <button className="link" onClick={() => { const v = prompt("Models, separated by commas (tried in this order):", p.model); if (v && v.trim()) run(async () => { await api.patchProvider(p.id, { model: v.trim() }); reload(); }); }}>Edit models</button>
        <button className="link" onClick={() => run(async () => { await api.patchProvider(p.id, { enabled: !p.enabled }); reload(); })}>{p.enabled ? "Turn off" : "Turn on"}</button>
        <button className="link danger" onClick={() => confirm(`Delete ${p.name}?`) && run(async () => { await api.deleteProvider(p.id); reload(); })}>Delete</button>
      </div>
    </li>
  );
}

export default function Providers() {
  const [items, setItems] = useState(null);
  const [presets, setPresets] = useState([]);
  const [err, setErr] = useState("");

  const load = () => api.providers().then(setItems).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
    api.presets().then(setPresets).catch(() => {});
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  async function move(i, dir) {
    const list = [...items];
    [list[i], list[i + dir]] = [list[i + dir], list[i]];
    setItems(list);
    await Promise.all(list.map((p, k) => api.patchProvider(p.id, { priority: (k + 1) * 10 })));
    load();
  }

  return (
    <section className="page">
      <h1>AI providers</h1>
      <p className="lead">These write the answers. They are tried from top to bottom; if one is busy or out of quota, the next one steps in. Tip: put a fast free cloud API (Groq, Gemini) first and a local Ollama last, so answers stay quick and never stop.</p>
      {err && <p className="error">{err}. Check the Backend connection panel.</p>}
      {items && items.length === 0 && <p className="empty-note">No providers yet. Add one below to enable AI answers.</p>}
      {items && (
        <ol className="chain">
          {items.map((p, i) => <Row key={p.id} p={p} i={i} last={i === items.length - 1} reload={load} move={move} />)}
        </ol>
      )}
      <AddForm presets={presets} onAdded={load} count={items?.length || 0} />
    </section>
  );
}
