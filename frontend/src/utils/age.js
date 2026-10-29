// The backend reports, in X-Listings-Updated, when its oldest saved listings were refreshed
// (seconds since 1970).
export function readUpdated(response) {
  const value = Number(response.headers.get("X-Listings-Updated"));
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function formatAge(epochSeconds, now = Date.now()) {
  const seconds = Math.max(0, Math.round(now / 1000 - epochSeconds));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}
