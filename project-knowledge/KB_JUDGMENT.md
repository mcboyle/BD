<!-- verified-against: v3.66.805 -->
# KB_JUDGMENT — the durable why/how layer

*Static PK. **Judgment, not facts.** This is the half a generator can never produce: named
failure shapes, system mental-models, decision criteria, and how Matt works. It changes only
when judgment changes — never on a routine release.*

**Decay convention.** Numbers, line-numbers, and SHAs named below are **illustrative anchors,
not live facts** — treat any bare number as *decaying*. Once the Tier-A indexes (ROUTE_INDEX,
PIN_INDEX) land, factual anchors here should transclude (`{{pin:X}}`/`{{route:X}}`) and resolve
at read-time. Until then: `bulk_downloader/__init__.py::__version__` is the version, `guards.json`
(via `bd-guardcheck`) is the guard-SHA truth, and counts are measured at decision time from the
tree -- never quoted. This doc is the *reasoning*. (STATE.json and the KB_HANDOFF series were the
pre-git session-pack channel and no longer exist in this repo.) **Last reviewed @v3.66.805.**

Pairs with: `CLAUDE.md` (workflow + release checklist), `AUTOMATION_POLICY`
(what may be automated + the gated-approval model), `SANDBOX.md` (env footguns). This doc is the
judgment view; those are the operating facts — it cross-references rather than restates them.

---

## 1. Failure taxonomy — name the shape, immunize it

Each past miss has a *shape*. Naming the shape is what lets a check catch the next instance
(the Phase-6 endpoint: a footgun isn't documented, it's immunized). Format: **shape — what it
is · canonical instance · the smell · the fix.**

- **gate-that-cannot-see-reports-OK** *(the dominant shape @709-718; it subsumes several below)* —
  a check asserts over a denominator that structurally excludes the thing being asked about, so it
  reports clean **truthfully** and is useless. Not a bug in the gate: a category error in its
  denominator. *Instances:* the config-parity ratchet counting manifest **rows**, so the entire
  `global_config` store hid behind one `"(global_config store)": "full"` row — 90 keys, 21 of them
  undeclared, including `automation.master_off_switch`, the emergency stop, which returned **200 and
  wrote nothing** while `open = 0`; `test_gui_parity` asserting `all(key.startswith("BD_"))`, which
  did not *describe* the blind spot but **created** it (the scan matched the prefix, so an unprefixed
  var could not be seen, and the test then certified none existed); `enumerate_surfaces.py` walking
  `url_map`, so the cockpit's client-side views have never appeared in any census (**and the number
  everyone repeated for them -- "44" -- was ITSELF an instance of this shape: see @729 below. The
  real count is 133**);
  `config_surface_inventory` asserting GUI exposure **without ever scanning the frontend** (57 env
  keys claimed `full` and *none* were writable, while the 22 it called `display-only` were the only
  writable ones — the assertions were **inverted**). *Smell:* a gate reads 0/clean on a surface
  nobody has ever seen fail; a coverage number whose denominator you cannot name out loud. *Fix:*
  **make the denominator contain the thing being asked**, and **derive** exposure/reachability rather
  than assert it. A number without its unit or its denominator is not a fact.
- **a-check-that-cannot-verify-must-not-say-OK** — the same disease one level down: the probe fails,
  and the failure path falls through to the success branch. *Instance:* `bd-sbcap` probed
  `$VENV/python` — **a path that does not exist** (the venv ships `python3`) — so the probe always
  returned empty, empty fell into the `else`, and it printed *"chromium build resolvable"* having
  determined **nothing**; meanwhile it checked the *chromium* build while `launch(headless=True)`
  executes the **headless shell**, a different build dir, so every bare `chromium.launch()` in the
  repo died on a "playwright install" banner under a green report. *Smell:* an OK with no evidence
  behind it; an `else` that is reached by both "verified good" and "could not verify". *Fix:*
  unknown is a **third state**, and it fails. `bd-render-env` says RENDER BLOCKED on the same box
  where `bd-sbcap` said fine.
- **caller-is-not-an-operator** — "something calls it" mistaken for "an operator can reach it".
  *Instance:* the endpoint-reachability ledger blobbed all of `tools/` as a console caller, so a
  **CLI script** posting to `/api/vpn/system_killswitch/<id>/apply` certified it WIRED — hiding the
  finding that mattered: `Vpn.tsx` **rendered** that kill switch's state and called **none** of its
  four endpoints. The GUI showed you the state of a safety control it could not operate. *Smell:* a
  reachability claim that doesn't name *which control* invokes it. *Fix:* reachability = a **rendered
  control** calls it. An `/api` route existing is not reachability.
- **identifier-masquerading-as-a-path** — a bare string that shares an endpoint's name read as a call
  site. *Instance:* `queryKey: ["sched_exports", "list"]` — a react-query **cache key** — certified
  `/api/sched_exports` as live; it fooled my prose first and then my matcher. *Smell:* a match on a
  name with no leading `/`. *Fix:* path fragments carry a slash; match on the static prefix **and**
  suffix for parameterized rules (matching only the stem means one `apiPost("/api/sites")` certifies
  every `/api/sites/<id>/…` route, dangerous ones included).
- **declared-writable-and-read-by-nothing (the decoy key)** — a config key declared in the schema
  (so a POST is accepted **and persists**) that no code reads. *Instance:* `auto_refresh` /
  `auto_repair` / `auto_quarantine` / `auto_promote` — declared with `safety: True`, while
  `is_enabled()` maps the short toggle NAME through `AUTOMATION_TOGGLES` to the **dotted** key and
  reads only that. An operator setting `auto_refresh: true` — the obvious name, declared,
  safety-flagged, 200 OK — got **nothing**. The comment *directly above the schema* warns about
  exactly this ("the feature is silently OFF — the 266 footgun"): **the schema was carrying the
  footgun it was written to prevent.** *Smell:* two keys that look like the same setting; a write
  that returns 200 and changes no behaviour. *Fix:* delete the decoy; make an unknown key **400**.
  Fix the *contract* before the schema — a discarded write reporting **success** is what let this
  live for 200+ cuts under clean gates.
- **overlay-deploy-never-deletes** *(bit @718; RETIRED 2026-07-27 -- see the Fix clause)* — deploy
  was `unzip -o`: it overwrote and added, it **never removed**. A file deleted in a cut kept living
  on stash, and the graph gates
  (`dependency_graph`, `import_graph_gate`) **glob the disk**, so the orphan is scanned as live
  source and its stale edge trips the frozen baseline. *Instance:* `app_sched_exports.py`, deleted
  @716 — the release zip was **correct** and the sandbox tree **green**, and stash still went RED on
  three suites because of one ghost file. *Smell:* a stash failure naming a module that is not in the
  zip. *Historical fix (pre-git deploy):* `bd-deploy-manifest --zip <rel> --script | sh` before the
  `unzip -o` overlay on any cut that deleted a file. **Retired 2026-07-27:** the box now deploys via
  `git fetch origin main` + `git reset --hard origin/main`, which removes deleted files natively, so
  this orphan class can no longer occur and the manifest step is no longer required. *But a git
  deploy only moves FILES -- it does not make the running system match them:* see
  `git-deploy-moves-files-not-state` in section 2 for what still has to be done by hand.
- **wiring-a-control-IS-a-route-change** *(bit @714)* — `ROUTE_INDEX` records a `spa_wired` flag per
  (method, path), so adding a **caller** flips the index even though `url_map` is untouched.
  *Instance:* the VPN kill-switch controls landed, ROUTE_INDEX was not regenerated, and the full
  suite went RED on stash (`only-in-parity = [the 4 killswitch routes]`). The test's own error message
  had been saying *"or its spa_wiring flipped"* the whole time; my mental model of "route change" was
  `url_map`-shaped. *Fix:* `FG-SPA-WIRING-ROUTE-INDEX` (blocking) — and regen order is **gui_parity
  BEFORE ROUTE_INDEX**, or the index goes stale against a parity inventory `bd-cut` regenerates.
- **the-band-is-not-the-diff** — a change's blast radius is its *denominator's* consumers, not its
  file's importers. *Instances:* the 710 denominator change went RED on 4 suites I had not banded
  (the band is every test touching the changed **module** — `grep -rl "config_surface_inventory\|
  config_parity_baseline\|config_gui_manifest" tests/` = 26 suites); @718, deleting a config **key**
  went RED on `test_v3_66_285`, which SETS the key by **literal** without importing anything, so a
  file-name band could never see it. *Smell:* a cut that changes a shared denominator or deletes a
  named value. *Fix:* for a module change, band its consumers mechanically; for a **deleted config
  key**, band `grep "<key>" tests/` — now automatic in `bd-band-derive` (rev-ar).

- **stale-copy-of-derived-fact** — a fact also computable from source gets hand-copied into a
  doc/test and drifts. *Instances:* STATE.json `living_files` reading 20/22/90 when source was
  10/17/125 (@354 verify); the `test_v3_66_302` count-dict; the F5.1 row citing a
  `route_map_snapshot.py` that doesn't exist. *Smell:* a number or list in prose a script could
  extract. *Fix:* generate it and delete the copy (Tier A) — you can't forget to update a copy
  that doesn't exist.
- **string-grep-not-decorator-accurate** — locating routes by text-grep instead of the Flask
  `url_map` yields wrong ownership. *Instance:* the Phase-4 plan's "actions_center owns
  /api/library" (false — those are top-level `@app.route`). *Smell:* a route-ownership claim not
  backed by a decorator/url_map read. *Fix:* ROUTE_INDEX off `ENDPOINT_CATALOG` (url_map-derived).
- **scan-to-find** — every task opens with grep because there's no index. *Instance:* locating
  anything in ~19.8k-line `app.py`. *Fix:* the query layer (A2/A4); scan only to *read* a slice
  you already located, never to find it.
- **trust-without-provenance** — believing a confidently-stated doc claim because of its tone,
  not its verification. *Instance:* the Phase-4 plan read as authoritative while being wrong.
  *Fix:* provenance on every answer (`{value, source, as_of, verified_by, confidence}`); prose
  claims tagged *decaying* so stale judgment *looks* stale.
- **restart-race / green-against-a-stale-process** — running a test or probe before
  `/api/health` confirms the new version → a green suite against the old process. *Instances:*
  the v3.66.161 pyc-shadow (on-disk 161, health 160); the @354 deploy where the suite ran against
  the *old* `cockpit_console.py`. *Fix:* confirm `/api/health` flipped before trusting any
  post-deploy run; clear pycache every deploy.
- **merge-not-overlay deploy gap** *(@354; HISTORICAL -- the mechanism required the retired
  `unzip -o ... -x` overlay deploy and cannot occur under `git reset --hard`)* — a deploy-excluded
  file (`tools/cockpit_console.py`) shipped its **test** but not its **change**
  → on-stash `capture.sh` failed against the old file (the signature: the *new* test fails, the
  other ~9800 stay green). *Historical fix:* deploy that one file as a targeted single-file overlay
  with no `-x`; the exclude existed only to protect live operator edits. **Retired 2026-07-27:**
  `git reset --hard origin/main` has no exclusion list, so no file can ship its test without its
  change.
- **equality-pin-whack-a-mole** — `== N` magnitude pins re-break on every subsequent tranche.
  *Fix:* convert legacy_parity `== N` to `<= N` ceilings the *same* cut a tranche moves the ratchet.
