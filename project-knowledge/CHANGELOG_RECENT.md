<!-- verified-against: v3.66.732 -->
<!-- generated: CHANGELOG_RECENT is a SLICE, not the source of truth -->
# CHANGELOG (recent 20 releases) -- v3.66.732 .. v3.66.753

## THIS IS NOT THE CHANGELOG. IT IS A SLICE OF IT.

The full CHANGELOG (17,784 lines, 1.5 MB) **ships in the release zip** at `CHANGELOG.md`
and is what every tool reads (`bd-changelog`, `bd-versync`, `bd-lineage`, `bd-release-note`,
`bd-coretest`, `bd-ready` all resolve `join(work, "CHANGELOG.md")`). **Nothing reads the
project-knowledge copy.**

It was removed from project knowledge at 754 for two reasons, and the second matters more:

1. It was **31% of the entire PK** (1,521,397 of 4,912,962 bytes) -- always-on context spent
   on history no tool consults.
2. **It had DRIFTED.** The PK copy was stale at v3.66.748 while the release carried 749-753.
   Same document, two channels, only one maintained -- **KB_JUDGMENT (j)**, the exact shape
   that also left a disproved root cause alive in the PK's `Backlog.md` after it had been
   deleted from the pack's. A stale mirror is worse than no mirror: it answers.

**If you need history: read `CHANGELOG.md` in the work tree.** That is the only copy.
This slice exists so a session has recent context without paying for all of it, and it is
REGENERATED from the tree's copy at each handoff -- never hand-edited.

---

## v3.66.753 - dark residual ADJUDICATED: a11y split, thumbs CLOSED, vpn deferred

The last three clusters of the DARK_CONTROL_SCOPE list are closed out.
Deliverable: DARK_CLUSTER_ADJUDICATION_v3_66_753.md (every verdict
derived from source read in-session, evidence cited).

A11Y -- SPLIT (the operator-vs-dev call, made on evidence):
- plain_language WIRED (spa_wired 425 -> 426; dark 108 -> 107): inline
  PlainLanguageHint on the NeedsReview message block -- the one place
  the input (a raw error string) exists on an operator's screen. Rule-
  based, no AI gate needed. Honesty rule in the component and its test:
  the endpoint RETURNS THE ORIGINAL on no pattern match, and
  plain == original renders as "no simpler phrasing is available",
  never as the same text presented twice as an explanation.
- audit + contrast CLOSED (dev-surface): an arbitrary HTML blob and a
  CSS color pair are inputs of a UI under construction; no operator
  workflow holds either (pack_H ships the dev a11y toolchain). Ledger
  classification CLOSED-DEV; no panel built for a workflow that does
  not exist.

THUMBS -- CLOSED, decision doc delivered (not a wiring cut):
- All three thumbs/* endpoints take body["path"] STRAIGHT INTO ffmpeg
  with no root confinement -- an arbitrary-file-read surface -- and
  duplicate the already-wired, hid-keyed thumbnail_sheets family whose
  paths resolve server-side from history rows. Classified CLOSED with
  provenance (contact_sheet was phantom-wired until the @752 matcher
  fix). Whether to DELETE the routes outright is left as an operator
  decision (route deletions trip the route-map baseline + the overlay
  never deletes).

VPN -- unchanged: deferred to F5; a leak-test button reporting against
a tunnel the launch path does not route through is worse than no button.

Scoreboard: 6 of 8 clusters wired (queue_templates, cookie_clipboard,
search+semantic, knowledge/notes, ai, a11y-operator-half), 2 closed
with justification (thumbs, a11y-dev-half), 1 deferred on F5 (vpn).
The dark residual is fully classified; probe UNKNOWN holds at 129 with
the new call site judged OK.

## v3.66.752 - dark ai cluster wired (3 endpoints); reachability tail-matcher hardened

Open item #3, fifth dark cluster -- and the wiring surfaced a matcher
soundness hole, fixed in-cut (the 743 pattern: a wiring cut exposes the
gate that was supposed to see it).

WIRED (spa_wired 422 -> 425, exactly the 3; probe UNKNOWN holds at 129
with all three new call sites judged OK, no fixture changes needed):
- AiHelpersPanel (AiAssist): ai/classify + ai/normalize_resolution, the
  two PURE helpers. Gated on the already-wired ai/status -- disabled
  with the reason, never fired into a disabled backend. normalize's
  `via` provenance is rendered: ok:true + resolution:null is a REAL
  "no-match" answer said out loud, never blank success. Empty inputs
  never fire.
- AiReanalyzeSection (NeedsReview rows): sites/<sid>/ai_reanalyze.
  PLACEMENT DEVIATES from the scope doc (SiteActions) deliberately: the
  endpoint requires the url to be a job the runner holds (404 "url not
  in queue") and its own context string names the needs_review moment
  -- so it mounts where sid+url+failure are already on screen, no URL
  picker. had_screenshot=false is said out loud (text-only analysis is
  a weaker answer); suggestions render as advisory proposals.

REACHABILITY MATCHER (endpoint_reachability._reaches, the gate fix):
- Wiring ai/classify would have phantom-certified retry_policy/classify
  as reachable -- the 1-segment tail rule matched the shared /classify
  suffix. Pulling that thread mechanically (blast-radius derivation of
  every endpoint wired ONLY via the 1-seg tail) found 10, and caller
  adjudication found ALL TEN phantom: comments containing /<token_id>
  certified shares/<token_id>; five GET */diagnose callers certified
  POST doctor/diagnose; and the real thumbnail_sheets caller's
  /contact_sheet/ substring certified the CLOSED raw-path thumbs family
  as operator-reachable -- a dark, un-adjudicated attack surface the
  ledger reported as wired.
