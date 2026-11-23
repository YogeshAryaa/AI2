import { useState } from "react";
import { api, conn, saveConn } from "./api.js";
import Chat from "./Chat.jsx";
import Providers from "./Providers.jsx";
import Knowledge from "./Knowledge.jsx";
import Keys from "./Keys.jsx";
import Studio from "./Studio.jsx";

const TABS = [
  ["chat", "Ask"],
  ["studio", "Studio"],
  ["providers", "AI providers"],
  ["knowledge", "My data"],
  ["keys", "Connect apps"],
];

function Connection() {
  const [open, setOpen] = useState(!conn().token);
  const [base, setBase] = useState(conn().base);
  const [token, setToken] = useState(conn().token);
  const [state, setState] = useState("");

  async function connect(e) {
    e.preventDefault();
    saveConn(base, token);
    setBase(conn().base); // show the cleaned-up address
    setState("Checking…");
    try {
      await api.health();
      await api.providers();
      setState("Connected");
      setTimeout(() => window.location.reload(), 500);
    } catch (err) {
      setState(`Could not connect: ${err.message}`);
    }
  }

  return (
    <div className="conn">
      <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
        Backend connection
      </button>
      {open && (
        <form onSubmit={connect} className="conn-form">
          <label>
            Server address
            <input value={base} onChange={(e) => setBase(e.target.value)} />
          </label>
          <label>
            API token
            <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="ADMIN_TOKEN from backend/.env" />
          </label>
          <button className="btn small">Connect</button>
          {state && <p className="hint">{state}</p>}
        </form>
      )}
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("chat");
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <span>Ask my data</span>
        </div>
        <nav>
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? "nav active" : "nav"} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>
        <Connection />
      </aside>
      <main className="main">
        {tab === "chat" && <Chat />}
        {tab === "studio" && <Studio />}
        {tab === "providers" && <Providers />}
        {tab === "knowledge" && <Knowledge />}
        {tab === "keys" && <Keys />}
      </main>
    </div>
  );
}
