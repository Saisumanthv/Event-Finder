export const APPROACHES = [
  {
    key: "fundamentals",
    label: "Fundamentals",
    blurb: "Learn the concepts and tools yourself.",
  },
  {
    key: "vibe",
    label: "Vibe coding",
    blurb: "Build with AI coding assistants and ready-made AI models. No machine learning background needed.",
  },
];

const AI_WORDS =
  /\b(ai|artificial intelligence|machine learning|ml|llms?|gpt|genai|generative|agents?|agentic|chatbots?|copilot|prompts?|neural|nlp)\b/i;

// The vibe-coding tab only makes sense for hackathons that look AI-related.
export function isAiRelated(card) {
  const text = [card.title, card.subtitle, card.category, card.description, card.summary, ...card.tags];
  return AI_WORDS.test(text.join(" "));
}
