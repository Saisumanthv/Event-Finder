import { warmPrepMap } from "../utils/prepMapStore.js";

export function normalizeCard(item) {
  return {
    id: item.id || "",
    step: item.step || 0,
    type: item.type || "",
    title: item.title || "",
    subtitle: item.subtitle || "",
    category: item.category || "",
    description: item.description || "",
    summary: item.summary || "",
    difficulty: item.difficulty || "",
    prerequisites: Array.isArray(item.prerequisites) ? item.prerequisites.slice(0, 3) : [],
    date: item.date || "",
    location: item.location || "",
    mode: item.mode || "",
    cost: item.cost || "",
    source: item.source || "",
    tags: Array.isArray(item.tags) ? item.tags.slice(0, 5) : [],
    link: item.link || "",
    reason: item.reason || "",
  };
}

export function isHackathon(card) {
  return (
    card.id.startsWith("hackathon_details-") ||
    card.type === "hackathon" ||
    card.category.toLowerCase() === "hackathon"
  );
}

export default function Card({ card, onPrepMap }) {
  const showPrepMap = Boolean(onPrepMap) && isHackathon(card);
  return (
    <div className="card">
      {(card.step > 0 || card.category || card.difficulty) && (
        <div className="pills">
          {card.step > 0 && <span className="step">Step {card.step}</span>}
          {card.step > 0 && card.type && <span className="type">{card.type}</span>}
          {card.category && <span className="category">{card.category}</span>}
          {card.difficulty && (
            <span className={`difficulty ${card.difficulty}`}>{card.difficulty}</span>
          )}
        </div>
      )}
      <h3>{card.title}</h3>
      {card.reason && <p className="reason">{card.reason}</p>}
      {card.subtitle && <p className="subtitle">{card.subtitle}</p>}
      {card.summary ? (
        <p className="summary">{card.summary}</p>
      ) : (
        card.description && <p>{card.description}</p>
      )}
      <ul className="meta">
        {card.date && <li>📅 {card.date}</li>}
        {card.location && <li>📍 {card.location}</li>}
        {card.mode && <li>💻 {card.mode}</li>}
        {card.cost && <li className="cost">💰 {card.cost}</li>}
        {card.source && <li>🌐 {card.source}</li>}
      </ul>
      {card.prerequisites.length > 0 && (
        <p className="prereq">
          <strong>Prerequisites:</strong> {card.prerequisites.join(", ")}
        </p>
      )}
      {card.tags.length > 0 && (
        <div className="tags">
          {card.tags.map((t, j) => (
            <span key={j} className="tag">
              {t}
            </span>
          ))}
        </div>
      )}
      {(card.link || showPrepMap) && (
        <div className="card-actions">
          {card.link && (
            <a href={card.link} target="_blank" rel="noopener noreferrer">
              View →
            </a>
          )}
          {showPrepMap && (
            <button
              type="button"
              className="prep-button"
              onMouseEnter={() => warmPrepMap(card)}
              onFocus={() => warmPrepMap(card)}
              onClick={() => onPrepMap(card)}
            >
              Prep Map
            </button>
          )}
        </div>
      )}
    </div>
  );
}
