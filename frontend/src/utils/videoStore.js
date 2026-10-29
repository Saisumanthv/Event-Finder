import { VIDEOS_URL } from "../config.js";

// Videos found this session, so reopening a skill never asks again.
const found = new Map();

const keyFor = (topic, subtopics) => `${topic}|${subtopics.slice(0, 2).join("|")}`.toLowerCase();

export function peekVideos(topic, subtopics) {
  return found.get(keyFor(topic, subtopics));
}

export function youtubeSearchUrl(topic, subtopics) {
  return `https://www.youtube.com/results?search_query=${encodeURIComponent([topic, ...subtopics.slice(0, 2), "tutorial"].join(" "))}`;
}

// Resolves to { available, videos, reason }. Only successful answers are remembered.
export async function fetchVideos(topic, subtopics) {
  const key = keyFor(topic, subtopics);
  if (found.has(key)) return found.get(key);
  const response = await fetch(VIDEOS_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic, subtopics: subtopics.slice(0, 2) }),
  });
  if (!response.ok) throw new Error("Could not reach the server");
  const data = await response.json();
  if (data.available) found.set(key, data);
  return data;
}
