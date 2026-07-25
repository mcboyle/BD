<!-- verified-against: v3.66.817 -->
<!-- generated: CHANGELOG_RECENT is a SLICE, not the source of truth -->
# CHANGELOG (recent 20 releases) -- v3.66.798 .. v3.66.817

## THIS IS NOT THE CHANGELOG. IT IS A SLICE OF IT.

The full CHANGELOG (19,282 lines, 1,619,211 bytes when this slice was generated)
ships at `CHANGELOG.md` and is the only changelog the tooling reads. This file is
a literal 20-release excerpt for recent static-KB context. Regenerate it from the
work-tree changelog; never hand-maintain a second history.

---

## v3.66.817 - Dependency audit and deployment graph pin hardening

- Upgraded React Router, Vite, Vitest, esbuild, and form-data to secure
  Node 18 compatible releases. Production npm audit is clean; the only
  remaining advisory is the documented low-severity Babel 7 issue.
- Replaced the optional persistent graph database check with an ephemeral
  source-graph comparison against a root-owned deployment pin outside the
  release tree. Required certification now fails on missing, unreadable, or
  mismatched pins and removes its temporary SQLite database on every exit.
- Added dependency-floor, graph-drift, cleanup, release-exclusion, and GUI
  parity regressions. Graph databases, sidecars, and pins cannot enter release
  archives.

## v3.66.816 -- Promote validated Filthykings template and enforce exact probe cap

## v3.66.815 - AI GPU boot readiness

- Add an independent systemd companion that warms the configured Ollama text
  and vision models after boot without blocking BulkDownloader startup.
- Verify live model GPU residency through Ollama, retry transient failures,
  and retain text-only readiness when vision warming fails.
- Persist boot-scoped readiness and surface ready, warming, degraded, and stale
  states through the AI status API and Integrations widget.

## v3.66.814 - Fail-closed capture certification

- Make `capture.sh` preserve its complete diagnostic bundle while returning a
  nonzero verdict for unit failures, live warnings/failures, malformed evidence,
  or failed required stages; add minute-by-minute progress heartbeats.
- Keep body-contract probes inert by forcing template onboarding into no-run
  mode, preventing detached browser captures from escaping the test harness.
- Let OPV L8 use durable green auth-health evidence after a service restart,
  while still requiring a real non-empty configured cookie jar on disk.
- Repair two tests that used unsupported dotted-string monkeypatch syntax in the
  repository's custom runner.

## v3.66.813 - Persist Phase-B login fallback telemetry

- Persist real `login_template_fallback` lifecycle events to
  `session_history` as well as the in-memory event stream, so the recovery
  path remains observable after restart and OPV L7 can verify genuine use.

## v3.66.812 - OPV live-warning contract and runtime fixes

- Normalize deployed live API shapes for site, extractor, auth-health, vision,
  kill-switch, and VPN inventory checks so valid runtime evidence is recognized.
- Load canonical JSON cookie jars in auth-health and pass account credentials to
  account-backed session keepers.
- Initialize persisted VPN runtime state at app startup, apply persisted
  auto-recovery settings, and reject configured tunnels that are not registered.

## v3.66.811 - MOD-1 Cut: render the GUI controls the 808/809 knobs were missing (close the section-0 gate hole)

- v3.66.808/809 declared captcha_vnc_display, captcha_vnc_websocket_port and
  netns_isolation and added them to settingsSchema.ts, which made the
  config-surface gate read gui_exposure=full -- but Settings.tsx renders global
  controls from EXPLICIT hand-written JSX, and none was added, so no control
  rendered (a browser render + a stash report both showed refs_in_Settings.tsx=0).
  The gate certified a string, not a control (CLAUDE.md 0). This cut adds the
  actual controls: VNC display + websocket-port text fields under "Challenge
  handling", a netns_isolation toggle under "Security & access", and -- found by
  the same audit -- automation.disco_enabled (a pre-existing omission from the
  Automation L4 toggle list). Adds the three keys to the GlobalConfigSubset type.
- Closes the gate hole with a RED-first test (test_mod1_c12_settings_controls_render)
  that re-derives the SUBJECT: EVERY settingsSchema key must have an explicit
  Settings.tsx control (a draft.<key> read or setField("<key>") write), not just a
  string in a .ts file. Four keys failed on pristine 810; all pass now.
