import { useState } from "react";
import { API_URL } from "../config.js";
import "../styles/WebhookDraft.css";

const BUTTONS = [
  { label: "Get Event Details", action: "event_details" },
  { label: "Get Hackathon Details", action: "hackathon_details" },
  { label: "New Courses", action: "new_courses" },
];

function extractItemList(data) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return null;
  for (const key of ["items", "results", "data"]) {
    if (Array.isArray(data[key])) return data[key];
  }
  for (const value of Object.values(data)) {
    if (Array.isArray(value)) return value;
  }
  return null;
}

function normalizeCards(list) {
  return list.slice(0, 10).map((item) => ({
    title: item.title || "",
    subtitle: item.subtitle || "",
    category: item.category || "",
    description: item.description || "",
    date: item.date || "",
    location: item.location || "",
    mode: item.mode || "",
    tags: Array.isArray(item.tags) ? item.tags.slice(0, 3) : [],
    link: item.link || "",
  }));
}

export default function WebhookDraft() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [cards, setCards] = useState([]);
  const [raw, setRaw] = useState(null);

  async function handleClick({ action, label }) {
    setLoading(true);
    setError("");
    setCards([]);
    setRaw(null);
    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, actionLabel: label }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();
      if (data?.status === "error") {
        throw new Error(data.message || "The agent returned no results");
      }
      const list = extractItemList(data);
      if (list) setCards(normalizeCards(list));
      else setRaw(data);
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <h1>Workflow Trigger Panel</h1>

      <div className="buttons">
        {BUTTONS.map((b) => (
          <button key={b.action} disabled={loading} onClick={() => handleClick(b)}>
            {b.label}
          </button>
        ))}
      </div>

      {loading && <p className="status">Loading...</p>}
      {error && <p className="status error">{error}</p>}

      {raw && <pre className="raw">{JSON.stringify(raw, null, 2)}</pre>}

      {!loading && !error && !raw && cards.length === 0 && null}

      <div className="grid">
        {cards.map((c, i) => (
          <div className="card" key={i}>
            {c.category && <span className="category">{c.category}</span>}
            <h3>{c.title}</h3>
            {c.subtitle && <p className="subtitle">{c.subtitle}</p>}
            {c.description && <p>{c.description}</p>}
            <ul className="meta">
              {c.date && <li>📅 {c.date}</li>}
              {c.location && <li>📍 {c.location}</li>}
              {c.mode && <li>💻 {c.mode}</li>}
            </ul>
            {c.tags.length > 0 && (
              <div className="tags">
                {c.tags.map((t, j) => (
                  <span key={j} className="tag">
                    {t}
                  </span>
                ))}
              </div>
            )}
            {c.link && (
              <a href={c.link} target="_blank" rel="noopener noreferrer">
                View →
              </a>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
