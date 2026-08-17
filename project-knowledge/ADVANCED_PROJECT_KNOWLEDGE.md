<!-- verified-against: v3.66.464 -->
# ADVANCED_PROJECT_KNOWLEDGE.md

The single consolidated reference for the **durable, still-applicable** lessons of
BulkDownloader: the footguns, failure shapes, invariants, and disciplines that a fresh
Claude instance must know and that are NOT obvious from the source tree. Every item here
was either observed directly or grep-validated against the **v3.66.464** work tree; stale
version-specifics have been stripped. Where an item belongs to a specific static card,
the home is named in **[brackets]** so it can be folded there too -- this doc is the
union, the cards are the per-topic views.

Reading order for a fresh session is unchanged: `STATE.json` -> newest `KB_HANDOFF` ->
this doc -> `KB_ACTIVE_INDEX` for anything deeper. **Source code is the final ground
truth over every doc here.**

---

## A. Sandbox & shell footguns (these fail SILENTLY)

- **`bash_tool` is `/bin/sh` (dash), not bash.** Each call is a fresh shell with no
  auto-loaded env. Wrap any bash-ism in `bd bash -c '...'` or a `bash script.sh`. `bd
  <cmd>` runs anything with the full env + background services.
- **`find -size N` defaults to 512-byte BLOCKS, not bytes.** `-size -2000000` means
  "< ~1 GB", so a filter meant as "< 2 MB" matches almost everything. Always append the
  `c` suffix: `-size -2000000c`. (Observed 464: the unit-less form began extracting 244
  full-tree zips and filled the disk to 0.)
- **`read -d ''` (NUL-delimited read) is a bash-ism -- it no-ops under dash**, printing
  `read: Illegal option -d` every iteration while the loop body never runs (silent
  no-op, exit 0). Iterate filenames via `python3` (`os.walk`/`glob`) or real bash. A
  plain `for f in *.zip` glob is fine only when names have no spaces.
- **No backticks inside `python3 -c "..."` under dash** -- the shell executes them before
  Python sees the string. Use a `create_file` script instead of inline `-c` for anything
  non-trivial.
- **Always invoke `python3` explicitly** (never bare `python`) in shell. Backend/import
  checks must use the service venv `venv/bin/python` (NOT `.venv/`), or a venv-only
  package (e.g. `cloakbrowser`) is invisible and `resolve_backend()` falsely reports
  `playwright`.
- **Network is OFF for `bash_tool`.** Live browser/noVNC launches are not runtime-testable
  here; interactive capture runs in the noVNC/sentinel/manual operator flow, not as a
  background Flask subprocess (Playwright sync/async conflict).
- **Playwright defaults to the UNINSTALLED headless-shell.** A launch that omits
  `executable_path` fails in-sandbox even when a browser is present; point it at the
  installed binary.
- **`app.run()` in a thread DEADLOCKS.** For an in-sandbox Flask harness use
  `werkzeug.serving.make_server` in the thread, not `app.run()`.
- **NEVER run the full test suite in the sandbox** (`run_tests.py tests/` or any all-tests
  invocation) -- it HANGS at `test_perf_lab.py`. Run targeted suites in small batches
  (`--timeout=30`). The binding full-suite gate runs on-stash via `capture.sh` (Matt runs
  it, ~3 min). `test_v3_66_146_nav_guard` timing out >200s in-sandbox is known, not a
  regression.
- **Don't importlib-juggle two source trees in one process** to compare builder/module
  output -- Python may import the wrong copy (e.g. the wrong `build_template`) and mislead
  you. Run `run_tests.py`/pytest from ONE tree's cwd; compare trees in separate processes. **[10_SANDBOX_SHELL_PREFLIGHT.md / SANDBOX.md]**

## B. Build & release discipline

