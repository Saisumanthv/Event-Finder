import Card from "./Card.jsx";

export default function CardSections({ cards, onPrepMap }) {
  const groups = [
    { key: "free", title: "Free", items: cards.filter((c) => c.cost === "free") },
    { key: "paid", title: "Paid", items: cards.filter((c) => c.cost === "paid") },
    {
      key: "unknown",
      title: "Price not listed",
      items: cards.filter((c) => c.cost !== "free" && c.cost !== "paid"),
    },
  ].filter((g) => g.items.length > 0);

  return groups.map((g) => (
    <section key={g.key} className="card-section">
      <h3 className={`section-title ${g.key}`}>
        {g.title} <span className="section-count">{g.items.length}</span>
      </h3>
      <div className="grid">
        {g.items.map((c, i) => (
          <Card card={c} key={c.id || i} onPrepMap={onPrepMap} />
        ))}
      </div>
    </section>
  ));
}
