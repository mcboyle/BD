import { useMutation } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";

import { apiPost } from "@/lib/api-client";

// v3.66.735 — the cookie_clipboard CONTROL cluster.
//
//   POST /api/cookie_clipboard/parse       {text} -> {format, cookies, count, confidence}
//   POST /api/cookie_clipboard/save/<sid>  {text} -> {ok, count, format, confidence, path}
//
// Both were GUI-dark: the blueprint exists and nothing in the SPA could reach
// it. Importing a browser cookie jar meant curling the API by hand.
//
// ============================================================================
// THE FACT THIS FILE EXISTS TO GET RIGHT:
//
//   /save/<sid> RE-PARSES THE RAW TEXT. It does NOT accept parsed cookies.
//
//   app_cookie_clipboard.py:
//       text   = body.get("text", "")
//       parsed = _cc.auto_detect_and_parse(text)
//       if not parsed.get("cookies"): return 400 "could not parse any cookies"
//
// The obvious "efficient" wiring — parse once, then POST the parsed cookies to
// /save — sends a body the endpoint never reads. `text` would be "", the
// re-parse would yield nothing, and every save would 400 "could not parse any
// cookies" while the preview on screen showed N cookies parsed perfectly. A
// type-correct, meaning-wrong control. The RAW TEXT goes to save, and
// useCookieClipboard.test pins that it stays that way.
// ============================================================================
//
// SECRETS: cookie values are session tokens. The parse preview carries them, so
// nothing in this path may log, echo, or persist a value. The panel renders
// name/domain only (see CookieClipboardPanel) — never the value.

/** One cookie as auto_detect_and_parse normalizes it. `value` IS the secret. */
export interface ParsedCookie {
  name: string;
  value: string;
  domain?: string;
  path?: string;
  expires?: number;
  httpOnly?: boolean;
  secure?: boolean;
  sameSite?: string;
}

export interface ParseResult {
  /** null when nothing recognizable was found. */
  format: string | null;
  cookies: ParsedCookie[];
  count: number;
  confidence: number;
  error?: string;
}

export interface SaveResult {
  ok: boolean;
  count?: number;
  format?: string | null;
  confidence?: number;
  path?: string;
  error?: string;
}

/** POST /api/cookie_clipboard/parse — a pure preview. Reads nothing, writes
 *  nothing, touches no site. It therefore gets NO confirm gate: a confirmation
 *  in front of a read-only action is theatre, and theatre is how operators
 *  learn to click through the real ones. Empty text is a real 400. */
export function useParseCookies() {
  return useMutation<ParseResult, Error, string>({
    mutationFn: (text) => apiPost<ParseResult>("/api/cookie_clipboard/parse", { text }),
  });
}

/** POST /api/cookie_clipboard/save/<sid> — parses AND writes the site's cookie jar.
 *
 *  Sends the RAW TEXT (see the header block). Known refusals, all real status
 *  codes rather than ok:false-at-200:
 *    404  no such site: <sid>
 *    400  site has no cookie_file configured
 *    400  could not parse any cookies
 *
 *  The FE cannot pre-check the cookie_file one: `cookie_file` is secret-classed
 *  (app_settings_center.py treats it like a secret key and excludes it from the
 *  editable surface), so the SPA genuinely cannot see whether a site has one.
 *  We do not invent a read to get around that — we surface the backend's reason.
 */
export function useSaveCookies() {
  const qc = useQueryClient();
  return useMutation<SaveResult, Error, { sid: string; text: string }>({
    mutationFn: ({ sid, text }) =>
      apiPost<SaveResult>(`/api/cookie_clipboard/save/${encodeURIComponent(sid)}`, {
        text,
      }),
    onSuccess: () => {
      // A fresh jar changes auth_state.
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["site-editable"] });
    },
  });
}