- tsc --noEmit clean; the four control labels are present in the rebuilt bundle;
  guards 7/7. No new config key, so config-parity is unchanged.

## v3.66.810 - MOD-1 Cut: predictive-relogin per-site knobs GUI-configurable (+ drop-on-reload fix)

- predictive_relogin_enabled / predictive_relogin_fraction (the F1.4 predictor:
  relogin at a fraction of the learned session-lifetime median) are read by
  runner_auth.py from the per-site config but were absent from CFG_FIELDS. Two
  bugs fixed: (1) DROP-ON-RELOAD -- _load_sites_config rebuilds each site as
  {k: cfg_in.get(k, DEFAULTS.get(k,"")) for k in CFG_FIELDS}, so a key not in
  CFG_FIELDS was silently dropped on restart; the feature could not persist
  per-site. (2) NO GUI CONTROL. Both now in CFG_FIELDS + DEFAULTS (off / 0.8,
  byte-identical to the pre-cut absent-key behaviour), categorized with the other
  relogin fields (gated -> renders a control in the schema-driven site editor),
  typed in site_editor (_FIELD_TYPES + NUMERIC_RANGES enforces the 0..1 fraction
  at a direct PUT). Ledgered gui_exposure=full.
- Version bump 3.66.810 (3 coupled edits + PIN_INDEX). RED-first with a bug-fix
  guard that a value SURVIVES the CFG_FIELDS reload rebuild
  (tests/test_mod1_c11_predictive_relogin_gui.py). Guards 7/7 unchanged.

## v3.66.809 - MOD-1 Cut: netns egress-isolation toggle becomes GUI-configurable

- netns_isolation (the C-7 opt-in egress confinement: wg0 sole route,
  fail-closed) is now a DECLARED global_config key with an FE toggle in the
  "Security & access" settings section. Before this cut it was read via
  cfg.get(), was absent from GLOBAL_CONFIG_SCHEMA, so POST /api/global_config
  rejected it 400 and no control existed. Declared type (bool, dict): the GUI
  toggle sends a bare bool while the advanced form
  ({enabled, egress:{wg_iface,wg_conf,address,mtu?}}) stays valid -- a dict value
  takes validate_config's dict branch (no scalar type check; netns's sub-keys do
  not shadow flat schema keys). safety=False preserves pre-cut behavior byte-for-
  byte (never validated/fail-closed before; enforcement fail-closes at the launch
  layer via NetnsRequiredError). Default OFF. Ledgered gui_exposure=full; ratchet
  open=0. RED-first with an explicit regression guard that the advanced dict form
  still validates clean (tests/test_mod1_c10_netns_config_gui.py). Guards 7/7.

## v3.66.808 - MOD-1 Cut: the two Arch-B VNC takeover knobs become GUI-configurable

- captcha_vnc_display and captcha_vnc_websocket_port are now DECLARED
  global_config keys (GLOBAL_CONFIG_SCHEMA) with FE controls in the "Challenge
  handling" settings section. Before this cut they were read via a plain
  config.get() with a code default, were absent from the schema, and so
  POST /api/global_config rejected them 400 ("unknown config key") -- an operator
  could not set the KasmVNC display or websocket port from the UI, the gap
  MOD1_ARCH_B_STATUS.md flagged. Declared as str (int()-coerced at the read site,
  mirroring captcha_takeover_max_concurrent); defaults :5 / 8444 match
  takeover_vnc's code defaults so behavior is byte-identical when unset. Ledgered
  gui_exposure=full in reports/config_gui_manifest.json; parity ratchet open=0.
  RED-first (tests/test_mod1_c9_vnc_config_gui.py). Guards 7/7 unchanged.

## v3.66.807 - MOD-1 C-8 fingerprint measurement + the box-only fixes that greened 806

