import { useEffect, useState } from "react";
import { api } from "./api.js";
import { FileCard } from "./Files.jsx";

// Everything here runs on your own server: no AI service is called.
const TOOLS = [
  ["scan", "Scan & read"],
  ["resume", "Resume builder"],
  ["poster", "Poster maker"],
  ["image", "AI image"],
];
const READ_ACCEPT = ".pdf,.docx,.txt,.md,.html,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff";
const IMG_ACCEPT = ".png,.jpg,.jpeg,.webp";

const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const keep = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } };

function Drop({ accept, onFile, busy, title, hint }) {
  const [over, setOver] = useState(false);
  return (
    <label
      className={`drop ${over ? "over" : ""} ${busy ? "busy" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (!busy && e.dataTransfer.files[0]) onFile(e.dataTransfer.files[0]); }}
    >
      <strong>{title}</strong>
      <span className="hint">{hint}</span>
      <input type="file" accept={accept} hidden disabled={busy} onChange={(e) => { if (e.target.files[0]) onFile(e.target.files[0]); e.target.value = ""; }} />
    </label>
  );
}

function Working({ text }) {
  return (
    <div className="working" role="status">
      <div className="bar"><i className="run" /></div>
      <span className="hint">{text}</span>
    </div>
  );
}

const isImage = (f) => /\.(png|jpe?g|webp|bmp|tiff?|gif)$/i.test(f.name);

// ---------------------------------------------------------------- Scan & read
function Scan({ onResume }) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState(null);
  const [msg, setMsg] = useState("");

  async function read(file) {
    setBusy(true); setErr(""); setMsg(""); setRes(null);
    setPreview((old) => { if (old) URL.revokeObjectURL(old); return isImage(file) ? URL.createObjectURL(file) : null; });
    try {
      const r = await api.studioRead(file);
      setRes(r); setText(r.text);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  }

  function download() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    a.download = (res?.filename || "text").replace(/\.[^.]+$/, "") + ".txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  async function saveToData() {
    try {
      const r = await api.ingestText({ title: res.filename, text });
      setMsg(`Added to my data (“${r.namespace}”, ${r.chunks} pieces). You can now ask about it in Ask.`);
    } catch (e) { setMsg(e.message); }
  }

  return (
    <div className="stack">
      <Drop accept={READ_ACCEPT} onFile={read} busy={busy}
        title="Drop a PDF, Word file or image (photo, screenshot, scan), or click to choose"
        hint="Text PDFs are read instantly. Images and scanned PDFs are read with OCR (English + Hindi) on your server." />
      {busy && <Working text="Reading… a photo or scanned page can take 20–60 seconds (the very first time also downloads the OCR model)." />}
      {err && <p className="error">{err}</p>}
      {res && (
        <div className="scan-out">
          {preview && <img className="scan-img" src={preview} alt="The uploaded image" />}
          <div className="stack">
            <p className="hint">
              <strong>{res.filename}</strong> · {res.method === "ocr" ? "read with OCR, check the text" : "text read from the file"} · {res.pages} page{res.pages === 1 ? "" : "s"} · {res.words} words
            </p>
            <textarea className="mono" rows={16} value={text} onChange={(e) => setText(e.target.value)} aria-label="Text from the file (you can edit it)" />
            {!text.trim() && <p className="warn small">No text found. Try a sharper, straight photo with good light.</p>}
            <div className="row">
              <button className="btn small" onClick={() => navigator.clipboard.writeText(text)} disabled={!text}>Copy</button>
              <button className="btn small ghost" onClick={download} disabled={!text}>Download .txt</button>
              <button className="btn small ghost" onClick={saveToData} disabled={!text.trim()}>Add to my data</button>
              {res.resume && <button className="btn small ghost" onClick={() => onResume(res.resume, res.filename)}>Make a resume from this</button>}
            </div>
            {msg && <p className="hint">{msg}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Resume builder
const EMPTY_RESUME = {
  name: "", headline: "", email: "", phone: "", location: "", links: "", summary: "", skills: "",
  sections: [
    { heading: "Experience", body: "" },
    { heading: "Education", body: "" },
  ],
  template: "classic", accent: "#2563eb",
};
const QUICK_SECTIONS = ["Experience", "Projects", "Education", "Certifications", "Achievements", "Languages"];
const ACCENTS = ["#2563eb", "#0e9f8e", "#6a57d5", "#c8423b", "#b9651a", "#12203a"];
const BODY_HINT = "Role | Company | 2021 – Present\n- What you did, with a number if you can\n- Another result";

function Resume({ seed }) {
  const [d, setD] = useState(() => load("studio_resume", EMPTY_RESUME));
  const [photo, setPhoto] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  const [file, setFile] = useState(null);

  useEffect(() => { keep("studio_resume", d); }, [d]);
  useEffect(() => {
    if (seed) { fill(seed.data, seed.from); }
  }, [seed]);

  const set = (k) => (e) => setD((x) => ({ ...x, [k]: e.target.value }));
  const setSec = (i, k, v) => setD((x) => ({ ...x, sections: x.sections.map((s, j) => (j === i ? { ...s, [k]: v } : s)) }));
  const move = (i, by) => setD((x) => {
    const s = [...x.sections]; const j = i + by;
    if (j < 0 || j >= s.length) return x;
    [s[i], s[j]] = [s[j], s[i]];
    return { ...x, sections: s };
  });

  function fill(parsed, from) {
    setD((x) => ({ ...EMPTY_RESUME, template: x.template, accent: x.accent, ...parsed,
      sections: parsed.sections?.length ? parsed.sections : EMPTY_RESUME.sections }));
    setNote(`Filled from ${from}. Check every field: it was read by simple rules, not AI.`);
    setFile(null);
  }

  async function readOld(f) {
    setReading(true); setErr("");
    try {
      const r = await api.studioRead(f);
      if (!r.resume) throw new Error("No text found in that file");
      fill(r.resume, r.filename);
    } catch (e) { setErr(e.message); }
    setReading(false);
  }

  async function create(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await api.studioResume(d, d.template === "modern" ? photo : null);
      setFile(r.files[0]);
    } catch (e2) { setErr(e2.message); }
    setBusy(false);
  }

  return (
    <div className="studio-split">
      <form className="stack" onSubmit={create}>
        <Drop accept={READ_ACCEPT} onFile={readOld} busy={reading}
          title="Start from an old resume (optional)" hint="PDF, Word or a photo of it. The fields below fill in automatically." />
        {reading && <Working text="Reading the resume…" />}
        {note && <p className="okc small">{note}</p>}

        <div className="grid2">
          <label>Full name<input value={d.name} onChange={set("name")} placeholder="Rahul Sharma" /></label>
          <label>Job title / headline<input value={d.headline} onChange={set("headline")} placeholder="Senior React Developer" /></label>
          <label>Email<input value={d.email} onChange={set("email")} /></label>
          <label>Phone<input value={d.phone} onChange={set("phone")} /></label>
          <label>City<input value={d.location} onChange={set("location")} placeholder="Pune, India" /></label>
          <label>Links (comma separated)<input value={d.links} onChange={set("links")} placeholder="linkedin.com/in/…, github.com/…" /></label>
        </div>
        <label>Profile summary<textarea rows={3} value={d.summary} onChange={set("summary")} /></label>
        <label>Skills (comma separated)<textarea rows={2} value={d.skills} onChange={set("skills")} placeholder="React, TypeScript, Node.js" /></label>

        {d.sections.map((s, i) => (
          <fieldset key={i} className="section-box">
            <div className="row">
              <input className="sec-head" value={s.heading} onChange={(e) => setSec(i, "heading", e.target.value)} aria-label="Section title" />
              <button type="button" className="icon" onClick={() => move(i, -1)} disabled={i === 0} aria-label="Move up">↑</button>
              <button type="button" className="icon" onClick={() => move(i, 1)} disabled={i === d.sections.length - 1} aria-label="Move down">↓</button>
              <button type="button" className="icon" onClick={() => setD((x) => ({ ...x, sections: x.sections.filter((_, j) => j !== i) }))} aria-label="Remove section">×</button>
            </div>
            <textarea rows={5} value={s.body} placeholder={BODY_HINT} onChange={(e) => setSec(i, "body", e.target.value)} aria-label={`${s.heading} details`} />
          </fieldset>
        ))}
        <div className="row">
          <span className="hint">Add section:</span>
          {QUICK_SECTIONS.map((h) => (
            <button type="button" key={h} className="chip" onClick={() => setD((x) => ({ ...x, sections: [...x.sections, { heading: h, body: "" }] }))}>+ {h}</button>
          ))}
          <button type="button" className="chip" onClick={() => setD((x) => ({ ...x, sections: [...x.sections, { heading: "New section", body: "" }] }))}>+ Other</button>
        </div>
        <p className="hint small">In a section, write <code>Role | Company | 2021 – 2024</code> for a bold line with the dates on the right, and start lines with <code>-</code> for bullets.</p>

        <fieldset className="plain stack">
          <legend><strong>Design</strong></legend>
          <div className="row">
            {[["classic", "Classic"], ["modern", "Modern (sidebar + photo)"]].map(([id, label]) => (
              <label key={id} className="check"><input type="radio" name="tpl" checked={d.template === id} onChange={() => setD((x) => ({ ...x, template: id }))} />{label}</label>
            ))}
          </div>
          <div className="row" role="radiogroup" aria-label="Accent colour">
            {ACCENTS.map((c) => (
              <button type="button" key={c} className={`swatch ${d.accent === c ? "on" : ""}`} style={{ background: c }} aria-label={c} aria-pressed={d.accent === c} onClick={() => setD((x) => ({ ...x, accent: c }))} />
            ))}
            <input type="color" value={d.accent} onChange={set("accent")} aria-label="Custom colour" />
          </div>
          {d.template === "modern" && (
            <label>Photo (optional)<input type="file" accept={IMG_ACCEPT} onChange={(e) => setPhoto(e.target.files[0] || null)} /></label>
          )}
        </fieldset>
        <div className="row">
          <button className="btn" disabled={busy}>{busy ? "Creating…" : "Create resume PDF"}</button>
          <button type="button" className="link danger" onClick={() => { if (confirm("Clear the whole form?")) { setD(EMPTY_RESUME); setFile(null); setNote(""); } }}>Clear form</button>
        </div>
        {err && <p className="error">{err}</p>}
      </form>
      <aside className="studio-result">
        {file ? <FileCard f={file} preview /> : <p className="empty-note">Your resume PDF appears here. Change anything and create it again as often as you like.</p>}
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------- Poster maker
const EMPTY_POSTER = {
  size: "square", theme: "ocean", accent: "", tag: "We're hiring", headline: "", subheadline: "", details: "", footer: "",
};

function Poster() {
  const [opts, setOpts] = useState(null);
  const [d, setD] = useState(() => load("studio_poster", EMPTY_POSTER));
  const [bg, setBg] = useState(null);
  const [logo, setLogo] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [files, setFiles] = useState([]);
  useEffect(() => { api.studioOptions().then(setOpts).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { keep("studio_poster", d); }, [d]);
  const set = (k) => (e) => setD((x) => ({ ...x, [k]: e.target.value }));

  async function create(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try { setFiles((await api.studioPoster(d, bg, logo)).files); } catch (e2) { setErr(e2.message); }
    setBusy(false);
  }

  // Same words, 4 looks: other colour themes, left and centred layouts. Click one to keep working on it.
  async function variations() {
    if (!d.headline.trim()) return;
    setBusy(true); setErr("");
    const ids = (opts?.themes || []).map((t) => t.id);
    const others = ids.filter((t) => t !== d.theme);
    const picks = [d.theme, ...others.sort(() => Math.random() - 0.5)].slice(0, 4)
      .map((theme, i) => ({ ...d, theme, accent: i === 0 ? d.accent : "", layout: i % 2 ? (d.layout === "center" ? "left" : "center") : d.layout || "left" }));
    const out = [];
    try {
      for (const v of picks) {
        const r = await api.studioPoster(v, bg, logo);
        out.push({ ...r.files[0], label: `${v.theme} · ${v.layout}`, _v: v }, { ...r.files[1], label: `${v.theme} · ${v.layout}`, _pdf: true });
        setFiles([...out]);
      }
    } catch (e2) { setErr(e2.message); }
    setBusy(false);
  }

  return (
    <div className="studio-split">
      <form className="stack" onSubmit={create}>
        <label>Size
          <select value={d.size} onChange={set("size")}>
            {(opts?.sizes || [{ id: "square", label: "Square" }]).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <fieldset className="plain">
          <legend className="hint">Colours</legend>
          <div className="row" role="radiogroup" aria-label="Theme">
            {(opts?.themes || []).map((t) => (
              <button type="button" key={t.id} className={`theme ${d.theme === t.id ? "on" : ""}`} aria-pressed={d.theme === t.id} onClick={() => setD((x) => ({ ...x, theme: t.id, accent: "" }))}
                style={{ background: `linear-gradient(135deg, ${t.colors[0]}, ${t.colors[1]})` }}>
                <i style={{ background: t.colors[2] }} /><span>{t.id}</span>
              </button>
            ))}
            <label className="check small">Highlight<input type="color" value={d.accent || "#ffd166"} onChange={set("accent")} /></label>
          </div>
        </fieldset>
        <div className="row" role="radiogroup" aria-label="Layout">
          <span className="hint">Layout:</span>
          {[["left", "Left aligned"], ["center", "Centred"]].map(([id, l]) => (
            <label key={id} className="check"><input type="radio" name="layout" checked={(d.layout || "left") === id} onChange={() => setD((x) => ({ ...x, layout: id }))} />{l}</label>
          ))}
        </div>
        <label>Small label on top (optional)<input value={d.tag} onChange={set("tag")} maxLength={40} placeholder="We're hiring / दिवाली ऑफर" /></label>
        <label>Headline<input required value={d.headline} onChange={set("headline")} maxLength={160} placeholder="React Developers wanted in Pune" /></label>
        <label>Sub-headline (optional)<input value={d.subheadline} onChange={set("subheadline")} maxLength={300} /></label>
        <label>Points (one per line, optional)<textarea rows={4} value={d.details} onChange={set("details")} placeholder={"3–6 years experience\nReact, TypeScript\nHybrid · Pune"} /></label>
        <label>Bottom line: contact / call to action (optional)<input value={d.footer} onChange={set("footer")} maxLength={160} placeholder="Apply: careers@company.com" /></label>
        <div className="grid2">
          <label>Background photo (optional)<input type="file" accept={IMG_ACCEPT} onChange={(e) => setBg(e.target.files[0] || null)} /></label>
          <label>Logo (optional)<input type="file" accept={IMG_ACCEPT} onChange={(e) => setLogo(e.target.files[0] || null)} /></label>
        </div>
        <div className="row">
          <button className="btn" disabled={busy || !d.headline.trim()}>{busy ? "Creating…" : "Create poster"}</button>
          <button type="button" className="btn ghost" disabled={busy || !d.headline.trim()} onClick={variations}>Show me 4 designs</button>
        </div>
        {err && <p className="error">{err}</p>}
      </form>
      <aside className="studio-result">
        {files.length ? (
          <div className="result-grid">
            {files.filter((f) => !f._pdf || files.length <= 2).map((f) => (
              <div key={f.id} className="stack">
                <FileCard f={f} />
                {f._v && <button className="link small" onClick={() => setD(f._v)}>Use this design</button>}
              </div>
            ))}
          </div>
        ) : <p className="empty-note">Your poster appears here as PNG (for WhatsApp / social) and PDF (for print). Not sure? Click “Show me 4 designs”.</p>}
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------- AI image
const IMG_SIZES = [["square", "Square"], ["portrait", "Portrait"], ["landscape", "Landscape"], ["story", "Story"]];
const PROVIDERS = [
  ["auto", "Auto", "Your own AI first, online only if it fails"],
  ["local", "Own AI only", "Runs on this server: free, private, unlimited (slower on a laptop)"],
  ["online", "Online only", "Faster, but the prompt is sent to an online service"],
];

function AiImage() {
  const [opts, setOpts] = useState(null);
  const [d, setD] = useState(() => ({ count: 1, ...load("studio_image", { prompt: "", style: "auto", colors: [], size: "square", provider: "auto", enhance: true }) }));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [out, setOut] = useState(null);
  const [secs, setSecs] = useState(0);
  useEffect(() => { api.studioOptions().then(setOpts).catch((e) => setErr(e.message)); }, []);
  // images made earlier are saved on the server: show them again after a reload / on another day
  useEffect(() => {
    api.studioImages()
      .then((files) => setOut((prev) => (prev ? prev : files.length ? { files, prompt: files[0].prompt } : null)))
      .catch(() => {});
  }, []);
  useEffect(() => { keep("studio_image", d); }, [d]);

  async function remove(f) {
    if (!window.confirm(`Delete ${f.name}? This cannot be undone.`)) return;
    try {
      await api.deleteStudioImage(f.id);
      setOut((prev) => {
        const files = (prev?.files || []).filter((x) => x.id !== f.id);
        return files.length ? { ...prev, files } : null;
      });
    } catch (e2) { setErr(e2.message); }
  }
  useEffect(() => {
    if (!busy) return undefined;
    setSecs(0);
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);
  const toggleColor = (c) => setD((x) => ({ ...x, colors: x.colors.includes(c) ? x.colors.filter((y) => y !== c) : [...x.colors, c].slice(-3) }));

  async function create(e) {
    e?.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await api.studioImage(d);
      setOut((prev) => ({ ...r, files: [...r.files, ...(prev?.files || [])] }));  // newest first, keep the old ones
      if (r.errors?.length) setErr(r.errors.join(" | "));
    } catch (e2) { setErr(e2.message); }
    setBusy(false);
  }

  return (
    <div className="studio-split">
      <form className="stack" onSubmit={create}>
        <label>What should the image show? (English, Hindi or Hinglish)
          <textarea rows={3} required value={d.prompt} onChange={(e) => setD((x) => ({ ...x, prompt: e.target.value }))} placeholder="Ek modern office mein laptop par kaam karti team" />
        </label>
        <fieldset className="plain">
          <legend className="hint">Style</legend>
          <div className="row">
            {(opts?.image_styles || []).map((s) => (
              <button type="button" key={s.id} className={`chip ${d.style === s.id ? "on" : ""}`} aria-pressed={d.style === s.id} onClick={() => setD((x) => ({ ...x, style: s.id }))}>{s.label}</button>
            ))}
          </div>
        </fieldset>
        <fieldset className="plain">
          <legend className="hint">Colours (up to 3, optional)</legend>
          <div className="row">
            {(opts?.colors || []).map((c) => (
              <button type="button" key={c.id} title={c.id} className={`swatch ${d.colors.includes(c.id) ? "on" : ""}`} style={{ background: c.hex }} aria-label={c.id} aria-pressed={d.colors.includes(c.id)} onClick={() => toggleColor(c.id)} />
            ))}
          </div>
        </fieldset>
        <div className="grid2">
          <label>Size
            <select value={d.size} onChange={(e) => setD((x) => ({ ...x, size: e.target.value }))}>
              {IMG_SIZES.map(([id, l]) => <option key={id} value={id}>{l}</option>)}
            </select>
          </label>
          <label>How many designs
            <select value={d.count} onChange={(e) => setD((x) => ({ ...x, count: Number(e.target.value) }))}>
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n === 1 ? "1" : `${n} (each in a different style when Style is Auto)`}</option>)}
            </select>
          </label>
        </div>
        <fieldset className="plain stack">
          <legend className="hint">Which AI makes it</legend>
          {PROVIDERS.map(([id, label, hint]) => (
            <label key={id} className="check"><input type="radio" name="imgprov" checked={d.provider === id} onChange={() => setD((x) => ({ ...x, provider: id }))} />
              <span><strong>{label}</strong> <span className="hint">· {hint}</span></span></label>
          ))}
          {opts && !opts.local_image_installed && <p className="warn small">Own image AI is not installed on this server yet, so Auto will use the online service.</p>}
        </fieldset>
        <label className="check"><input type="checkbox" checked={d.enhance} onChange={(e) => setD((x) => ({ ...x, enhance: e.target.checked }))} />Let the text AI improve my prompt first (translates Hindi, adds detail)</label>
        <button className="btn" disabled={busy || d.prompt.trim().length < 2}>{busy ? `Creating… ${secs}s` : "Create image"}</button>
        {busy && <Working text="Your own AI on a laptop CPU takes about 1–2 minutes per image (the first one also loads the model). A GPU server makes it a few seconds." />}
        {err && <p className="error">{err}</p>}
      </form>
      <aside className="studio-result">
        {out ? (
          <>
            <div className="row">
              <button className="btn small" onClick={() => create()} disabled={busy}>{busy ? `Creating… ${secs}s` : "Not right? Make another"}</button>
              <span className="hint small">{out.files.length} saved image{out.files.length === 1 ? "" : "s"}</span>
            </div>
            <div className="result-grid">{out.files.map((f) => <FileCard key={f.id} f={f} onDelete={remove} />)}</div>
            <details><summary className="hint small">Prompt used</summary><p className="small">{out.prompt}</p></details>
          </>
        ) : <p className="empty-note">Your image appears here. Try a style and a couple of colours.</p>}
      </aside>
    </div>
  );
}

export default function Studio() {
  const [tool, setTool] = useState(() => load("studio_tool", "scan"));
  const [seed, setSeed] = useState(null);
  useEffect(() => { keep("studio_tool", tool); }, [tool]);
  return (
    <div className="page wide">
      <h1>Studio</h1>
      <p className="lead">Read any PDF or photo, build a resume or design a poster: these run on your own server with no AI at all. AI image uses your own image AI first, and an online service only if you allow it.</p>
      <div className="tabs" role="tablist">
        {TOOLS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tool === id} className={`tab ${tool === id ? "active" : ""}`} onClick={() => setTool(id)}>{label}</button>
        ))}
      </div>
      <div className="panel">
        <div hidden={tool !== "scan"}><Scan onResume={(data, from) => { setSeed({ data, from }); setTool("resume"); }} /></div>
        <div hidden={tool !== "resume"}><Resume seed={seed} /></div>
        <div hidden={tool !== "poster"}><Poster /></div>
        <div hidden={tool !== "image"}><AiImage /></div>
      </div>
    </div>
  );
}