- **`build_release` self-stamps the in-zip `STATE.json` on build #1** (no two-pass): it
  writes the version + guard SHAs INTO the zip it is producing. So the guard/version gate
  is **internal-consistency only** -- it proves the zip agrees with itself, never that the
  zip matches the intended baseline. **YOU** enforce the real baseline: re-derive the 7
  guard SHAs and the banner/version from the **EXTRACTED** zip and diff them against the
  declared baseline in the newest `KB_HANDOFF`. `build_release`'s pin-scan does NOT catch
  a stale banner -- run `tools/verify_release.py --zip <zip>` and gate on `$?` (exit 1 FAIL
  / 0 PASS), never on a piped `tail`/`grep`. **[8_BUILD_RELEASE_CHEATSHEET.md]**
- **Band always from the EXTRACTED/built zip, never the work tree.** An overlay zip
  sitting in `/home/claude/out` causes the cutter to extract the wrong archive.
- **A non-ASCII CHANGELOG entry bands `test_v3_43_78_static_analysis_fixes.py`** (an
  on-stash emoji/ASCII gate). CHANGELOG entries must be ASCII-only; re-verify zero
  non-ASCII before a cut. **[2_RELEASE_TEST_BAND.md]**
- **`gui_parity_inventory` is a `.json` + `.md` PAIR** -- the gates read the `.json`, the
  humans read the `.md`; ship/restore both together or a drift check reads a stale pair.
  It auto-discovers every `tools/*.py`, so any NEW tool file requires regenerating the
  inventory AND re-banding its version-pinned test in the SAME cut. **[GATE_AUTHORITY.md]**
- **Version bump is a 3-part edit landed together:** `bulk_downloader/__init__.py`
  `__version__` + CHANGELOG top `## vX.Y.Z` + every version-pinned test. `grep -rnE
  '__version__ *== *"3\.66\.' tests/` each release -- don't trust the pin list to stay at
  one entry. **[3_VERSION_COUPLED_TESTS.md]**
- **The 7 release-guard files must stay byte-identical** unless a SHA is explicitly
  declared changed (baseline lives in the newest `KB_HANDOFF`, not hard-coded). Distinct
  from the 5 ASI-separator checks in `test_dom_recorder_asi.py` -- same word "guard",
  narrower set. `build_release.py` does NOT re-derive guard SHAs; update STATE guards
  manually before building on a guard cut.
- **Predict the gates BEFORE the bump with `tools/precut_check.py`** -- it forecasts
  version-consistency, guard SHAs to declare, in-sync regens, and the band suite, turning
  mid-build gate FAILURES into up-front facts. Derive the deploy overlay with
  `tools/make_overlay.py` (from `diff_release_zips` added+changed) so it can't
  under-deploy by a missed file -- never hand-list the overlay.
- **The in-zip `STATE.json` is explicitly NON-authoritative** -- the canonical state is
  the session pack, the in-zip copy is build-stamped. `verify_release` leads with
  `built_version` and labels it so. `verify_release` enforces a **tree==zip** invariant,
  which is why `diff_release_zips.py` refuses a `--baseline` and why a build-only
  side-file (e.g. a `BUILD_INFO.json` not in the tree) can't be added -- it would break
  tree==zip. (`PROCESS_CONVENTIONS.md` section 7/section 8 carry the versioning policy: batch
  non-functional cuts, still isolate guard changes, run precut_check + make_overlay.)
- **`spa_serve.py`'s isolation fix must ship patched.** An unpatched `spa_serve.py` leaks
  a runtime DB into `work/`, which breaks the byte-identical bd-preflight; the patched
  version is load-bearing for a clean cut. (Render-harness lesson.)

## C. Failure taxonomy / defect shapes [KB_JUDGMENT.md section 1]

- **Persist-swallow = silent data loss (D2).** An in-memory dict mutated, then a save
  that swallows its error (try/except pass, or a backend that returns False/None instead
  of raising) leaves the process "succeeding" with un-persisted state. Security/credential
  backends must RAISE on save failure so the caller can roll back the in-memory mutation.