- MOD-1 C-8 (KASM-T10): tools/kasm_fingerprint_probe.py measures whether the
  live-X (KasmVNC) takeover browser presents a materially worse fingerprint than
  headless -- the counter-tell for Arch B. It launches headful-on-X vs headless
  with the real takeover anti-automation args and diffs the bot-check surface
  (WebGL vendor/renderer, screen, cores, webdriver, UA, canvas hash). Honest
  about its floor: on a GPU-less host both modes report a software renderer, so it
  flags gpu_less_run and states the result understates the real-hardware
  magnitude. RED-first unit tests on the diff/verdict logic; verified live against
  KasmVNC. The magnitude needs real GPU hardware:
  `python tools/kasm_fingerprint_probe.py --display :5 --json c8.json`.
- Folds in the 5 box-only failures the Python-3.12 full suite caught on 806 (they
  landed after the 806 CHANGELOG entry): graph/index artifacts re-frozen under
  3.12 (the 3.11 sandbox could not parse the 3.12-only f-string in
  tools/diag_csrf_bootstrap.py, dropping its edges); takeover_vnc routed through
  cloak.launch_browser (cloak-parity) with cloak preserving a caller DISPLAY on
  the netns path; BD_VNC_CHROME ledgered as host-managed in the envfile editor.
- Guard files 7/7 unchanged.

## v3.66.806 - MOD-1 Arch B (remote_vnc / KasmVNC) coexist path + config-parity repair

MOD-1 coexist C-series (Arch B captcha takeover over KasmVNC), all RED-first,
seven SHA-pinned guard files unchanged, verified live against KasmVNC 1.4.0:

- C-4b: wire the C-2 self-downgrade ladder into the runtime admission path, so
  captcha_takeover_mode=remote_vnc is a VISIBLE downgrade to remote (with a
  reason) instead of a silent dead toggle. remote/visible paths byte-identical.
- C-5: the remote_vnc transport (bulk_downloader/takeover_vnc.py) -- a dedicated
  headful browser on its own Xvnc display, bound into the C-1 registry as
  kind="vnc" so the one shared cap and the no-orphan sweep both count it, with a
  DERIVED capability probe (observes the endpoint, UNKNOWN downgrades) and a
  sweep census.
- C-6: cockpit KasmVNC viewer embed + effective-mode/reason readout. PendingCaptcha
  gains mode/mode_reason/vnc_url so the polled cockpit shows what is running and
  why it downgraded; TakeoverViewer renders KasmVNC in an iframe for remote_vnc.
- C-7 (KASM-T8): egress containment for the takeover browser -- unix-domain X by
  construction (no X-over-TCP) and launch through the netns fail-closed path so
  wg0 (or default-drop) is the sole route. Verified: external egress from inside
  the namespace is blocked while the X unix socket stays reachable.

Repo + config-parity repair:

- Track the reports/ parity baselines (config_gui_manifest, config_parity_baseline,
  legacy_parity_baseline) that the blanket reports/ gitignore had silently dropped
  from git -- the missing baselines were what made the parity gates fail as if
  environmental. Generated inventories stay ignored; vapid_keys.json now ignored.
- Ledger the cloud-setup.sh bootstrap flags (BD_SKIP_* opt-outs, BD_REPO_CANDIDATES)
  and the BD_VNC_CHROME deploy pin; rewrote skip() to name each flag explicitly so
  the scanner no longer sees a bare BD_SKIP_ token. open_runtime_tunable back to 0.

## v3.66.805 - plugin quarantine state honours BD_HOME (out of the install tree)

- plugins.py::_quarantine_state_path() no longer anchors the quarantine state
  file unconditionally inside the install tree. Runtime state written into the
  deployed code tree leaked into the first 797 build (the @798 manifest
  exclusion papered over the symptom, not the cause) and an overlay deploy could
  clobber or resurrect it.
- The cause was one level down from where the register named it: _plugin_dir()
  returns INSTALL_DIR/plugins and never consulted BD_HOME. The state -- not the
  plugin CODE -- now resolves under BD_HOME when _plugin_dir() is the install
  default, mirroring the interop_registry / backup_verify convention
  (BD_HOME or "."); no new BD_-prefixed env var enters the config surface.
- Isolation preserved: an EXPLICIT _plugin_dir override (external plugin dirs,
  the plugin_py_bridge tests) keeps its own co-located state file, so a
  quarantine in one plugin set cannot bleed into another through a shared
  BD_HOME ledger. With BD_HOME unset the path is unchanged (485 contract, stash
  behaviour intact).
