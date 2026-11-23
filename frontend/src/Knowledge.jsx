import { useEffect, useRef, useState } from "react";
import { api, waitJob } from "./api.js";

const NS_KEY = "rag_namespace";
const getNs = () => { try { return localStorage.getItem(NS_KEY) || "default"; } catch { return "default"; } };

function NamespacePicker({ ns, setNs, known }) {
  return (
    <label className="ns-pick">
      Data group (namespace)
      <input list="ns-list" value={ns} onChange={(e) => setNs(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))} placeholder="default" />
      <datalist id="ns-list">{known.map((n) => <option key={n.namespace} value={n.namespace}>{n.documents} items</option>)}</datalist>
      <span className="hint">Keep each system's data in its own group, e.g. staffing, marketing. API keys can be limited to groups.</span>
    </label>
  );
}

function AddText({ ns, done }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  async function submit(e) {
    e.preventDefault();
    try {
      const r = await api.ingestText({ title, text, namespace: ns });
      setMsg(`Added to “${r.namespace}” (${r.chunks} pieces)`);
      setTitle(""); setText(""); done();
    } catch (err) { setMsg(err.message); }
  }
  return (
    <form onSubmit={submit} className="stack">
      <label>Title<input required value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>Text<textarea required rows={6} value={text} onChange={(e) => setText(e.target.value)} /></label>
      <button className="btn">Add to my data</button>
      {msg && <p className="hint">{msg}</p>}
    </form>
  );
}

const ACCEPT = ".pdf,.docx,.txt,.md,.html,.csv,.tsv,.xlsx,.json,.jsonl,.ndjson";

