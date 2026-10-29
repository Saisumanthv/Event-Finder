// Set VITE_API_URL (for example https://your-backend.onrender.com) when building for
// deployment; it is baked in at build time. Local development falls back to port 8000.
const BASE_URL = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

export const API_URL = `${BASE_URL}/button-action`;
export const RECOMMEND_URL = `${BASE_URL}/recommend`;
export const SEARCH_URL = `${BASE_URL}/search`;
export const LEARNING_PATH_URL = `${BASE_URL}/learning-path`;
export const WEB_SEARCH_URL = `${BASE_URL}/web-search`;
export const ASSISTANT_URL = `${BASE_URL}/assistant`;
export const PREP_MAP_URL = `${BASE_URL}/prep-map`;
export const LEARN_URL = `${BASE_URL}/learn`;
export const CARD_CHAT_URL = `${BASE_URL}/card-chat`;
export const CARD_CHAT_INGEST_URL = `${BASE_URL}/card-chat/ingest`;
export const VIDEOS_URL = `${BASE_URL}/videos`;