- **fixture-looks-like-a-pin** — a synthetic version/SHA string inside a test fixture gets matched
  by a pin-scanner or the `bd-cut` bump() grep. *Instances:* `test_release_hygiene_gates`,
  `test_scan_version_pins_fixture`. *Fix:* allowlist the known fixture files; PIN_INDEX ships a
  coverage map naming what it excludes.
- **CHANGELOG-emoji gate** *(hit ×3)* — emoji in the *current-release* CHANGELOG entry fails an
  on-stash-only gate *post-deploy*. *Fix:* keep the current entry emoji-free; the gate is folded
  into the FINALIZE band, but the habit is the real defense.
- **single-symptom-multiple-causes** — one report maps to several independent code paths.
  *Instance:* "focus makes the site unusable" had two causes — the collapse-primitive CSS bug
  (348) *and* a separate selectable-Focus-layout trap (349). *Fix:* grep/fix **all** paths, not
  the first found; when a UI mode "can't be exited," check whether the exit affordance is rendered
  inside the element that mode hides.
- **env-var-opt-in-trips-the-env-tranche-gate** *(fresh @674, ×2 on-stash)* — a backend-only
  feature flag read from `os.environ.get("BD_*")` or `env.get("BD_*")` is discovered by
  `config_surface_inventory` (BOTH `_ENV_RE` and the `_ENV_ALIAS_RE` `env.get(...)` form) as an
  *open* env_var → `test_v3_66_319::test_no_open_env_vars_remain` fails and `open_runtime_tunable`
  rises so `test_v3_66_305_config_danger` fails. GLOBAL_CONFIG_SCHEMA is no escape (a call-time
  runtime-tunable key targets `gui_exposure=full` = a GUI control; the parity ratchet is parked but
  the classification still stands). *Smell:* reaching for an env var to keep a sandbox cut
  "backend-only." *Fix:* opt in via an **undeclared site-cfg key** read `cfg.get("key")` — invisible
  to the inventory unless it's declared in `site_editor.py` (NUMERIC_RANGES/_FIELD_TYPES/SECRET/
  REQUIRED) or `app.py` CFG_FIELDS (the `multi_conn_count` precedent); band `test_v3_66_305` +
  `test_v3_66_319` on any config/env-reading cut. *Corollary:* function-local imports DO register a
  tracked dep/import-graph edge (supersedes the older "lazy imports don't" note) — re-freeze
  `import_graph_gate --update` in the same cut; and *removing* an import stales DEPENDENCY_GRAPH too
  (regen after any import change, add or remove).

*The entries below were folded in from operational memory (durable footguns that lived only in
the memory store); they belong in the versioned PK, not an ephemeral summary.*

- **test-function-mistaken-for-file** (band naming) — passing a test *function* name to
  `bd-cut --suites` as if it were a file. *Instance:* @513 `test_spa_wired_join_is_faithful` is a
  function inside `tests/test_route_index_in_sync.py` (~L86), not a file → `run_tests` falls back to
  a broad run → timeout → band ABORT. *Fix:* band the FILE. SPA wiring must use full `/api/…`
  literals (not a concatenated base var) or the scanner won't count it `spa_wired`.
- **prestaged-PYTHONPATH-false-positive** — `bd` sets `PYTHONPATH=/tmp/prestaged_site_packages`,
  which already ships pytest (+ many pkgs). So `venv/bin/python -c "import pytest"` under `bd` is a
  FALSE POSITIVE, and a `pip install` with PYTHONPATH set sees prestaged pkgs as satisfied and SKIPS
  installing them into the venv. *Fix:* to probe/install the venv's OWN site-packages (or any offline
  wheelhouse), run `env -u PYTHONPATH venv/bin/python …`.
- **static-ffmpeg-hls-segfault** — the media-kit static ffmpeg (johnvansickle 7.0.2) SIGSEGVs
  (139, empty stderr) on HLS+HTTPS in-sandbox — NOT a BD bug (BD reports `ffmpeg_failed` cleanly).
  *Fix:* `apt-get install ffmpeg` + shadow `/tmp/media/tools_bin/{ffmpeg,ffprobe}` → `/usr/bin`
  (distro 6.1.1, dynamic TLS); `_find_ffmpeg` is `shutil.which` so the PATH shadow is used (now
  folded into `bd-venv`).
- **pin-tightening-partial-coverage** — tightening a dependency pin at one site while duplicate pin
  sites drift. *Fix:* grep the whole repo for every `<pkg>[><=]` occurrence and write a guard test
  covering ALL sites. A `data_layer` route add is the parallel case: it must update BOTH
  `test_wave2_backlog` AND `test_v3_66_302_gui_parity_reconcile`.
- **core-req-stales-cloak-pack** *(fresh @682)* — the cloak pack ships CORE + cloak wheels, so a
  change to **requirements.txt** (not just requirements-cloak.txt) can stale it. *Instance:*
  `authlib>=1.3,<2.0` added to requirements.txt (core, OIDC/SSO) @681; the 539 cloak pack had no
  authlib wheel → offline `bd-venv` failed `No matching distribution found for authlib` and had to
  live-fetch. *Fix:* on any requirements.txt change, refresh `bd_cloak_pack` too; the dep-fingerprint
  guard must watch requirements.txt, not only the cloak file. (`cryptography` stayed at the packed
  45.0.7 because the `<46` pin held even though a bare `pip download authlib` pulls 49.x.)
- **optional-pack-invisible-to-tools** *(fresh @682)* — `pack_E-H` are flat single-capability packs
  with no inner kit, so `bd-install` indexes them but they're invisible to the toolchain. *Fix:*
  `bd-optpack` is the authoritative detect/report/install-on-demand tool; `bd-status` surfaces their
  state. They are NOT auto-installed (G needs root apt; E/H are heavy).

*The four shapes below were reconciled in from a parallel @682 session's KB_JUDGMENT
(scoping/validation footguns); union-merged so neither session's shapes were lost.*

- **scope-from-oldest-catalog-not-newest-state** *(fresh @682)* — a scoping/validation pass
  re-derives "what's open" from the oldest planning catalogs while a *newer* consolidated-state /
  validation-report doc already holds the ground truth → the derived picture conflicts with, and is
  less accurate than, the prior analysis. *Instance:* the first @682 scoping produced "18 open / 8
  buildable" from the @601 catalogs; `BD_CONSOLIDATED_STATE_v3_66_679` + `VALIDATION_REPORT_v3_66_679`
  (a prior 44-round pass) already had the right built-status — reconciling only after Matt surfaced
  them showed IEX-3 + AI-5 were built. *Smell:* opening a scope against a doc whose header version is
  far behind live, without first `ls`-ing for the newest `BD_CONSOLIDATED_STATE_*` /
  `VALIDATION_REPORT_*` / phased-execution doc. *Fix:* the catalogs are the item-*universe*; the newest
  state/validation doc is the built-status *truth* — reconcile against it FIRST, then only chase the residual.
- **catalog-id-not-shipped-module** *(fresh @682)* — a catalog item is declared OPEN because the module
  its *planning-ID* names lacks it, when the capability shipped under a different cut/module. *Instance:*
  IEX-3 "export/import checksum manifest" declared open (`app_export.py` had none) shipped in
  `marketplace.py` (signed bundles + verify-on-import) + `eol_export.py` (sha256); AI-5 "promotion gate"
  declared open (`app_ai`) shipped in `auto_promote.py` (clean-staged-diff) + `ai_review.py`. The
  `VALIDATION_REPORT`'s own warning — "redaction lives at the export data layer, not app_export.py" — is
  this exact trap. *Smell:* an OPEN verdict backed by grepping only the one module the @601 ID names.
  *Fix:* before OPEN, grep the *capability* whole-tree (synonyms + data layer + adjacent modules) and ask
  whether it shipped under another module/cut ID; @601 planning IDs ≠ shipped module names.
- **round-count-mistaken-for-rigor** *(fresh @682)* — many validation/audit rounds reach
  "3-consecutive-clean" yet still ship a wrong verdict, because every round re-checks the same axis with
  the same method and the audit verifies *internal consistency* rather than independently re-deriving the
  load-bearing claim. *Instance:* 40 planning + 23 audit rounds, 3-clean twice, still shipped the
  IEX-3/AI-5 over-count; the audit checked module-refs-exist + counts-add-up, never re-asked "is this
  really open?" from a fresh source. *Smell:* rounds that vary the *item* but not the *method*; an audit
  that re-confirms your own conclusions. *Fix:* a clean streak only counts if consecutive rounds use
  *different* checks; the audit must attack load-bearing claims from an INDEPENDENT angle (newest
  validation doc + whole-tree capability sweep), not re-run the original method. *Corollary:*
  computed totals (`tasktracker_gen`) stay correct while free-text meta (`live_on_stash`, `last_updated`)
  drifts — glance at `meta.live_version` vs `STATE.built_version` each session.
- **optional-pack-extract-strips-mode-bits-and-symlinks** *(fresh @688)* — a pack whose payload
  is loose files dropped into place (vs pip/dpkg, which set perms) loses exec bits and has
  symlinks flattened to regular files when extracted with `zipfile.extractall`; the installer's
  "installed" probe can pass while the capability is dead (EACCES browsers, dead jscpd `.bin`).
  *Immunize:* extract entry-by-entry restoring `external_attr` modes + rematerializing symlinks,
  run an ELF/`#!` exec-restore safety net, and verify the CAPABILITY (launch/run `--version`),
  never the install flag — `bd-optpack verify` mechanizes this.
- **mtime-as-freshness-inverts-after-extraction** *(fresh @688)* — choosing the "newest/
  authoritative" copy of a file by mtime (`ls -t`, `max(key=getmtime)`) inverts after any zip
  extraction, because extraction resets mtimes to "now": a STALE packed manifest/STATE looked
  newest and a freshness gate demanded a re-paste of an OLDER set. *Immunize:* order candidates
  by CONTENT (a `generated` field, `built_version`, a version parsed from the filename), mtime
  only as tiebreak. Fixed in bd-boot (kbsync + footguns), bd-doctor, bd-guard-declare.
- **bd-state-auto-compares-a-stray-zip** *(fresh @682)* — `bd-state --state STATE.json` (no
  `--zip`) auto-discovers a source zip on disk and pin-checks against it; a leftover
  `BulkDownloader_v3_66_<old>.zip` from a prior session yields **4 red FAILs** (version / sha /
  file-count / name) that read like STATE is corrupt when it is fine. *Smell:* bd-state FAILs
  immediately after a read-only/doc session where you built no zip. *Fix:* pass `--zip <matching-zip>`
  explicitly; or run `bd-freshest` (it lists stray zips whose version != `built_version`); or just
  trust STATE if you didn't build this session — the byte-pins were set at the last real build.
  *This is not STATE corruption.*

*New tools that immunize the @682 shapes above (ship in `bd_new_tools_v3_66_682`): `bd-capsweep`
(whole-tree capability sweep → kills catalog-id-not-shipped-module + the round-count blind spot),
`bd-freshest` (newest-authoritative-doc locator → kills scope-from-oldest + surfaces the stray zip),
`bd-session` (multi-cut ledger → kills hand-typed session-arc drift).*

*This section is the one most likely to graduate to its own file + a Phase-6 regression-registry
meta-gate (each entry → the check that catches it, run every cut).*

---

### Added @723-728 — the dominant shape keeps generalising

The shape above (**gate-that-cannot-see-reports-OK**) is not a phase we are finishing. It was
found in the app, in the toolchain, in the detectors *built to hunt it*, and in the test
assertions written to pin it — all in one session. Assume it is in whatever you look at next.