- **A consumer grep that omits `tools/` misses real call sites (D3).** When checking
  "who calls X" before a rename/removal, walk `tools/` as well as `bulk_downloader/` --
  a tool-only consumer is invisible to a package-only scan and breaks post-cut.
- **UTC/local time-column mix (D6).** Writing one timestamp in UTC and rendering/comparing
  another in local time produces off-by-hours bugs that pass every same-zone unit test.
  Pin the zone at the boundary; store UTC, convert at display only.
- **Non-atomic JSON write (D7).** A direct `open(path,"w")` + `json.dump` that is
  interrupted leaves a truncated/corrupt file. Write to a `.tmp` sibling + `os.replace`
  (atomic), and pass `encoding="utf-8"` explicitly for all text I/O. `test_contracts.py`
  enforces atomic state-file writes.
- **`pytest.fail()` / pytest builtins are absent from `run_tests.py` (D9).** The custom
  runner chdirs to a temp dir per run, injects no pytest builtins (no `tmp_path`,
  unreliable `monkeypatch`), and zero-arg test functions only. A test that imports
  `pytest.fail` or relies on fixtures passes under real pytest but errors/structurally
  no-ops in the runner. Use real pytest (8.4.2) for monkeypatch-heavy/reload-sensitive
  code; restore module globals in `try/finally`.
- **ReDoS in a per-string redaction/scan regex (the `_KV_SECRET_RE` shape).** A
  bounded-but-greedy quantifier applied per-line over unbounded input backtracks
  catastrophically. Redaction/scan regexes run on attacker-influenced text must be
  linear-time (anchored, possessive, or length-capped) -- a redaction primitive that can
  hang is a denial-of-service, not a safety feature.
- **Rendering a generated session-pack artifact INTO the work tree leaks it into the
  zip.** The full-tree build walks `work/` and "tree wins", so any handoff/pack file
  rendered under `work/` ships inside the release. Render packs to `/home/claude/out`,
  never under `work/`; bd-preflight catches the contamination if you forget.
- **Predicate-level pins don't substitute for content-level pins.** A test asserting "a
  match exists" / "the call returned truthy" stays green when the CONTENT is wrong. Pin
  the actual value/shape, not the existence of a result. (Same family as the
  perf-proxy and dedup-call-count lessons.)

## D. Secrets / vault / security invariants [9_SETTINGS_CENTER_SAFETY_SPEC.md]

These are "deliberately NOT changed" -- a fresh instance would "helpfully fix" them and
break things. All validated live @464 (`secrets_store.py`, `vpn_config.py`,
`password_import.py`, `backup.py`).

- **`PlaintextBackend.set/get/delete` return None/False unconditionally BY DESIGN** -- the
  degraded-mode baseline; the password lives on the site config dict and the runner reads
  `cfg["password"]` directly. Migration aborts if `backend.name == "plaintext"`. Do not
  "implement" it to store somewhere.
- **`MasterPasswordBackend.is_unlocked()` is intentionally lock-free** (`secrets_store.py:212`,
  a single atomic attribute read) -- adding a lock is a hot-path bottleneck for nothing.
- **Secrets backends RAISE on save failure** (`secrets_store.py:196`), do not
  swallow-and-return-bool -- the caller rolls back on the raise. (Credential-store instance
  of D2.)
- **`password_import` CSV header check uses set membership**, not exact column order -- a
  stricter ordered check rejects valid Chrome exports with reordered/extra columns.
- **`vpn_config` keeps its OWN `_CRED_PREFIX = "@cred:"`** (`vpn_config.py:88`), equal to
  `secrets_store.CRED_PREFIX` but deliberately NOT imported -- vpn_config must stay
  importable when secrets_store fails to load (cryptography unavailable). The duplicate
  constant is intentional.
- **The release/backup manifest excludes `secrets.json` + `vault_tokens.json` +
  `secrets_meta.json` (+ their `.tmp` siblings)** -- all secret-bearing, none may ship.
