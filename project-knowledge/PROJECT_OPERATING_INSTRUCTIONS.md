<!-- verified-against: v3.66.276 -->
# BulkDownloader — Project operating instructions

How to work in this project. Pairs with the newest `KB_HANDOFF_v3_66_*.md` from
the per-session version.zip (current state). Read this first in a fresh conversation.

---

## 0. Automation posture

`AUTOMATION_POLICY.md` is the **canonical** automation doc — read it for what may
be automated, where approval is required, and the **current implementation state**
of each item (IMPLEMENTED / PARTIAL / PLANNED). The essentials:

- **Automation-positive default:** prefer automation and functionality. Treat
  guardrails as checks to automate — lint, redaction, drift detection, backup,
  staged diffs, evidence bundles, tests, and rollback — not as reasons to make a
  workflow manual by default.
- Keep every automated step inside the guardrails: authenticated operator
  session; site-provided playback/download controls or approved site API
  endpoints; no credentials/tokens/signed URLs/challenge artifacts in templates;
  no access-control bypass; lint/blocked-term/drift checks; logs/tests/rollback.
- **Approval checkpoints:** automation may prepare, capture, normalize, lint,
  stage, diff, test, and recommend. The final approval stays explicit for a
  first-time host enable, a new API trust boundary, a failed drift/safety check,
  or a protected-template overwrite before automated backup/stage/diff exists.
- **Current state:** the runtime already auto-applies *enabled* templates; the
  write-side lifecycle (auto-refresh / repair / quarantine / backup / promote) is
  roadmap until source + tests prove it. See `AUTOMATION_POLICY.md` for the
  labeled list.

### Wording conventions

Use: browser compatibility backend · authenticated profile reuse · challenge
detection/logging · manual challenge handoff · route uncertainty to review ·
site-provided download flow · reviewed-template automation · automated evidence
bundle · staged diff · rollback-ready automation.

Avoid wording that suggests: defeating / evading / bypassing / solving challenge
systems · unlocking access · scraping an entire site · persisting secrets in
templates · using signed or expiring URLs as reusable patterns.

---

## 1. Bootstrap a fresh sandbox

The kit mechanics are proven and unchanged:

1. `bash /mnt/project/setup.sh` (or `/mnt/user-data/uploads/setup.sh`) — picks up
   the patched `bd-install`.
2. `bd-install` — lands the kits (expect **20/20**) and **REFRESHES** the source
   tree in `/home/claude/work/` from the highest-version uploaded zip every run
   (preserving `frontend/node_modules`). Picks the source zip by **content**
   (looks for `bulk_downloader/__init__.py` inside) and highest embedded version,
   so handoff/runbook zips can sit in uploads without interfering. Auto-unwraps the
   double-wrapped `bulkdl_bdutils_kit.zip` and handles the BDUTILS chmod issue (see
   `BDKIT_FIXES.md`). (The old "extract only if work/ absent" guard is gone — it let
   a stale image-staged tree shadow the uploaded zip.)
3. **`bd-preflight`** — assert the work tree matches the source zip byte-for-byte
   (version, every tracked file, node_modules/package-lock). Run this FIRST after
   bd-install; it hard-fails on a stale `frontend/src`. Add `--determinism` before a cut.
4. **`bd-state`** — assert the pack's `STATE.json` pin matches the zip: **full-zip sha256** (the binding identity check, added v3.66.276), version, file-count, and the 7 guard SHAs. (A guard/version match on a *sha-mismatched* same-named zip is exactly the off-pin trap the sha gate now catches.)
5. `bd-status` — expect 20/20 kits OK, services up.
6. Version check: `bd -c 'cd /home/claude/work && python3 -c "from
   bulk_downloader import __version__; print(__version__)"'` — should match the
   uploaded source zip.

`bd <cmd>` runs anything with full env + background services (replaces the old
12-line export block from `SANDBOX.md §0`). `bash_tool` is **`/bin/sh` (dash)** —
each call is a fresh shell with no auto-loaded env; wrap bash-isms in `bd bash`.