- test_v3_66_805 pins all three arms (honour, fallback, override-isolation).

## v3.66.804 - MOD-3 cut 5 of 5: cutover + rollback (MOD-3 COMPLETE)

- The centre of this cut is the PREFLIGHT REFUSAL, not the flip. The failure it
  exists to make impossible: cutting over because shadow-read reported "0
  divergences" while having performed ZERO comparisons -- truthful, clean, and
  catastrophic, the empty denominator authorising a move of the authoritative
  store on no evidence. preflight_cutover() requires compared > 0 AND
  diverged == 0; either alone is not evidence. Cut 3 built `compared` for
  exactly this demand. Engine-free, so binding on stash.
- FAIL-CLOSED: cutover_engaged() is false unless cutover was requested AND the
  preflight positively passes. An unverifiable precondition never reads as
  permission; a preflight that raises returns not-engaged.
- REVERSIBILITY OVER CONFIDENCE: SQLite keeps receiving every write while cut
  over, so the old store stays current and rollback is a flag flip with nothing
  to reconcile. Pinned by a test that flips forward, writes, flips back and
  requires the row to be there.
- Reads genuinely ROUTE to Postgres when engaged (proven by planting a
  PG-only row and reading it back), and rows are sqlite3.Row-compatible --
  row["col"], row[0], tuple(row), iteration -- so cutover does not require
  touching 371 call sites. Bare tuples would not have failed here; they would
  have raised deep inside unrelated consumers.
- OUTAGE IS NOT EMPTINESS: read_authoritative() returns None for "could not
  serve", never an empty list, and the seam falls back to the SQLite cursor. A
  Postgres outage during cutover degrades to the old store instead of
  masquerading as data loss. Pinned.
- HONEST CEILING: this delivers the MECHANISM. The real exit criterion remains
  the tracker's EXIT-3 row -- full on-stash suite green post-cutover plus an
  operator soak -- which stays OPEN and operator-bound. Nothing here shortens
  it. Default OFF; production behaviour is unchanged until an operator sets
  MOD3_CUTOVER and the preflight passes.

## v3.66.803 - MOD-3 cut 4 of 5: migration rehearsal

- pg_backend.rehearse_migration() backfills the SQLite history into a SCRATCH
  Postgres schema, verifies by CONTENT, reports, and tears the schema down.
  Cuts 2/3 move new writes and compare reads; neither moves the data that was
  already there, and neither answers the question cutover depends on -- would a
  full migration succeed and would the result be equal?
- Contract inherited verbatim from backup_verify.rehearse() (X-AUTO-1 @706):
  never raises, and NOT-OK is the honest answer for the empty case. Zero rows
  migrated with zero mismatches is arithmetically perfect and epistemically
  worthless -- the empty denominator wearing a green badge -- so an empty
  source reports not-ok with a reason saying exactly that.
- Verification compares CONTENT, never counts, and the gate FALSIFIES it: a
  planted same-count/different-content corruption must be detected. Equal
  counts can mask a swap; a count-only verifier is clean and blind.
- Isolated + self-cleaning: the rehearsal runs in a uuid-suffixed scratch
  schema so it cannot corrupt the live mirror cut 3 compares and cut 5 will
  trust, and drops it unconditionally in a finally -- a scratch schema left
  behind turns every later run into a comparison against stale debris. Both
  pinned (live-canary row untouched; schema absent afterwards).
- Reuses the cut-3 row normaliser for "equal", so the two stages cannot drift
  apart in what equality means.
- The @795 seam gate EARNED ITS KEEP: a first draft opened its own
  sqlite3.connect to read the source, and the gate failed it. The reasoning was
  also unnecessary -- the proxy does not alter read results (cut-3
  caller-isolation pins that) -- so the read now goes through db_conn() and the
  one-connection-point invariant holds.

## v3.66.802 - fix: a BD_ token in a DOCSTRING moved the config surface

- STASH RED at 800/801 (7 gates): config_surface_inventory does a BARE TOKEN
  scan for BD_[A-Z0-9_]+ over each .py file -- code, string, comment and
  DOCSTRING alike. Cut 2 wrote the BD_-prefixed name out in the prose
  explaining why it was NOT being used, and that mention alone registered as an
  open operator-tunable env var (open=1 vs pinned 0), failing the env-tranche
  and config-parity cluster. Naming the footgun triggered the footgun.
