# AUDIT RUN-01 — runner kernel (runner.py + runner_extractors.py) · v3.66.532

**Batch:** RUN-01 (runner kernel; batch max_risk 0.499)
**Audited:** `bulk_downloader/runner.py` (2884 SLOC, CC=163) · `bulk_downloader/runner_extractors.py` (1827 SLOC, sink_weight=45)
**Mode:** READ-ONLY. No cut / bump / guard-edit / baseline-update / tracker-write / stash. `/home/claude/work` untouched.
**Version:** derived from `STATE.json` (`live_version` 3.66.532); `bd-preflight` + `bd-state` both PASS byte-identical; graph module shas match live source (`runner.py`=`ac92700bc99521c0`, `runner_extractors.py`=`7244a7f4d9379b53`).

## Verdict

- **3 findings** — 2 confirmed (1 boundary-deferred reachability), 1 low/probable. **0 criticals** by stock scanners.
- **F-RUN01-01** · SSRF · med-low · the listing-scrape guard is a denylist that accepts CGNAT `100.64.0.0/10` (violates the runner-side twin of I0003).
- **F-RUN01-02** · injection · high · `_build_ytdlp_cmd` has no `--` before the URL → a `-`-leading job URL is argument-injected into yt-dlp (`--exec` ⇒ RCE). **First confirmed real instance of the pilot candidate DP-CAP01-cand-1.**
- **F-RUN01-03** · type/None · low · `float(config)` disk/bandwidth thresholds feed a bounds compare with no `math.isfinite` (NaN evades; same class as I0004).
- **SSRF cross-check (the kickoff ask):** the payload download path does **not** re-validate host — but unlike CAP-01's `live_recorder` there is **no cam-site allowlist to bypass**; a bulk downloader fetches arbitrary user URLs by design. So this is *not* an F-CAP01-01-style bypass — it is an inherent, proxy/VPN-mitigated residual, recorded as `ssrf=residual` on the extractors file, not a new finding.
- `audit_emit_gate`: **PASS** (5 falsifiable claims, every one witnessed). All 10 witnesses **DEMONSTRATED**.

## Guard files

None of the two audited files is a SHA-pinned guard file. The 7-guard set was confirmed intact for the whole tree via `bd-state` (`7 guards match`) and `bd-preflight` (2207 files byte-identical). `guards_verified_byte_identical=0` for this batch by construction; `guard_touch=false`.

## Per-file (risk-ordered)

| File | SLOC | CC | prior | risk | rubric verdicts |
|---|---|---|---|---|---|
| `runner.py` | 2884 | 163 | 1.0 | 0.499 | auth·na · injection·ok · **ssrf·finding** · secrets·ok · err·ok · **type/None·finding** · conc·ok · resource·ok · input·ok · dead·ok |
| `runner_extractors.py` | 1827 | 53 | 0.0 | 0.212 | auth·na · **injection·finding** · ssrf·**residual** · secrets·ok · err·ok · type/None·ok · conc·na · resource·ok · **input·finding** · dead·ok |

`_process_one` (CC=163, radon-confirmed) is a dispatch orchestrator — a chain of opt-in short-circuits (library/jsonapi/JD/qB/teach) + the Playwright challenge stack (Turnstile/FlareSolverr/captcha), each wrapped in degrade-open try/except (the DP-13 "swallowed" population — deliberate resilience, not bugs). `runner_extractors` carries the batch's one subprocess sink + the HLS/ffmpeg sinks delegated to `hls_downloader` (other batch).

## Findings

### F-RUN01-01 — listing-scrape SSRF guard is a denylist without `is_global` · med-low · **confirmed**
`_scrape_listing_urls` (runner.py:538–573) validates the resolved IP with `is_private or is_loopback or is_link_local or is_multicast or is_reserved or is_unspecified` (566–568). This is the exact VR-P15/DP-11 class that **I0003 fixed in `provider_resolve_impl/_common.py` by switching to `ipaddress.is_global`**. On Python 3.12.3 the denylist **accepts `100.64.0.1`** (RFC6598 CGNAT: `is_private=False`, `is_reserved=False`, `is_global=False`). TEST-NET / benchmarking / 240-4 are caught by `is_reserved`; **CGNAT is the specific residual gap.** runner.py is out of compliance with its own I0003 standard.
- **Fix:** `if not ip_obj.is_global: raise` (the I0003 standard).
- **Reachability:** config-driven subscription URL, operator-authenticated; the docstring itself frames it as hygiene. Second-order: listing-discovered `<a href>` URLs are re-enqueued without re-passing even this guard (618 only checks `startswith("http")`).
- **Witness:** `witnesses/run01_witnesses.py::W1`. **Repro (RED-first, to write in fix-cut):** `test_runner_ssrf_listing_is_global.py::test_listing_guard_rejects_cgnat`.