- **Fail CLOSED on a missing security input** (never default-to-True). A missing/empty/
  unparseable security flag, capability, or auth input must resolve to the denied/off
  state.
- **An unanchored regex on a URL path is a phishing primitive.** Origin matching for a
  stored credential must be a dot-bounded SUFFIX match on the HOSTNAME, not `re.search`
  over the whole URL -- else `evil.com/site.com/...` matches `site.com` and a credential
  leaks cross-origin.
- **A missing lock does NOT show up in unit tests.** A concurrent `set` during
  `change_password` silently drops entries while single-threaded units stay green forever.
  Concurrency/atomicity invariants need an explicit concurrent test (threads + barrier).

## E. Bulk file-ops, dedup & compaction [KB_JUDGMENT.md section 1 / continuity]

- **Bulk file ops hollow the canonical copy unless you EXCLUDE it explicitly.** `work/`
  (asserted byte-for-byte by bd-preflight/bd-state) and the live session pack are the two
  trees a dedup/cleanup must never walk. Content-dedup is non-destructive of *content* (a
  byte-identical twin is always kept) but destructive of *folder completeness* -- whichever
  copy loses the keeper tie is emptied. Protect the live copy by **scoping it out of the
  walk**, never by trusting a keeper-rank to spare it: a rank that parses a version from
  the path scores an unparseable path (e.g. `pack_464/...` has no `v3_66_` token) LOWEST, so
  the live pack is exactly what gets gutted. Re-run **bd-preflight after every bulk op** to
  prove `work/` is byte-for-byte. (Observed 464.)
- **For large nested-zip archives, dedup INCREMENTALLY one inner zip at a time** against a
  persisted seen-set -- extract -> hash -> keep net-new -> drop the rest + the zip ->
  next. This bounds peak disk to one inner archive instead of unpacking everything at
  once. (464: ~344k duplicate file instances across 8 zip-of-zips collapsed to 1,295
  net-new files without ever exceeding disk.)
- **The filesystem wins over a compaction summary.** A compaction summary (or any prose
  recap) is a faithful snapshot but the tree/`STATE.json` is ground truth when they
  differ. A **duplicate user message is a compaction tell** -- the turn tail (e.g. a
  `present_files`) may have been dropped, so re-verify what actually landed (did the file
  get written? presented? was an `--apply` already run?) and complete only the missing
  step. An idempotent dry-run that still reports the full to-do count proves nothing was
  applied yet.

## F. Automation posture & permanently-declined scope [AUTOMATION_POLICY.md]

Automation-positive default: prefer automation; treat guardrails (lint, redaction, drift
detection, backup, staged diffs, evidence bundles, tests, rollback) as things to automate,
not reasons to go manual. Keep every step inside: authenticated operator session;
site-provided playback/download controls or approved API endpoints; no secrets in
templates; no access-control bypass; lint/blocked-term/drift checks; logs/tests/rollback.

**Permanently out of scope -- do not propose building these** (declined with rationale,
still declined @464):
- **DRM bypass** (Widevine / FairPlay / PlayReady).
- **Behaviour simulation / "looks-human" mimicry** (the F9 evasion posture).
- **YouTube Data API** integration (declined separately -- yields no playable URLs).
- **Auto-clicking a control that consents to a charge / purchase.** A charge-consent
  click always requires explicit operator action, never automation.

Wording: use browser-compatibility backend / authenticated profile reuse / challenge
detection+logging / manual challenge handoff / site-provided download flow /
reviewed-template automation / staged diff / rollback-ready. Avoid wording that suggests
defeating/evading/bypassing challenges, unlocking access, scraping an entire site, or
persisting secrets/signed-URLs in templates.

## G. Tracker reconciliation method [KB_JUDGMENT.md section 3]