- Fix, RED-first via two new selftest NEG rows (failed on pristine with
  TypeError->logic, pass after): (1) a final segment appearing on more
  than one url_map rule (ANY method) is not distinctive and never
  certifies alone; (2) the tail occurrence must be TERMINAL -- followed
  by more path it is a different endpoint's URL. Both guards fail
  toward DARK (visible nuisance beats invisible laundering).
- Ledger re-derived: dark 102 -> 108 (my 3 leave dark as real wirings;
  9 pre-existing phantoms enter honestly). All 9 classified with their
  phantom provenance; thumbs/contact_sheet carries the CLOSED-PENDING
  decision-doc note. The ratchet base moves UP because the old number
  was measured with a matcher that could not see -- a floor derived
  from a blind scan was never a floor (the 743 lesson, verbatim).

## v3.66.751 - dark knowledge/notes cluster wired (3 endpoints)

Open item #3, fourth dark cluster. KnowledgeNotesPanel on SiteSettings
(beside CookieClipboardPanel, the 735 precedent): list/filter, add, and
delete operator failure notes (pattern -> resolution lore the runbook
surfaces next to failed rows).

WIRED (spa_wired 419 -> 422, exactly the 3; reachability dark 105 -> 102,
re-pinned via --declare-reach):
- GET list: the {notes: [], error} 500 shape is RENDERED as an error --
  a broken store must never read as an empty one.
- POST add: submit disabled until pattern AND resolution present (the
  endpoint's real 400 -- never fire a doomed POST). `kind` is a
  CONSTRAINED select over the DERIVED vocabulary {failure|login|
  rate_limit}: nothing in the backend branches on kind (verbatim SQL
  filter only); the set comes from add_note's docstring reservation.
  Free text becomes meaning-wrong the day a consumer branches.
- DELETE: confirm-gated (destructive to future failure matching).
- The library item notes endpoint is a DIFFERENT store and deliberately
  NOT merged (identifier-masquerading risk) -- scoped to Library.

BODY-CONTRACT: calls artifact regenerated (253 -> 255 mutating call
sites); fixtures extended so both new sites are JUDGED, not UNKNOWN --
per-path PATH_VALUES for pattern/resolution/kind (both names collide
with different semantics elsewhere: rights URL globs, interop kinds) and
a real knowledge note row so the int-typed DELETE probes a live id.
UNKNOWN holds at exactly 129; DEAD 0; HARNESS-FAULT 0.

TWO SCANNER FINDINGS (one fixed in-cut, one for backlog):
- A conditional template literal for the GET made the call INVISIBLE to
  the gui_parity scanner (wired 2-of-3); restructured to the recognized
  literal-ends-at-? shape (useHistoryData precedent).
- The scanner harvests /api literals from RAW TEXT INCLUDING COMMENTS: a
  prose comment naming the library-notes path WIRED that endpoint (+1
  phantom). Comment reworded in-cut; the scanner defect (code/prose
  indistinguishable to the harvest) is logged for backlog.

## v3.66.750 - body-contract fixture isolation: the world includes global config

- The 729 ratchet's +1 flap tolerance is DISCHARGED and the baseline is
  TIGHTENED 130 -> 129. The documented root cause (setup_site site-id
  collision) was WRONG: the endpoint is order-independent (double-post
  200/200). The real channel was _app_cfg -- probe_fixtures
  snapshot/restored s_cfg but not global config, and a replayed settings
  probe left path_allowlist = ["x"] in the module dict, so run 2's
  scratch download_dir failed validation and setup_site flapped
  OK -> 400/UNKNOWN (129 first run, 130 after; 5 of 5 stash captures).
- Fixes: Fixtures.build() pins path_allowlist to the scratch home and
  snapshots _app_cfg; ensure() restores that baseline AND drops
  probe-created sites (the originally documented hygiene, still real);
  probe_fixtures snapshot/restores _app_cfg exactly as it does s_cfg.
- Order-independence proven MECHANICALLY before tightening: full probe
  run twice in one process under the worst case (app imported early,
  outer-home seeding) -> 129/129, identical UNKNOWN sets, setup_site OK
  in both, process-wide _app_cfg restored afterward. New tests:
  test_verdicts_are_order_independent_across_probe_runs +
  test_ensure_resets_global_config_and_drops_probe_created_sites
  (the latter is the RED anchor; the former alone is satisfiable by two
  identically-poisoned runs).
- App code untouched this cut (tools/ + tests/ only).

## v3.66.749 - secret_scan skip set DERIVED from the manifest canon

- The "not source" canon (_MANIFEST_EXCLUDE_DIRS) moved to dev_suite/_common;
  release_lint re-exports the name unchanged (zero new import edges).
- audit_security._SECRET_SKIP_DIRS is now DERIVED (canon | tests,
  sast_results, dast_results) instead of a re-typed literal that had
  silently drifted 7 dirs behind (.hypothesis, state, results, profiles,
  screenshots, .pytest_cache, .mypy_cache). On stash the scanner was
  regex-reading every .json/.txt line of exactly the runtime accretion
  the canon already declared out of scope -- the same unpruned-walk
  disease 742 fixed for bat_lint/sh_lint, plus the KB_JUDGMENT (f)
  mirror shape: any future canon addition is now picked up structurally.
- RED-first: tests/test_v3_66_749_secret_scan_skipset_derived.py failed
  on pristine on exactly the 7 drifted dirs (set-relation + descend-time
  behavioral proof).
- NOT touched, with reasons derived from source: sh_lint was already
  fixed at 742; lockfile_scan is a flat non-recursive listdir of the
  temp root; mem_audit does no filesystem I/O at all (its cost is heap
  introspection, which is its job). Forcing the walk-prune pattern onto
  those would be rigor about the wrong subject (KB_JUDGMENT e).

## v3.66.748 - CSRF column DERIVED from the hook (audit R11-13); .hypothesis forbidden (R18)

The two remaining round-2 audit findings. R11-13 was its highest-severity item,
and the fix it asked for is a test, not a patch.

THE DRIFT. _check_csrf gates ("/api/", "/cockpit/api/"). The scanner every
artifact calls (build_endpoint_catalog._csrf_fires_for) had RE-TYPED that rule
and never learned the second prefix, so 28 cockpit write endpoints were
PROTECTED by the app and reported csrf:false by ROUTE_INDEX, ENDPOINT_CATALOG,
and -- the one that SHIPS -- the OpenAPI spec at /api/openapi.json, which
therefore omitted the required X-CSRF-Token header and the 403 response for
them. A generated client breaks on its first cockpit write; a reviewer reads
the spec and concludes cockpit is unprotected. It is not.

THE PART THAT MATTERS MOST (R13): because the artifact already said false,
someone dropping /cockpit/api/ from the hook would change NO artifact and trip
NO gate. The mirror being wrong in the SAFE direction had burned down the alarm
for a move in the DANGEROUS one.

DON'T MIRROR -- DERIVE:
- app.py now exports the route-level CSRF policy as the single source of truth:
  CSRF_TRIPPING_METHODS, CSRF_GUARDED_PREFIXES, CSRF_EXEMPT_PATHS, and the one
  predicate csrf_fires_for(). _check_csrf READS them (the prefix tuple and the
  pair/redeem exemption are no longer inlined -- there is nothing left to copy).
- build_endpoint_catalog binds to the APP'S OBJECTS (lazily: a module-scope
  import would beat _import_app's BD_DISABLE_KEEPALIVE=1 and spawn the very
  subsystems that tool exists to keep asleep) and _csrf_fires_for DELEGATES to
  the app's predicate.