> **Network:** the sandbox now has outbound internet from `bash_tool` (DNS + HTTPS
> egress; `pip` / `npm` / `curl` reach PyPI, npm, GitHub, etc.). The **offline packs
> are now a fallback / determinism aid, not a hard requirement** — the tree can be
> stood up and dependencies fetched live if a pack is missing. The packs remain the
> *preferred* source (pinned versions, reproducible, no network flakiness), so keep
> uploading them; but "a pack didn't attach" is no longer a hard stop for
> dependency-only needs. This does **not** change the *source-zip* rule (the app tree
> still comes from the uploaded zip, pinned in `STATE.json`) or any release-verify
> gate. **Headless browser automation is now sandbox-capable too** (verified
> @535): a staged headless chromium drives real pages in-sandbox. What remains
> **not** sandbox-testable is the **display-attached / operator** flow specifically
> — noVNC launches, interactive capture, real-challenge acceptance, VPN
> tunnel/killswitch — because those need an operator session, not just a browser or
> the network (see §5).

> For a fresh install at the current version, the source zip to upload is the
> latest full-tree zip attached this session (its name + sha are pinned in
> `STATE.json`), **not** any older zip still sitting in uploads.

## 2. Continuity system — where context comes from

**This is the current convention** (the old per-release `v*_handoff.md` files in
outputs, 138–149, are legacy — archive them; keep only the newest
`KB_HANDOFF_v3_66_<n>.md` active, so stale deploy instructions can't override the
current state):

1. **`STATE.json`** (in the per-session pack) — the machine-readable pin (live
   version, zip sha, guard SHAs, parity, next). Validate it with `bd-state`. This
   is the first read; prose docs narrate, STATE.json is the truth.
2. **The per-session pack** — CONTINUATION + KB_HANDOFF + Backlog/Roadmap +
   kickoff + delta-spec + wiring specs. `/mnt/transcripts/journal.txt` is legacy
   and is often empty now — check it only if the pack is missing context.
3. **The newest transcript** in `/mnt/transcripts/` (if present) — the full detailed record
   (module APIs, paths, line numbers, decisions). They are **large**; read
   **incrementally** (grep/sed/`view` ranges), never all at once.
3. **The newest `KB_HANDOFF_v3_66_<n>.md`** — distilled current state. It is
   **not** in static project knowledge; it arrives in the per-session
   `version.zip` (see §2.5). Read whichever `KB_HANDOFF_v3_66_*.md` is in uploads.
4. **`SANDBOX.md`** — env + footguns.

For the full doc reading order (charter, goals, automation policy, schemas, …),
follow **`KB_ACTIVE_INDEX.md`** — the single active index.

If a conversation was compacted, the compaction summary at the top is also a
faithful snapshot — but the transcript is the ground truth if they ever differ. A
**duplicate user message** (identical to the one just before it) is itself a compaction
tell: compaction can drop the tail of a turn (e.g. a `present_files` call), so the prior
deliverable may have been *built but never surfaced*. On a duplicate, re-verify what
actually landed (does the output file exist? was it presented?) and complete only the
missing step — don't blindly redo the whole turn or assume it's done. Keep a running
per-session compaction count and record the session total in KB_HANDOFF at close.
**Source code is the final ground truth** over any doc.

### 2.5 Static project knowledge vs per-session `version.zip`

Project knowledge holds only **version-agnostic** files (charter, goals,
automation policy, these operating instructions, sandbox, schemas, the kit
scripts + bdkit docs, the capture runbook, `README_KB.md`, the version-agnostic
`bd_starting_message.txt` + `KB_ACTIVE_INDEX.md`, `Manifest.md`, the consolidated
`ADVANCED_PROJECT_KNOWLEDGE.md` + its `DANGER_MAPv2.md` invariant registry, and the durable
reference cards 2/3/4/6/7/8/9/10). It is **set once** and changed only when one of
those docs genuinely changes — never on a routine release.

