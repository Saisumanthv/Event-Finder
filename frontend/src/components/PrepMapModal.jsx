import { useEffect, useState } from "react";
import { APPROACHES, isAiRelated } from "../utils/approaches.js";
import { downloadPrepMapPdf } from "../utils/prepMapPdf.js";
import { fetchPrepMap, peekPrepMap } from "../utils/prepMapStore.js";
import Loader from "./Loader.jsx";

export default function PrepMapModal({ card, onClose }) {
  const tabs = isAiRelated(card) ? APPROACHES : APPROACHES.slice(0, 1);
  const [tab, setTab] = useState(APPROACHES[0].key);
  // Maps already generated in the background are there from the first render.
  const [maps, setMaps] = useState(() => {
    const ready = {};
    APPROACHES.forEach(({ key }) => {
      const data = peekPrepMap(card, key);
      if (data) ready[key] = data;
    });
    return ready;
  });
  const [errors, setErrors] = useState({});
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState(() => new Set());
  const [saving, setSaving] = useState("");
  const [pdfError, setPdfError] = useState("");

  // Wait for the current tab's map if it is not ready yet (or when "Try again" is pressed).
  // A map still being generated in the background is shared, not requested twice.
  useEffect(() => {
    if (maps[tab]) return undefined;
    let cancelled = false;
    setErrors((e) => ({ ...e, [tab]: "" }));
    fetchPrepMap(card, tab)
      .then((data) => {
        if (!cancelled) setMaps((m) => ({ ...m, [tab]: data }));
      })
      .catch((err) => {
        if (!cancelled) setErrors((e) => ({ ...e, [tab]: err.message || "Something went wrong" }));
      });
    return () => {
      cancelled = true;
    };
  }, [card, tab, attempt]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  const approach = APPROACHES.find((a) => a.key === tab);
  const data = maps[tab];
  const error = errors[tab];
  const skills = data?.skills || [];
  const expandable = skills.map((s, i) => (s.topics.length > 0 ? i : -1)).filter((i) => i >= 0);
  const openKey = (i) => `${tab}:${i}`;
  const allOpen = expandable.length > 0 && expandable.every((i) => open.has(openKey(i)));

  function toggle(index) {
    setOpen((current) => {
      const next = new Set(current);
      const key = openKey(index);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleAll() {
    setOpen((current) => {
      const next = new Set(current);
      expandable.forEach((i) => (allOpen ? next.delete(openKey(i)) : next.add(openKey(i))));
      return next;
    });
  }

  // `which` is "tab" (the visible approach) or "both"; a tab that was never opened is fetched first.
  async function handleDownload(which) {
    setSaving(which);
    setPdfError("");
    try {
      const keys = which === "both" ? tabs.map((t) => t.key) : [tab];
      const loaded = { ...maps };
      await Promise.all(
        keys
          .filter((key) => !loaded[key])
          .map(async (key) => {
            loaded[key] = await fetchPrepMap(card, key);
          }),
      );
      setMaps((m) => ({ ...m, ...loaded }));
      await downloadPrepMapPdf(
        card,
        keys.map((key) => ({ ...APPROACHES.find((a) => a.key === key), prep: loaded[key] })),
      );
    } catch (err) {
      setPdfError(err.message || "Could not create the PDF");
    } finally {
      setSaving("");
    }
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="prep-title">
        <header className="modal-header">
          <div>
            <p className="modal-kicker">Prep Map</p>
            <h2 id="prep-title">{card.title}</h2>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {tabs.length > 1 && (
          <div className="prep-tabs" role="tablist">
            {tabs.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                className={`prep-tab ${tab === t.key ? "active" : ""}`}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}

        <div className="modal-body">
          {tabs.length > 1 && <p className="prep-blurb">{approach.blurb}</p>}

          {!data && !error && (
            <>
              <Loader />
              <p className="loading-note">
                The AI is working out which skills and topics to learn before this hackathon. This
                usually takes 10 to 30 seconds.
              </p>
            </>
          )}

          {error && (
            <div className="modal-error">
              <p className="status error">{error}</p>
              <button type="button" className="modal-button" onClick={() => setAttempt((a) => a + 1)}>
                Try again
              </button>
            </div>
          )}

          {data && (
            <>
              {data.fit_note && <p className="prep-warning">{data.fit_note}</p>}
              {data.overview && <p className="prep-overview">{data.overview}</p>}
              {data.basis === "general" && !data.fallback && (
                <p className="prep-note">
                  The listing gave few details, so this map is based on the field the title suggests.
                </p>
              )}

              {skills.length === 0 && <p className="status">There is nothing to show for this hackathon.</p>}

              {expandable.length > 0 && (
                <div className="prep-controls">
                  <span>{skills.length} skills to learn</span>
                  <button type="button" className="link-button" onClick={toggleAll}>
                    {allOpen ? "Collapse all" : "Expand all"}
                  </button>
                </div>
              )}

              {skills.map((skill, i) => {
                const canOpen = skill.topics.length > 0;
                const isOpen = open.has(openKey(i));
                return (
                  <div className={`skill ${isOpen ? "open" : ""}`} key={`${tab}-${i}`}>
                    <button
                      type="button"
                      className="skill-head"
                      aria-expanded={canOpen ? isOpen : undefined}
                      disabled={!canOpen}
                      onClick={() => toggle(i)}
                    >
                      <span className="skill-number">{i + 1}</span>
                      <span className="skill-name">{skill.name}</span>
                      {canOpen && (
                        <span className="skill-meta">
                          {skill.topics.length} {skill.topics.length === 1 ? "topic" : "topics"}
                        </span>
                      )}
                      {canOpen && (
                        <span className="chevron" aria-hidden="true">
                          ▾
                        </span>
                      )}
                    </button>
                    {skill.why && <p className="skill-why">{skill.why}</p>}
                    {isOpen && (
                      <div className="skill-body">
                        {skill.topics.map((topic, j) => (
                          <div className="topic" key={j}>
                            <h4>{topic.name}</h4>
                            {topic.subtopics.length > 0 && (
                              <ul>
                                {topic.subtopics.map((sub, k) => (
                                  <li key={k}>{sub}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </>
          )}
        </div>

        <footer className="modal-footer">
          <p>
            AI-generated study suggestions based on the listing. Check the official page for the
            tracks, rules and tools.
          </p>
          <div className="modal-footer-actions">
            {pdfError && <span className="status error">{pdfError}</span>}
            <button
              type="button"
              className="modal-button primary"
              disabled={!data || skills.length === 0 || Boolean(saving)}
              onClick={() => handleDownload("tab")}
            >
              {saving === "tab" ? "Creating PDF..." : tabs.length > 1 ? "⬇ This tab (PDF)" : "⬇ Download PDF"}
            </button>
            {tabs.length > 1 && (
              <button
                type="button"
                className="modal-button"
                disabled={!data || Boolean(saving)}
                onClick={() => handleDownload("both")}
              >
                {saving === "both" ? "Creating PDF..." : "⬇ Both tabs (PDF)"}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
