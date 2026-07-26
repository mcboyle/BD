<!-- verified-against: v3.66.464 -->
# BulkDownloader -- GLOSSARY / lexicon

The project's working vocabulary. The handoffs, tracker, and CHANGELOG are jargon-dense;
this is the decode ring so a fresh session can parse them fast. Static (version-agnostic);
definitions reflect the @464 codebase. Where a term names a tracker item, the live status is
in `TASK_TRACKER`, not here.

## Capture & redaction

- **capture / capture session** -- an authenticated, operator-driven browser run that records
  a site's login -> play -> download flow. Produced via the noVNC / sentinel / manual operator
  path, NOT a background Flask subprocess (Playwright sync/async conflict).
- **WACZ** -- the Web Archive Collection Zipped file a capture is exported to (`wacz_export.py`).
  Carries the rrweb DOM log, the network log, and metadata.
- **rrweb** -- the in-page DOM-event recorder. `maskAllInputs:true` (Wave 2/F2) masks ALL input
  values; the older default masked only `type=password` (the email/text/hidden leak).
- **dom_recorder / dom_capture / capture_bodies / session_capture** -- the capture-side guard
  modules (rrweb injection, DOM snapshotting, response-body capture, session orchestration).
- **scrubber** -- the redaction pass (`capture_artifact_redact` / `redact_capture`) that replaces
  secret values with `<scrubbed>` placeholders.
- **floor / floor scan / floor scanner** -- `scan_floor_secrets`, the FINAL WACZ-export gate that
  fails loud (`WaczRedactionError`) on any residual secret. The "floor" = the minimum-cleanliness
  bar a capture must clear to be written. Never loosen the scanner; fix the scrubber instead.
- **capture_scrub** -- Matt's standalone offline redactor (`capture_scrub.py`, stdlib-only, no BD
  imports, no network). `--preview` "`0 planned` + `verify CLEAN`" is the authoritative clean signal.
- **F1 / F2** -- Track-F feature waves. **F1** = admission/robustness (bad-hours retry, disk-aware,
  cookie-expiry). **F2** = capture/redaction hardening (screenshot triage, input redaction, the
  floor, the live-capture HUD).
- **archetype** -- a site's capture/download pattern class (e.g. video.js + Cloudflare + HLS;
  Aylo/Project1Service API-download; inline direct-link). Recognition classifies captures into
  archetypes. Live captures define the archetype, never the prior fixture.
- **reptyle** -- the codename reference site used to exercise the whole capture -> template ->
  promote pipeline end-to-end (`REPTYLE_CAPTURE_RUNBOOK.md`). "reptyle-draft" = the template-
  inventory curation draft (a known-benign verify note).
- **cloak / CloakBrowser** -- the browser-compatibility backend (`cloak.resolve_backend()` ->
  `cloakbrowser`); the runtime-fidelity layer for authenticated capture.

## Recognizer, templates & extraction

- **recognizer / recognition** -- the logic that identifies a capture's player family, anti-bot
  vendor, delivery class, and media/API URLs. Recognition (`extraction_core`) is strong; the
  historical bottleneck was pattern EXTRACTION.
- **extraction_core** -- the FROZEN guard module that recognizes media/API URLs via
  `network_patterns` / `observed_api_hosts`. A recognition CEILING: new shapes are added in the
  builder, never here.
- **build_template_from_wacz** -- the NON-guard builder (`tools/`) that turns a WACZ into a
  template; where recognizer breadth is extended (a normal cut, not a guard change).
- **Content-Disposition** -- the `attachment` response header; the strongest site-agnostic
  download signal. Now used by the builder.
- **template / draft / review-candidate / reviewed** -- the three template-pipeline schemas
  (`SCHEMAS.md`): a builder draft -> a review candidate -> an operator-reviewed template.
- **promote** -- moving a reviewed template into the live set so the runtime auto-applies it.
- **demoter (REC-1/REC-2)** -- recognizer logic that DEMOTES an over-confident weak-markup match
  from a player family down to `native_custom` (reclassifies a false-positive recognition).
- **observed_api_hosts** -- the set of API hosts `extraction_core` recognizes from a capture
  (bounded by the frozen guard -> the recognition ceiling).
- **D++ / DPP** -- the generalized-media-recognition program in `build_template_from_wacz`
  (rendition ladders, protocol/MSE disambiguation, protection tagging).

## Release, build & gates

