// All backend calls go through here. In development the Vite proxy forwards /api, so the base
// is empty. In production set VITE_API_BASE to the backend origin, e.g. https://api.example.com.
const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function getJson(url, params) {
  const query = analysisQueryString(params || {});
  let response;
  try {
    response = await fetch(`${apiUrl(url)}?${query}`);
  } catch {
    throw new ApiError("Could not reach the backend. Is the Django server running?", 0);
  }

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      body.error ||
      (typeof body === "object"
        ? Object.entries(body)
            .map(([field, msg]) => `${field}: ${Array.isArray(msg) ? msg.join(" ") : msg}`)
            .join(" · ")
        : "Request failed");
    throw new ApiError(detail || "Request failed", response.status);
  }
  return body;
}

export function searchPlaces(query) {
  return getJson("/api/geocode/", { q: query }).then((data) => data.results);
}

export function analysisQueryString(params) {
  return new URLSearchParams(params).toString();
}

export function fetchAnalysis(params) {
  return getJson("/api/analysis/", params);
}