The **volatile** current-state files travel in a per-session **`version.zip`**:
the newest `KB_HANDOFF_v3_66_<n>.md`, `Backlog.md`, `Roadmap.md`,
`KB_VALIDATION_NOTES.md`, reference cards **1** (artifact provenance) + **5**
(delta spec), (the canonical source hash + version pin live in `STATE.json` + the newest `KB_HANDOFF`; the former `CONTINUATION_MESSAGE.md` is retired — `CONTINUATION_TEMPLATE.md` is an optional narrative template). Regenerate `version.zip` at **session close** and attach it
to the next chat alongside the source zip + packs.

**The rule:** at session close, rebuild `version.zip` from the volatile set above;
touch project knowledge only when a *static* doc actually changes. **Enforcement:** run `bd-handoff --kb-dir <static_kb_working_dir>` at close -- it stages a paste-ready `BulkDownloader_project_files_v<ver>.zip` + a `PROJECT_KNOWLEDGE_UPDATE.md` flag via `bd-kb-sync` whenever a durable static doc changed, and reseeds `STATIC_KB_MANIFEST.json` so the pack is the truth; `bd-boot` then verifies the pasted static set's integrity + freshness next session. See `KB_SYNC_WORKFLOW.md`. "Newest handoff
only" is then enforced by *what you attached this session*, not by editing project
knowledge — so a stale handoff can't override current state. **Guard:** the
bootstrap halts if no `KB_HANDOFF_v3_66_*.md` is present in uploads (the
`version.zip` wasn't attached) — never proceed from static project knowledge
alone. If packs change, update `Manifest.md` in project knowledge that release.

## 3. How Matt works

- **Terse/directive** — often one word ("Continue", "Next", "1"). Execute
  immediately; lead with results, not preamble.
- **Honest over optimistic.** Never claim something passed/shipped without
  verifying. Flag limits plainly (e.g. browser/noVNC and cockpit UI
  click-throughs aren't sandbox-testable).
- **Deploys via `unzip -o` overlay + `sudo systemctl restart`**, NOT git.
- **Matt also overlays files himself**, so the work tree and stash can diverge —
  report divergence candidly rather than assuming the tree is authoritative.
- **Stash is headless** (no display); the venv has Flask/Playwright, system
  `python3` does not — but the chain CLIs (build/normalize/promote) are
  stdlib-only, so plain `python3` runs them fine.
- He interrupts with "hold"/"wait". Respect it.

## 4. Release checklist

1. Change + tests green.
2. Bump version as a **3-part edit landed together** (see reference card #3): `bulk_downloader/__init__.py`
   **line 26** `__version__` + `CHANGELOG.md` top `## vX.Y.Z` + any version-pinned test (currently
   `tests/test_settings_center_slice4.py`). `grep -rnE '__version__ *== *"3\.66\.' tests/` each release —
   don't trust the pin list to stay at one entry.
3. Regen `FUNCTION_INDEX.md` (`python tools/build_function_index.py`) **only if** a function was added/renamed
   in `app.py` or `runner.py` (it tracks only those two files' line numbers — cockpit/blueprint page funcs are
   NOT tracked, so a new cockpit page usually leaves it unchanged; confirm it stays in-sync).
4. Regen the other in-sync docs **if a route changed** (a GUI-parity write cut touches all of these):
   `tools/build_endpoint_catalog.py` (needs Flask; includes cockpit routes), `tools/dependency_graph.py`
   (regens DEPENDENCY_GRAPH.json/.md), `tools/gui_parity_inventory.py` (flips the endpoint's `spa_wired`).
   Then `tools/check_route_counts.py` (G12 gate: source-decorators == inventory == test-pin). SPA wiring must
   use FULL `/api/…` literals, not a concatenated `base` var, or the scanner won't count it `spa_wired`.
5. `CHANGELOG.md` entry — `test_contracts.py` requires the current version present with matching health. When
   prepending, anchor the `str_replace` on the **previous** version's `## ` header and re-emit it.
6. Confirm the **7 release-guard SHAs** are byte-identical to baseline (declare any intentional change with its
   SHA), then build. The byte-identical release-guard set (the authoritative list — other docs should point
   here, not restate a bare count): `bulk_downloader/extraction_core.py`, `bulk_downloader/session_capture.py`,
   `tools/capture_session.py`, `bulk_downloader/dom_capture.py`, `bulk_downloader/dom_recorder.py`,
   `bulk_downloader/capture_bodies.py`, and `tools/build_release.py` (added to the guard surface once it
   carried the in-sync gate logic — hence the historical 6→7). Confirm the current SHAs against the newest
   `KB_HANDOFF` (the per-release baseline lives there, not hard-coded here, so a *declared* guard change updates
   the baseline cleanly). NOTE: this is distinct from the **5 ASI-separator checks** that
   `test_dom_recorder_asi.py` exercises (reference card #2) — same word "guard," different, narrower set.
7. **Verify from the extracted/built zip**, never from the work tree alone: run the band from the extracted
   zip AND run `tools/verify_release.py --zip <zip>` and confirm `RESULT: PASS` (banner/version_consistency
   gate — `build_release`'s pin-scan does not catch a stale banner; see reference card #2). It exits 1 on FAIL /
   0 on PASS, so gate on `$?` (not on a piped `tail`/`grep`); benign notes are the reptyle-draft status and
   `frontend/package.json 0.1.0` (independent versioning).
8. **At session close**, rebuild the per-session **`version.zip`** from the volatile set (§2.5) — new
   `KB_HANDOFF`, `Backlog`, `Roadmap`, `KB_VALIDATION_NOTES`, reference cards 1 + 5, the source hash + version pin (now recorded in `STATE.json` + `KB_HANDOFF`; `CONTINUATION_MESSAGE` retired). Leave static project knowledge untouched unless a static doc changed.

### Full-tree build
Build with `tools/build_release.py` (deterministic; gates route-counts /
version-pins / capture-model and emits the zip). Or run **`bd-cut`**, which wraps
the whole finalize: tsc → vite build → inventory regen → 3-part bump →
build_release with auto-derived `--approved-frontend` → extract → band → verify_release
on true `$?`. (The legacy `/tmp/build_15x.sh` template is retired.) For reference the
zip layout = the
137-base zip path-list ∪ work-tree walk (`bulk_downloader tests tools docs kb
live_tests extension frontend/src frontend/dist scripts templates` + root
`*.md`/`*.txt`; excludes `__pycache__`/`.pyc`/`node_modules`/`venv`); **tree
wins**. Produces ~7.9M / ~1078 files. Expect **3 "missing"** = stale 137 dist
hashes; the tree ships the live hashes. (Output is the flat-layout zip in
outputs.)

### Deploy
- **Overlay update:** verify the zip's sha256, then `unzip -o <zip>` over
  `~/BulkDownloader`, **clear bytecode caches**, restart, and **confirm the running
  version**:
  ```
  # v3.66.718 -- FIRST, if this cut DELETED any file. `unzip -o` NEVER removes, so a
  # deleted file keeps living on stash; the graph gates GLOB THE DISK, so the orphan is
  # scanned as live source and trips the frozen baseline. The release zip can be CORRECT
  # and stash still goes RED (bit @718: app_sched_exports.py, deleted @716 -> 3 failures).
  # bd-deploy-manifest ships in the SANDBOX bdsuite, not on stash -- generate the rm lines
  # there and paste them here:
  #     bd-deploy-manifest --zip <release.zip> --script      # in the sandbox
  #     <paste the emitted `rm -f ...` lines>                # on stash
  cd ~/BulkDownloader && unzip -o <zip>
  find ~/BulkDownloader -name '__pycache__' -type d -prune -exec rm -rf {} +
  find ~/BulkDownloader -name '*.pyc' -delete
  sudo systemctl restart bulkdownloader
  curl -s localhost:5555/api/health        # CONFIRM "version" flipped to the new release
  ```
  Exclude `tools/cockpit_console.py ENDPOINT_CATALOG.md` if Matt has live cockpit edits.
  - **Why the cache clear is load-bearing:** an overlaid `.py` with an mtime *older*
    than an existing `__pycache__/*.pyc` makes CPython run the **stale** bytecode.
    Observed at v3.66.161 (on-disk `__init__.py`=161 but `/api/health` reported 160,
    and `changelog_lint` resolved a stale `_read_version()`) until caches were cleared.
- **Fresh install:** unzip the full tree + clear caches (harmless if none) + restart.
  No exclude needed.
- **Always confirm `/api/health` reports the new version before trusting any
  post-deploy test run.** A green suite against a stale process is worthless.
- **Service venv is `venv/` (NOT `.venv/`).** `ExecStart` =
  `/home/mboyle/BulkDownloader/venv/bin/python`. `python` resolves (python-is-python3)
  but is the **system** interpreter — backend/import checks **must** use
  `venv/bin/python`, or a venv-installed package (e.g. `cloakbrowser`) is invisible and
  `resolve_backend()` falsely reports `playwright`.
- **Post-deploy backend check** (the point of an install-path release):
  ```
  venv/bin/python -c "from bulk_downloader import cloak; print(cloak.resolve_backend())"   # expect cloakbrowser
  venv/bin/python -m cloakbrowser info
  ```
- **Rollback:** `python tools/rollback.py --archive <ver> --from <zip>`.
- Open question to close: does `./capture.sh` clear pycache? If not, add the cache-clear
  there too so the capture/verify helper can't run against a stale process.

## 5. Sandbox footguns (test + env)

- **Test runner is custom, not pytest CLI:**
  ```
  timeout 90 env BD_HOME=$(mktemp -d) BD_DISABLE_KEEPALIVE=1 \
    PYTHONPATH=/tmp/prestaged_site_packages \
    PLAYWRIGHT_BROWSERS_PATH=/home/claude/.cache/ms-playwright \
    python3 run_tests.py tests/<file>
  ```
  It **chdirs to a temp dir per run** → tests derive repo root from
  `Path(__file__).resolve().parent.parent`. No pytest builtins injected (no
  `tmp_path` → use `tempfile.mkdtemp`); zero-arg test functions; `monkeypatch`
  unreliable → restore module globals in `try/finally`. `prestaged_site_packages`
  **has Flask** (test client works in-runner).
- **`run_tests.py tests/` (whole dir) HANGS** at `test_perf_lab.py`. Run targeted
  suites in small batches. Don't `pkill -9 run_tests`.
- **`test_v3_66_146_nav_guard` times out (>200s) in the sandbox** — known, not a
  regression.
- **`bash_tool` now HAS outbound internet** (DNS + HTTPS; `pip`/`npm`/`curl` reach
  PyPI/npm/GitHub — verified live). **Headless browser automation is ALSO now
  sandbox-capable** (verified @535): a staged headless chromium drives real pages
  in-sandbox (Playwright `chromium.launch(headless=True)` + `goto` against live
  sites works under `bd`). Tools: `bd-live` (headless URL smoke test),
  `bd-recognizer-drift` (run the real recognizer path against a live/fixture DOM),
  `bd-doctor` (reports live capabilities), and `bd-scan --ts`/`--jscpd` +
  `bd-rev`/`bd-fetch` (live-provisioned audit battery). What is STILL not
  sandbox-testable is anything needing a **display or an operator session** — NOT
  headless automation and NOT the network: **noVNC / display-attached launches,
  interactive capture, real-challenge acceptance, and VPN tunnel / killswitch
  behavior**. A display (Xvfb :99) is present for rendering but is not an operator
  session, and bd-live/bd-recognizer-drift REPORT-and-stop on a detected
  challenge rather than handling it. So those specific rows stay operator-gated;
  everything else that only needed a headless browser or the network is now
  in-sandbox. (Prefer the offline packs for reproducibility, but a network fetch
  is a valid fallback for a missing dependency — see §1.)
- **Interactive capture runs in the noVNC / sentinel / manual operator flow**, not
  as a background Flask subprocess where Playwright sync/async can conflict (153).
- Snapshot any source file into `/home/claude/patches/originals/` before editing
  (one snapshot per file per version baseline).

## 6. Output conventions

- All deliverables → `/mnt/user-data/outputs`, then `present_files`.
- One **consolidated** release per slice of work (bump once, one zip), plus an
  overlay zip for quick deploys when not a fresh install.
- Releases are zips named `BulkDownloader_v3_66_<n>.zip`; overlays
  `…_<n>_overlay.zip`.
