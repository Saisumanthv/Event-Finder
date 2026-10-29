import { CARD_CHAT_INGEST_URL, CARD_CHAT_URL } from "../config.js";

// Keeps every field within the limits the server accepts.
const cut = (value, max) => String(value ?? "").slice(0, max);
const list = (value, count, max) => (Array.isArray(value) ? value.slice(0, count).map((v) => cut(v, max)) : []);

export function chatPayload(card) {
  return {
    title: cut(card.title, 200),
    type: cut(card.type, 30),
    subtitle: cut(card.subtitle, 200),
    category: cut(card.category, 100),
    description: cut(card.description, 2000),
    summary: cut(card.summary, 600),
    difficulty: cut(card.difficulty, 30),
    prerequisites: list(card.prerequisites, 10, 100),
    date: cut(card.date, 100),
    location: cut(card.location, 200),
    mode: cut(card.mode, 50),
    cost: cut(card.cost, 50),
    source: cut(card.source, 100),
    tags: list(card.tags, 10, 100),
    link: cut(card.link, 500),
  };
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error("Could not reach the server");
  return response.json();
}

// Reads the card's page and prepares it for questions; resolves to { ready, passages, page }.
export function ingestCard(card) {
  return post(CARD_CHAT_INGEST_URL, { card: chatPayload(card) });
}

export function askCard(card, question, history) {
  return post(CARD_CHAT_URL, {
    card: chatPayload(card),
    question: cut(question, 500),
    history: history.slice(-6).map((m) => ({ role: m.role, text: cut(m.text, 1500) })),
  });
}
