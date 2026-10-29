import { useCallback, useEffect, useState } from "react";
import { API_URL, ASSISTANT_URL, LEARNING_PATH_URL, RECOMMEND_URL } from "../config.js";
import Card, { isHackathon, normalizeCard } from "./Card.jsx";
import CardSections from "./CardSections.jsx";
import Loader from "./Loader.jsx";
import PrepMapModal from "./PrepMapModal.jsx";
import SearchBox from "./SearchBox.jsx";
import { formatAge, readUpdated } from "../utils/age.js";
import { prefetchPrepMaps } from "../utils/prepMapStore.js";
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

// Shown under the spinner so a long wait has a reason.
const WAIT_ASSISTANT =
  "This takes a while because the assistant is an AI agent: it decides which tools to use, searches the listings (and sometimes the web) over several steps, then writes its answer.";
const WAIT_PATH =
  "This takes a while because the AI is comparing courses, hackathons and events across all the sources to put them in the right order.";
const WAIT_RANKING =
  "This takes a moment because the AI is ranking the listings against your profile.";
const WAIT_LISTINGS =
  "Loading the listings. If this is the first load, it is collecting live data from the websites and having the AI add summaries, which can take up to a minute.";

const STEP_LABELS = {
  search_listings: "Searched the listings",
  get_details: "Checked an item's details",
  build_learning_path: "Built a learning path",
  search_the_web: "Searched the web",
};

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

export default function WebhookDraft() {
  const [profile, setProfile] = useState(loadProfile);
  const [loading, setLoading] = useState(false);
  const [loadingText, setLoadingText] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [heading, setHeading] = useState("");
  const [overview, setOverview] = useState("");
  const [steps, setSteps] = useState([]);
  const [ordered, setOrdered] = useState(false);
  const [cards, setCards] = useState([]);
  const [raw, setRaw] = useState(null);
  const [updated, setUpdated] = useState(null);
  const [prepCard, setPrepCard] = useState(null);
  const closePrepMap = useCallback(() => setPrepCard(null), []);

  useEffect(() => {
    saveProfile(profile);
  }, [profile]);

  // Prepare the hackathons' prep maps in the background so the pop-up opens instantly.
  useEffect(() => {
    prefetchPrepMaps(cards.filter(isHackathon));
  }, [cards]);

  function startRequest(waitText = "") {
    setLoading(true);
    setLoadingText(waitText);
    setError("");
    setNotice("");
    setHeading("");
    setOverview("");
    setSteps([]);
    setOrdered(false);
    setCards([]);
    setRaw(null);
    setUpdated(null);
  }

  async function handleAsk(message) {
    startRequest(WAIT_ASSISTANT);
    try {
      const res = await fetch(ASSISTANT_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      setUpdated(readUpdated(res));
      const data = await res.json();
      const picked = (data.cards || []).map(normalizeCard);
      setCards(picked);
      setOrdered(true);
      setHeading("Assistant");
      setOverview(data.answer || "");
      setSteps(data.steps || []);
      if (picked.some((c) => c.id.startsWith("web-"))) {
        setNotice(
          "Some picks came from a web search, not our listed sites. Check the page before you rely on dates and prices."
        );
      } else if (data.fallback) {
        setNotice("Showing plain keyword matches while the assistant is unavailable.");
      }
    } catch (err) {
      setError(err.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function handlePath(goal) {
    startRequest(WAIT_PATH);
    try {
      const res = await fetch(LEARNING_PATH_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal }),
      });
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      setUpdated(readUpdated(res));
      const data = await res.json();
      const path = data.path || [];
      // the step's why_next line is shown in the card's highlighted reason slot
      setCards(path.map((step) => normalizeCard({ ...step, reason: step.why_next })));
      setHeading("Your learning path");
      setOrdered(true);
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

  async function handleClick({ action, label }) {
    // With a profile, ask for recommendations for this source; otherwise the default call.
    const useProfile = !isProfileEmpty(profile);
    startRequest(useProfile ? WAIT_RANKING : WAIT_LISTINGS);
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
      setUpdated(readUpdated(res));
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
        throw new Error(data.message || "No source returned any items");
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

      <SearchBox
        onSearch={handleAsk}
        disabled={loading}
        placeholder="Ask the assistant, e.g. I know Python and want a hackathon for my portfolio"
        buttonLabel="Ask"
      />

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

      {loading && <Loader />}
      {loading && loadingText && <p className="loading-note">{loadingText}</p>}
      {error && <p className="status error">{error}</p>}
      {notice && <p className="status">{notice}</p>}

      {raw && <pre className="raw">{JSON.stringify(raw, null, 2)}</pre>}

      {heading && !loading && !error && <h2 className="results-heading">{heading}</h2>}
      {overview && <p className="overview">{overview}</p>}

      {steps.length > 0 && (
        <details className="steps">
          <summary>What the assistant did ({steps.length} steps)</summary>
          <ol>
            {steps.map((s, i) => (
              <li key={i}>
                <strong>{STEP_LABELS[s.tool] || s.tool}</strong>
                {s.detail && <span className="step-detail"> ({s.detail})</span>}
                {s.result && <span className="step-result"> → {s.result}</span>}
              </li>
            ))}
          </ol>
        </details>
      )}

      {cards.length > 0 && updated && !loading && (
        <p className="updated-note">Listings updated {formatAge(updated)}</p>
      )}

      {ordered ? (
        <div className="grid">
          {cards.map((c, i) => (
            <Card card={c} key={c.id || i} onPrepMap={setPrepCard} />
          ))}
        </div>
      ) : (
        <CardSections cards={cards} onPrepMap={setPrepCard} />
      )}

      {prepCard && <PrepMapModal card={prepCard} onClose={closePrepMap} />}
    </div>
  );
}
