import { useEffect, useState } from "react";
import {
  API_URL,
  LEARNING_PATH_URL,
  RECOMMEND_URL,
  SEARCH_URL,
  WEB_SEARCH_URL,
} from "../config.js";
import Card, { normalizeCard } from "./Card.jsx";
import CardSections from "./CardSections.jsx";
import SearchBox from "./SearchBox.jsx";
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
  return list.map(normalizeCard);
}

function filterChips(filters) {
  const chips = ["type", "mode", "level", "cost"]
    .filter((key) => filters[key])
    .map((key) => filters[key]);
  if (filters.date_from && filters.date_to) {
    chips.push(`${filters.date_from} to ${filters.date_to}`);
  } else if (filters.date_from) {
    chips.push(`from ${filters.date_from}`);
  } else if (filters.date_to) {
    chips.push(`until ${filters.date_to}`);
  }
  return chips.concat(filters.keywords || []);
}

export default function WebhookDraft() {
  const [profile, setProfile] = useState(loadProfile);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [heading, setHeading] = useState("");
  const [chips, setChips] = useState([]);
  const [overview, setOverview] = useState("");
  const [isPath, setIsPath] = useState(false);
  const [lastQuery, setLastQuery] = useState("");
  const [webCards, setWebCards] = useState([]);
  const [webLoading, setWebLoading] = useState(false);
  const [webMessage, setWebMessage] = useState("");
  const [cards, setCards] = useState([]);
  const [raw, setRaw] = useState(null);

  useEffect(() => {
    saveProfile(profile);
  }, [profile]);

  function startRequest() {
    setLoading(true);
    setError("");
    setNotice("");
    setHeading("");
    setChips([]);
    setOverview("");
    setIsPath(false);
    setLastQuery("");
    setWebCards([]);
    setWebMessage("");
    setCards([]);
    setRaw(null);
  }

  async function handleWebSearch() {
    setWebLoading(true);
    setWebMessage("");
    try {
      const res = await fetch(WEB_SEARCH_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: lastQuery }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();
      const found = (data.results || []).map(normalizeCard);
      setWebCards(found);
      if (found.length === 0) {
        setWebMessage(data.message || "The web search found nothing for that query.");
      }
    } catch (err) {
      setWebMessage(err.message || "Something went wrong");
    } finally {
      setWebLoading(false);
    }
  }

  async function handlePath(goal) {
    startRequest();
    try {
      const res = await fetch(LEARNING_PATH_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();
      const path = data.path || [];
      // the step's why_next line is shown in the card's highlighted reason slot
      setCards(path.map((step) => normalizeCard({ ...step, reason: step.why_next })));
      setHeading("Your learning path");
      setIsPath(true);
      setOverview(data.overview || "");
      if (path.length === 0) {
        setNotice(data.message || "Nothing in the current listings fits that goal yet.");
      } else if (data.fallback) {
        setNotice("Smart planning is unavailable, so this is a simple course, hackathon, event order.");
      }
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleSearch(query) {
    startRequest();
    try {
      const res = await fetch(SEARCH_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();
      const results = data.results || [];
      setCards(results.map(normalizeCard));
      setHeading("Search results");
      setLastQuery(query);
      setChips(filterChips(data.filters || {}));
      if (results.length === 0) {
        setNotice("No results match your search. Try different words or fewer filters.");
      } else if (data.fallback) {
        setNotice("Smart search is unavailable, so this is a plain keyword match.");
      }
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handleClick({ action, label }) {
    startRequest();

    // With a profile, ask for recommendations for this source; otherwise the default call.
    const useProfile = !isProfileEmpty(profile);
    try {
      const res = await fetch(useProfile ? RECOMMEND_URL : API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          useProfile
            ? { action, profile: profileToRequest(profile), limit: 100 }
            : { action, actionLabel: label }
        ),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data = await res.json();

      if (useProfile) {
        const recs = data.recommendations || [];
        setCards(recs.map(normalizeCard));
        setHeading("Recommended for you");
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

      <SearchBox onSearch={handleSearch} disabled={loading} />

      <div className="or-divider">
        <span>or</span>
      </div>

      <SearchBox
        onSearch={handlePath}
        disabled={loading}
        placeholder="Tell us your goal, e.g. get into machine learning"
        buttonLabel="Build my path"
      />

      <details className="manual">
        <summary>Browse and filter manually</summary>
        <ProfileForm profile={profile} onChange={setProfile} disabled={loading}>
          <div className="buttons">
            {BUTTONS.map((b) => (
              <button key={b.action} disabled={loading} onClick={() => handleClick(b)}>
                {b.label}
              </button>
            ))}
          </div>
        </ProfileForm>
      </details>

      {loading && <div className="spinner" role="status" aria-label="Loading" />}
      {error && <p className="status error">{error}</p>}
      {notice && <p className="status">{notice}</p>}

      {raw && <pre className="raw">{JSON.stringify(raw, null, 2)}</pre>}

      {heading && !loading && !error && <h2 className="results-heading">{heading}</h2>}
      {overview && <p className="overview">{overview}</p>}
      {chips.length > 0 && (
        <div className="chips">
          {chips.map((c, i) => (
            <span key={i} className="chip">
              {c}
            </span>
          ))}
        </div>
      )}

      {isPath ? (
        <div className="grid">
          {cards.map((c, i) => (
            <Card card={c} key={c.id || i} />
          ))}
        </div>
      ) : (
        <CardSections cards={cards} />
      )}

      {lastQuery && !loading && (
        <div className="web-search">
          <button onClick={handleWebSearch} disabled={webLoading}>
            🌐 Search the web for more
          </button>
        </div>
      )}
      {webLoading && <div className="spinner" role="status" aria-label="Searching the web" />}
      {webMessage && <p className="status">{webMessage}</p>}
      {webCards.length > 0 && (
        <section className="card-section">
          <h3 className="section-title web">
            From the web <span className="section-count">{webCards.length}</span>
          </h3>
          <p className="web-note">
            Found by web search, not from our listed sites. Dates and prices may be less
            accurate, so check the page before you rely on them.
          </p>
          <div className="grid">
            {webCards.map((c, i) => (
              <Card card={c} key={c.id || i} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
