// Thin client for the FastAPI service in backend/. The panel never ranks anything itself.

export const DEFAULT_API_BASE = "http://localhost:8000";

export function makeApi(base) {
  const root = (base || DEFAULT_API_BASE).replace(/\/+$/, "");

  async function call(path, options = {}) {
    const res = await fetch(root + path, {
      ...options,
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        // body was not JSON; keep the status text
      }
      throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    }
    return res.json();
  }

  return {
    root,
    presets: () => call("/presets"),
    vocabulary: () => call("/vocabulary"),
    recommend: (body) => call("/recommend", { method: "POST", body: JSON.stringify(body) }),
    search: (params) => call("/courses?" + new URLSearchParams(params)),
    saveSession: (body) => call("/sessions", { method: "POST", body: JSON.stringify(body) }),
    consistency: (body) => call("/consistency", { method: "POST", body: JSON.stringify(body) }),
    extractStatus: () => call("/extract/status"),
    extract: (transcript) => call("/extract", { method: "POST", body: JSON.stringify({ transcript }) }),
  };
}