- Artifacts regenerated: ROUTE_INDEX (28 csrf flips, all cockpit, all
  false->true -- matching the audit's count exactly), ENDPOINT_CATALOG, and
  openapi.json (28 cockpit write ops now declare X-CSRF-Token + a 403).
- New gate: tests/test_v3_66_748_csrf_scanner_derives_from_hook.py. Proves the
  derivation TWO ways: structurally (the scanner's constants ARE the app's
  objects -- identity, not equality, so a fork cannot even be expressed) and
  BEHAVIOURALLY (for every mutating route on the live app, drive the REAL hook
  with a cookie session and no token and compare the actual 403 to the
  scanner's verdict). The behavioural half is the alarm R13 asked for: it fails
  the day the hook and the scanner disagree. Plus a can-it-fail control.
  NO app behaviour change -- the app was always right.

R18 -- .hypothesis/ (and the audit's one wrong assumption):
- 27 .hypothesis/ entries were SHIPPING while the forbidden-artifact gate
  reported clean: its denominator did not contain them (0 of 27 flagged). The
  signature failure of this program, once more.
- FIXED at both ends: .hypothesis excluded from the release manifest
  (dev_suite._MANIFEST_EXCLUDE_DIRS -> keeps it OUT of the build) and added to
  diff_release_zips.FORBIDDEN_SEGMENT (-> if it ever returns, the gate SAYS so
  instead of reporting clean over it).
- The audit said this needed a guard-SHA re-declaration because build_release.py
  is guard #7. It did NOT: the forbidden lists live in diff_release_zips.py and
  the exclusion set in dev_suite/release_lint.py, neither of which is
  guard-pinned. Verified against bd-guardcheck's 7-file list BEFORE editing.
  All 7 guard SHAs remain byte-identical.

## v3.66.747 - exec-bridge option-injection guards (audit round 2, R14)

tool_bridge is a strong control (no shell, resolved argv0, path containment,
minimal env, 30s timeout, 256KiB cap) with one gap the round-2 audit found: a
POSITIONAL whose value looks like an option passed straight to the tool, because
build_argv appended positionals with no `--` separator and the url str-positional
had no leading-dash / scheme guard. build_argv("yt-dlp", {"url": "--evil"}) ->
['yt-dlp', '--evil']. Not shell injection (the no-shell defense holds) -- OPTION
injection into the tool itself (yt-dlp options can write files, run
post-processors, read option files). CSRF-gated + same-origin, so
defense-in-depth, not a remote hole -- but the bridge's whole premise is
"paranoid, centrally."

Three guards, belt-and-suspenders:
- build_argv now inserts a `--` end-of-options separator before positionals
  (only when there are any). Everything after `--` is a positional operand the
  tool cannot parse as an option.
- str positional values that begin with '-' are REFUSED at validation (400),
  not merely fenced.
- The url positional gained an opt-in scheme allowlist (["http","https"]),
  mirroring the rigor the `path` type already has: file://, ftp://, javascript:
  and non-URL values are refused.

New gate: tests/test_v3_66_747_exec_bridge_option_injection.py -- the case the
717 suite lacked (its denominator was "unknown flag KEYS"; the gap was "known
positional VALUES"). RED-first: 4/4 failing on 746 source. The existing 717 +
719 + 730 bridge suites stay green (the `--` separator does not disturb the
ffprobe path positional). tool_bridge is not guard-pinned; no SHA
re-declaration.

## v3.66.746 - L34 gates the operator surface; diagnostics are advisory

The 745 stash capture proved L34 finally WORKS -- 8 workers + triage + serial
re-confirmation probed 423 routes and correctly surfaced /api/dev/bat_lint as
a real >8s route -- and it still FAILed the deploy, for the program's oldest
reason: the wrong denominator. Of 122s total sweep cost, 79s (65%) was the
/api/dev + /cockpit/api DIAGNOSTIC surface: 202 routes, 28 of 41 slow ones.
bat_lint, lockfile_scan, sh_lint, secret_scan, mem_audit are INTROSPECTION
endpoints that do real scan work by design. Failing an operator deploy because
a dev tool's filesystem walk takes 9s asserts the wrong thing at the wrong cost.

L34's own docstring calls it "the natural post-deploy gate" -- for the OPERATOR
surface. So partition it:

- OPERATOR routes (not /api/dev, not /cockpit) are the HARD GATE: a 5xx or
  unreachable there FAILs, exactly as before. In-sandbox that is 265 of 524
  param-free GET routes; on the capture ~220 routes at ~196ms = ~43s of work,
  which fits phase 1 at 8 workers with no UNPROBED.
- DIAGNOSTIC routes are still SMOKED -- in a labeled ADVISORY pass on leftover
  wall, at the triage budget, that REPORTS slow/5xx/unreachable dev routes
  (named, counted, in both log and verdict tail) but does NOT fail the deploy.
  Dropping them would be the "shrink the denominator to hide a finding" sin
  this program exists to prevent; reporting-but-not-gating is the honest scope.

The advisory pass never eats the operator gate's wall (it runs only after, only
with _left() budget) and never blocks on a hung dev route (short per-probe
timeout). bat_lint stays visible -- it is a real slow route -- it just no longer
sinks the operator deploy gate.

- New gate: tests/test_v3_66_746_l34_operator_gate.py -- a clean operator
  surface behind a wall of hanging diagnostics PASSES while still naming them
  (RED-first); an operator 5xx still FAILs; no operator UNPROBED.
- tests/test_v3_66_737: the "genuinely slow route still FAILs" case moved to an
  operator route (its old /cockpit route is now correctly advisory), with a
  companion pinning the diagnostic case as advisory-but-visible.

FOLLOW-UP (product, not harness): bat_lint and lockfile_scan/sh_lint are
genuinely slow filesystem walks. 742 pruned bat_lint's walk denominator; the
same prune likely applies to lockfile_scan/sh_lint/secret_scan. Tracked for a
product cut -- now surfaced as advisory findings instead of a deploy-blocker.

## v3.66.745 - L34 workers 4 -> 8 (reverses my 741 decision, on stash evidence)

The 744 stash capture: triage fixed the slow-route starvation AND the sweep
still missed its deadline -- 346/523 UNPROBED. The log's own numbers close
the case: 157 probed routes sum to 66s of real latency (mean 420ms on a
loaded box; sibling live checks hammer the app during the sweep), 66/4
workers = 16.5s, plus 20 triage timeouts x 5s / 4 = 25s -- ~43s against the
39.6s phase-1 share. Meanwhile ALL 20 suspects RECOVERED serially in ~14s
(including /api/dev/bat_lint at 4.1s -- the 742 prune verified on stash; the
733 "five 8s routes" were contention ghosts).

So the constraint has moved: with triage (744) capping a suspect's phase-1
cost at 5s and serial re-confirmation clearing healthy routes at their real
latency, contention artifacts are CHEAP -- and throughput is not. The 741
workers cut treated the artifact symptom at the cost of the sweep. Workers
go back to 8: 66s of work is 8.3s at 8 workers; expected phase 1 ~25-30s
including contention-suspect timeouts, well inside the share, with the
reserve intact for recoveries.

- tests/test_v3_66_741_l34_phase1_bounded.py: workers pin updated 4 -> 8
  with the reversal rationale (a pin that moves must say why, with a capture).
- tests/test_v3_66_744_l34_triage_budget.py: NEW throughput control -- 64
  uniformly-moderate routes (0.25s) against the scaled phase-1 share fit at
  8 workers (2.0s) and starve at 4 (4.0s); proven RED on the 744 tree.
- The deadline + UNPROBED honesty remain the correctness floor: a
  pathological box still gets a truthful verdict, never an overrun.

## v3.66.744 - L34 phase 1 triages at a short budget (fixes the 743 regression I shipped)

THE 743 STASH CAPTURE: `[FAIL] L34 -- 372 route(s) UNPROBED (phase-1
deadline)`. The 741 deadline held (the honesty half worked) but the sweep
starved: phase 1 probed every route at the FULL 8s adjudication budget, so
each genuinely-slow route held one of the 4 workers for 8s -- one slow route
costs ~400 fast-route slots. 151 of 523 probed; 372 honestly UNPROBED. A
correct verdict about an incorrectly-budgeted sweep. My regression: 741
lowered the workers AND left phase 1 on the full budget; the sandbox test
missed it because its world was UNIFORMLY slow, a shape that cannot starve.

- NEW `_L34_TRIAGE_BUDGET_S = 5`: phase 1 probes at the short triage budget;
  a route that misses it is a SUSPECT, and phase 2 re-probes suspects
  serially at the full `_L34_ROUTE_BUDGET_S` inside the reserve. Triage
  decides who gets adjudicated; it never issues the verdict. 5s is above the
  heaviest healthy view measured alone (4.1s @733) so a slow-but-fine route
  is not manufactured into a suspect.
- Deadline + UNPROBED honesty unchanged -- they are the correctness property;
  triage is what makes throughput fit inside them. Stash arithmetic: ~40
  worker-seconds across 523 routes at 4 workers = ~10s of a 39.6s phase-1
  share, with the full reserve left for adjudication.
- Fixed the R15 audit finding while here: the _L34_WORKERS comment asserted
  "the wall clock tracks the SLOWEST route rather than the sum" -- only true
  when every probe is cheap; rewritten with the real cost model (K*B/W).
- New gate: tests/test_v3_66_744_l34_triage_budget.py, modeling the BIMODAL
  stash shape (12 hanging routes ahead of 60 fast ones; starvation is
  arithmetic, not scheduling luck). RED proven mechanically: the
  bd-mutation-test row L34/triage-starvation@744 re-plants the full budget in
  the phase-1 submit and the gate goes RED (CAUGHT 1/1). An earlier draft of
  the test produced a FALSE RED (its assertion matched the literal "0
  unprobed" summary token) -- caught by hand-verifying the mutation survivor,
  which is the reason that rule exists.

## v3.66.743 - dark search cluster wired (7 endpoints); body-contract denominator fixed

Open item #3, first cut of the dark CONTROL clusters. The `search` + `semantic`
families (7 endpoints, re-scoped from the triage's 4) get a GUI, and wiring them
surfaced a stale gate denominator that had been hiding real defects.

WIRED (spa_wired 412 -> 419, exactly the 7, nothing else moved):
- SiteSearchPanel (Queue): search/sites_available (capability READ),
  search/site, search/all. The load-bearing control is the READ: both search
  families degrade at HTTP 200 ("search_extractor unavailable", ok:false), so
  the panel reads the capability signal and DISABLES rather than firing a
  doomed POST. Shadow-guarded against history /api/search (GET-args, a
  different job already on History.tsx).
- SemanticSearchPanel (Templates): semantic/status, semantic/search,
  semantic/reindex. status's `indexed` count is rendered always -- an empty
  index is said out loud, never passed off as "no matches"; reindex is
  single-fire (disabled while pending).
- SearchFacetsStrip (History): search/facets, breaking the FTS match count
  down by site and status.

BODY-CONTRACT DENOMINATOR (the gate this cut fixed):
- frontend/scripts/body_types.mjs ran with a RELATIVE ROOT, so tsconfig handed
  createProgram relative root-file names and the walk's includes("/src/")
  filter silently dropped every root file. The committed BODY_CONTRACT_CALLS.json
  held 126 call sites of a real 253 -- the gate had been half-blind since it
  shipped. Fixed ROOT to absolute; regenerated the artifact (126 -> 253).
- That exposed one genuine DEAD control: useJsonapiProbe sent {url} to
  /api/jsonapi/probe, which reads `site_root` -- every probe 400'd while the
  wiring ledgers scored it WIRED (the 724/726 class). Fixed to {site_root}.
- And one real robustness defect: /api/fed/pending_review did int(id) inside
  its try, so a client-typo'd id 500'd instead of 400ing. Now validated ->
  400. RED-first test: tests/test_v3_66_743_fed_review_id_400.py.
- Fixture-world repairs (all mechanism #4/#5 from the 729 soundness guard):
  stub load_urls now returns the real 3-tuple (was int -> 500); fixture site
  gains a cookie_file; per-path semantic `text` value for cookie_clipboard
  (a jar, not a URL list). UNKNOWN ratchet re-baselined 64 -> 130 against the
  now-complete denominator (every new UNKNOWN is a previously-invisible call
  site; +1 covers a pre-existing setup_site fixture-isolation flap, documented).

New FE gates: SiteSearchPanel.test.tsx (7), SemanticSearchPanel.test.tsx (5),
SearchFacetsStrip.test.tsx (2). All RED-first.

## v3.66.742 - bat_lint / sh_lint walk only the tree they certify

The one CONFIRMED >8s-alone route of the 740 capture: /api/dev/bat_lint.
Root cause: `_repo_root().rglob(...)`. The release zip's tree is ~2.5k
files, but the lints run against the INSTALL DIR on stash -- which accretes
everything the zip never ships: the service venv, frontend node_modules,
__pycache__, and overlay orphans (the overlay never deletes). rglob walked
all of it to find a handful of scripts. The route was slow because its
denominator was the wrong tree.

- New `_iter_source_files()`: an os.walk that never DESCENDS into
  `_MANIFEST_EXCLUDE_DIRS` (a post-hoc filter would still pay the walk).
  Both bat_lint and sh_lint use it; the lints and the manifest verifier now
  certify the same "source" denominator under one declared set
  (`_LINT_WALK_EXCLUDE_DIRS is _MANIFEST_EXCLUDE_DIRS`, pinned by test).
- Deliberately NOT touched: zip_manifest_check keeps its full walk -- its
  job is spotting files that should not be there; pruning it would create a
  blind gate.
- Not just a speedup: a .bat inside venv/ is runtime accretion, not an
  operator-authored script; flagging it was a false subject.
- New gate: tests/test_v3_66_742_lint_walk_pruned.py (RED-first: 3 of 4
  proven failing on pristine source; the depth-preservation control passed
  throughout).
- Expected on-stash effect: /api/dev/bat_lint leaves the L34 exceeded list.
  If it does not, the residual cost is not the walk -- re-derive.

## v3.66.741 - L34 phase 1 honors the reserve it declared (workers 8 -> 4)

The 740 run's own number: 24 of 25 suspects UNCONFIRMED. The wall-aware fix
at 740 bounded phase 2 only -- `_L34_PHASE2_RESERVE = 0.45` was declared with
a comment ("fraction of the wall held back for re-confirmation") and NEVER
REFERENCED. Phase 1 ran an unbounded pool.map over every route; phase 2 got
the scraps. A reserve nobody enforces is a comment, not a budget.

- Phase 1 now runs under a deadline of `wall * (1 - reserve)`. At the
  deadline it cancels everything not-done in ONE sweep (a lazy per-iteration
  cancel races the pool's own queue-draining and never cancels anything --
  measured: the full 8s sweep ran to completion against a 1.5s wall). The
  <= workers in-flight probes drain within a single route budget, so the
  overshoot is bounded by one probe, never by the sweep.
- Routes phase 1 could not reach are UNPROBED -- named in the log, named in
  the verdict, and FAILING. Unknown is a third state; a deadline that
  silently shrank the denominator would be the blind-gate shape with a clock
  for a denominator. Every route lands in exactly one bucket:
  ok / confirmed / recovered / UNCONFIRMED / UNPROBED.
- `_L34_WORKERS` 8 -> 4. The 8-way fan-out manufactured the very suspects
  phase 2 then had no budget to clear (37 false suspects at 734, 25 at 740,
  nearly all contention artifacts; each costs a full serial probe budget to
  clear). Halving the fan-out roughly doubles phase-1 wall cost but starves
  phase 2 of artifacts -- which is where the budget actually went.
- New gate: tests/test_v3_66_741_l34_phase1_bounded.py (RED-first: 3 of 4
  proven failing on pristine 740 source; the fast-clean-app control passed
  throughout).

## v3.66.740 - L34 budgets itself against the harness wall (fixes the 737 regression)

- FIXES A BUG I SHIPPED AT 737, caught by the 737 capture: L34 TIMED OUT after 90s and
  leaked its thread AGAIN. 737 added serial re-confirmation (to strip the contention
  734's own 8-way fan-out was creating) and made it UNBOUNDED: ~37 suspects x 8s
  = ~300s against a 90s wall. The fix for the contamination reintroduced the timeout the
  contamination fix was built on top of. Two releases, same failure mode, opposite causes.
- THE REAL DEFECT, which neither 734 nor 737 addressed: L34 was never WALL-AWARE. The
  harness gives each check 90s and on expiry ABANDONS the thread -- which keeps smoking
  all 523 routes underneath L31 (memory) and L33 (leak scan) and poisons their readings.
  A check that CAN run past the wall does not merely fail, it corrupts the checks after
  it. Any check whose cost tracks a growing denominator (523 routes and climbing) will
  cross the wall eventually; bounding it is the correctness property, not an optimisation.
- L34 now watches the clock (_L34_WALL_S = 72s of the harness's 90s). Phase 2 re-confirms
  suspects only while budget remains. Whatever it cannot reach is UNCONFIRMED -- which is
  UNKNOWN, and unknown FAILS, named in the verdict. Never silently dropped, never assumed
  innocent. It cannot overrun, so it cannot leak the thread, so L31/L33 stop being
  measured underneath it.
- Mutation-tested: removing the wall check, and silently dropping unconfirmed suspects
  instead of failing on them, each turn tests red.


(TODO changelog body)

## v3.66.737 - L34: a concurrent probe must not report its OWN load as a finding

- FIXES A BUG I SHIPPED AT 734, caught by the 735 capture. 734 made L34 concurrent so
  it could finish 523 routes inside the harness's 90s wall. It finished -- and reported
  37 of 523 routes over the 8s budget. That number was CONTAMINATED.
- Measured serially in the 733 capture: /api/community_scrapers/index answers in 4090ms,
  /api/data/kb_analytics in 3613ms. Both appeared in 735's ">8s" list. A route that
  answers in 4.1s when probed ALONE is not an 8s route -- it doubled because an 8-way
  fan-out put load on the very app it was measuring. The instrument changed the thing it
  measured and reported its own contention back as a defect. That is the shape
  KB_JUDGMENT already names ("the fan-out WAS the slowdown; fan-out is a bet on cores you
  have") -- committed while fixing a different instance of it.
- FIX: phase 1 (concurrent) is TRIAGE and yields HYPOTHESES, not verdicts. Every non-ok
  route -- exceeded, 5xx, unreachable alike -- is RE-PROBED SERIALLY against a quiesced
  app. Only a route that STILL misbehaves alone is reported. One that recovers is logged
  RECOVERED ("phase-1 flag was our own load, not a defect") and is NOT a finding; a probe
  that quietly discards its own false positives teaches nobody anything.
- The verdict now says "did not answer within 8s WHEN PROBED ALONE", so a reader can tell
  a real finding from a contention artifact.
- Phase 2 only re-probes SUSPECTS (one extra GET each), so it does not cost the serial
  sweep it replaced. Concurrency buys the wall clock; it does not buy a verdict.
- Mutation-tested in BOTH directions: skipping phase 2 (the 734 bug) turns it red, and
  making phase 2 a laundry that passes everything ALSO turns it red.


(TODO changelog body)

## v3.66.736 - build_release._load_exclusions no longer poisons the interpreter (GUARD RE-SHA)

- GUARD SHA DECLARED: tools/build_release.py e8142436 -> f7c220d2 (operator-authorized).
- The @730 finding, fixed at the source. _load_exclusions() did
  `sys.path.insert(0, root)` + `from bulk_downloader.dev_suite import ...` and NEVER
  undid either. Run against a temp tree -- which is exactly what
  tools/precut_check.py::_tree_files does -- it left that tree on sys.path AND cached
  `bulk_downloader` -> the temp tree's STUB __init__ in sys.modules for the rest of the
  interpreter. Every later import of bulk_downloader then resolved to the stub.
- That is why version-pin / settings suites FAILED IN THE BAND but PASSED STANDALONE:
  the band shares one interpreter and whoever ran precut_check first poisoned it. A test
  that passes alone and fails in company is the tell.
- Now a try/finally pops the inserted path and restores sys.modules to exactly what it
  was -- evicting what this import caused, and re-binding anything it replaced. Safe for
  the two callables returned: _manifest_excluded is pure and zip_manifest_check only
  imports zipfile at call time; dropping a module from sys.modules does not destroy the
  module object while a function's __globals__ still references it.
- test_precut_check.py's snapshot/restore workaround stays as belt-and-braces, but it was
  a CALLER defending itself against a CALLEE that corrupted global state -- every future
  caller would have had to remember.
- Both halves mutation-tested: removing the path-pop and removing the sys.modules restore
  each turn exactly one test red.


(TODO changelog body)

## v3.66.735 - cookie_clipboard CONTROL cluster wired (FE); save takes the RAW TEXT

- The 2 dark cookie_clipboard endpoints get a GUI: CookieClipboardPanel on the
  per-site settings page. POST /api/cookie_clipboard/parse and
  POST /api/cookie_clipboard/save/<sid> had a blueprint and no operator path;
  importing a browser cookie jar meant curling the API.
- The vocabulary is DERIVED from app_cookie_clipboard.py. /save/<sid> RE-PARSES the
  raw text itself and reads nothing else from the body. The obvious "efficient"
  wiring -- parse once, then POST the parsed cookies -- sends a body the endpoint
  never reads: text would be "", the re-parse would yield nothing, and every save
  would 400 "could not parse any cookies" while the on-screen preview showed a
  perfect parse. A negative control pins that save sends the raw text; mutation-
  tested (swapping in the parsed cookies turns it red).
- SECRETS: cookie values are session tokens. auto_detect_and_parse returns them and
  the preview NEVER renders one -- name/domain/flags only, value shown as "<N chars>"
  with no reveal affordance anywhere. Mutation-tested.
- Save overwrites the site's jar and gets a Tier-A confirm; parse is read-only and
  gets none. Both buttons are disabled on empty text (the backend 400s).
- The panel does NOT pre-gate Save on cookie_file: that key is secret-classed and
  excluded from the editable surface on purpose (app_settings_center.py), so the SPA
  genuinely cannot see it. Rather than inventing a read to get around that exclusion,
  the backend's 400 reason is surfaced INLINE and persists -- it is an instruction
  ("no cookie_file configured"), not a notification that should flash past in a toast.
- Reachability ledger re-pinned in-cut: dark 111 -> 109.


(TODO changelog body)

## v3.66.734 - L34 full-route-smoke: EXCEEDED is a finding, not a story

- L34 FAILED on the 732 and 733 stash captures with "TIMEOUT after 90.0s -- thread
  leaked". The budget was fully accounted: 398 routes cost 7.5s (mean 19ms), 28 slow
  routes cost 42.3s, and FIVE routes cost 40.0s -- 44% of the wall -- by each burning
  the full 8s per-route timeout. It reached 426 of 523 routes.
- L34 logged each of those five as "TIMEOUT <rule> (likely a stream)" and returned
  WARN. That cause was ASSERTED, not observed, and it was wrong every time: all five
  are plain `return jsonify(...)` views (capture_diagnostics, replay_validation,
  housekeeping/preview, autonomy/queue, autonomy/notifications), and four are
  operator-facing (Report Center sections / cockpit views). The label hid an
  operator-facing performance defect for at least two releases.
- FIX: a route that blows its per-route budget is now EXCEEDED -- a third state that
  names NO cause and FAILS, naming the offending routes. A route that streams forever
  must be DECLARED in _L34_STREAMING_SKIP; an undeclared one that cannot answer is a
  defect or an undeclared stream, and only a human can say which. Unknown fails.
- FIX: the sweep is now concurrent (ThreadPoolExecutor, 8 workers; probes are
  I/O-bound and the app is threaded). 523 routes could not fit the harness's 90s wall
  serially and the route count only grows -- raising the ceiling is a treadmill. This
  also ends the thread leak, which had been smoking routes underneath L31/L33 and
  poisoning their readings (L31's "possible memory leak" is unproven as a result).
- The five slow routes are NOT added to the skip set -- that would delete the finding,
  and test_u44's test_skip_set_only_contains_streaming_routes already forbids it. A new
  test names all five so a future session cannot quietly silence them.
- All three new controls mutation-tested: restoring the "(likely a stream)" label,
  downgrading EXCEEDED to WARN, and reverting to a serial loop each turn exactly one
  test red.


(TODO changelog body)

## v3.66.733 - queue_templates CONTROL cluster wired (FE); mode rides the query string

- The 3 dark queue_templates endpoints get a GUI: QueueTemplatesPanel on /queue.
  GET/POST /api/queue_templates, GET/PUT/DELETE /api/queue_templates/<tid>, and
  POST /api/queue_templates/<tid>/apply/<sid> had a blueprint and no operator path;
  saving a reusable URL set meant curling the API.
- The vocabulary is DERIVED from app_queue_templates.py, not invented. `mode` is a
  QUERY PARAM (request.args), not a body field: a body {"mode":"replace"} would be
  accepted, return 200 ok:true, and silently APPEND. A negative control pins mode to
  the query string and was mutation-tested (moving it into the body turns it red).
- mode=replace clears the target site's queue first (queue_delete_site + jobs.clear)
  and gets a Tier-A confirm; append is additive and gets none. Only append/replace are
  offered (any other mode 400s). Create is gated on a non-empty name (backend 400s on
  empty); Save is gated on dirty (a no-op PUT returns ok:false having done nothing).
- Reachability ledger re-pinned in-cut: dark 114 -> 111.


(TODO changelog body)

## v3.66.732 - the 7 dark cockpit views get navigation: dark=0, orphans=0

The cockpit is ONE Flask route (/cockpit) rendering its views client-side: the
router dispatches PAGES[p](), so the renderable view set is the PAGES registry.
A view in PAGES with no <a data-p="..."> anchor and no REDIRECT alias is
UNREACHABLE -- fully implemented, renders fine, and no operator can ever get to
it. bd-gui-surface (rebuilt @729, after "44 views" turned out to be folklore)
derived SEVEN such views. None were stubs.

Wired, with anchors placed where each view belongs:
  Home     -- daily (Daily Mission), inbox (Inbox)
  Reports  -- orghealth (Org Health), maturity (Maturity), complexity (Complexity)
  Advanced -- advlanding (Advanced Overview), at the TOP of the drawer
  System   -- syslanding (System Overview), at the TOP of the drawer

advlanding and syslanding are the sharpest case in the set: their entire body is
nav cards pointing at other views. They are NAVIGATION HUBS THAT NOTHING
NAVIGATED TO. They now open their own drawers.

Surface: 133 renderable views, nav anchors 83 -> 90, 61 redirect aliases,
dark 7 -> 0, orphan anchors 0.

New suite tests/test_v3_66_732_cockpit_dark_views.py pins dark == 0 and orphans
== 0 GENERALLY -- not just for the seven -- so a future view added with no way in
fails the gate. It also guards the premise (the seven must remain REGISTERED:
deleting a view is not a way to make it reachable).

A NOTE ON THE TEST ITSELF: its first draft matched redirects as REDIRECTS['key']
lookups. The real source shape is a single object literal, const REDIRECT = {...},
so the pattern matched ZERO aliases and the test reported 50 dark views instead
of 7. The denominator was wrong, so the verdict was wrong -- the exact shape this
programme exists to catch, committed inside the test written to catch it. Caught
only because bd-gui-surface disagreed. Two independent derivations now converge
on 133/83/61/7, and the fixed test agrees with the tool exactly.

tools/cockpit_console.py only; no route change, no FE (SPA) change, no backend change.