- Fix is prose-only: the token is no longer spelled out in pg_backend.py or the
  800 gate. No behaviour change -- MOD3_PG_DSN / MOD3_SHADOW_READ were always
  the real names, and the scanner now reports open_runtime_tunable = 0.
- Root cause was NOT the environment: the sandbox reproduces the class exactly
  (a throwaway docstring-only BD_ token moves the count 0 -> 1 and back). The
  band simply never contained those 7 suites, because the author reasoned "no
  BD_ variable was added" instead of deriving it -- asserting exposure rather
  than deriving it, the shape this project exists to catch.
- Toolchain (bdsuite rev-802): bd-band-derive SIGNAL 6 ENV-TRANCHE. A changed
  .py whose BD_-token set DIFFERS from the zip's copy now bands the 7-suite
  cluster automatically, so the footgun no longer has to be held in anyone's
  head. Derived, not asserted: only a token DELTA bands, so files that have
  always carried BD_ tokens do not drag the cluster into every unrelated edit.
  New selftest control (POS: docstring-only token delta bands all 7; NEG:
  unchanged token set bands none); the 15 pre-existing controls still pass.

## v3.66.801 - MOD-3 cut 3 of 5: shadow-read comparison

- Reads BOTH stores for the same SELECT and compares, so divergence is measured
  BEFORE cutover instead of discovered after it. Opt-in via MOD3_SHADOW_READ,
  and only when dual-write is already on: shadow-reading a store nothing has
  written to would diverge on every row and teach nothing.
- UNKNOWN IS NOT MATCH -- the property this cut is built around.
  shadow_compare() returns None (counted as `skipped`) for an untranslatable
  statement, an unreachable shadow, or an oversized result, and NEVER counts
  those as agreement. shadow_stats() exposes `compared` beside `diverged`
  because "0 diverged" is meaningless without its denominator; a comparator
  that skips what it cannot translate and reports clean is exactly the failure
  shape this project exists to catch.
- Ordering is not a divergence: without ORDER BY the engines may legitimately
  return the same rows in different orders, so comparison is order-insensitive
  by normalisation (and Decimal/date coercion stops type artefacts from
  manufacturing false divergences). Pinned by tests both ways -- reordered rows
  agree, a changed value does not.
- CALLER ISOLATION IS STRUCTURAL, not argued: the comparison RE-EXECUTES the
  statement on the same SQLite connection rather than consuming the caller's
  cursor, so the caller's result object is untouched by construction. Cost is
  one extra SQLite read while the opt-in mode is on. Pinned by a test that
  makes the shadow return entirely different data and requires the caller to
  still receive SQLite's rows.
- Gate falsifies itself: a divergence is PLANTED in Postgres (UPDATE the shadow
  only) and the comparator is required to notice. A comparator that cannot fail
  proves nothing when it reports agreement.
- HONEST CEILING (unchanged): the real-PG classes SKIP on stash with the reason
  named. Binding on stash are DEFAULT-OFF, caller-isolation, UNKNOWN-not-MATCH
  and the ordering semantics -- all engine-free. Divergence detection itself is
  sandbox-proven against a real PG 16.

## v3.66.800 - MOD-3 cut 2 of 5: history-DB dual-write to Postgres

- New bulk_downloader/pg_backend.py mirrors history-DB WRITES to Postgres while
  SQLite stays authoritative. Reads are untouched (cut 3 is shadow-read); no
  backfill (cut 4). Interception is AT the @795 seam -- the single connection
  point cut 1 exists to provide -- so no module can escape the mirror.
- DEFAULT OFF: no MOD3_PG_DSN means no proxy, no psycopg import, no contact;
  the seam returns the bare sqlite3.Connection, so the default path is
  unchanged rather than merely forwarded.
- FAIL-OPEN throughout: a dead server, an absent driver, or an untranslatable
  statement can never propagate to the caller committing the AUTHORITATIVE
  write. Failures are counted and logged (pg_backend.stats(), one-shot degrade
  warning) so a degraded mirror is VISIBLE rather than merely absent -- cut 3
  needs to know whether the shadow store was ever actually written.