### F-RUN01-02 — yt-dlp argv has no `--` end-of-options separator · high · **confirmed defect / probable reachability**
`_build_ytdlp_cmd` (runner_extractors.py:83–103) does `cmd.append(url)` (102) with **no preceding `--`**; `subprocess.run(cmd, shell=False)` at 160. `shell=False` blocks shell injection (assurance), but yt-dlp's parser reads any `-`-leading argv element as an **option**. Witnessed end-to-end: real yt-dlp **2026.03.17**, handed the builder's output with `url="--version"`, parsed the slot as `--version` (printed a version, exited 0, no fetch). yt-dlp's `--exec` / `--config-location` ⇒ arbitrary command execution. **This is the first confirmed real instance of the pilot's DP-CAP01-cand-1.**
- **Fix:** `cmd.append("--"); cmd.append(url)` — one line, caller-independent.
- **Reachability:** gated by (1) `use_ytdlp_fallback` (opt-in, default off) and (2) a job URL able to begin with `-`. Some enqueue paths validate `startswith("http")` (app.py:1881, :4897) but many exist (`api_bulk_enqueue`, `poll_sources` [external plugin URLs], `discover_all`, `saved_searches`, `capture_schedules`), several ingesting **external** URLs — airtight non-reachability spans the APP/CORE_BD batches, so **PROBABLE, boundary-deferred** (mirrors F-CAP01-01). The defect + fix stand regardless.
- **Witness:** `witnesses/run01_witnesses.py::W2` (SAFE `--version` probe, never `--exec`). **Repro:** `test_build_ytdlp_cmd_optsep.py::test_dashdash_before_url`.

### F-RUN01-03 — `float(config)` thresholds with no `math.isfinite` guard · low · **probable**
`threshold=float(config.get("disk_threshold_gb",2.0))` (795) → `free < threshold` (799); `target_mbps=float(config.get("bandwidth_target_mbps",0) or 0)` (1058) → `target_mbps <= 0` (1059). A NaN in config makes `free < NaN` vacuously False → the low-disk gate **never fires** (disk fills silently); +inf inverts it. Same class as **I0004** (guarded in `site_editor.py`); `json.loads` accepts a bare `NaN` token, so a hand-edited config carries it. **[verify_audit completeness note: a third `float(disk_threshold_gb)` site exists at :1041 (second gate) plus a reporting-dict at :1279 — the fix must cover :1041 too.]**
- **Fix:** `if not math.isfinite(threshold): threshold = <default>` at both sites (defense-in-depth even if validated upstream).
- **Reachability:** operator-config fields; whether they flow through the I0004 validator vs a direct `global_config` load is owned by the settings/site-editor batch. Filed low/probable; the consuming code does not self-guard either way.
- **Witness:** `witnesses/run01_witnesses.py::W10`. **Repro:** `test_runner_disk_threshold_isfinite.py::test_nan_threshold_still_fires_low_disk`.

## New invariants (UNGUARDED candidates → INVARIANTS.json)

- **I-RUN01-ssrf-is-global** — every runner SSRF host classifier must use `ipaddress.is_global` (positive allowlist), not an enumerated denylist (misses CGNAT). Runner-side twin of I0003. UNGUARDED; witness W1.
- **I-RUN01-ytdlp-argv-sep** — the yt-dlp builder must place `--` immediately before the URL argv; list-form + `shell=False` is necessary but not sufficient. UNGUARDED; witness W2. (Concrete guard for DP-CAP01-cand-1.)

## New defect-patterns (→ new_patterns[] / DEFECT_PATTERN_CATALOG.md at consolidation)

