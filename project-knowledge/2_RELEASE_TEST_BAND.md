<!-- verified-against: v3.66.276 -->
# #2 — Release test band + the builder's test trap

## The band (what "tests are green" means)

Run these from the **extracted zip** (not the work tree). Counts are the historical 160-era baseline (131);
re-confirm per release, since suites are added over time (e.g. the ASI guard below was added at 164). **The binding band = STATE.validation's per-cut set + the on-stash full suite; this table is a FLOOR, not the ceiling — newer GCW/probe/capture suites are added over time. Use `TOUCHED_FILE_TO_TEST.md` to pick the regression set for a given change.**

| Suite | Count |
|-------|------:|
| `tests/test_put_numeric_range_backstop.py` | 10 |
| `tests/test_settings_center_slice4.py` | 10 |
| `tests/test_settings_center_slice5.py` | 11 |
| `tests/test_settings_center_secret_classifier.py` | 6 |
| `tests/test_settings_center_wiring.py` | 12 |
| `tests/test_contracts.py` | 17 |
| `tests/test_gui_parity.py` | 12 |
| `tests/test_v3_66_133_live_config_apply.py` | 23 |
| `tests/test_extraction_core.py` | 21 |
| `tests/test_extraction_core_characterization.py` | 9 |
| `tests/test_dom_recorder_asi.py` *(added v3.66.164)* | 5 static (+3 behavioral that SKIP without a healthy browser) |
| `tests/test_secret_display_never.py` *(added v3.66.182)* | runs on stash (~17s) — the no-stored-secret-echo gate (G0/G12) |
| `tests/test_fresh_install_gui_smoke.py` *(added v3.66.182)* | fresh-install GUI smoke; render tier opt-in via `BD_GUI_SMOKE_RENDER=1` (off by default) |

One-liner per suite:
```
bd bash -c 'cd <extracted> && BD_DISABLE_KEEPALIVE=1 python3 run_tests.py tests/<file>.py'
```

**`test_dom_recorder_asi.py` note:** the 5 ASI-separator checks always run (separator present; not the bare-newline
join; bundle tail lacks `;`; no CDN/runtime network; vendored assets from disk). The 3 behavioral tests need a
healthy capture browser and SKIP cleanly on `Page.goto` timeout in the sandbox (and on any host where the
browser can't navigate — see the Capture Navigation Blocker). For a browser-free runtime check use the Node
A/B (`asi_node_ab.js`): OLD join throws the ASI error, NEW join starts recording.
These 5 ASI-separator checks are a *narrower* set than the byte-identical
release guards in `CLAUDE.md` section 2 — same word "guard," don't conflate them.

## The builder trap (cost the most time)

`tools/build_release.py` step 4 extracts the zip and runs **`run_tests.py` with no args** (the whole suite) and
**only emits the zip if `Failed: 0`** — but the no-arg run can hit the **`test_perf_lab.py` hang** (the whole
`tests/` dir is not safe to run unattended). So:

- **Build with `--skip-tests`** (zip + manifest/version/catalog gates only), **then run the band explicitly**
  from the extracted zip. This is the reliable path.
- `--quick` makes tests "an exhortation rather than a gate" (between releases only, never on a real cut).
- Default (no flag) = full gate, but be ready for the perf_lab hang under a long ceiling.

**Exit codes:** `0` all gates passed · `1` built but a gate failed (verifier drift / test failures) · `2`
couldn't build (missing source, version mismatch, IO).

## FINALIZE banner/version gate — `verify_release.py --zip` (added after the 180 audit)

`build_release.py`'s version gate is only the **`APP VERSION-PIN SCAN`** (`__version__` test pins). It does
**not** catch a stale *version banner* anywhere else in the tree — so a drifted banner passes the build and
only `tools/verify_release.py` surfaces it. **Run it on every cut and confirm `RESULT: PASS`:**
```
bd bash -c 'cd <extracted-or-build-tree> && PYTHONPATH=/tmp/prestaged_site_packages BD_DISABLE_KEEPALIVE=1 python3 tools/verify_release.py --zip <BulkDownloader_vX.Y.Z.zip>'
```
**Gate-safe:** the process **exits 1 on FAIL, 0 on PASS** — so it can be wired as a hard `$?` gate in FINALIZE
(do NOT judge by piping into `tail`/`grep` and reading `$?` — that captures the pipe's last command, not the
tool). Use `--json` for machine-parsing and `--tests gate|full` to control the test scope.

It checks: version_consistency (banners + CHANGELOG entry), required docs, template-inventory, and
zip==tree. **Known-benign notes that are NOT failures:** (a) the **template-inventory** reptyle-draft curation
status (e.g. `app.reptyle.com … needs-review`) is informational; (b) `version_consistency` reports
`frontend/package.json version = '0.1.0' (NOT aligned … by design)` — the SPA is versioned independently; do
NOT "align" it. A historically-recurring **false alarm** was the `capture.sh:2` banner reading `v3.63.6+`;
**the fix is to drop the version token from that `capture.sh:2` comment** (land it with a cut so the live tree
matches this note). Any *new* stale banner must be resolved before the cut. `capture.sh` is **not** a guard,
so reword freely; `build_release.py` **is** a guard, so do not move this gate into it (that would change its SHA).