- **Tracker-vs-CHANGELOG gap analysis must parse version RANGES first.** Items cover spans
  (`F5.1` = "422-446"), so a per-version scan over-reports massively (a naive scan flagged
  an 89-version gap; range-aware the true gap was 36 -> 6 program items). The CHANGELOG is
  the authoritative *shipped* record; every entry <= live version is "completed", but grep
  the `work/` tree to validate each claim (module/route/function exists) -- don't trust the
  doc.
- **The task register is data-driven:**
  `project-knowledge/IMPROVEMENT_BACKLOG.md` is the sole canonical table and
  accepts exactly OPEN, CLOSED, and MOOT. Its repo-wide gate parses every row,
  validates status/evidence/text, and binds the published exact row count,
  OPEN count, and ordered-ID digest to the current bytes. A new classification
  is therefore a backlog-schema and gate change, not a renderer or view edit.

---

## H. Capture & redaction footguns [KNOWN_FOOTGUNS / 9_SETTINGS_CENTER_SAFETY_SPEC]

The product is a capture tool; these bite specifically in the capture/redaction path.

- **Presence-only diffs miss drifted content.** Restoring "dropped" files by checking
  *presence* leaves present-but-STALE copies in place (observed: restoring 7 dropped
  build files left 6 more present-but-older). Diff by CONTENT/hash, not existence -- the
  same exclude-don't-rank discipline as bulk dedup, applied to build-drift.
- **Rasters carry PII even when the event log is redacted.** A snapdom/screenshot PNG can
  show visible secrets on screen even though the rrweb/DOM event log was scrubbed.
  Redaction must cover rasters, not just the structured log.
- **rrweb's default `maskAllInputs:false` masks only `type=password`** -- email / text /
  hidden values (login-email, hidden Turnstile inputs) leak into the DOM log as cleartext.
  The fix is `maskAllInputs: true` (shipped as Wave 2 / F2 in `dom_recorder.py` @464);
  it masks the VALUE only -- element + attributes (id/name/type) remain, so attribute-based
  selector derivation is unaffected. Network-log token redaction is a SEPARATE path
  (`capture_artifact_redact`), so "the DOM log is masked" does not imply the network log is.
  (Note: PHC-1, the LAST item, is VPN/secrets/AI-editor hardening -- NOT this masking, which
  is already done.)
- **`dom_log` + `network_log` succeeding does NOT prove the DOM was attached.** Network
  capture can pass while the DOM recorder never bound; check `dom_integrity_ok` /
  `dom_attached`, not just that logs exist.
- **A stale Chromium `Singleton*` lock causes a FALSE nav blocker** -- a capture wedge
  that clears after the lock is removed is not a code bug. A bare `page.goto` hitting the
  default `load` timeout is often a *consequence* of a dead navigation, not the cause;
  diagnose the nav, don't just bump the timeout.
- **WACZ resource-count misreads.** The WACZ entry count (or a tiny page list) is NOT the
  network-resource count; don't infer "2 resources captured" from the archive layout.
- **The floor scanner is the LAST proof no secret remains -- never loosen it.** If
  placeholder/scrub skew ever recurs, fix the *scrubber* (so it emits `<scrubbed>`),
  never the *scanner* (forgiving more tokens is a security bypass: `token=ABC123REDACTED`
  would pass). The safe robustness lever is force-scrub-then-rescan at the export
  boundary (adds scrubbing), not detection-loosening (removes it).
- **Live captures define the archetype, not the prior fixture.** A fix scoped to a
  fixture's assumption (e.g. "site X is modal") can be killed/reshaped the moment a real
  capture shows otherwise. Let the real corpus, never the prior fixture, define the
  site archetype before scoping recognizer/extraction work.
- **Guard-defer is a recognition CEILING, not a bug.** `extraction_core` is a frozen
  guard: `observed_api_hosts`/`network_patterns` recognize only the shapes it already
  knows. A new API/media shape that isn't recognized is closed by a **builder-side
  supplement** (`tools/build_template_from_wacz.py`, NON-guard), NEVER by editing the
  guard core. This is why recognizer-breadth cuts are normal cuts, not guard-declared
  changes. (`Content-Disposition: attachment` is the strongest site-agnostic download
  signal -- ensure the builder uses it.)