- **DP-RUN01-cand-1** — denylist-style SSRF IP classifier: flag an `is_private/is_loopback/…` OR-chain used as the accept/reject test **without** a positive `is_global` in the same predicate. Strengthens DP-11. Confirmed instance: F-RUN01-01.
- **DP-CAP01-cand-1 → PROMOTE** — list-form `shell=False` subprocess is not sufficient for a request-derived URL argv; require a `--` separator (shape/arg-injection). **First confirmed real instance: F-RUN01-02** — promote candidate → confirmed.

## False-positive confirmations (for `bd-triage`)

- **runner.py:1052** `sql_fstring` (DP-10/taint) → **FP**: it is `self.log_event("disk_throttle", f"Disk pressure: …")` — a log call, numeric interpolations, no SQL. Rule: an f-string in `log_event`/`print`/`stderr.write` is not a SQL sink even in a DB-adjacent function.
- **runner_extractors.py:343** `sql_fstring` (DP-10/taint) → **FP**: `sys.stderr.write(f" deep-detect: WARNING … {self.site_id!r} …")` — a warning log, no SQL.
- **runner.py:1144** DP-06 (getattr on possibly-undefined name) → **FP**: `login_session = getattr(self, "_manual_login_handle", None)` binds unconditionally (default None); no NameError path.

## Assurances (witnessed) + cross-batch dependencies

**Assurances:** yt-dlp subprocess is list-form + `shell=False` — no shell-command injection (W6, distinct from the arg-injection F-RUN01-02) · cookies/bypass-cookies logged as **counts** (`len(...)`), never values (W7) · post-download path sinks join under `dl_dir` + gate on `os.path.isfile`; yt-dlp path sanitized by `--restrict-filenames` (W8) · **no** eval/exec/os.system/`shell=True`/pickle anywhere in either file; whole-file sweep clean (W9); `setattr` uses the literal `"_site_usage_cache"` · extractor write targets go through `resolve_filename_template` + `safe_dest` before `os.path.join(dl_dir)` · `page.goto(url)` is scheme-validated by Playwright · the ~78+23 DP-13 "swallowed exception" flags are deliberate degrade-open resilience (bandit 53× B110), not filed · dead_code: the 4 shipped-flagged functions are `threading.Thread` targets / dynamic dispatch (hedged at 0.5), not dead.

**Cross-batch dependencies (host-check / traversal responsibility that leaves these two files):** `hls_downloader.download(src.url,…)` byte-write + ffmpeg (CORE_BD/transport) · `detect.py::safe_dest` traversal-sanitizer strength (REC) · `provider_resolve_impl/_common.py` I0003 for the provider-embed path (REC/provider) · the app/plugins/discovery/saved_searches/capture enqueue paths' URL scheme validation, which sets F-RUN01-02 reachability (APP/CORE_BD) · `qb_bridge`/`jd_bridge` torrent/JD URL handling.

## Independent corroboration (`~/rev` offline wheelhouse)

- **radon 6.0.1** — `_process_one` **CC=163** and `_try_jsonapi_extractor` **CC=53** confirmed exactly; the shipped graph risk metadata is faithful.
- **bandit 1.9.4** — 57 findings, **all LOW, 0 medium/high**. `B603` fires on `subprocess.run(:160)` ("check for execution of untrusted input") = exactly F-RUN01-02; `B110`×53 = the swallowed-exception population. bandit does **not** surface the SSRF-denylist or arg-injection specifics (no stock rule) — those came from the custom defect-patterns + source + witnesses.
- **vulture 2.16** — dead-code flags are FP-prone here (`sync_playwright` used 5× yet flagged); no true dead code.
- Note: the shipped `KNOWLEDGE_GRAPH.db.sha256` pin mismatches the actual db — **pilot finding P2** (pin drifts, ungated). The db is valid sqlite, correct counts (1005/7183/55054), faithful shas.

## Attestation

`guard_touch=false` · `tracker_write=false` · `/home/claude/work` re-verified byte-identical (`bd-preflight` PASS post-audit) · deliverables written only under `review/` · versions/shas derived from `STATE.json`+graph, not hard-coded · every finding + invariant carries an executable witness (`audit_emit_gate` PASS).

---
*Independently verified in the Session-2 continuation: module SHAs matched the reviewer's 532 tree, all 10 witnesses re-ran and DEMONSTRATED, `audit_emit_gate` PASS, `verify_audit` ACCEPT, merged clean (ledger reviewed=11/1005, findings_open=4).*
