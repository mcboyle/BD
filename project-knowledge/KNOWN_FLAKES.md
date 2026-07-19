<!-- verified-against: v3.66.754 -->
# KNOWN FLAKES & EXPECTED SKIPS — do not re-investigate

A durable card so a fresh instance never burns a turn re-discovering a known
non-regression — and never wrongly waves off a REAL failure by pattern-matching
"probably a flake." Re-derive from source if anything here looks off, but these
are stable across many sessions.

## Sandbox-only hangs / timeouts (NEVER run the whole tests/ dir)
- **`run_tests.py tests/` (whole dir) HANGS** at `test_perf_lab.py`. Run targeted suites.
- **`test_v3_66_146_nav_guard`** times out >200s in the sandbox. Known; not a regression.
- **`pytest tests/`** blows past the ~5-min bash wall-clock at nproc=1 (~7–9 min) — partition into 10 phases (see SANDBOX ops notes).
- Do NOT `pkill -f <short string>` — it matches your own python3/sh args and kills the script you launched (looks like an unexplained timeout).

## Expected SKIPS in the on-stash binding suite (57 at 185 — all benign)
- **Capture-corpus-absent** (the bulk of them): `test_v3_66_82..89_*`, `test_v3_66_77_*`, `test_v3_66_101_*` evidence-diff, offline-capture-ingest. They SKIP "captures not present" because real `.wacz` are local-only / not in the release tree. This is the A2/A6 data-gate, not a failure.
- **Dev-raw package absent** (`test_v3_66_59_redactor_seam.py` TestDevRawMode ×3): "bd_dev_inspect dev package not installed (release tree) — raw capability correctly absent." This is the CORRECT posture — the raw-capture capability is supposed to be missing from a release tree.
- **`test_dom_recorder_asi.py :: test_old_bare_newline_join_does_not_start_recording`**: SKIPs on `Page.goto` timeout (navigation unstable). The 5 ASI-separator *static* checks always run; only the 3 *behavioral* (browser) ones skip without a healthy navigating browser. For a browser-free check use the Node A/B (`asi_node_ab.js`).
- A couple of fixture-shape skips (`test_v3_66_68` no content_id slot, etc.) — fixture-specific, benign.

## Flakes that PASS on retry (not regressions)
- **snapshot_replay round-trip** under `--workers` (parallel) can flake; **passes on serial retry**. Re-run serial before calling it a failure.
- **`test_v3_66_729_body_contract_fixtures.py`** flakes under high `--workers` (seen at 749/750/753/754). OPEN P1 (`FLAKE-729-PARALLEL`). **DERIVED @754, not theorised** (two prior sessions guessed and both were wrong -- 748: setup_site id collision; 753: _app_cfg contamination; both looked INSIDE the tests):
  - The failure is **FILE-LEVEL, not inside any test function**. The runner's flaky label read `...fixtures.py :: ...fixtures.py` -- the FILE named where a FUNCTION should be. run_tests names a row after the file in exactly four places, ALL file-level: IMPORT ERROR (629), TIMEOUT>900s (983), "worker produced no result file" (987), "could not read worker result" (992). That alone refutes both prior theories.
  - **OOM ruled out**: stash has 590 GiB RAM, 522 GiB free, swap untouched.
  - The file is the **2nd-slowest in the suite** (139.6s, over the 120s soft budget).
  - **The runner DESTROYS the evidence**: the parallel pass stores `(name, ERROR, ok, dur)`, prints only `FAIL <file>`, then the serial retry OVERWRITES the error before anything reports it -- which is why it kept getting guessed at. `bd_729_probe.py` (wraps `_retry_failures_serial`, no source change) dumps the parallel error before the overwrite. **Run it with `venv/bin/python`, NOT `python3`** (the system interpreter has no flask -> 400+ false import failures).
- **`test_session_keeper.py :: test_get_takeover_lock_is_reentrant`** flakes under `--workers`, passes serial. Known, benign.

## Live-test WARNs that are environmental (not failures) — on-stash 21/14/0 shape (was 20/15/0 @185)
The 15 live-test WARNs at 185 are all "nothing to exercise here," not defects:
- no sites configured → L6/L7/L8/L9/L11/L13/L14/L28/L30 warn
- AI assist disabled by config → L17/L18/L19 warn (Ollama not required)
- no display on headless stash → L2 (headed launch) warn
- firefox/webkit absent → L4 warn (BD only uses Chromium today; not a blocker)
- no warm live session → L5 collision-guard not exercisable
These flip to PASS once a site is configured + a download has run. 20 PASS / 15 WARN / 0 FAIL is the healthy baseline.

## The build-step trap (cost real time historically)
`build_release.py` step 4 (no `--skip-tests`) runs `run_tests.py` with NO args =
the whole suite = the perf_lab hang. Always `--skip-tests` and run the band
separately from the EXTRACTED zip. (bd-cut does this for you.)

## What is NOT a flake (treat as real)
- A guard-SHA change you did not sha-declare.
- `verify_release.py --zip` RESULT: FAIL (banner/version_consistency) — e.g. a stale
  version banner like the 179 `capture.sh` "3.63.6". Real; fix it.
- Any `Failed: N>0` in a targeted suite from the extracted zip.
- A change-surface namelist diff with files you didn't intend.

## test_consolidation.py::test_health_wrapper_equals_core (parallel-only, @780)
Surfaced in the 780 stash capture: flaky under `--workers`, PASSED on serial
retry, NOT counted as a failure by the capture gate ("1 flaky ... passed on serial
retry"). Second known parallel-only flake alongside the `test_session_keeper`
reentrant-lock passer. Treat as environmental parallel contention, not a defect,
unless it fails on a SERIAL run.