- **`capture_scrub --preview`: the tally is not the cleanliness signal.** The
  `redactions:` count tallies rule matches and the bump always increments; the
  authoritative "clean" signal is **`0 planned` + `verify CLEAN`** (plus a value sample).
  Don't read a non-zero redactions tally as residual leakage.

## I. Runner / db / blueprint load-bearing invariants [DANGER_MAPv2 / DECOMP_HAZARD_REGISTER]

"Do not clean up" invariants in the runner/capture/db core and the post-decomposition
blueprints -- a fresh instance would "fix" these and break captures or create import
cycles. All validated live @464 (`runner.py`, `db.py`, `app.py`, 14 `runner_*.py`, 169
`app_*.py`). The full numbered registry is `DANGER_MAPv2` (use
`tools/explain_invariant.py INV-NNN` to find the in-source site).

- **`runner.py::_process_one` dispatch order is load-bearing**, and the dispatch tracer
  MIRRORS it -- keep the two in sync. Phase-B login fallback stays in `login_async`, not
  `_process_one`.
- **`resume_site_keepers` does NOT exist and must NEVER be created.** A torn-down browser
  reconnects automatically on its next heartbeat; a resume function would be noise (the
  runner.py comment at the call site is the in-source guard -- don't "implement" it).
- **`session_keeper` must pause before any nested `sync_playwright` spawn** -- nesting a
  sync Playwright inside the keeper without the pause deadlocks.
- **`db.py::db_conn`'s WAL isolation-level hack is load-bearing** -- the
  `isolation_level`/`journal_mode=WAL` combination lets multiple readers coexist; a
  "cleanup" to default isolation breaks concurrent capture reads.
- **`learned.login` is the login-template teach-skip trigger** -- merging a LOGIN
  template's selectors into a site's `learned.login` is what suppresses re-teaching; don't
  rename/relocate it blindly.
- **A leaf blueprint imports flask/stdlib, NEVER `app`.** Shared state
  (`from bulk_downloader.app import s_cfg`) is imported INSIDE the function body, never at
  module top -- a top-level import creates a cycle (see the archived
  `../docs/archive/2026-07-22-doc-hygiene/kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md`).
  Blueprints reference routes by URL, never by function name, and never register an
  app-wide hook.


| Section | Home card(s) |
|---|---|
| A Sandbox/shell | `10_SANDBOX_SHELL_PREFLIGHT.md`, `SANDBOX.md` |
| B Build/release | `8_BUILD_RELEASE_CHEATSHEET.md`, `2_RELEASE_TEST_BAND.md`, `3_VERSION_COUPLED_TESTS.md`, `GATE_AUTHORITY.md` |
| C Defect shapes | `KB_JUDGMENT.md` section 1 |
| D Secrets/vault | `9_SETTINGS_CENTER_SAFETY_SPEC.md`, `7_VPN_CONFIG_API_SURFACE.md` |
| E Bulk ops/compaction | `KB_JUDGMENT.md` section 1 + continuity section 2 |
| F Automation posture | `AUTOMATION_POLICY.md` |
| G Tracker method | `KB_JUDGMENT.md` section 3 |
| H Capture/redaction | `KNOWN_FOOTGUNS.md`, `9_SETTINGS_CENTER_SAFETY_SPEC.md` |
| I Runner/db/blueprint invariants | `DANGER_MAPv2` (full registry), `DECOMP_HAZARD_REGISTER.md`, and the archived `../docs/archive/2026-07-22-doc-hygiene/kb/decomp/CROSS_MONOLITH_IMPORT_GRAPH.md` |

*Provenance: consolidated from this session's direct observations + the historical
recovery passes (parts 1-3) mined from the dropped archive corpus, all re-validated
against v3.66.464. Supersedes the loose `HISTORICAL_KB_RECOVERY_*` and `LESSONS_*`
working files.*