- **aspirational-comment** — a comment **asserts** a property instead of the code **having** it,
  and the assertion is then trusted by everyone who reads it. *Canonical:* the comment directly
  above the 708 pipeline halt read *"The halt must be VISIBLE: a guardrail the operator cannot see
  fired is not a guardrail. This surfaces on the AF5 timeline / AF7 digest."* It surfaced on
  **neither** — the verdict was returned up to a scheduler wrapper that **discarded it**, and was
  persisted nowhere. *Smell:* a comment that states a safety property in the present tense; docs
  that describe intent as if it were behaviour. *Fix:* the comment must **cite the mechanism**
  ("persisted by X, rendered by Y") or say the property does not hold yet. Aspirational docs are
  worse than no docs: they stop people checking.

- **reader-with-no-consumer** — the data IS persisted, a reader function DOES exist and works, and
  **nothing calls it**. *Canonical:* `backup_verify.last_rehearsal()` (706) — called by nothing but
  its own test, for 17 releases. A verdict nobody hears. *Smell:* a `get_last_*` / `latest_*`
  function whose only caller is a test. *Fix:* `grep -rn "<reader>" --include=*.py . | grep -v tests/`
  as a matter of routine on any "we persist it" claim. Persisted ≠ surfaced.

- **dead-control** — a control calls the **right endpoint** with a body it **rejects**. A guaranteed
  4xx, and **every reachability ledger scores it WIRED**. *Canonical:* "Delete ALL jobs" (724)
  posted `{}` to an endpoint demanding `{urls}` → 400, 100% of the time, *after* the operator typed
  a hard-confirm — and the label lied twice, because that endpoint has **no "all" semantic at any
  body**. "Start import" (726) posted `{}` to an endpoint needing `{text}`; **no field on the page
  could have supplied the list**. *Smell:* a generic `${suffix}` mutation posting `{}`; a button
  whose label promises a scope the endpoint has no parameter for. *Fix:* `bd-body-contract` — replay
  the body the control ACTUALLY SENDS against the real app. **A dead control is worse than a missing
  one: a missing one tells the truth.**

- **assertion-a-label-can-satisfy** — a wiring test names a property, then asserts a string that
  something *other than the wiring* also produces. *Canonical:*
  `test_automation_status_readouts_are_surfaced` asserted `"rehearsal" in src.lower()` — satisfied
  by the **toggle label** the same cut added. It certified "readouts are surfaced" against a surface
  with **no readout**, and stayed green for 12 releases. *Smell:* an assertion on a prose word rather
  than a route path or a code identifier. *Fix:* **a wiring test is only sound if its assertion
  literal is something ONLY THE WIRING CAN PRODUCE** — a route path (`/api/x/y`) or a component
  identifier. And **strip comments before asserting**: three times in one session a prose comment
  naming a route satisfied (or broke) an assertion about a *call* to it.

- **unknown-laundered-into-OK** — a check that **cannot** verify reports OK. *Canonical:* a fresh
  automation readout with no run on record; `bd-sbcap` reporting "chromium build resolvable" having
  probed nothing. *Fix:* **UNKNOWN IS A THIRD STATE AND IT FAILS.** "I have never run" and "I ran and
  passed" are different answers; a readout that infers health from the absence of failures is the
  house bug wearing a lab coat. Silence is not consent.

- **denominator-blast-radius** — banding from the **diff** when the cut changed the **denominator**.
  *Canonical:* 724 went RED on stash (`test_dark_ratchet_fell`); the band came from the changed
  modules, and that test names none of them. *Fix:* **a denominator change has the blast radius of
  the DENOMINATOR, not the diff.** Enumerate every ratchet gate mechanically
  (`grep -rl "ratchet\|_baseline.json\|dark_count" tests/`) — do not guess.

- **environment-shrinks-the-denominator** — a module fails to IMPORT, so its routes never enter
  `url_map`, and the endpoints **vanish from the census** — counted "not dark" rather than "unknown".
  *Canonical:* under the sandbox runner (`PYTHONPATH=/tmp/prestaged_site_packages`) `httpx` is absent,
  `app_scrape_listing` fails to register, and the sandbox computes `dark=119` while stash computes
  `120`. Re-pinning from the sandbox would have gone RED a second time. *Fix:* build any census/ledger
  under the **service venv** (`env -u PYTHONPATH work/venv/bin/python`), and if the venv is missing,
  **say you cannot verify and FAIL** — never fall back to a python whose missing imports silently
  shrink the denominator.

- **the-ordering-discarded-it** *(the resolver family — 5 instances @723-726)* — the denominator
  **contains** the right item, and the **sort key throws it away**. *Canonical:* four tools globbed
  BOTH `/mnt/user-data/uploads` and `/home/claude`, then sorted the **path strings** with
  `reverse=True` — and `"/mnt..." > "/home..."` lexicographically, so the **read-only stale copy
  always won regardless of version**. `bd-since` diffed the work tree against a **four-release-stale
  zip** while looking entirely correct. *Smell:* `sorted(globs, reverse=True)` over paths from more
  than one directory. *Fix:* sort by **the thing being asked about** (the version), not by where the
  file happens to live. **And when ONE resolver has this bug, SWEEP THE WHOLE TOOLCHAIN** — fixing
  `bd-state`'s `find_state()` and not its `find_zip()` cost a close.

- **shadow-endpoint** — two endpoints doing one job, the frontend uses one, and **wiring the unused
  one scores as a reachability WIN**. *Canonical:* `bulk_reorder` (`{order:[urls]}`) duplicated
  `jobs/reorder` (`{ordering:{url:ord}}`), which the drag UI already used; 724 nearly wired it.
  Precedent: the `/api/sched_exports` family (716). *Smell:* an endpoint called by nothing but its
  own route, whose job another endpoint already does. *Fix:* before wiring anything from a CONTROL
  list, ask **"does something else already do this?"** A second path to one behaviour is not
  reachability, it is debt. Remove it; leave it visibly `spa_wired=False` until you do.

- **safety-theatre** — a confirm gate in front of a call that is doomed, or that changes nothing.
  *Canonical:* "Delete ALL jobs" made the operator **type `DELETE ALL JOBS`** and then 400'd, every
  time. *Fix:* a confirmation in front of a broken call is not safety, it is theatre — and **theatre
  is how operators learn to click through the real ones.** Read-only calls (e.g. a dedup *scan*) get
  **no** confirm gate, and a test pins that they never grow one.

- **gate-pollutes-the-tree-it-inspects** — the checker has side effects on its subject. *Canonical:*
  `body_contract`'s probe boots the app, which writes runtime state to **CWD-relative** paths
  (`plugins/plugins.json`, `notify_apprise.json`, `cockpit_tasks/operator_state.json`); probing from
  inside the work tree dirtied the source tree and **bd-cut packaged the droppings into the release
  zip**. *Fix:* probe from a scratch CWD; pin the no-pollution property with a test. **A gate that
  corrupts the tree it inspects is not a gate.**

- **detector-with-the-bug-it-hunts** *(the meta-shape — read this one twice)* — the tool built to
  find the shape **has the shape**. `bd-body-contract` took **SIX** iterations; the first five each
  reported confident nonsense (7, then 99, then 36, then 10, then 9 false positives) and every one
  failed **identically**: *the denominator did not contain the question.* (Suffix-substring matching;
  a probe filling every key with `None`; conflating "sends no body" with "sends a typed variable";
  conflating `Record<string,X>` with a scalar; conflating an **open dict** with an **empty body**.)
  The same thing happened twice more while trying to *size* a gate-soundness sweep with regexes.
  *Fix, and it is the discipline this whole layer exists to enforce:*
  1. **Verify a sample of hits BY HAND before believing any count.** A number from an unvalidated
     detector is worse than no number.
  2. **Seek the falsifier, not the confirmation.** What killed the bad rule was a **live fact**: it
     flagged `/api/tools/run` DEAD, and we had *watched that work* at 719. One observation beat five
     plausible arguments.
  3. **State the limit instead of faking coverage.** Empirical replay **cannot** distinguish "missing
     key" from "invalid value" when the endpoint reports both identically (`/api/queue/v2/cancel`
     answers *"unknown site_id"* to both). Settling those needs **real fixtures**. Saying so is the
     correct deliverable — **"I do not trust my own denominator" is a finding, not a failure.**
  4. **"No keys" and "keys I cannot see" are different facts.** Collapsing them is the bug itself.


## 2. Mental models — how the system actually behaves

