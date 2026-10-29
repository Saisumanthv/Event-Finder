import { PREP_MAP_URL } from "../config.js";
import { APPROACHES, isAiRelated } from "./approaches.js";

// Prep maps are generated in the background as soon as hackathon cards are on screen, so the
// pop-up can open with the map already there. Requests are shared: opening the pop-up while
// a map is still being generated waits for that request instead of starting another.
const MAX_PARALLEL = 3;
const entries = new Map(); // key -> { data, promise }
let queue = [];
let running = 0;

const keyOf = (card, approach) => `${approach}|${card.id || card.title}`;

// Both approaches for hackathons that look AI-related, otherwise just the first.
function approachesFor(card) {
  return isAiRelated(card) ? APPROACHES : APPROACHES.slice(0, 1);
}

async function request(card, approach) {
  const res = await fetch(PREP_MAP_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: card.title,
      subtitle: card.subtitle,
      category: card.category,
      description: card.description,
      summary: card.summary,
      difficulty: card.difficulty,
      tags: card.tags,
      prerequisites: card.prerequisites,
      approach,
    }),
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return res.json();
}

export function fetchPrepMap(card, approach) {
  const key = keyOf(card, approach);
  let entry = entries.get(key);
  if (!entry) {
    entry = { data: null, promise: null };
    entry.promise = request(card, approach).then(
      (data) => {
        // A fallback is a stand-in for a failed generation, so do not keep it.
        if (data.fallback) entries.delete(key);
        else entry.data = data;
        return data;
      },
      (error) => {
        entries.delete(key);
        throw error;
      },
    );
    entries.set(key, entry);
  }
  return entry.promise;
}

// The finished map, or null if it is missing or still being generated.
export function peekPrepMap(card, approach) {
  return entries.get(keyOf(card, approach))?.data ?? null;
}

function pump() {
  while (running < MAX_PARALLEL && queue.length > 0) {
    const { card, approach } = queue.shift();
    if (entries.has(keyOf(card, approach))) continue;
    running += 1;
    fetchPrepMap(card, approach)
      .catch(() => {})
      .finally(() => {
        running -= 1;
        pump();
      });
  }
}

// Replaces the waiting list with these cards, in display order. Requests already running continue.
export function prefetchPrepMaps(cards) {
  queue = cards.flatMap((card) => approachesFor(card).map(({ key }) => ({ card, approach: key })));
  pump();
}

// Starts one card's maps right away, ahead of the waiting list (used when the button is hovered).
export function warmPrepMap(card) {
  approachesFor(card).forEach(({ key }) => fetchPrepMap(card, key).catch(() => {}));
}
