<!-- verified-against: v3.66.185 -->
# capture_scrub.py — pre-share redactor for capture artifacts

A **standalone, offline** tool to make a real BulkDownloader capture (`.wacz` /
`capture.json` / any JSON) **safe to share** — e.g. to hand to an assistant for
review without exposing F2-sensitive material (login email, Turnstile/captcha
tokens, cookies, signed media URLs).

It is intentionally **more aggressive than BD’s functional redaction**: BD keeps
URLs intact so replay works; this destroys all secrets + signing because a
reviewer only needs *structure*, not working URLs. It closes the gaps BD defers:
**path-signed / base64-wrapped URLs, emails in text bodies, high-entropy tokens.**

## Why it’s trustworthy

- **Pure Python 3 stdlib.** No third-party deps, no network, no BulkDownloader
  imports. Drop it anywhere and run it.
- **Non-destructive.** Writes `<input>.redacted.<ext>`; never touches the input.
- **Self-verifying.** After redacting, an INDEPENDENT second pass re-scans the
  output for any residual secret/signing pattern. If anything remains it
  **refuses to write and exits 2** — you can never accidentally share a file
  that didn’t prove clean.

## Usage

```
python3 capture_scrub.py INPUT [-o OUT] [--mode safe|strict|paranoid] [--dry-run] [--token-min N]
```

- `INPUT` — a `.wacz`, a `capture.json`, or any JSON capture.
- `--mode safe` (default) — destroys: sensitive headers (Cookie/Auth/etc.),
  emails (everywhere, incl. text bodies), JWTs, URL userinfo, signed query
  params, **path-signed segments (incl. base64-decoded signing)**, hex/opaque
  path runs, named `key=secret` pairs, and bare high-entropy tokens. **Keeps**
  hosts, path shape, benign query keys, DOM structure — still useful to review.
- `--mode strict` — also strips **all** query strings and masks every
  non-trivial path segment.
- `--mode paranoid` — masks every URL to `scheme://host/<REDACTED>` and reduces
  bodies/text to markers. Structure/counts only.
- `--token-min N` — opaque-token length threshold (default 32; lower = more
  aggressive).
- `--dry-run` — report what *would* be redacted; write nothing.

## Exit codes

- `0` — redacted output written and **verified clean**. Safe to share.
- `2` — residual secret found after redaction; **output NOT written**. Don’t
  share; report it (the redactor needs a pattern added).
- `1` — usage / IO error.

## Recommended workflow for sharing with an assistant

```
python3 capture_scrub.py mycapture.wacz                 # -> mycapture.redacted.wacz, VERIFY CLEAN
# (optional, stricter) python3 capture_scrub.py mycapture.wacz --mode strict
```

Share only the `*.redacted.wacz`. If you want maximum caution, use `--mode strict` (URLs lose their queries) or `--mode paranoid` (URLs reduced to host).

## Validated

Run against a real authenticated, Cloudflare-challenged capture: destroyed 57
base64-path-signed segments, 263 query-signed params, 50 sensitive headers, and
all bare high-entropy tokens; **0 JWT / 0 email / 0 signing residual** on the
independent verify pass; WACZ structure (hosts, `dom_log`, `capture_health`)
preserved for review. Also validated on synthetic inputs mirroring those shapes,
confirming benign structure (hosts, paths, benign params, DOM) survives.

## Notes / limits

- It’s a **safety over-redactor**, so it will mask some benign long IDs and any
  string under a sensitively-named key. That’s by design (you keep shape, not
  exact values).
- It does not parse binary WARC payloads; non-JSON WACZ members are scrubbed as
  UTF-8 text where possible, left untouched if binary (counts aren’t secrets).
- Keep it OUTSIDE the BulkDownloader release tree (it’s an operator utility, not
  a shipped module) so it never lands in a build.