The durable models that, once internalized, prevent a class of mistakes. (Anchors decay; the
*model* doesn't.)

- **overlay-can't-delete (RETIRED 2026-07-27).** Under the pre-git `unzip -o` model the deploy
  overlaid files and never removed them, so a deletion cut needed a physical `rm` on stash or
  `rsync --delete` (the empty `app_actions_center`/`app_monitoring` modules @353 needed an operator
  `rm`). The box now deploys via `git fetch origin main` + `git reset --hard origin/main`, which
  deletes natively -- so that orphan class, and the `bd-deploy-manifest` ceremony that detected it,
  are gone.
- **git-deploy-moves-files-not-state.** A git deploy makes the *files* match origin/main. It does
  **not** make the running system match them, and none of the following were ever properties of the
  overlay, so none of them went away when the overlay did. Ask each one every deploy:
  1. **`__pycache__/*.pyc` is not cleared.** `git reset --hard` leaves stale bytecode exactly as
     `unzip -o` did -- see the next entry; the 161 footgun is unchanged.
  2. **Gitignored generated artifacts are not refreshed**, and `git clean -fd` will not remove them
     either (that needs `-x`). A stale `reports/gui_parity_inventory.json` reads as parity drift and
     fails the ENTIRE suite; `.claude-env-report.md` is the same class and worse, because its own
     header tells the reader to trust it.
  3. **The service is not restarted** -- confirm `/api/health` flipped before trusting any
     post-deploy run.
  4. **`frontend/dist/` is not delivered at all.** `git ls-files frontend/dist` returns 0 files and
     `frontend/.gitignore` ignores `dist/`; `bulk_downloader/app.py` serves a uniform 503 when the
     bundle is missing, so a missing or stale bundle is a silent 503 on the SPA. Rebuild with
     `cd frontend && npm ci && npm run build` whenever SPA source changed.
  This is a **condition to re-derive, not a count to memorise** -- if you find a fifth, add it here
  rather than trusting the length of the list.
- **pyc-shadow-on-older-mtime.** An overlaid `.py` whose mtime is *older* than an existing
  `__pycache__/*.pyc` makes CPython run the **stale** bytecode → clear caches every deploy (the
  161 incident).
- **`venv/` ≠ `.venv/`.** The service venv is `venv/`; system `python3` (python-is-python3)
  resolves but lacks Flask/cloakbrowser → backend/import checks **must** use `venv/bin/python`, or
  `resolve_backend()` falsely reports `playwright`.
- **live ≠ built when cuts stack.** What is *built* (committed to origin/main) legitimately runs
  ahead of what is *live* (what the box last reset to and restarted on) whenever cuts stack
  undeployed. Confirm the deployed version from `/api/health`, never from the tree. (The STATE.json
  `live_version`/`built_version` pins that `bd-handoff`/`bd-pack` maintained no longer exist in this
  repo -- the file is gone; the model is not.)
- **band from the extracted zip, never the work tree.** Work-tree pyc lies; always band the
  extracted/built zip and gate `verify_release --zip` on the **true `$?`**, not a piped `tail`.
- **the import gate tracks function-local imports too** *(@582).* `dependency_graph.py::_internal_imports`
  uses `ast.walk`, so `import_graph_gate` counts **every** `import` / `from ... import` — nested and
  lazy ones included. A function-local import is "free" (no `--update`) *only when the src→dst edge
  already exists*; a genuinely NEW module→module dependency is flagged and must be declared in the
  same cut (`import_graph_gate --update` + `dependency_graph` regen), exactly like a guard-SHA change.
  582 proved it: the lazy `diagnostics_bundle → capture_artifact_redact` in `_attach_logs` moved the
  count **1231→1232**. Corrects the earlier "lazy imports are never tracked" note — that held only
  because those specific edges already existed at module level.
- **vite hash non-determinism / stale-137 dist.** Frontend bundles rehash; a full-tree build
  expects **3 "missing"** = stale 137-base dist hashes — the tree ships the live hashes (tree
  wins). `frontend/dist/*` rehash churn is *noise*, not a change.
- **`position:fixed` is viewport-relative only without a transformed ancestor** *(@354).* Any
  ancestor with `transform`/`filter`/`will-change`/`contain`/`perspective` re-bases a fixed child
  to *that* element → validate fixed shell elements at the relevant width band (e.g. 420–820px),
  not just 1280.
- **structural tests are blind to JS runtime + rendered layout.** Marker/structural tests pass
  while the page is broken — the Slice-D `const NAV_BADGES` TDZ killed the whole cockpit at runtime
  with 42 structural tests green; 346/347 shipped an unusable 50px sidebar that structural tests
  never saw. The **chromium runtime probe + `render_check` (computed content width)** are
  *mandatory* cockpit gates, not optional.
- **the runtime auto-applies enabled templates.** A stale *enabled* reviewed template silently
  drives a broken flow — the long-parked reptyle "nav blocker" was template-drift
  (`app.py:11667`), not a `page.goto` problem. Suspect the template before the browser/network;
  disable a site's enabled template before re-capturing it.

---

## 3. Decision criteria — the judgment calls

The recurring decisions and how to make them. (The *rule*; the operating mechanics live in
`CLAUDE.md`.)

- **guard vs non-guard.** The **7 byte-identical release guards** — `extraction_core.py`,
  `session_capture.py`, `tools/capture_session.py`, `dom_capture.py`, `dom_recorder.py`,
  `capture_bodies.py`, `tools/build_release.py` — must stay byte-identical unless the change is
  **operator-declared with its new SHA**, and SHAs are **re-derived from the extracted zip**, not
  the work tree. Everything else is non-guard and moves freely. (Distinct from the 5 ASI-separator
  checks — same word "guard," narrower set.)
- **operator-gate vs proceed.** Read-only and planning work is **free** — do it immediately.
  Runtime, build, version, guard, and release changes need **explicit per-task authorization**.
  Always-explicit regardless: first-time host enable, a new API trust boundary, a failed
  drift/safety check, a protected-template overwrite before backup/stage/diff exists. (Full model:
  `AUTOMATION_POLICY`.)
- **cut vs hold (cockpit workflow).** RED-first failing test → implement → green → **chromium
  runtime probe** → **`render_check`** → *then* await an explicit cut signal. Build+validate is
  in-scope once the work is authorized; the **cut itself** (3-part bump + `build_release` +
  `verify_release`) is a *separate* explicit gate ("go"/"cut"). Never bump+build without it.
- **minimal vs clean refactor.** A bugfix does **not** move the ratchet or parity floor; a tranche
  move does — and converts its equality pins to ceilings the same cut. **Pure motion (F5.1) is
  never batched with a feature.** When in doubt, smaller and reversible.
- **deliverable shape.** Standalone artifact the operator keeps/ships (release zip, plan doc) →
  a file in the repo (or the session scratchpad for throwaways), surfaced explicitly. A
  strategy/summary/analysis they'll read now → inline. Prefer markdown/inline over heavyweight
  formats unless a downloadable doc is clearly wanted. One consolidated release per slice of work
  (bump once, one zip).
- **fact vs judgment (the KB's own rule).** If a script can extract it (route, count, SHA,
  version), it's a **fact** → generate + gate + transclude, never hand-copy. If it's a why/how/criterion,
  it's **judgment** → this doc, decay-tagged. A confident number in prose with no generator behind
  it is the `stale-copy-of-derived-fact` shape waiting to happen.

---

## 4. Collaboration contract — how Matt works

The judgment view of the working relationship (operating facts: `CLAUDE.md`
§3).

- **Terse/directive; single-word approvals are binding.** "go" / "cut" / "1" / "Continue" =
  full authorization — execute immediately, lead with results, no preamble. A pasted terminal
  block = deployment confirmation (read it as ground truth about what landed).
- **Session-close docs are minimal — heavy regeneration annoys Matt.** Update only the *changed*
  fields, ~1 line each (a machine pin is not a narrative); one short close-out summary, not three
  overlapping docs; don't re-read/regenerate carried-forward docs to "mirror format," and don't
  write multi-paragraph reconciliation/validation essays — state
  the outcome in a line or two. **Docs only:** the release *gates* stay intact (RED-first, band from
  the extracted zip, `verify_release --zip` PASS, guard-SHA declaration).
- **Honest over optimistic — always.** Never claim something passed/shipped without verifying it
  **from the extracted artifact** (not the work tree). State sandbox-untestable limits plainly
  (live browser/noVNC, cockpit click-throughs). A correct refusal/limit beats a confident wrong
  "done."
- **Stop on "hold"/"wait."** Respect the interrupt immediately.
- **The tree is not automatically what the box runs.** The box is `git reset --hard origin/main`,
  so committed source matches by construction -- but your local tree may hold uncommitted work, and
  gitignored generated artifacts (`reports/gui_parity_inventory.json`, `__pycache__`,
  `.claude-env-report.md`) survive a reset and go stale independently, as does an unbuilt
  `frontend/dist/`. Report divergence candidly; never claim a state on the box you have not been
  told.
- **Duplicate user message = compaction tell.** Compaction can drop the tail of a turn (e.g. a
  `present_files`); on a duplicate, re-verify what *actually* landed (does the file exist? was it
  presented?) and complete only the missing step — don't blindly redo the whole turn.
- **Read order in a fresh session:** `CLAUDE.md` -> this judgment layer -> `KB_ACTIVE_INDEX` for the
  rest, re-deriving any figure from the tree at decision time. Source code is the final ground
  truth over any doc. (STATE.json and the KB_HANDOFF series no longer exist in this repo -- do not
  send a session to either.)

---

## The denominator shape, four more times (v3.66.728 -- toolchain session)

**The shape:** *a gate whose denominator structurally excludes the thing being asked about
reports clean -- truthfully, and uselessly.* Four fresh instances. **Two of them were mine,
committed while hunting the other two.** That is the point: this is not a mistake other
people make. It is the default failure mode of anyone measuring anything.

**1. The band derivers measured FILENAMES, not consumption.**
`bd-band-derive` had 3 signals: filename-stem glob, a curated map, a declared count. None of
them is *"who actually consumes this module."* So a test that exercises module X under an
unrelated name was invisible, and the band reported itself complete. **`tool_bridge.py` --
the security-critical allowlisted exec bridge -- banded ZERO TESTS.** Editing it would have
run none of its 11 RED-first negative controls. Fixed: SIGNAL 4 (module-consumer).

**2. The packaging gate measured SELFTESTS, not whether the tool runs.**
`bd-guardcheck` shipped twice (726, 728) NameError-ing on every real invocation -- a
*release-gating guard tool*. Static lint: clean. FULL runtime lint: clean. `--selftest`:
PASS. `--help`: clean. Only *running it* fails. The runtime lint runs `--selftest`, and that
selftest drives a different code path than the entry point. Fixed: `bd-tool-smoke`.
**Corollary I got wrong first: I claimed the full-runtime-lint fix would catch this class.
It does not.** Running the selftests is strictly better than not, but it is not the same
question as "does the tool work."

**3. bd-since measured PATH STRINGS, not versions.** (parallel session's find)
It globbed uploads + /home/claude and `sorted(..., reverse=True)`. Lexicographically
`/mnt...` > `/home...`, so the read-only uploads copy always won *regardless of version* --
diffing the work tree against a four-release-stale zip while looking entirely correct.
*The denominator CONTAINED the right file. The ORDERING discarded it.* **My own
bd-deploy-rehearse, written the same day, had the identical bug.**

**4. MINE: a deadness scan where a catalog listing counted as a USE.**
`bd-consumer-graph --dead-tools` first reported *1 unreferenced of 241* -- a 99.6%-alive
toolchain. Too clean. `bd-tools` IS the category map and names all 237 tools, so **every tool
was "referenced" by construction.** The answer was preordained by the denominator. Fixed: a
catalog listing is not a use; three states (used / listed-only / orphan); the real number is
**84 listed-only**.

**5. MINE, and the worst: I truncated my own evidence and reasoned from the truncation.**
I ran `bd-kb-sync verify | tail -8`, saw 7 CHANGED, and told the operator the tool was
"structurally blind to untracked files -- the same shape as the config ratchet." It is not.
It reports all 13 ADDED correctly; `_report()` prints ADDED **first**, and my `tail -8` cut
them off. I asserted a defect in a working tool from a window that excluded the evidence.
**Check the full output before diagnosing a blind spot. A pipe is a denominator.**

### The rules that fall out

- **Make the denominator CONTAIN the thing being asked.** Derive exposure/reachability/
  consumption; never assert it, and never measure a proxy for it (a filename, a selftest, a
  path string, a catalog entry).
- **UNKNOWN is a third state, and it FAILS.** A check that cannot verify must SAY SO.
  `bd-job` reports UNKNOWN rather than inferring "not running + no error = done" -- and that
  caught the author destroying a running job's state dir. `bd-tool-smoke --run` reports
  DID-NOT-COMPLETE, never folding a slow tool into a crash.
- **A negative control that cannot fail is not a control.** Every control added this session
  was verified by *reintroducing the bug* and watching the selftest fail with the right
  diagnosis. A green selftest that would be green anyway is a lie with a checkmark.
- **A restore/cleanup must VERIFY itself.** `bd-tool-smoke --run` re-hashes after restoring
  and says DIRTY if it failed. And it kills PROCESS GROUPS -- orphaned grandchildren
  otherwise outlive the kill and race (and beat) the restore.
- **The exec limit is not a hard ceiling.** `setsid nohup` survives the bash_tool boundary.
  Five tools grew chunk/resume flags to route around a constraint that does not bind. Use
  `bd-job`.

---

## The harness that lied seven ways (v3.66.729 -- the integration-fixture session)

**The task:** settle the 66 UNKNOWN body contracts by replaying each control's real body
against a real world. **The result:** 0 dead controls -- the 724/726 remediation held.

**The lesson is not the result. It is the cost of getting there:** ~45 FALSE DEAD verdicts
across SEVEN distinct mechanisms, every one of them **the harness lying, not the product
breaking**. Each was plausible enough to have shipped as a confident bug report. None
reached Matt, and the only reason is that every DEAD was interrogated before it was
believed.

**This is the general law of the session:** *when you build an instrument to find defects,
the instrument will manufacture them, and its output will be indistinguishable from a real
finding.* The differential rule that says "DEAD" reads exactly the same whether the control
is broken or your fixture is.

### The seven

1. **(16 fakes) Trusted a field's NAME instead of its MEANING.** The literal parser's
   `NO-BODY-ARG` shape does not mean *"this control sends no body."* `apiPost`'s payload is a
   **REQUIRED positional** -- a body-less call would not typecheck. It means *"the regex could
   not parse a `{...}` literal"*, and it fires on shorthand props (`{ site_id, url }`) and
   multi-line objects, both of which DO send bodies. **Absence of evidence, labelled as
   evidence of absence, and then built upon.**
2. **(13 fakes) The differential rule collapses when YOUR value is invalid.**
   `A refused identically to {} => the keys made no difference => DEAD` is sound only if your
   value is VALID. `/api/queue/v2/cancel` answers `"unknown site_id"` to BOTH `{}` (key
   missing) and to a body carrying a site that is not in queue-v2's store. **Same string,
   opposite meanings.** A fixture's poverty and a control's defect are not distinguishable
   from the error text alone.
3. **Replay ORDER contaminated the verdicts.** 126 **MUTATING** calls replayed against **ONE
   shared world**. `apiDelete("/api/sites/${}")` fires, and every later `/api/sites/<sid>/*`
   call 404s. **The verdicts were a function of what ran first, which means none of them were
   evidence.** Fix: re-establish the world before EVERY probe (and again between the A and B
   halves of a differential -- A is itself a mutator).
4. **A type-correct, MEANING-WRONG fixture.** `text` for `/api/import/start` is the
   operator's pasted **URL LIST**, not prose. Filling it with `"fixture text"` yields *"no
   valid URLs"* -- identical to `{}` -- and the rule condemned a control that had been
   **FIXED at 726**, with the source comment saying so three lines above the call site.
   ***A type-correct, meaning-wrong fixture is just a slower way of making things up.***
5. **(4 fakes) A stub that INVENTS attributes.** `__getattr__` returning a callable for
   everything handed the app a function where it expected a dict (`runner.jobs`), producing
   500s the product never had. **A mock that answers every question will eventually answer
   one whose answer escapes into the response.** Rule: **data attributes must be REAL;
   unknown methods may no-op, but every fabrication is RECORDED and printed.** (Making it
   raise instead was ALSO wrong -- the app catches `AttributeError` and remaps it to
   `400 "request body must be a JSON object"`, so an honest error came back disguised as a
   BODY defect and manufactured six more phantom DEADs. **A truthful error the caller
   mistranslates is still a lie in the report.**)
6. **A broken DB MANUFACTURES dead controls.** `library`/`tags` are created by MIGRATIONS,
   which run at app import -- not in a fresh scratch DB. Every `/api/library/*` call then
   answers `"db error: OperationalError"` -- to a real body and to `{}` alike -- and the
   differential rule reads that identical refusal as **proof of a dead control**. ***The most
   dangerous false positive of the lot: it looks exactly like a real one.*** An INFRA error
   can only ever be a HARNESS-FAULT, and it must be checked **before** any DEAD rule can see it.
7. **Cross-test contamination.** 726's probe leaves its `_probe` site in module-level
   `s_cfg`; under a single-boot band the gate probed a dirty world and UNKNOWN rose above the
   ratchet. **It passed standalone and failed in the band.** Same bug as (3), one level up.
   Fix: `probe_fixtures` is HERMETIC -- snapshot/clear/restore `s_cfg`/`s_meta`/`runners` and
   `BD_HOME`.

### The guards that fall out (do not weaken these)

- **A RESOURCE/VALUE complaint, or an EMPTY error message, can NEVER return DEAD.** It
  returns **FIXTURE-GAP**: a named, countable admission that our world is too thin to judge
  this endpoint. **A to-do list, not a verdict.**
- **An INFRA error or a 5xx is HARNESS-FAULT, never a product finding** -- checked first.
- **Status before prose.** A 2xx is a success whatever text its body carries. (Checking the
  infra-substrings first flagged 200s as faults because their `hint` field matched a word.)
- **Verify the world exists before judging anything in it.** `SELECT 1` from the tables the
  fixtures need; raise rather than emit verdicts against a broken DB.
- **A fixture the app cannot see is not a fixture.** The endpoints read the sqlite `queue`
  table, not `runner.queue`; a MagicMock there is decoration.
- **Ids are TYPED.** `/api/library/<int:lid>` takes an int. A string id 404s in **ROUTING**,
  not in the view -- so a malformed probe URL is indistinguishable from a missing resource.
  Resolve ids **per family**, never shove `site_id` into every `${}` slot.

### AND THE ONE THAT WOULD HAVE BEEN FARCE

The first version of the new gate **SKIPPED 5 of its 6 tests in the extracted zip.**
`ts_calls()` shells out to `node scripts/body_types.mjs`, which needs `frontend/node_modules`
-- **which is not in the release zip.** The tests skipped, **and a skip reads as green.**

**I nearly shipped a body-contract gate that cannot see, inside the cut whose entire thesis
is that a gate which cannot see is worse than no gate.**

**Fix (the ROUTE_INDEX pattern, and the general answer to this class):** make the input a
**committed derived artifact** (`tools/BODY_CONTRACT_CALLS.json`), regenerate it with an
explicit `--regen`, and hold it in sync with its own gate. **The gate reads the artifact and
ALWAYS RUNS. Node is needed to REGENERATE, never to ENFORCE.**

**Three of the seven were caught ONLY because the band runs from the EXTRACTED ZIP.**
Sandbox-green is necessary and not sufficient -- again, and it will be again.

### The corollary, stated plainly

**A skip is not a pass. A timeout is not a pass. An unknown is not a pass. A gate that
degrades to silence when it cannot see is worse than no gate, because it also consumes the
attention that would have gone to checking.** Every instance in this file is a variation on
that one sentence.

---

## A rebuilt gate regrows the blind spot (v3.66.730-732 -- wiring + toolchain session)

**Two of the tools that bit this session had ALREADY been rebuilt to be authoritative.**
`bd-gui-surface` was reconstructed at 729 precisely because its "44 cockpit views" turned out
to be folklore. `bd-tool-lint`'s budget logic was fixed at 729 with the sentence *"a budget
below the real cost is a permanent false alarm."* Both regrew the same defect in a new place.
**Being the tool that was fixed last time confers no immunity. Assume the shape is in the
instrument you trust most, because it is.**

### 1. "No anchor" is not "unreachable" -- it is "unreachable BY ANCHOR"

`bd-gui-surface` called a cockpit view DARK when no static `<a data-p>` anchor and no REDIRECT
alias named it. It reported **7 dark views**. I set out to wire all 7.

**All 7 were FALSE POSITIVES.** The cockpit reaches views by two mechanisms the scan could not
see: **TAB CONSOLIDATION** (v3.66.107 deliberately merged inbox+daily into the tabbed
`priority` page, and the composite indices into `scores` -- nav entries removed ON PURPOSE,
renderers KEPT on purpose so deep-links resolve) and **PROGRAMMATIC TIER LANDING**
(`_tierLanding()` returns `advlanding`/`syslanding` by layout mode; **code** navigates, not an
anchor).

**Wiring anchors back would have reverted a deliberate consolidation.** What stopped it was
not judgment -- it was `test_v3_66_107`, the product's own test, failing in the band. **The
tool's report was wrong and the product was right.** A finding from an instrument is a
hypothesis; the tests are the evidence.

**FIX PATTERN:** a reachability verdict must enumerate EVERY mechanism that reaches, and
report a **third state**. Now: **UNREACHABLE** (a real finding) vs **CLOSED** (anchor-less by
design, reachable another way -- informational). And it VERIFIES the consolidation is live in
source rather than asserting it: kill the `scores` host page and its 3 views correctly flip
CLOSED -> UNREACHABLE (mutation-tested).

**The operational danger is specific and worth naming:** *reporting a deliberately-closed
control as DARK invites the next session to wire it back.* A false "dark" finding is not a
harmless over-count. It is an instruction to break something.

### 2. Chasing the number instead of the lever (the SLOW_TOOLS map)

`bd-tool-lint` refused to package: tools UNVERIFIED (timed out). The refusal was **correct**
-- and it **NEVER NAMED THE TOOL.** Three full re-runs were burned discovering *who* timed
out. **A gate that refuses without naming what tripped it teaches the operator to override the
gate instead of fix the tool, and an override reflex is how a real finding eventually gets
waved through.** Fixed: the refusal now always prints `TIMED OUT : <tool>`.

Underneath it, two more:

- The per-tool budget for `bd-precut` was **60s -- below its measured 62s cost.** A permanent
  false alarm **inside the very map built to prevent permanent false alarms.**
- Raising the **global** `--probe-timeout` did nothing, because `SLOW_TOOLS.get(tool, global)`
  **overrides per-tool**. I raised it 20 -> 90 -> 150 -> 180s and the tool kept timing out.

**The 180s failure was the diagnostic, not a setback.** A cost that keeps moving as you raise
the budget is not a slow tool -- it is *the wrong lever*. `bd-precut` **orchestrates the gate
battery** (it spawns `bd-footguns`' full fan-out, `bd-coretest`, `bd-ratchet`, `run_tests`).
Probing it *during* the lint re-runs everything recursively under core contention, so its cost
is a function of the load the probe itself creates. It belongs in **SELF_REFERENTIAL** (the
`bd-fullsuite` rule: *a lint must not detonate the thing it is linting*) -- not in a budget
map at any value. After that the FULL runtime gate passed **honestly**, not overridden.

**When a number has to keep growing to make a gate stop complaining, stop growing the number.**

### 3. Parallelism as the pessimization; cache as the thing NOT to reach for

Asked "would a cache help, or should we diagnose" -- **diagnose. A cache would have hidden all
of it.**

- **`bd-footguns`: the fan-out WAS the slowdown.** `ThreadPool(8)` spawning 8 app-booting
  subprocesses on a **1-core** box: **77s wall against a 52s SERIAL sum.** The concurrency
  added 25s of pure contention. Fixed by sizing workers to `min(len, cpu_count, 4)` and
  submitting longest-first by measured `cost_hint`. **Fan-out is a bet on cores you have.**
- **`bd-precut` ran 6 of its 7 in-sync suites TWICE** -- once itself, once inside the footgun
  detectors seconds later, same tree, same invocation. The second run can only ever agree with
  the first.
- And **`_run_insync()` was CALLED BY NOTHING.** It had a docstring, a `--no-insync` flag, and
  **zero call sites.** *A skip flag for a check that never runs is a silencer with no siren
  attached.* (Cf. `ts_calls()`/`probe_typed()` at 729 -- documented, flagged, never called.
  **Search for the call site. The flag is not the evidence.**)

**Dedup WITHIN one invocation is sound** (same tree, same process tree -- a trivially valid
key, and coverage is DERIVED from the live registry, so deactivating a footgun returns its
suite to the caller). **A PERSISTENT cache on a gate is not**: a stale key turns the gate into
exactly the thing this whole file is about.

### 4. The instrument's blind spot lands in YOUR analysis too

Deriving which CONTROL endpoints were GUI-dark, my own matcher searched for the endpoint path
as a **contiguous string literal**. `SiteActions.tsx` builds them from a **data table** --
`apiPost(`/api/sites/${sid}/${suffix}`)` where `suffix` comes from `{ suffix: "accounts/rotate" }`.
Two live controls read as dark. There is even a comment in `Queue.tsx` naming this exact blind
spot. **Cross-checking against `ROUTE_INDEX.spa_wired` caught it** -- two independent
derivations, and where they disagreed, one of them was wrong every time. **Never trust a
single derivation of a denominator, including your own, especially your own.**

---

## v3.66.740 -- FOUR DISTINCT FORMS OF THE SHAPE (keep them distinct; the fixes differ)

The dominant failure shape has now been observed in four forms. Collapsing them into "a gate
that lies" loses the diagnosis, because each has a different fix.

### (a) The gate CANNOT SEE -- the denominator excludes the subject
The original. `bd-band-derive` reported "changed source (0)" and a **2-test band** on an FE cut
whose real consumer family was **52 suites**.
**CORRECTION (v3.66.760): the mechanism is worse than "does not count `.ts`/`.tsx`."** The real
bug is in `diff_tree`: it maps zip namelist entries with `n.split("/", 1)[-1]`, turning
`bulk_downloader/captcha_relay.py` into `captcha_relay.py`, while the work-tree side uses full
relpaths (`bulk_downloader/captcha_relay.py`). The changed set is `set(zsha) & set(wt)` -- an
intersection of basenames-after-first-slash against full relpaths -- which **structurally
excludes EVERY file in a subdirectory**, backend `.py` included, not just FE. It only ever
matched the one file the author hand-remapped for literal-diffing (`global_config.py`, via the
`zsrc` special-case -- proof the author knew `rel` was mangled but patched only that file). So
`bd-band-derive` reports "changed source (0)" for a pure-backend cut too. Until fixed, derive
bands mechanically: `grep -rl "<module>" tests/` over each changed module, plus the
footgun-mandated gates (config-key -> 710/713/716/720/parity/gui_parity; route -> parity_method_aware; etc.).
**Fix (the tool's):** key both sides on the same namespace (full relpath), then the intersection
contains every changed file. Derive the denominator, never assert it.

### (b) The gate SEES PERFECTLY AND NOTHING FAILS -- a finding with no failing mode
NEW, and it is NOT the same as (a). `bd-gui-surface` **correctly** flipped `dark_view_count`
0 -> 3 when its subject (the `scores` tab host) was destroyed -- and **exited 0**. The number
went only into `--json`, was never printed in the human report, and `--gate` could only ever
fail on missing SCREENSHOTS. The whole three-state reachability model could regress and every
automated check stayed green.
**A finding no one can fail on is not a finding -- it is a log line.**
**Fix:** wire a failing mode to the finding the tool already computes. Ask of every tool:
*what, exactly, makes this exit non-zero?* If the answer is not the thing it exists to find,
it is not a gate.

### (c) The gate CHANGES WHAT IT MEASURES -- and reports its own load back as a finding
L34 @734: made concurrent so it would fit its wall, it reported **37 of 523** routes over an
8s budget. `/api/community_scrapers/index` answers in **4090ms probed alone** and appeared in
the ">8s" list -- an 8-way fan-out was loading the app it was measuring.
**Concurrency buys the wall clock; it does not buy a verdict.**
**Fix:** a finding from an instrument is a HYPOTHESIS. Re-confirm it serially, on a quiesced
subject, before reporting. Say "RECOVERED -- our own load" out loud; a probe that quietly
discards its own false positives teaches nobody anything.

### (d) The gate OVERRUNS AND CORRUPTS THE NEXT GATE
L34 @733 and @737: it exceeded the harness's 90s wall, and on expiry the harness **ABANDONS
the thread** -- which kept smoking all 523 routes underneath L31 (memory) and L33 (leak scan).
L31 duly reported **"RSS rose 109.1 MB -- possible leak"**. There was no leak. It was the
abandoned thread. With L34 bounded, L31 PASSES (+0.0 / +21 / +9.4 MB).
**A check that can run past its wall does not merely fail -- it corrupts the checks after it.**
**Fix:** any check whose cost tracks a growing denominator MUST budget itself against the wall
and report UNKNOWN for what it could not reach. Bounding is a correctness property, not an
optimisation. And: **treat a neighbouring gate's warning as UNPROVEN while an overrunning gate
runs beside it.** That call saved us hunting a memory leak that did not exist.

### THE META-SHAPE: the detector built to hunt these HAD one of them
`bd-mutation-test` -- the tool nominated to run the gate-soundness sweep -- defined its **own
reference implementation** of a guard and compared its own mutants **against that same
reference**: *"does a function that differs from my reference differ from my reference?"* True
by construction. Kill-rate 4/4, forever. It never opened the tree (`--work` accepted, never
used). **DEMONSTRATED: it printed "all mutations caught -- the invariant is discriminating"
and exited 0 against an EMPTY DIRECTORY**, and against a tree with the real SSRF guard gutted.
**Fix:** mutate the REAL source, run the REAL gate, and VERIFY THE MUTATION LANDED. Four
states: CAUGHT / SURVIVED / BASELINE-RED / UNKNOWN -- and UNKNOWN FAILS. A baseline that is
already red cannot prove it fails for THIS reason.

---

## v3.66.740 -- OTHER DURABLE LESSONS

### Verify a hit BY HAND before believing it -- especially your own tool's hit
THREE of my first-pass mutation-test SURVIVORs were FALSE, each manufactured by my own harness:
- `bd-guardcheck` hardcodes `--tree=/home/claude/work` and IGNORES cwd -- so it inspected the
  PRISTINE tree while I mutated a scratch copy, came back green, and I nearly filed "the guard
  gate is blind."
- `bd-gui-surface` -- I ran it in REPORT mode, not `--gate` mode.
- `body_contract` -- I paired the mutation with `test_..._726` when the consumer of
  `BODY_CONTRACT_CALLS.json` is `test_..._729_body_contract_fixtures`.
**A number from an unvalidated detector is worse than no number.** Many `bd-*` tools hardcode
their tree path; a harness that runs them in a scratch dir MUST inject the path explicitly.

### A COMMENT can destroy the evidence
A `//` line comment containing the glob `/api/batch/*` opened a **fake block comment** for a
regex comment-stripper, which then ate 200 lines -- including the very `apiPost` calls being
asked about. Three WIRED endpoints were reported dark, *because a comment mentioning them
destroyed the evidence that they were wired.* **Strip comments with a string-aware scanner,
never a regex.** Verify with negative controls (a path only in a comment must be erased; a
path in a string must survive; `/*` inside a string literal must not open a comment).

### Written to one channel, read from another
`bd-guard-declare` writes the new SHA to `/home/claude/STATE.json`, but every checker
(`bd-guardcheck`, `bd-precut`, `bd-state`) reads the baseline from **inside the version zip**.
The declaration lands where nothing reads it and the guard gate stays red until STATE is
repacked. Same shape: `bd-handoff` regenerates STATE and **silently drops keys not in
`STATE_schema.json`** -- it discarded `static_kb_manifest_pin` this session.
**Before trusting a write, confirm the READER reads that channel.**

### A manifest cannot certify itself
`STATIC_KB_MANIFEST.json` ships INSIDE the bundle it certifies and never hashes itself, so
`bd-kb-sync verify` could only ever answer *"does this bundle agree with itself?"*
**DEMONSTRATED: a 2-file bundle containing the literal text "TOTALLY BOGUS PK CONTENT",
seeded with its own manifest, returned INTEGRITY OK exit 0.** Self-consistency is not canon.
The pin must live OUTSIDE the artifact, and **no pin -> UNKNOWN -> FAIL.**

### Reconciling two forks: sort by CONTENT, not by version number
A parallel toolchain line (728a -> 728b -> 728c) looked *older* by its version number than this
session's 738 -- and was in fact far AHEAD: our fork PREDATED 728a entirely (zero
`require_corpus`; no bd-sweep/bd-golden/bd-fixture-lint). A tool-by-tool set-diff said "mine
moved" for ~29 tools; **it cannot tell you which direction.** Most were base drift, and taking
them would have silently reverted the whole F-1 corpus-guard program.
**Establish ancestry from CONTENT (does branch X contain branch Y's centerpiece?), take the
strictly-ahead branch as the base, and replay only the changes you can PROVE are forward.**

---

## v3.66.748 -- TWO NEW FORMS (the 740 list had four; these are (e) and (f))

The 740 taxonomy was about a gate's DENOMINATOR and its FAILING MODE. This session
produced two shapes that are neither -- the gate can see, and it can fail, and it is
still wrong.

### (e) The gate is RIGOROUS ABOUT THE WRONG SUBJECT
L34 -- the post-deploy live gate -- spent FIVE cuts (741, 742, 744, 745, 746) getting
progressively more correct: wall-aware, phase-bounded, triage-budgeted, worker-tuned.
Every one of those fixes was right. The check still failed the deploy, because 65% of
its sweep cost (79s of 122s; 202 routes; 28 of the 41 slow ones) was the `/api/dev` +
`/cockpit/api` surface -- INTROSPECTION endpoints that do real scan work BY DESIGN.

**Failing an operator deploy gate because a dev tool's filesystem walk takes 9s asserts
the wrong thing at the wrong cost.** The check was a correct machine pointed at the
wrong surface, and every iteration made the machine better without ever asking what it
was FOR.

FIX SHAPE: partition the subject. The surface the gate OWNS is the hard gate; the rest
is smoked and reported ADVISORY.

**THE LINE THE FIX MUST NOT CROSS -- advisory means VISIBLE, not DROPPED.** Scoping a
finding out of the GATE is legitimate. Dropping it from the REPORT is the
shrink-the-denominator sin (shape (a)) wearing a different hat. bat_lint stayed named,
counted, and in the verdict tail -- it just stopped sinking the deploy.

DIAGNOSTIC: before hardening a check, ask what it is FOR. If the answer names a surface,
check that the check's denominator IS that surface.

### (f) A MIRROR WRONG IN THE SAFE DIRECTION BURNS DOWN THE ALARM
`_check_csrf` gates `("/api/", "/cockpit/api/")`. The scanner every artifact calls had
RE-TYPED that rule and never learned the second prefix. So 28 cockpit write endpoints
were PROTECTED by the app and reported `csrf: false` by ROUTE_INDEX, ENDPOINT_CATALOG,
and -- the one that SHIPS -- the OpenAPI spec, which therefore omitted the required
`X-CSRF-Token` header and the 403 response. A generated client breaks on its first
cockpit write; a security reviewer reads the spec and concludes cockpit is unprotected.

That is bad. **This is worse:** because the artifact ALREADY said `false`, someone
dropping `/cockpit/api/` from the hook would change NO artifact, diff NOTHING, and trip
NO gate. *The mirror being wrong in the SAFE direction had burned down the alarm for a
move in the DANGEROUS one.*

**A gate that is wrong-but-reassuring cannot detect the thing it exists to detect.**

FIX SHAPE: **DON'T MIRROR -- DERIVE.** The app EXPORTS the policy
(`CSRF_GUARDED_PREFIXES`, `CSRF_EXEMPT_PATHS`, `CSRF_TRIPPING_METHODS`, and the one
predicate `csrf_fires_for()`); the hook READS it and the scanner DELEGATES to it. A
predicate that must be manually synced WILL drift. One that cannot be re-typed cannot.

PROVE THE DERIVATION TWO WAYS, because "derived" is a claim:
  1. STRUCTURALLY -- assert IDENTITY, not equality. `scanner.X is app.X`. Equality still
     permits a fork that drifts on the next edit; identity makes the fork unexpressible.
  2. BEHAVIOURALLY -- drive the REAL hook. For every mutating route, issue an actual
     cookie-session request with no token and compare the actual 403 to the scanner's
     verdict. *This is the alarm.* It goes red the day the two disagree.

GENERALIZE: audit every place a tool re-types a rule the app owns. Each one is a drift
waiting to assert a property the app does not have -- to a reviewer, and to a client.

---

## v3.66.748 -- LESSONS ABOUT MY OWN TESTS AND MY OWN DECISIONS

### My own test's WORLD can hide my own bug
The 741 phase-1-bound test used a world where every probe cost the SAME 0.5s. It went
green. The real failure shape was BIMODAL -- hundreds of ~20ms routes queued behind a
handful of 8s ones -- and a uniformly-slow world CANNOT STARVE. The test was rigorous
about a scenario that could not exhibit the defect.

**Model the failure you are AFRAID of, not the one that is easy to write.** When a fix
targets a tail behaviour, the fixture must have a tail.

### A test that "fails on pristine" for the WRONG REASON is not RED-first
Twice this session a new test was RED on the pristine tree for a reason unrelated to the
defect (once because the assertion matched a literal summary token; once because the
miniature world was too small to starve at the new worker count). **A coincidental RED is
not a proof.** `bd-mutation-test` caught both: replant the defect, and if the gate does
NOT go red, the test is not wired to its subject.

**The mutation row is the mechanical proof that a RED was real.** Add one for every gate
whose failure mode you actually care about.

### Reverse your own decision when the capture disagrees with your model
@741 I lowered L34's workers 8 -> 4 to starve phase 2 of contention artifacts. It was
defensible on the evidence I had. Two captures later the operator's own logs showed
artifacts were CHEAP (all 20 suspects recovered serially in ~14s) and THROUGHPUT was the
binding constraint -- so @745 put it back to 8 and said so in the changelog, in the pin
test, and in the handoff.

**A pin that moves must say why, with a capture.** Do not defend a number because you
chose it.

### Verify the guard list BEFORE assuming a guard ceremony
The round-2 audit said the `.hypothesis/` fix needed a guard-SHA re-declaration because
`build_release.py` is guard #7. It did not: the forbidden lists live in
`diff_release_zips.py` and the exclusion set in `dev_suite/release_lint.py`, neither of
which is pinned. **Check `bd-guardcheck`'s actual 7-file list before paying the
ceremony** -- and equally, before skipping it.


### (g) THE INSTRUMENT CANNOT PARSE ITS OWN SUBJECT
Not (a). In (a) the denominator excludes the subject. Here the denominator is right and
**the tool reading it is wrong** -- so it returns a confident, specific, entirely fabricated
answer.

Measuring the blast radius of the gui_parity comment-harvest defect (@753), I wrote a
throwaway comment-stripper: kill `/* ... */`, then kill `^\s*//.*$`. It reported **22
endpoints wired only by comments** -- a big, alarming, plausible finding, naming
`vpn/tunnels/*/start`, `batch/delete`, `dedup/scan`.

`dedup/scan` has a real `apiPost` caller on Dedup.tsx line 109.

The stripper had eaten it. A line comment in that file reads `//   * status - scan - ...`
and the `/*` **inside the `//` comment** opened a block comment that ran 6,448 characters
forward to the next `*/` (a JSX `{/* ... */}`), deleting 40% of the file, live call sites
included. Every endpoint whose only caller lived in that swath became a "phantom."
True figure, measured with a tested state machine: **8**.

**A regex is not a tokenizer. Do not size a change with an instrument you have not tested
against a known-good case.** The falsification was free and I nearly skipped it: pick one
item off the alarming list and grep for a real caller. One grep killed the whole number.

**Corollary -- the meta-rule:** the measuring apparatus IS a gate, and every rule in this
file applies to it. A blind gate that audits gates is worse than no audit: it launders
fabrication as rigor. `bd-mutation-test` learned this at 737 (it was itself a blind gate).
The 753 session learned it **four times in one day** -- see (h), plus: it declared the 748
tracker-gate fix "never shipped" because the `bd-pack` it *had* lacked the gate. The fix
had shipped; it had been bootstrapped from an early, incomplete snapshot. **Judge the
artifact only after verifying you have the right artifact.**

### (h) A PIPED EXIT CODE IS NOT THE TOOL'S EXIT CODE
`cmd | tail` then `echo $?` reports **tail's** status. Tail always succeeds.

The 753 session ran `verify_release ... | tail -6` / `echo "exit=$?"`, read `exit=0` on an
error path, and filed *"verify_release fails open on a bad cwd"* as a defect. It does not:
it returns **2**, correctly. The defect was the measurement. The identical error was then
made on `bd-evidence --selftest` (piped read said 0; the tool actually returned 1) and
caught only because the first had bred suspicion.

The release rules **already said this** -- *"check via `echo "exit=$?"` on the next line,
never piped."* The rule existed and was broken anyway, because the pipe was for READABILITY
and the exit check felt incidental. **The convenience that truncates the output is the same
convenience that truncates the verdict.**

Run the command bare and read `$?` on the next line. To shorten output, redirect to a file
and tail the file.

### (i) A RATCHET THAT ABSORBS A KNOWN BUG HAS STOPPED BEING A RATCHET
`UNKNOWN_BASELINE` sat at 130 with a comment explaining, precisely and honestly, that 129
was the true count and the +1 was a known fixture-isolation flap. Every word was true. The
baseline still lied: it certified a world in which a known bug was permanently affordable.

Worse, the documented CAUSE was wrong (a `setup_site` id collision; the endpoint is
order-independent -- double-POST returns 200/200). The real channel was global config
(`_app_cfg` snapshot/restored for `s_cfg` but not for itself). **The tolerance had been
sized against a fiction and it fit anyway** -- because a +1 fudge fits almost any story you
tell about it.

1. **Tolerance is not documentation.** If you can name the bug, fix the bug. A named,
   tolerated defect is a defect with a permit.
2. **Fix, then PROVE order-independence, then tighten.** Tightening first and seeing if it
   passes is a green gate over an unfixed bug. Note the trap: the order-independence
   property **passes on pristine** -- two identically-poisoned runs agree with each other.
   The RED anchor must be the isolation contract itself (poison the config, leave a site,
   assert `ensure()` cleans both), not the property it implies.
3. **And a root cause is a CLAIM until it is mechanically reproduced.** The 748 session
   wrote its hypothesis into four durable artifacts without ever running the two-probe test
   that would have falsified it -- the test the 750 session then wrote in a single cut. A
   plausible story that fits the symptom is not a diagnosis. **Reproduce it, or label it a
   hypothesis.** A wrong explanation in the durable layer is worse than none, because the
   next session will trust it.


### (g-corollary) A RE-MEASUREMENT IS A NEW CLAIM. RE-RUN THE FALSIFICATION.
Recorded because it happened ONE SESSION after (g) was written, in the exact form (g)
warns about -- and I did not catch it; a parallel reviewer did, with a single grep.

Sizing the gui_parity comment defect, a naive stripper fabricated 22 phantoms (that was
(g)). I fixed the instrument (regex -> state machine), re-measured to 8, wrote *"every one
then falsified by hand"* -- and did NOT actually re-run the grep-for-a-real-caller on the
NEW list of 8. **6 of the 8 were real, operable controls** the CALL HARVEST could not see
(a dispatcher `${t.action}` norms to `*`, which does not match the literal route segment
`start`; a nested `${enc(x ?? "_global")}` truncates the path regex). Only 2 were genuine
phantoms. Had the fix landed, it would have flipped six operable controls from
accidentally-right to confidently-wrong -- the ledger reporting
render-what-you-cannot-operate for controls the operator CAN operate.

The rule (g) already stated: *"pick one item off the alarming list and grep for a real
caller."* I ran it on the FIRST list and it saved me. I did not re-run it on the SECOND.
**When the instrument changes, the discipline must move with it. A re-measurement is not a
correction you can trust for free -- it is a fresh claim, owed the same falsification as the
original.** The count changed; the scrutiny didn't follow.

Corollary to the corollary: **the comment bug was MASKING a call-harvest bug.** Fixing the
visible defect alone would have exposed the hidden one in the wrong direction. When a check
is wrong, ask what ELSE it was compensating for before you "fix" it -- a phantom that
happens to spell the right path is load-bearing until the real caller is visible.


### (j) THE SAME DOCUMENT IN TWO CHANNELS: YOU WILL FIX ONE

`Backlog.md`, `Roadmap.md` and the handoff exist in BOTH the session pack AND the static
project knowledge. At 753 I deleted a false root cause from all four PACK artifacts, wrote
the retraction, and verified it with a scan that reported zero surviving bare assertions.

**The PK's copies still carried it.** Its `Backlog.md` still named the PREVIOUS release as
live and kept the disproved `setup_site` cause; the stale 748 handoff in the PK kept both.
(The offending literals are deliberately not reproduced here -- a retraction that quotes the
string it retracts trips every grep-based staleness check, which is how a correction becomes
indistinguishable from the claim.) The next
session found it in its first minute -- after I had run **24 verification rounds
specifically designed to hunt surviving false claims.**

Why every round missed it: my sweep's denominator was *the pack, plus three PK files I had
edited*. The PK files I had **not** edited were never in the denominator -- so the scan
could not see the copies that were wrong precisely BECAUSE I had not touched them. **The
check looked exactly where the bug could not be.** That is (a), aimed at myself, inside the
tool built to catch (a).

Three rules:
1. **A doc that lives in two channels has two truths until you prove otherwise.** When you
   correct a document, `grep` the correction across EVERY channel that ships a copy --
   pack, PK, release zip, bdsuite. Do not enumerate the files you remember editing; sweep
   the whole artifact.
2. **Do not maintain the second copy -- DERIVE it.** The fix here was `cp pack/Backlog.md
   static_kb/`, not an edit. A hand-kept mirror drifts; that is what mirrors do. (Same
   lesson as `FOOTGUNS.json`, which was ALSO duplicated between `bin/` and the PK, and had
   ALSO drifted -- three entries behind, missing the very footgun about ledgers silently
   going stale.)
3. **The verification you design will share your blind spot,** because you build it from
   the same mental model that produced the bug. A reviewer who did not write the fix found
   this in one minute. **Rounds of self-verification do not substitute for a denominator
   you did not choose.**

## v3.66.760-761 -- hidden-cross-test-state, and a correct fix that exposes a lying test

MOD-1 A-5b added the honest thing: an SSE viewer disconnect now tears down the takeover
channel (a `finally: close_channel(sid)` in `takeover_screencast`), freeing the A-5a
concurrency slot. That is *correct* -- a viewer who leaves should not hold a channel open.
It also turned one stash gate RED: `test_v3_66_757::test_a2_input_route_exists` began
returning 400 where it had returned 200 at 759.

**The test was never really green.** `test_a1` (screencast) and `test_a2` (input) both used
the default sid `tk-red-1`. At 759 `test_a1` opened a channel and the OLD `takeover_screencast`
had **no teardown**, so when the test closed the stream the channel *leaked open*. `test_a2`
then reused that leaked channel and got its 200. It passed on state it never established --
the precondition for input (an open channel, which in production the operator's screencast
subscription provides) came for free from an earlier test's leak. A-5b's correct teardown
removed the leak, and `test_a2` correctly 400'd: **input into a session nobody is viewing must
be rejected.**

### The shape: HIDDEN-CROSS-TEST-STATE
A test certified green by fixture state it did not set up but that leaked from a sibling. It is
distinct from ordinary flakiness (it was deterministic given the file's run order) and distinct
from the (a) denominator shape (the test's *own* assertion was fine; its *setup* was incomplete
and silently completed by a leak). **A behavior change that is correct in itself will surface
these -- the RED is the messenger, not the bug.** The fix is never to revert the correct
change; it is to make the test self-sufficient (here: `test_a2` opens its own channel,
mirroring the real viewer subscription).

### The corollary I got wrong first, stated so I do not repeat it
I saw `test_a2` fail on *pristine* in the sandbox and called it "sandbox-only" -- reasoning
that on stash's full parallel run some ordering would supply the channel. **That was a
rationalization, and stash proved it: the test failed on stash too.** A pristine failure that
you cannot explain by a *named* environmental difference is a real failure. "Sandbox-only" is
a claim that requires a mechanism (a missing service, a display, a network fixture), not a
hope about test ordering. When the mechanism is "some other test probably leaks the state I
need," that is not an environment gap -- that is the bug, and it will travel.

## v3.66.780 -- THE WRITE DENOMINATOR EXCLUDES A KEY THE READ SIDE OF THE SAME FILE USES

### The shape: SPLIT-DENOMINATOR-WITHIN-ONE-FILE (the dominant shape, one layer in)
A LIVE HIGH bug: every Settings save 400'd. `/api/global_config` accepts a POSTed
key iff it is in `set(GLOBAL_CONFIG_SCHEMA) | _EXPLICIT_BRANCH_KEYS` (the WRITE
denominator). Six keys the SAME FILE reads and writes via explicit
`for k in (...): if k in data: _app_cfg[k] = ...` branches were in NEITHER set. The
write is atomic (the FE submits the full config draft), so any one unaccepted key
400'd the whole page. The @709 "unknown key = 400" contract fired correctly against
a denominator missing legitimate keys.

This is the dominant failure shape one layer in: not a gate whose denominator
excludes its subject, but a WRITE-accept denominator that excludes keys the READ
path of the same module depends on. The read side and the write side of one file
disagreed about which keys are legitimate.

### Why every existing gate stayed green
- `test_gui_parity` checks route/control WIRING, not config-key acceptance.
- The @709 read-side scan (`test_explicit_branch_keys_match_source`) uses a regex
  matching `"key" in data` / `data.get("key")` LITERALS -- but NOT the
  `for k in (...): if k in data` LOOP pattern. So its own denominator excluded the
  six loop-read keys: the gate meant to catch read-vs-declared drift was itself
  blind to loop reads. A check about denominators, blind on a denominator.
- The config-parity ratchet's denominator is `GLOBAL_CONFIG_SCHEMA` only, so the
  `_EXPLICIT_BRANCH_KEYS` half is outside its scope entirely.

### The fix pattern (both directions)
1. Widen the accepted set: the six join `_EXPLICIT_BRANCH_KEYS` (they already had
   write branches; siblings of ai_*/watch_* which live there, NOT in the schema, so
   the config-parity manifest is unperturbed -- no re-baseline).
2. Close the CLASS from the FE side: a parity test asserting the FE SETTINGS_SCHEMA
   key set is a SUBSET of the backend write denominator. A Settings field the
   backend would 400 on is now a test failure, not a shipped bug. This is the
   symmetric contract the @709 comment asked for and never got.
3. Read-side residual (still open, test-only): widen the @709 scan regex to see
   loop reads, or the read-vs-declared gate stays blind to the same pattern.

### The meta-lesson
The operator reported THREE keys (the ones they happened to touch). The
class-closing derivation (FE keys vs backend accepted) found SIX -- the other three
(session_keep_alive_*_min) were equally broken but unobserved. A user-reported
symptom is a lower bound on the class. Derive the full class before patching the
reported instances; if the derivation finds more, they are the same defect and ride
the same cut.

---

## v3.66.788 -- A-DISCO activation: an automation toggle must be GUI-EXPOSED, not deferred-debt

### The shape
Adding an automation config key half-way is the worst state. Declaring
`automation.disco_enabled` in `GLOBAL_CONFIG_SCHEMA` (so it is writable) WITHOUT a
GUI control + a `config_gui_manifest.json` row satisfies the "writable" gates
(709: an undeclared automation key read at runtime is a silent no-op, POST 200
writes nothing) but trips a STRICTER, separate family that the config-parity
ratchet baseline does NOT cover:
- 720 `test_open_parity_debt_is_zero` -- `base["open_count"] == 0`, a HARD zero;
- 711 `test_every_automation_key_has_a_control` -- the key literal must appear in
  frontend/src (gui_exposure is DERIVED from the FE, not asserted);
- 710 `test_every_global_config_key_has_its_own_manifest_row` -- every schema key
  needs a `reports/config_gui_manifest.json` "exposed" row;
- 319 `test_every_automation_key_is_full`.
So an automation toggle either ships FULLY (schema + FE control + manifest row +
parity debt re-pinned 0) or NOT AT ALL. It cannot ship as "open debt" the way a
generic runtime-tunable can. The 716 ratchet alone would accept a nonzero re-pin;
these gates would then catch it. Adding a toggle is a two-registry act
(lifecycle_automation.AUTOMATION_TOGGLES + global_config.GLOBAL_CONFIG_SCHEMA) with
a mandatory GUI-exposure tail.

### bd-cut --plan under-bands across module families (recurring)
A cut touching two families (automation modules + global_config) makes `--plan`
derive its band off ONE family and silently drop the other's suites -- plus it
never includes the new isolated-module test or the changed graph baselines
(import + dependency). The honest band is the UNION of a `--plan` per family +
`test_import_graph_no_new_edges` + `test_dependency_graph_in_sync` + the new test.
This arc needed an 85-suite hand-union. Candidate bd-band/bd-cut --plan fix: union
across changed module families; always fold changed graph baselines + new
isolated-module tests.

### Sandbox: test_all_modules_import needs a display + typelibs
Under the prestaged PYTHONPATH, `tray_app` imports real Gtk -> needs the Gtk +
AyatanaAppIndicator typelibs (apt) AND a live X display. Xvfb :99 runs but DISPLAY
is not auto-exported in fresh shells; `export DISPLAY=:99` before bd-cut or the
band false-fails on `tray_app: Bad display name`. Under a bare `bd` env (no
prestaged gi) tray_app degrades gracefully and it passes -- which is why a smaller
band that happened to have DISPLAY live earlier passed the same test. The failure
is environmental, not a code regression; do not chase it as one.

## v3.66.805 -- TWO SHAPES FROM THE PLUGIN-STATE CUT

### (e) The fix RELOCATES the leak instead of closing it -- shared state keyed to the wrong scope
`plugins._quarantine_state_path()` wrote runtime state INSIDE the install tree.
The obvious fix -- "anchor it under BD_HOME" -- was wrong, and the band caught it:
keying the state file to BD_HOME *unconditionally* made every plugin dir share ONE
ledger, so a quarantine raised in one plugin set bled into another. The first
design traded an install-tree leak for a cross-tenant leak; both are leaks, and the
second is worse because it is invisible (nothing appears in the wrong directory --
the wrong ANSWER just comes back).

The tell: the pre-existing tests patched `_plugin_dir()` to a tempdir precisely
BECAUSE per-plugin-dir isolation was load-bearing. A pre-existing isolation
mechanism is a specification. When a fix makes an existing isolation seam
redundant, that is evidence the fix is wrong, not evidence the seam was
unnecessary.

RULE: before moving state to a new anchor, ask what the OLD anchor was
DISCRIMINATING. `_plugin_dir()` was not merely a location, it was the IDENTITY of
a plugin set. The correct fix moved only the DEFAULT (install-tree) case and left
an explicit override owning its own state. Relocate the default; never collapse
the key.

Corollary for review: a green band on the narrow feature test is not enough. The
suite that broke (`test_v3_66_482_py_bridge`) named neither BD_HOME nor quarantine
state -- it failed because a SUBPROCESS re-imported the module without the
monkeypatch. Cross-process state questions need a cross-process test.

### (f) AST is the right instrument; a substring predicate still makes it a grep
MOD-6 was re-scoped "by AST" to 12 playwright importers. Re-deriving it, the same
AST walk returned 13 -- because the predicate was `'playwright' in name`, which
matches `playwright_stealth`, a DIFFERENT PyPI package. Parsing the tree correctly
and then asking a substring question reproduces the exact grep failure the AST was
adopted to eliminate.

RULE: match module identity EXACTLY (`n == 'playwright' or
n.startswith('playwright.')`), never by substring. The instrument constrains the
DENOMINATOR (which nodes are real imports); the PREDICATE decides the SUBJECT.
Getting the denominator right buys nothing if the predicate is loose. State the
predicate in the finding, not just the instrument -- "by AST" is not a
methodology, "by AST, exact module match" is.

### (g) "Unblocked" answers reachability, not VALUE
`bd_metrics_baseline.json`'s PK deletion was certified UNBLOCKED: verified that
bd-decomp's candidate list has no /mnt/project fallback, so nothing breaks. True,
and insufficient. The PK copy carried `_declared_at`, `_why` and `_meta` -- a
hand-authored account of why spa_wired was re-baselined 424 -> 422 -- while the
live `~/.bd_metrics_baseline.json` bd-boot seeds is four bare numbers with no
provenance, and `bd-mkbdsuite` ships the file only `if os.path.isfile(...)` from a
path that does not exist. Deleting it breaks nothing and destroys the only copy of
the reasoning.

RULE: a deletion check must ask TWO questions -- does anything RESOLVE to this
file (reachability), and does this file carry content that cannot be REGENERATED
(value)? Auto-generated and hand-authored content can live in the same file; a
"stale duplicate" that carries prose is not a duplicate. Diff the CONTENT against
the copy you intend to keep before deleting, never just the resolution path.

### (g.1) The content rescued from `bd_metrics_baseline.json` before deleting it

Applying (g) to itself: the PK-resident `bd_metrics_baseline.json` was deleted at
805, but only AFTER its non-regenerable half was moved here. The numbers were
always regenerable (`bd-ratchet --baseline` re-derives them live, and `bd-boot`
seeds `~/.bd_metrics_baseline.json` every session); the REASONING was not. It is
preserved verbatim in substance below.

**Declared at v3.66.755. `spa_wired` re-baselined 424 -> 422.**

> The 424 was a declaration-time number that NEVER matched the tool's from-disk
> count: the shipped 754 zip measures **418** via `gui_parity_inventory.build()`.
> Apples-to-apples (same tool, both from disk): 754 zip = 418, 755 work tree =
> 422 -- i.e. the singleton-wiring cut ADDED 4 (`live/parse_url`,
> `queue/dead_letter`, `queue/dead_letter/requeue`,
> `sites/<sid>/heuristic/fingerprint`). The metric moved UP, not down; the
> apparent -2 was an artifact of comparing against the never-true 424. 422 is the
> honest post-cut floor.

The 755-era baseline values, kept as a historical anchor (NOT live figures --
`bd-ratchet` is authoritative; at 805 the live seed reads
`unwired_operator_endpoints` 223 / `defect_DP_total` 2278 / `coupling_ratio`
0.397 / `spa_wired` 442):

    unwired_operator_endpoints  237
    defect_DP_total            2227
    coupling_ratio             0.399
    spa_wired                   422

**Why this is judgment and not a fact table.** A declaration-time number that was
never measured by the tool is a lie that ratchets forward: every later comparison
inherits it, and a metric moving UP can present as a REGRESSION. The rule: a
ratchet floor must be produced by the SAME tool, reading from the SAME place, as
the check that will later enforce it. Never declare a baseline by hand from a
number you did not just measure -- and when re-baselining, state which of the two
numbers was never true, or the next reader reconciles a delta that does not exist.
This is the same shape as `RATCHET_INCIDENT_706` (the ratchet read a lie for three
cuts) and it is why `bd-boot` now seeds the baseline live rather than shipping one.
