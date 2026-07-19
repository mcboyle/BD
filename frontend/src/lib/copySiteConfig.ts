// Cut 6.7 — copy-whole-site-config. Serialises a site config for the clipboard
// with SECRET VALUES OMITTED. Keys that look secret keep their key in the output
// (so the shape is legible) but their value is replaced with a redaction marker —
// the literal secret never reaches the clipboard.
//
// REDACT-SOT Cut 3 (D3): the secret substrings are sourced from the server
// URL/query SoT via generated constants (URL_SECRET_SUBSTRINGS), so this masking
// can never drift from capture_redact.SENSITIVE_QS_KEY. The server's anchored-exact
// tail (code / k / state / nonce / otp / ...) is deliberately NOT applied here:
// on a clipboard copy of a config those short bare keys are usually benign fields,
// and masking them would corrupt legible output (the pre-existing F-FE09-01 choice).

import { isClipboardSecretKey } from "@/lib/secretKeys.generated";

const REDACTED = "<omitted>";

export function buildSiteConfigClipboard(site: Record<string, unknown>): string {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(site)) {
    out[k] = isClipboardSecretKey(k) ? REDACTED : v;
  }
  return JSON.stringify(out, null, 2);
}
