export function normalizeCard(item) {
  return {
    id: item.id || "",
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
    tags: Array.isArray(item.tags) ? item.tags.slice(0, 5) : [],
    link: item.link || "",
    reason: item.reason || "",
  };
}

export default function Card({ card }) {
  return (
    <div className="card">
      {(card.category || card.difficulty) && (
        <div className="pills">
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
      {card.link && (
        <a href={card.link} target="_blank" rel="noopener noreferrer">
          View →
        </a>
      )}
    </div>
  );
}
