# Bulk Downloader — security tooling

Two analysis pipelines you can run locally.

| Script | What it does |
|---|---|
| `sast.sh` / `sast.bat` | Static analysis: Bandit, Semgrep, pip-audit, Ruff, ESLint, project preflight. |
| `dast.sh` / `dast.bat` | Dynamic analysis: in-process probe + optional OWASP ZAP, Nikto, sqlmap. |
| `dast_probe.py`        | The in-process probe (called by `dast.sh`/`dast.bat`; also runnable directly). |

Both pipelines write per-tool reports under `tools/sast_results/` and
`tools/dast_results/` plus a `SUMMARY.txt` aggregate.

---

## Quick start

```bash
# POSIX
chmod +x tools/sast.sh tools/dast.sh
tools/sast.sh    # static
tools/dast.sh    # dynamic

# Windows
tools\sast.bat
tools\dast.bat
```

First run takes longer — the scripts auto-install missing Python tools
via `pip` (Bandit, Semgrep, pip-audit, Ruff) and ESLint via `npm`.

Exit code `0` = clean; `1` = findings; `2` = setup error.

---

## SAST coverage

| Tool | What it catches | Auto-installed? |
|---|---|---|
| **Bandit** | Python security anti-patterns (eval, pickle.load, hardcoded secrets, weak crypto, SQL string formatting) | yes |
| **Semgrep** | Polyglot rule packs: `p/security-audit`, `p/python`, `p/javascript`, `p/owasp-top-ten`. Catches things Bandit misses, plus JS issues. | yes |
| **pip-audit** | Known CVEs in `requirements.txt` (queries OSV + PyPI advisories). | yes |
| **Ruff** | Bug-prone patterns + selected Bandit-equivalent rules (`S` prefix), bugbear (`B`), syntax (`E9`, `F63`). | yes |
| **ESLint + security plugin** | XSS sinks, regex DoS, eval-like, unsafe object access in `app.js`. | yes (needs Node.js) |
| **`preflight.py`** | The project's own cross-cutting validation (malformed JSON, CSRF, rate limit, path traversal storage). | (bundled) |

### Scanner availability

`sast.sh` returns setup-error exit `2` when a scanner is unavailable, fails,
or does not produce a complete report; it never treats that state as clean.
The Windows batch pipeline retains its own availability handling.

---

## DAST coverage

| Stage | Tool | What it catches |
|---|---|---|
| 1 | **`dast_probe.py`** (in-process) | XSS round-trip verification (the FTS fix), path traversal, malformed input, SQL injection probes, cookie_file auto-fill, auto-relogin opt-out, ReDoS, response-time variance, security headers, state stability under load. Fast (~2 s) and deterministic. |
| 2 | (boot app on `127.0.0.1:9876`) | Background app for external scanners |
| 3 | **OWASP ZAP** baseline (Docker) | OWASP Top 10 web scan: missing headers, reflected XSS, basic SQLi, etc. |
| 4 | **Nikto** | Common misconfigurations, default credentials, known bad URLs. |
| 5 | **sqlmap** quick probe | SQL injection on a parametrised endpoint (`/api/logs/tail?lines=`). |

### What the probe checks (12 sections)

1. `serve_ss` path traversal (Bug 5 fix verification)
2. FTS stored XSS via `<mark>` delimiters (Bug 9 fix verification)
3. `cookie_file` auto-fill on save (v3.47.7 login-loop fix)
4. `auto_relogin_enabled=False` opt-out + type-confusion on `auto_relogin_interval_hours`
5. `POST /api/logs/clear` truncates + sweeps archives
6. `GET /api/logs/tail` edge cases: absurd line counts, non-numeric, scientific notation
7. Dev mode enabled by default + `BD_DEV_MODE_DISABLE=1` kill-switch + `/api/dev/run` path-escape rejection
8. Malformed JSON, oversized payloads, unicode poisoning on `/api/sites`
9. SQL injection probes against `db_search_fts` (parameterised queries proof)
10. Response-time + ReDoS probe (100 repeated requests, 10000-char search)
11. Security response headers (CSP, X-Content-Type-Options, Referrer-Policy)
12. State stability: 50 sequential creates + 100 concurrent reads

### Installing the optional external scanners

```bash
# POSIX
sudo apt install nikto sqlmap docker.io          # Debian/Ubuntu
brew install nikto sqlmap                         # macOS
docker pull ghcr.io/zaproxy/zaproxy:stable        # ZAP via Docker

# Windows
# Docker Desktop: https://www.docker.com/products/docker-desktop/
# Nikto:  Strawberry Perl + `git clone https://github.com/sullo/nikto.git`
# sqlmap: pip install sqlmap   (works on Windows too)
```

If a scanner isn't installed the stage prints `SKIPPED` and the pipeline
continues with the others. Only stage 1 (the in-process probe) is
required — the rest are belt-and-suspenders.

---

## What these scripts do NOT cover

- **Fuzz testing.** `preflight.py` covers a fixed set of edge inputs.
  For deeper fuzzing add `pip install atheris hypothesis` and write
  targeted harnesses against `db.py` / config parsing.
- **Authenticated DAST.** ZAP baseline runs unauthenticated by default;
  configure it with a `replacer` rule to inject your session token if
  you want it to scan auth-gated routes.
- **Browser-side runtime XSS.** The probe verifies the server NEVER
  emits live `<script>` from indexed data, but doesn't render JS in a
  real browser. The included Playwright tests (`tests/test_e2e_smoke.py`)
  cover that surface.
- **Network-layer pen testing** (nmap, masscan). Out of scope —
  this is a single-port Flask app on `0.0.0.0`.
- **Adult-content-site target probes.** Out of scope; the targets are
  third-party and probing them would be inappropriate use.

---

## Running on every commit

Add to `.git/hooks/pre-push` (or your CI):

```bash
#!/usr/bin/env bash
set -e
tools/sast.sh
BD_DISABLE_KEEPALIVE=1 python tools/dast_probe.py
```

The probe alone is ~2 seconds; full SAST is ~30 s on a warm cache; full
DAST with ZAP is ~2 min.

---

## Interpreting results

- **`0 bugs, 0 crits`** in the probe summary = the v3.47.7 fixes are
  intact end-to-end.
- **`warn`** findings are documented defensive gaps in the single-user
  LAN threat model — not bugs in current use, but worth tightening if
  the deployment model changes (multi-user, internet-exposed, etc.).
  See `RELEASE_REPORT.md` for the threat-model context.
- **`crit`** findings should never appear on a clean v3.47.7 install.
  If one does, treat it as a regression and bisect against this
  commit.
