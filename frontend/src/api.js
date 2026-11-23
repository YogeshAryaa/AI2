// When the backend serves this UI (production), talk to the same address by default.
// In development (npm run dev, any port) the backend is always separate, on :8000.
const defaultBase = () =>
  !import.meta.env.DEV && typeof window !== "undefined" && window.location.protocol.startsWith("http")
    ? window.location.origin
    : "http://localhost:8000";

// "localhost:8000", " http://localhost:8000/ ", "http//localhost:8000", "http://localhost:8000/health"
// all become "http://localhost:8000". A wrong address would otherwise hit this app's own page.
export function cleanBase(raw) {
  let b = String(raw || "").replace(/\s+/g, "");
  if (!b) return defaultBase();
  b = b.replace(/^(https?)[:/]*/i, "$1://");
  if (!/^https?:\/\//i.test(b)) b = "http://" + b.replace(/^\/+/, "");
  try {
    const u = new URL(b);
    const path = u.pathname.replace(/\/(health|docs|chat|providers)(\/.*)?$/i, "").replace(/\/+$/, "");
    return u.origin + path;
  } catch {
    return b.replace(/\/+$/, "");
  }
}

const NOT_BACKEND = (base) =>
  `${base} is not the backend (it answered with a web page). Use the backend address, normally http://localhost:8000, not this app's own address (:5173).`;
const UNREACHABLE = (base) => `Cannot reach ${base}. Is the backend running (run-backend.bat)?`;

async function call(base, path, init) {
  let res;
  try {
    res = await fetch(base + path, init);
  } catch {
    throw new Error(UNREACHABLE(base));
  }
  if (!(res.headers.get("content-type") || "").includes("json")) throw new Error(NOT_BACKEND(base));
  return res;
}

export const conn = () => ({
  base: cleanBase(localStorage.getItem("rag_base")),
  token: localStorage.getItem("rag_token") || "",
});

export function saveConn(base, token) {
  localStorage.setItem("rag_base", cleanBase(base));
  localStorage.setItem("rag_token", token.trim());
}

async function req(path, { method = "GET", json, body } = {}) {
  const { base, token } = conn();
  const headers = { "X-API-Key": token };
  if (json) headers["Content-Type"] = "application/json";
  const res = await call(base, path, { method, headers, body: json ? JSON.stringify(json) : body });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(msg || `HTTP ${res.status}`);
  }
  return res.json();
}

const enc = encodeURIComponent;
const qs = (o) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return p.length ? "?" + p.map(([k, v]) => `${k}=${enc(v)}`).join("&") : "";
};

