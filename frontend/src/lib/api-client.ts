// Centralised fetch wrapper for the SPA. Handles:
//   - CSRF token retrieval (state-changing requests need it)
//   - JSON parse + error normalisation
//   - Throws on !response.ok so TanStack Query treats it as an error
//
// CSRF strategy: we lazy-fetch the token from /api/csrf on the first
// state-changing call and cache it for the page session. /api/csrf is
// the canonical token source (self-minting since P0.1 / v3.66.202 —
// it creates a session cookie if none exists and returns the matching
// token, so a SPA deep-link works cold). The bd_session cookie carries
// the auth; the token in the header is the matching CSRF challenge. If
// the token rotates server-side (rare) the request returns 403 and we
// refetch + retry once.
//
// v3.66.208 FIX: this previously fetched a nonexistent route (the
// "auth surface" dev map — which actually lives at /api/dev/auth_map,
// dev-gated). The fetch 404'd, the token stayed null, no X-CSRF-Token
// header was ever sent, and EVERY cookie-session SPA write 403'd on a
// real deployment (the test client carries no bd_session cookie, so
// the suite never saw it). Do not point this anywhere but /api/csrf —
// tests/test_t5_t6_wired.py pins it.

let _csrfToken: string | null = null;
let _csrfFetchPromise: Promise<string | null> | null = null;

async function getCsrfToken(): Promise<string | null> {
  if (_csrfToken) return _csrfToken;
  if (_csrfFetchPromise) return _csrfFetchPromise;
  _csrfFetchPromise = (async () => {
    try {
      const r = await fetch("/api/csrf", { credentials: "same-origin" });
      if (!r.ok) return null;
      const body = await r.json();
      // /api/csrf exposes the per-session token; prefer csrf_token,
      // fall back to csrf, fall back to token (historical names).
      _csrfToken = body.csrf_token || body.csrf || body.token || null;
      return _csrfToken;
    } catch {
      return null;
    } finally {
      _csrfFetchPromise = null;
    }
  })();
  return _csrfFetchPromise;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: unknown,
  ) {
    // Surface the backend's descriptive reason (jsonify({error: "..."}))
    // in .message so every error toast shows WHY, not just the status line.
    const detail =
      body !== null &&
      typeof body === "object" &&
      typeof (body as { error?: unknown }).error === "string"
        ? (body as { error: string }).error
        : null;
    super(detail ? `${message}: ${detail}` : message);
    this.name = "ApiError";
  }
}

// o1826-c48 M205: the ONE CSRF send for every state-changing helper. Sends
// with the cached token; on 403 the token may have rotated -- refetch once
// and retry with the fresh one. Returns the final Response either way.
async function fetchWithCsrf(
  path: string,
  init: RequestInit & { headers: Record<string, string> },
): Promise<Response> {
  const token = await getCsrfToken();
  const headers = { ...init.headers };
  if (token) headers["X-CSRF-Token"] = token;
  const r = await fetch(path, { ...init, credentials: "same-origin", headers });
  if (r.status !== 403) return r;
  _csrfToken = null;
  const retryToken = await getCsrfToken();
  if (!retryToken || retryToken === token) return r;
  return fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { ...headers, "X-CSRF-Token": retryToken },
  });
}

// A failed response becomes an ApiError that carries the backend's JSON
// body (and so its reason) -- including after a CSRF retry.
async function throwApiError(method: string, path: string, r: Response): Promise<never> {
  let body: unknown = undefined;
  try {
    body = await r.json();
  } catch {
    /* ignore */
  }
  throw new ApiError(`${method} ${path} → ${r.status}`, r.status, body);
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(path, { credentials: "same-origin", signal });
  if (!r.ok) await throwApiError("GET", path, r);
  return r.json() as Promise<T>;
}

async function sendJson<T>(
  method: string,
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const r = await fetchWithCsrf(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!r.ok) await throwApiError(method, path, r);
  return r.json() as Promise<T>;
}

export async function apiPost<T>(
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return sendJson<T>("POST", path, payload, signal);
}

// v3.64.x — added for B-2 (per-site widget config). The widgets API
// uses PUT to replace a scope's selection and DELETE to clear an
// override; until now the SPA only had GET/POST helpers. Same CSRF
// pattern as apiPost: header on every request, refetch + retry once
// on 403.

export async function apiPut<T>(
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return sendJson<T>("PUT", path, payload, signal);
}

export async function apiPatch<T>(
  path: string,
  payload: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return sendJson<T>("PATCH", path, payload, signal);
}

export async function apiDelete<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const r = await fetchWithCsrf(path, { method: "DELETE", headers: {}, signal });
  if (!r.ok) await throwApiError("DELETE", path, r);
  return r.json() as Promise<T>;
}

// GUI parity (177) — backup tier needs two extra CSRF-aware shapes:
//  * apiPostForm: multipart upload (backup restore sends a file + fields).
//  * apiPostDownload: POST that streams a file back (backup create), or a JSON
//    error on failure. Same CSRF header + single 403 refetch/retry as apiPost.
export async function apiPostForm<T>(
  path: string,
  form: FormData,
  signal?: AbortSignal,
): Promise<T> {
  // No Content-Type — the browser sets the multipart boundary.
  const r = await fetchWithCsrf(path, { method: "POST", headers: {}, body: form, signal });
  if (!r.ok) await throwApiError("POST", path, r);
  return r.json() as Promise<T>;
}

export async function apiPostDownload(
  path: string,
  payload: unknown,
  fallbackName: string,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetchWithCsrf(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  // The endpoint returns JSON (not a file) on failure.
  if (!r.ok) await throwApiError("POST", path, r);
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const m = /filename="?([^"]+)"?/.exec(cd);
  const name = m ? m[1] : fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
