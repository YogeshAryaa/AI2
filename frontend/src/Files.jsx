import { useEffect, useState } from "react";
import { fileUrl } from "./api.js";

export const kb = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

// A file the backend made (PDF / image). Fetched with the auth header and shown through a blob URL.
// preview: also show a PDF inline.
export function FileCard({ f, preview = false, onDelete = null }) {
  const [url, setUrl] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let u = null, alive = true;
    setUrl(null);
    fileUrl(f.url).then((x) => { u = x; if (alive) setUrl(x); else URL.revokeObjectURL(x); }).catch((e) => alive && setErr(e.message));
    return () => { alive = false; if (u) URL.revokeObjectURL(u); };
  }, [f.url]);
  const isImage = f.kind === "image";
  return (
    <div className={`file-card ${f.kind} ${preview ? "wide" : ""}`}>
      {isImage && (url ? <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={f.name} /></a>
        : !err && <div className="img-wait" aria-label="Loading image" />)}
      {!isImage && preview && url && <iframe className="pdf-view" src={url} title={f.name} />}
      <div className="file-row">
        {!isImage && <span className="file-icon" aria-hidden="true">PDF</span>}
        <span className="file-name" title={f.name}>{f.label && <b className="file-label">{f.label}</b>}{f.name}<small>{kb(f.size)}</small></span>
        {url && !isImage && <a className="btn small ghost" href={url} target="_blank" rel="noreferrer">Open</a>}
        {url && <a className="btn small" href={url} download={f.name}>Download</a>}
        {onDelete && <button type="button" className="link" onClick={() => onDelete(f)} title="Delete this file">Delete</button>}
      </div>
      {err && <p className="error">{err}</p>}
    </div>
  );
}