function AddFiles({ ns, done }) {
  const [rows, setRows] = useState([]);
  const [over, setOver] = useState(false);
  const [titleField, setTitleField] = useState("");
  const set = (i, v) => setRows((r) => r.map((x, k) => (k === i ? { ...x, ...v } : x)));

  async function upload(list) {
    const files = [...list];
    if (!files.length) return;
    const start = rows.length;
    setRows((r) => [...r, ...files.map((f) => ({ name: f.name, status: "queued" }))]);
    for (let i = 0; i < files.length; i++) {
      const idx = start + i;
      try {
        set(idx, { status: "uploading" });
        const job = await api.ingestFile(files[i], ns, titleField.trim());
        const j = await waitJob(job, (s) => set(idx, { status: s.status, job: s }));
        set(idx, { status: j.status, job: j });
      } catch (e) {
        set(idx, { status: "failed", error: e.message });
      }
    }
    done();
  }

  return (
    <div className="stack">
      <label
        className={`drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}
      >
        <strong>Drop files here, or click to choose (several at once is fine)</strong>
        <span className="hint">Documents: PDF, Word, text, Markdown, HTML. Bulk data: CSV, Excel, JSON, JSONL, one record per row.</span>
        <input type="file" multiple accept={ACCEPT} hidden onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
      </label>
      <label>Column to use as the record title (optional, for CSV/Excel/JSON)
        <input value={titleField} onChange={(e) => setTitleField(e.target.value)} placeholder="e.g. name (guessed if empty)" />
      </label>
      <p className="hint">Uploading a file with the same name again updates its records instead of duplicating them.</p>
      {rows.length > 0 && (
        <ul className="docs">
          {rows.map((r, i) => {
            const j = r.job;
            const pct = j?.total ? Math.min(100, Math.round(((j.processed + j.skipped) / j.total) * 100)) : null;
            return (
              <li key={i} className="file-row">
                <div style={{ flex: 1 }}>
                  <strong>{r.name}</strong>
                  <div className="bar"><i className={r.status === "running" && pct === null ? "run" : ""} style={{ width: r.status === "done" ? "100%" : pct !== null ? `${pct}%` : undefined }} /></div>
                  <span className="muted small">
                    {r.status === "queued" && "Waiting…"}
                    {r.status === "uploading" && "Uploading…"}
                    {r.status === "running" && `Indexing… ${j?.processed || 0}${j?.total ? ` of ${j.total}` : ""}`}
                    {r.status === "done" && `Done: ${j.processed} record${j.processed === 1 ? "" : "s"}${j.skipped ? `, ${j.skipped} empty skipped` : ""} · ${j.chunks} pieces`}
                  </span>
                  {r.status === "failed" && <div className="small error">{r.error || j?.error}</div>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ImportCollection({ ns, done }) {
  const [dbs, setDbs] = useState(null);
  const [database, setDatabase] = useState("");
  const [cols, setCols] = useState([]);
  const [col, setCol] = useState("");
  const [info, setInfo] = useState(null);
  const [fields, setFields] = useState([]);
  const [title, setTitle] = useState("");
  const [updated, setUpdated] = useState("");
  const [filter, setFilter] = useState("");
  const [job, setJob] = useState(null);
  const [auto, setAuto] = useState(true);
  const [mins, setMins] = useState(5);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => { api.databases().then((d) => { setDbs(d); setDatabase(d.default); }).catch((e) => setErr(e.message)); }, []);
  useEffect(() => {
    if (!database) return;
    setCol(""); setInfo(null);
    api.collections(database).then(setCols).catch((e) => setErr(e.message));
  }, [database]);
  const dbParam = dbs && database !== dbs.default ? database : undefined;

  async function choose(name) {
    setCol(name); setFields([]); setTitle(""); setJob(null); setMsg(""); setErr("");
    try {
      const i = await api.sample(name, dbParam);
      setInfo(i);
      setUpdated(i.updated_field_guess || "");
    } catch (e) { setErr(e.message); }
  }
  const toggle = (f) => setFields((s) => (s.includes(f) ? s.filter((x) => x !== f) : [...s, f]));

  async function start() {
    setErr(""); setMsg("");
    let flt = {};
    if (filter.trim()) {
      try { flt = JSON.parse(filter); } catch { setErr("Filter is not valid JSON"); return; }
    }
    const base = { collection: col, database: dbParam, text_fields: fields, title_field: title || null, filter: flt, namespace: ns };
    try {
      if (auto) {
        const s = await api.addSyncSource({ ...base, updated_field: updated || null, interval_minutes: Number(mins) || 5 });
        setMsg(`Connected “${s.id}”. The first sync is running; after that, changes are picked up automatically.`);
        done();
        return;
      }
      const j = await waitJob(await api.ingestCollection(base), setJob);
      if (j.status === "done") done();
    } catch (e) { setErr(e.message); }
  }

  return (
    <div className="stack">
      <div className="grid2">
        <label>Database
          <select value={database} onChange={(e) => setDatabase(e.target.value)}>
            {(dbs?.databases || []).map((d) => <option key={d}>{d}</option>)}
          </select>
        </label>
        <label>Collection
          <select value={col} onChange={(e) => choose(e.target.value)}>
            <option value="" disabled>Choose a collection…</option>
            {cols.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
      </div>
      {info && (
        <>
          <p className="hint">{info.count.toLocaleString()} records. Pick the fields to search (none selected = all fields; fields that look like passwords or tokens are skipped).</p>
          <div className="chips">
            {info.fields.filter((f) => f !== "_id").map((f) => (
              <button type="button" key={f} className={`chip ${fields.includes(f) ? "on" : ""} ${info.sensitive_fields.includes(f) ? "secret" : ""}`} onClick={() => toggle(f)} aria-pressed={fields.includes(f)} title={info.sensitive_fields.includes(f) ? "Looks secret: only indexed if you select it" : ""}>{f}</button>
            ))}
          </div>
          <div className="grid2">
            <label>Field to use as title (optional)
              <select value={title} onChange={(e) => setTitle(e.target.value)}>
                <option value="">Record id</option>
                {info.fields.map((f) => <option key={f}>{f}</option>)}
              </select>
            </label>
            <label>“Last updated” field (makes sync read only changes)
              <select value={updated} onChange={(e) => setUpdated(e.target.value)}>
                <option value="">None</option>
                {info.fields.map((f) => <option key={f}>{f}</option>)}
              </select>
            </label>
          </div>
          <details>
            <summary className="link">Only some records (filter)</summary>
            <label>MongoDB filter as JSON, e.g. {`{"status": "active"}`}
              <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="{}" />
            </label>
          </details>
          <label className="check">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            Keep in sync automatically (new, changed and deleted records are picked up by themselves)
          </label>
          {auto && (
            <label>Also check for changes every (minutes). With MongoDB Atlas or a replica set, changes arrive instantly anyway.
              <input type="number" min="1" max="1440" value={mins} onChange={(e) => setMins(e.target.value)} />
            </label>
          )}
          <button className="btn" onClick={start} disabled={job?.status === "running"}>{auto ? `Connect to “${ns}” and keep in sync` : "Import once"}</button>
        </>
      )}
      {job && (
        <div className="job">
          <div className="bar"><i className={job.status === "running" && !job.total ? "run" : ""} style={{ width: job.status === "done" ? "100%" : job.total ? `${Math.round((job.processed / job.total) * 100)}%` : undefined }} /></div>
          <p className="hint">
            {job.status === "running" && `Importing… ${job.processed}${job.total ? ` of ${job.total}` : ""}`}
            {job.status === "done" && `Imported ${job.processed} records${job.skipped ? `, skipped ${job.skipped} empty` : ""}`}
            {job.status === "failed" && `Failed: ${job.error}`}
          </p>
        </div>
      )}
      {msg && <p className="hint">{msg}</p>}
      {err && <p className="error">{err}</p>}
      <ConnectedSources onChange={done} />
    </div>
  );
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function ConnectedSources({ onChange }) {
  const [items, setItems] = useState(null);
  const [err, setErr] = useState("");
  const seen = useRef("");

  const load = () => api.syncSources().then((list) => {
    setItems(list);
    const sig = list.map((s) => `${s.id}:${s.last_sync_ts}:${s.last_event_at}`).join("|");
    if (seen.current && sig !== seen.current) onChange();
    seen.current = sig;
  }).catch((e) => setErr(e.message));

  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  const act = (fn) => async () => { setErr(""); try { await fn(); await load(); } catch (e) { setErr(e.message); } };
  if (!items || items.length === 0) return null;

  return (
    <div className="stack">
      <h2>Connected collections</h2>
      <ul className="docs">
        {items.map((s) => {
          const r = s.last_result;
          return (
            <li key={s.id}>
              <div>
                <strong>{s.id}</strong> {s.live && <span className="tag live">live</span>} <span className="tag">{s.namespace}</span>
                <span className="muted small">
                  {" "}{!s.enabled ? "paused" : s.running ? "syncing…" : `checked ${ago(s.last_sync_ts)}`} · every {s.interval_minutes} min
                  {s.updated_field && ` · reads only changes (${s.updated_field})`}
                  {r && !s.running && ` · last ${r.kind || "run"}: ${r.added} new, ${r.updated} changed, ${r.removed} removed${r.failed ? `, ${r.failed} failed` : ""}`}
                </span>
                {s.last_error && <div className="small error">{s.last_error}</div>}
                {!s.live && s.live_error && <div className="small muted">{s.live_error}</div>}
              </div>
              <div>
                <button className="link" disabled={s.running} onClick={act(() => api.runSyncSource(s.id))}>Full sync now</button>
                <button className="link" onClick={act(() => api.patchSyncSource(s.id, { enabled: !s.enabled }))}>{s.enabled ? "Pause" : "Resume"}</button>
                {!s.auto && <button className="link danger" onClick={() => confirm(`Disconnect “${s.id}” and remove its synced data?`) && act(() => api.deleteSyncSource(s.id))()}>Disconnect</button>}
              </div>
            </li>
          );
        })}
      </ul>
      {err && <p className="error">{err}</p>}
    </div>
  );
}

function Settings() {
  const [s, setS] = useState(null);
  const [st, setSt] = useState(null);
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.settings().then(setS).catch(() => {});
    const loadSt = () => api.status().then(setSt).catch(() => {});
    loadSt();
    const t = setInterval(loadSt, 5000);
    return () => clearInterval(t);
  }, []);
  if (!s) return null;
  const set = (k, v) => { setSaved(""); setS({ ...s, [k]: v }); };
  const cal = s.calibration;
  const save = async () => {
    const { calibration, effective_min_score, effective_ai_min_score, ...rest } = s;
    setS(await api.saveSettings(rest));
    setSaved("Saved");
  };

  return (
    <div className="stack">
      <fieldset className="stack plain">
        <legend><strong>How closely data must match a question</strong></legend>
        <label className="check"><input type="radio" checked={s.min_score_mode === "auto"} onChange={() => set("min_score_mode", "auto")} />Automatic (measured from your own data, recommended)</label>
        <label className="check"><input type="radio" checked={s.min_score_mode === "manual"} onChange={() => set("min_score_mode", "manual")} />Manual</label>
        {s.min_score_mode === "auto" ? (
          <p className="hint">
            {cal?.ok
              ? `Current level ${s.effective_min_score.toFixed(2)} (off-topic questions score about ${cal.noise}, your data about ${cal.signal}). Re-measured automatically as your data grows.`
              : cal?.reason || "Not measured yet. It runs once you have at least 20 pieces of data."}
          </p>
        ) : (
          <label>Match level: <strong>{s.min_score.toFixed(2)}</strong>
            <input type="range" min="0.2" max="0.95" step="0.01" value={s.min_score} onChange={(e) => set("min_score", Number(e.target.value))} />
          </label>
        )}
        <button className="btn small" type="button" disabled={busy} onClick={async () => {
          setBusy(true); setSaved("");
          try { setS(await api.calibrate()); setSaved("Measured again"); } catch (e) { setSaved(e.message); }
          setBusy(false);
        }}>{busy ? "Measuring…" : "Measure now"}</button>
      </fieldset>
      <label className="check"><input type="checkbox" checked={s.ai_fallback} onChange={(e) => set("ai_fallback", e.target.checked)} />When the answer is not in my data, let a general AI answer</label>
      <label className="check"><input type="checkbox" checked={s.query_rewrite} onChange={(e) => set("query_rewrite", e.target.checked)} />If a search misses, retry once with an AI-rewritten English query (helps Hinglish)</label>
      <label className="check"><input type="checkbox" checked={s.cache_enabled} onChange={(e) => set("cache_enabled", e.target.checked)} />Reuse saved answers for repeated questions</label>
      <label className="check"><input type="checkbox" checked={!s.send_context_to_external} onChange={(e) => set("send_context_to_external", !e.target.checked)} />Keep my data on local providers only (e.g. Ollama)</label>
      <button className="btn small" onClick={save}>Save settings</button>
      {saved && <p className="hint">{saved}</p>}
      {st && (
        <div className="status-box small">
          <strong>Index</strong>
          <div>{st.chunks.toLocaleString()} pieces from {st.documents.toLocaleString()} items · model {st.embedding_model}{st.reranker ? ` · re-ranker ${st.reranker}` : ""}</div>
          <div>Vector search: {st.vector_backend}{st.vector_index ? ` · ${st.vector_index.rows.toLocaleString()} vectors in memory (${st.vector_index.ram_mb} MB)` : ""}</div>
          {st.reindex && <div className="warn">Re-embedding after a model change: {st.reindex.done.toLocaleString()} of {st.reindex.total.toLocaleString()}</div>}
        </div>
      )}
    </div>
  );
}

export default function Knowledge() {
  const [docs, setDocs] = useState(null);
  const [mode, setMode] = useState("file");
  const [err, setErr] = useState("");
  const [ns, setNsState] = useState(getNs);
  const [known, setKnown] = useState([]);
  const [q, setQ] = useState("");
  const [view, setView] = useState("");
  const setNs = (v) => { setNsState(v); try { localStorage.setItem(NS_KEY, v || "default"); } catch {} };

  const load = (more = false) => {
    api.namespaces().then(setKnown).catch(() => {});
    return api.documents({ q, namespace: view, skip: more ? docs?.items.length : 0 })
      .then((d) => setDocs(more ? { ...d, items: [...docs.items, ...d.items] } : d))
      .catch((e) => setErr(e.message));
  };
  useEffect(() => { const t = setTimeout(() => load(), 250); return () => clearTimeout(t); }, [q, view]);

  return (
    <section className="page">
      <h1>My data</h1>
      <p className="lead">Everything here is searched before any AI is asked.</p>
      {mode !== "match" && <NamespacePicker ns={ns} setNs={setNs} known={known} />}
      <div className="tabs" role="tablist">
        {[["file", "Upload files / bulk"], ["text", "Write text"], ["col", "MongoDB (auto-sync)"], ["match", "Settings"]].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={mode === id} className={mode === id ? "tab active" : "tab"} onClick={() => setMode(id)}>{label}</button>
        ))}
      </div>
      <div className="panel">
        {mode === "text" && <AddText ns={ns || "default"} done={() => load()} />}
        {mode === "file" && <AddFiles ns={ns || "default"} done={() => load()} />}
        {mode === "col" && <ImportCollection ns={ns || "default"} done={() => load()} />}
        {mode === "match" && <Settings />}
      </div>
      <h2>Saved items {docs && <span className="muted">({docs.total.toLocaleString()})</span>}</h2>
      <div className="grid2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find by title" aria-label="Find by title" />
        <select value={view} onChange={(e) => setView(e.target.value)} aria-label="Data group">
          <option value="">All groups</option>
          {known.map((n) => <option key={n.namespace} value={n.namespace}>{n.namespace} ({n.documents})</option>)}
        </select>
      </div>
      {err && <p className="error">{err}</p>}
      {docs && docs.items.length === 0 && <p className="empty-note">Nothing here yet. Upload a file, write text, or connect a collection above.</p>}
      <ul className="docs">
        {docs?.items.map((d) => (
          <li key={d.id}>
            <div><strong>{d.title}</strong><span className="muted small"> {d.chunk_count} pieces · {d.source} · {d.namespace}</span></div>
            <div>
              {d.source?.startsWith("file:") && d.metadata?.row && (
                <button className="link danger" onClick={() => confirm(`Remove every row of ${d.metadata.file}?`) && api.deleteSource(d.source, d.namespace).then(() => load())}>Remove file</button>
              )}
              <button className="link danger" onClick={() => confirm(`Remove “${d.title}”?`) && api.deleteDocument(d.id).then(() => load())}>Remove</button>
            </div>
          </li>
        ))}
      </ul>
      {docs && docs.items.length < docs.total && <button className="btn small" onClick={() => load(true)}>Show more</button>}
    </section>
  );
}
