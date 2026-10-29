import { LEARN_URL } from "../config.js";

// Lessons written this session, so choosing the same subtopics again is instant.
const lessons = new Map();

const keyFor = (topic, skill, picked) => [topic, skill, ...picked].join("|").toLowerCase();

export function peekLesson(topic, skill, picked) {
  return lessons.get(keyFor(topic, skill, picked));
}

export async function fetchLesson(topic, skill, hackathon, picked) {
  const key = keyFor(topic, skill, picked);
  if (lessons.has(key)) return lessons.get(key);
  const response = await fetch(LEARN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      topic: topic.slice(0, 100),
      skill: skill.slice(0, 100),
      hackathon: hackathon.slice(0, 200),
      subtopics: picked.slice(0, 6).map((s) => s.slice(0, 100)),
    }),
  });
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail;
    } catch (err) {
      // the server did not send a readable message
    }
    throw new Error(typeof detail === "string" && detail ? detail : "Could not reach the server");
  }
  const data = await response.json();
  lessons.set(key, data);
  return data;
}
