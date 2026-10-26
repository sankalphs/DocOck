export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001"
).replace(/\/$/, "");

/** Jaeger's UI lives under its own base path on 16686, not behind the API. */
export const JAEGER_UI_URL = (
  process.env.NEXT_PUBLIC_JAEGER_URL ?? "http://127.0.0.1:16686/jaeger/ui"
).replace(/\/$/, "");