- **guard files (the 7)** -- the byte-identical release-guard set: `extraction_core.py`,
  `session_capture.py`, `dom_capture.py`, `dom_recorder.py`, `capture_bodies.py`,
  `tools/capture_session.py`, `tools/build_release.py`. Must stay byte-identical unless a SHA is
  declared changed.
- **ASI separators (the 5)** -- the 5 separator checks `test_dom_recorder_asi.py` exercises. Same
  word "guard," NARROWER set -- distinct from the 7 guard files.
- **band / proof band** -- the targeted test suite run against a cut, always from the EXTRACTED
  zip (never the work tree).
- **in-sync gates / G12** -- the regenerated-on-route-change docs (DEPENDENCY_GRAPH,
  FUNCTION_INDEX, ENDPOINT_CATALOG, gui_parity_inventory) + `check_route_counts.py` (G12:
  source-decorators == inventory == test-pin).
- **spa_wired / gui_parity** -- whether an endpoint has a SPA caller; `gui_parity_inventory`
  tracks it (and auto-discovers every `tools/*.py`). Wiring must use full `/api/...` literals.
- **3-part bump** -- a version bump landed together: `__init__.py` line 26 + `CHANGELOG.md` top +
  any version-pinned test.
- **verify_release / tree==zip** -- the post-build gate; enforces that the zip == the work tree
  (so no build-only side-files). The in-zip `STATE.json` is NON-authoritative.
- **precut_check / make_overlay** -- tools that forecast the gates before a bump and derive the
  deploy overlay (so it can't under-deploy).

## Architecture & decomposition

- **decomposition program** -- the (now CLOSED) multi-phase split of the monoliths (dev_suite,
  deep_detect, runner, app.py) into packages/blueprints. F5.1 closed it @446.
- **leaf band** -- the DECOMP-LEAF cuts (448-454) that extracted leaf modules (templates,
  template_extractor, login, capture_workbench, learn, provider_resolve) from app.py.
- **kernel + mixins** -- runner.py's structure after Phase 3: a kernel + 13 behavior mixins
  (Accounts/Auth/Browser/Challenge/Extractors/Integrations/Integrity/Manual/Queue/Scheduler/
  Teach/Telemetry/Transport).
- **blueprint / leaf blueprint** -- a Flask blueprint (`app_<group>.py`, 169 of them). A leaf
  imports flask/stdlib, NEVER `app`; `s_cfg` is imported inside the function body or it cycles.
- **session_keeper** -- the runner component holding a browser open across a queue; pauses before
  any nested `sync_playwright` spawn. (`resume_site_keepers` must NEVER exist -- keepers
  auto-reconnect on heartbeat.)
- **_process_one** -- runner.py's per-URL dispatch; its order is load-bearing and mirrored by the
  dispatch tracer.

## Tracker, process & operations

- **the tracker (TASK_TRACKER)** -- the authoritative, drift-gated work registry
  (`TASK_TRACKER_DATA.json` -> `tasktracker_gen.py`). Buckets: incomplete / completed /
  awaiting_operator / decided_against.
- **OPV (OPV-*)** -- Operator Verification: tasks that need live on-stash operator action with
  screenshot proof (historical guide archived at
  `../docs/archive/2026-07-22-doc-hygiene/project-knowledge/OPERATOR_VERIFICATION_GUIDE.md`);
  they sit in awaiting_operator.
- **PHC-1 / Phase C** -- the F2 capture-hardening overlay (VPN control, secrets lifecycle, AI
  editor): the designated LAST sandbox-buildable feature item.
- **Track-K** -- the VPN egress program (vpn_config / vpn_api; the T5/T6 bind decision is open).
- **awaiting_operator** -- built + sandbox-green, but needs live-stash verification (not
  sandbox-testable: noVNC click-throughs, capture runs, week-long soaks).
- **stash** -- the headless production host (mboyle@10.0.70.20) running the systemd
  `bulkdownloader` service. Deploys via `unzip -o` overlay + cache clear + restart.
- **bd-* toolchain** -- the session scripts (`bd-boot`, `bd-install`, `bd-preflight`, `bd-state`,
  `bd-cut`, `bd-band`, `bd-render`, `bd-handoff`, `bd-pack`, ...). `bd <cmd>` runs anything with
  full env + background services.
- **STATE.json / version.zip / pack** -- the machine-readable pin (`STATE.json`) + the per-session
  bundle of volatile current-state docs (`version.zip` / "the pack"), regenerated at session close.
- **the work tree** -- `/home/claude/work/`, refreshed from the highest uploaded source zip each
  bootstrap; Claude stages here and NEVER deploys.
