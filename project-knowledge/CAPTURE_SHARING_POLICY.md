<!-- verified-against: v3.66.185 -->
# CAPTURE SHARING POLICY — operator has an offline pre-share scrubber

*Durable (version-agnostic) addendum for the static KB. Read this before reacting to any capture file the operator sends.*

## The fact

Matt has a **standalone offline redactor, `capture_scrub.py`** (stdlib-only, no BD imports, no network; lives in session outputs / recreatable from `capture_scrub_README.md`). He uses it to scrub a real capture **before sending it**, so he can keep an assistant in the loop on real captures without exposing F2 material. It is intentionally MORE aggressive than BD’s functional redaction (it destroys URLs/signing because a reviewer needs structure, not working URLs) and it **self-verifies**: an independent second pass re-scans the output and the tool refuses to write if any secret residual remains.

## Why this matters for you (the assistant)

Do **not** reflexively refuse a capture the operator sends just because captures are normally F2-sensitive. A **scrubbed, verified-clean** capture is no longer a sensitive artifact — the login email, Turnstile/captcha token, cookies, and signed URLs have been destroyed. Reviewing it does not violate the F2 rule. Equally, do **not** blindly trust a filename or the operator’s say-so — verify independently (below). The point is calibrated handling, not reflexive rejection or reflexive trust.

## Decision rule

1. **File is scrubbed** (named `*.redacted.wacz` / `*.redacted.json`, or the operator says they ran `capture_scrub.py`):
- **Independently re-verify it’s clean first.** Re-scan for F2 residual (run `capture_scrub.py <file> --preview`, or the equivalent scan: JWTs, emails, URL userinfo, signed query params, base64/path-signed segments, sensitive-key string values, named `key=secret` pairs). The tool self-verifies, but confirm anyway — defense in depth.
- **If CLEAN → review freely.** You may open structure, DOM, network shape, recognizer signals, etc. Still report findings as **kinds/counts/structure, never raw values** (good habit; also a scrubbed file may retain benign IDs that needn’t be quoted).
- **If residual found → treat as F2.** Do NOT use it for analysis, fixtures, or anything circulated. Tell the operator the scrub has a gap (cite the residual kind + JSON path, not the value) so a pattern can be added to `capture_scrub.py`. This is a tool bug, not a reason to proceed.
1. **File is a RAW, un-scrubbed real capture** (`.wacz` / `capture.json` that wasn’t run through the scrubber):
- It is **F2-sensitive**. Do NOT fixture it, commit it, circulate it, or include its values in any output.
- You MAY inspect it **locally for a redaction/audit purpose, reporting kinds/counts only, never values** (e.g. “0 JWTs, N signed URLs, 1 base64 path-signed survivor”) — this is how redaction posture gets validated.
- For any further/shareable work, **produce a scrubbed copy first**: run `capture_scrub.py <file>` (locally), confirm VERIFY CLEAN, then work from the `*.redacted.*` output.
- Prefer asking the operator to scrub before sending; if a raw file has already arrived, handle it as above — don’t redistribute it.
1. **Synthetic fixtures** remain the only captures that may be committed/circulated. Unchanged.

## What the scrubber covers (so you know what “clean” means)

Destroyed in `--mode safe`: sensitive headers (Cookie/Set-Cookie/Authorization/etc.), emails everywhere (incl. text bodies), JWTs, URL userinfo, signed query params, **path-signed segments including base64-wrapped** (the gap BD defers), hex/opaque path runs, named `key=secret` pairs, string values under sensitively-named keys, and bare high-entropy tokens. Preserved: hosts, path shape, benign query keys, DOM structure, `capture_health`/`redaction_profile` stamps — i.e. everything useful for review. `--mode strict` also drops all query strings; `--mode paranoid` reduces URLs to host and bodies to markers.

## Note on scope

This policy is about **reviewing operator-provided captures safely**. It does NOT relax any other rule: the BD release artifacts still must be free of real `.wacz`/caches/secrets; BD’s own captures are still local-only until the F2 hardening pass; the credential floor stays unconditional. The scrubber is an operator convenience for sharing, not part of BD’s shipped redaction.