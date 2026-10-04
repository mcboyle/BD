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

// o1671-a13 (AUDIT-13): the predicate applies at EVERY depth. A one-level walk left a
// nested block under a benign key ("login", "accounts", ...) serialised verbatim, so the
// same password/cookie/token keys that were redacted at the top level reached the
// clipboard in plaintext one level down. Objects and arrays keep their shape; a secret
// key's value is replaced whole, whatever it holds.
function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = isClipboardSecretKey(k) ? REDACTED : redactSecrets(v);
    }
    return out;
  }
  return value;
}

export function buildSiteConfigClipboard(site: Record<string, unknown>): string {
  return JSON.stringify(redactSecrets(site), null, 2);
}