- DML ONLY (INSERT/UPDATE/DELETE), gated at the seam AND inside mirror().
  The PG schema is explicit PG-dialect DDL rather than a translation of
  SQLite's AUTOINCREMENT/strftime CREATE statements -- translating DDL is where
  a migration acquires silent divergence. translate() returns None (SKIP, never
  guess) for INSERT OR REPLACE/IGNORE and other sqlite-only constructs: a wrong
  mirror write is worse than a missing one when cut 3 will compare the stores.
- Both connection AND cursor are proxied. Consumers use cx.execute() and
  cx.cursor().execute(); covering only the former would be a mirror whose
  denominator excludes half the call sites.
- Env var is MOD3_PG_DSN, deliberately NOT BD_PG_DSN: config_surface_inventory
  bare-token-scans for BD_[A-Z0-9_]+, so a BD_ name would register as an
  operator-tunable setting and owe FE wiring plus a manifest row (ENV-TRANCHE
  footgun). Internal staged-migration switch; NETNS_NS precedent.
- psycopg is an OPTIONAL dependency, imported lazily; absence degrades to
  SQLite-only. New import edge db->pg_backend re-frozen in this cut (1348).
- HONEST CEILING: the real-Postgres round-trip tests SKIP on stash (no driver,
  no server) with the reason named. The binding stash gate verifies DEFAULT-OFF
  and FAIL-OPEN only; the round-trip is sandbox-proven against a real PG 16. Do
  not read a green stash suite as evidence the round-trip works.

## v3.66.799 - audit wrapper convergence (bd-triage / bd-audit-gate)

- tools/bd-triage.py and tools/bd-audit-gate.py had drifted from their
  bdsuite bin/ twins: same names, different behaviour by invocation path.
  For bd-audit-gate neither side was canonical -- the TREE carried the
  newer @533 in-tree-first path logic, the BIN carried the delegation
  selftest. Converged to ONE body per tool: tree logic + a context-aware
  selftest (script-dir and repo-root candidates first, sandbox PK/bin as
  fallback) that FAILS honestly where the delegation target genuinely
  does not exist. On stash both report SELFTEST FAIL by design (no
  TRIAGE_RULES.json, no bd-audit there); the gate stays green because it
  asserts the exit code agrees with the verdict, never PASS itself.
- Gate tests/test_v3_66_799_audit_tool_selftests.py: --selftest is
  HANDLED (verdict line, no fall-through to main) and HONEST (exit 0 iff
  SELFTEST PASS). Deliberately no bin-parity assertion in tests/ -- bin
  does not exist on stash; cross-copy parity is bd-pk-mirror's job.

## v3.66.798 - runtime-state manifest exclusions (live_recordings + plugin state)

- dev_suite exclusion predicate gains two runtime-state carriers, same leak
  class as app_config.json @263 / state-heartbeat @B5 / .hypothesis @748:
  live_recordings/ (DIR-scoped: app.py resolves _live_state_dir under the
  deployed tree; recordings.json is live recorder persistence -- unexcluded,
  a release ships it and the unzip -o overlay clobbers the operator's live
  recordings on stash) and plugins/.plugin_state.json (PATH-scoped: plugin
  quarantine state anchored inside the install tree by
  _quarantine_state_path(); leaked into the first 797 build past a clean
  namelist -- zero rules covered it. PATH not DIR because plugins/ ships
  plugins.json).
- Two-kinded gate tests/test_v3_66_798_runtime_state_manifest_exclude.py:
  behavioural POS/NEG (dir-segment-only matching; the shipped plugins.json
  and basenames merely containing the words still ship) plus source-constant
  trackers pinning the exclusions to what app.py and plugins.py actually
  write, so a rename fails loudly.
- Toolchain (bdsuite, same cut): bd-deploy-manifest's _RUNTIME_DIR_FLOOR
  bridge for live_recordings is RETIRED -- the floor and the rule must not
  both exist. Protection is now exclusively the zip's own predicate; the
  selftest asserts both the empty floor and predicate-only protection of a
  planted live_recordings/recordings.json.

