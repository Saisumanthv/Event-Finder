import { useEffect, useState } from "react";
import { API_URL, RECOMMEND_URL } from "../config.js";
import Card, { normalizeCard } from "./Card.jsx";
import ProfileForm, {
  isProfileEmpty,
  loadProfile,
  profileToRequest,
  saveProfile,
} from "./ProfileForm.jsx";
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
  return list.slice(0, 10).map(normalizeCard);
}

export default function WebhookDraft() {
  const [profile, setProfile] = useState(loadProfile);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [personalized, setPersonalized] = useState(false);
  const [cards, setCards] = useState([]);
  const [raw, setRaw] = useState(null);

  useEffect(() => {
    saveProfile(profile);
  }, [profile]);

  async function handleClick({ action, label }) {
    setLoading(true);
    setError("");
    setNotice("");
    setPersonalized(false);
    setCards([]);
    setRaw(null);

    // With a profile, ask for recommendations for this source; otherwise the default call.
    const useProfile = !isProfileEmpty(profile);
    try {
      const res = await fetch(useProfile ? RECOMMEND_URL : API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          useProfile
            ? { action, profile: profileToRequest(profile), limit: 10 }
            : { action, actionLabel: label }
        ),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();

      if (useProfile) {
        const recs = data.recommendations || [];
        setCards(recs.map(normalizeCard));
        setPersonalized(true);
        if (recs.length === 0) {
          setNotice(
            data.message || "Nothing matches your profile. Clear the filters to see everything."
          );
        } else if (data.fallback) {
          setNotice("Smart ranking is unavailable, so these are the closest matches.");
        }
        return;
      }

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
      <h1>Event Finder</h1>
      <p className="tagline">
        Find events, hackathons and courses to showcase your skills or learn new ones.
      </p>

      <ProfileForm profile={profile} onChange={setProfile} disabled={loading} />

      <div className="buttons">
        {BUTTONS.map((b) => (
          <button key={b.action} disabled={loading} onClick={() => handleClick(b)}>
            {b.label}
          </button>
        ))}
      </div>

      {loading && <p className="status">Loading...</p>}
      {error && <p className="status error">{error}</p>}
      {notice && <p className="status">{notice}</p>}

      {raw && <pre className="raw">{JSON.stringify(raw, null, 2)}</pre>}

      {personalized && cards.length > 0 && <h2 className="results-heading">Recommended for you</h2>}

      <div className="grid">
        {cards.map((c, i) => (
          <Card card={c} key={c.id || i} />
        ))}
      </div>
    </div>
  );
}