export const api = {
  health: () => call(conn().base, "/health").then((r) => r.json()),
  status: () => req("/status"),
  // providers
  presets: () => req("/providers/presets"),
  providers: () => req("/providers"),
  addProvider: (json) => req("/providers", { method: "POST", json }),
  patchProvider: (id, json) => req(`/providers/${id}`, { method: "PATCH", json }),
  deleteProvider: (id) => req(`/providers/${id}`, { method: "DELETE" }),
  testProvider: (id) => req(`/providers/${id}/test`, { method: "POST" }),
  models: (id) => req(`/providers/${id}/models`),
  // knowledge
  namespaces: () => req("/namespaces"),
  documents: (o = {}) => req("/documents" + qs({ limit: 50, ...o })),
  deleteDocument: (id) => req(`/documents/${enc(id)}`, { method: "DELETE" }),
  deleteSource: (source, namespace) => req("/documents/delete-source" + qs({ source, namespace }), { method: "POST" }),
  ingestText: (json) => req("/ingest/text", { method: "POST", json }),
  ingestFile: (file, namespace, titleField) => {
    const fd = new FormData();
    fd.append("file", file);
    if (namespace) fd.append("namespace", namespace);
    if (titleField) fd.append("title_field", titleField);
    return req("/ingest/file", { method: "POST", body: fd });
  },
  databases: () => req("/databases"),
  collections: (database) => req("/collections" + qs({ database })),
  sample: (name, database) => req(`/collections/${enc(name)}/sample` + qs({ database })),
  ingestCollection: (json) => req("/ingest/collection", { method: "POST", json }),
  job: (id) => req(`/jobs/${id}`),
  // auto-sync of MongoDB collections
  syncSources: () => req("/sync/sources"),
  addSyncSource: (json) => req("/sync/sources", { method: "POST", json }),
  patchSyncSource: (name, json) => req(`/sync/sources/${enc(name)}`, { method: "PATCH", json }),
  runSyncSource: (name) => req(`/sync/sources/${enc(name)}/run`, { method: "POST" }),
  deleteSyncSource: (name) => req(`/sync/sources/${enc(name)}`, { method: "DELETE" }),
  saveQA: (json) => req("/knowledge/qa", { method: "POST", json }),
  settings: () => req("/settings"),
  saveSettings: (json) => req("/settings", { method: "PUT", json }),
  calibrate: () => req("/settings/calibrate", { method: "POST" }),
  // chat history
  conversations: () => req("/conversations?limit=50"),
  conversation: (id) => req(`/conversations/${enc(id)}`),
  deleteConversation: (id) => req(`/conversations/${enc(id)}`, { method: "DELETE" }),
  // API keys for other systems
  keys: () => req("/keys"),
  addKey: (json) => req("/keys", { method: "POST", json }),
  patchKey: (id, json) => req(`/keys/${id}`, { method: "PATCH", json }),
  rotateKey: (id) => req(`/keys/${id}/rotate`, { method: "POST" }),
  deleteKey: (id) => req(`/keys/${id}`, { method: "DELETE" }),
  // Studio: read files, resumes, posters (all on the server, no AI)
  studioOptions: () => req("/studio/options"),
  studioRead: (file) => { const fd = new FormData(); fd.append("file", file); return req("/studio/read", { method: "POST", body: fd }); },
  studioResume: (data, photo) => {
    const fd = new FormData(); fd.append("data", JSON.stringify(data)); if (photo) fd.append("photo", photo);
    return req("/studio/resume", { method: "POST", body: fd });
  },
  studioImage: (json) => req("/studio/image", { method: "POST", json }),
  studioImages: () => req("/studio/images"),
  deleteStudioImage: (id) => req(`/studio/images/${enc(id)}`, { method: "DELETE" }),
  studioPoster: (data, background, logo) => {
    const fd = new FormData(); fd.append("data", JSON.stringify(data));
    if (background) fd.append("background", background); if (logo) fd.append("logo", logo);
    return req("/studio/poster", { method: "POST", body: fd });
  },
};

// Files the AI made in the chat (PDF / image). Fetched with the auth header, shown through a blob URL.
export async function fileUrl(path) {
  const { base, token } = conn();
  const res = await fetch(base + path, { headers: { "X-API-Key": token } });
  if (!res.ok) throw new Error(res.status === 404 ? "File not found (it may have been deleted)" : `HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

// Polls a background job until it finishes. onUpdate gets each state.
export async function waitJob(job, onUpdate) {
  let j = job;
  onUpdate?.(j);
  while (j.status === "running") {
    await new Promise((r) => setTimeout(r, 1000));
    j = await api.job(j.id);
    onUpdate?.(j);
  }
  return j;
}

// Server-Sent Events over fetch (so we can send the auth header).
// While the server restarts (e.g. after an update) it briefly answers "Not Found" or not at all: wait and try again.
async function postWithRetry(url, init, tries = 3) {
  for (let i = 1; ; i++) {
    try {
      const res = await fetch(url, init);
      if (i < tries && [502, 503, 504].includes(res.status)) throw new Error("busy");
      if (i < tries && res.status === 404) {
        const j = await res.clone().json().catch(() => ({}));
        if (j.detail === "Not Found") throw new Error("restarting");  // generic 404 = route not loaded yet
      }
      return res;
    } catch (e) {
      if (init.signal?.aborted || i >= tries) throw e;
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
}

export async function streamChat(body, onEvent, signal) {
  const { base, token } = conn();
  const res = await postWithRetry(base + "/chat/stream", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", "X-API-Key": token },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.status === 401 ? "Wrong API token" : `HTTP ${res.status}`;
    try { const j = await res.json(); if (typeof j.detail === "string") msg = j.detail; } catch {}
    throw new Error(msg);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split("\n\n");
    buf = parts.pop();
    for (const p of parts) {
      if (p.startsWith("data:")) onEvent(JSON.parse(p.slice(5)));
    }
  }
}
