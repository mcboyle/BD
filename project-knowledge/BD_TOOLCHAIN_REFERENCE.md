<!-- verified-against: v3.66.805 -->
# BulkDownloader `bd-*` toolchain — reference

Flags + behavior for the **major** `bd-*` tools, so you don't have to read the scripts.
Grouped by lifecycle.

> **COVERAGE MEASURED v3.66.805 — this line previously claimed "every `bd-*` tool".**
> It documents **110** tools against **244** `bd-*` in live `bin/` — under half. No
> documented tool is stale (the only documented-but-absent names are `bd-blast` /
> `bd-suites` / `bd-touched`, explicitly marked RETIRED at rev-702 below), so what is
> here is trustworthy; it is simply **not exhaustive**. For the complete live index run
> `venv/bin/python toolchain/bin/bd-tools --bin toolchain/bin` (categorized). **Without
> `--bin` it defaults to the old sandbox path `/home/claude/bin`, finds nothing, and
> prints "0 tools total" while exiting 0** -- a zero-in-every-bucket summary with a
> success code, not a pass. Or read `BD_TOOLCHAIN_WHEN_TO_USE.md`
> (regenerated @805 via `bd-tools --emit-doc`, 247 tools). Absence from this file is
> NOT evidence a tool does not exist. Any tool count in this file is a snapshot --
> re-derive with `ls toolchain/bin/bd-* | wc -l` rather than quoting one.

**Where:** `sandbox` = the dev/cut environment; `stash` =
the headless host (runs with system `python3`, stdlib-only). Every tool is
**read-only or gated**; **none deploy** — deploy is always a separate human step.

Conventions: tools live on `PATH` (via `setup.sh` → `/usr/local/bin`); run them
under `bd` so the env + services are loaded, e.g. `bd python3 bd-cut ...` or just
`bd-band ...`. Exit `0` = success; non-zero = a gate failed (per tool below).

**Sandbox paths below are the tools' own hardcoded defaults, not this checkout.**
`/home/claude/work` and `/home/claude/bin` do not exist in a git clone (`/home/claude`
is empty), so a tool run bare will operate on nothing and often still exit 0. Most
accept `--work` / `--tree` / `--root` / `--bin` to override. See
`docs/repo/TOOLCHAIN_PORTABILITY.md` for which tools are sandbox-bound.

---


## RETIRED at v3.66.858 -- seven zero-coupling tools

Removed from `toolchain/bin/` and `project-knowledge/`, with their `bd-tools`
category rows and `STATIC_KB_MANIFEST.json` entries:

`bd-packs` `bd-intake` `bd-time-travel` `bd-rollback-plan`
`bd-deploy-rehearse` `bd-audit` `bd-parallel`

WHY. Their SUBJECT died with the zip/overlay world (CLAUDE.md section 7): the
release zip is retired and `git diff` replaced it, the uploads mount does not
exist, snapshots are branches, and `scripts/deploy.sh` replaced the overlay
rehearsal. Each was invoked by nothing.

WHY ONLY SEVEN. Twenty were proposed. The coupling was MEASURED rather than
assumed and came to 65 references across 36 files -- roughly triple the estimate.
These seven are the ones with ZERO surviving references outside the `bd-tools`
category registry, so no surviving tool needed editing. The other twelve each
require an edit in a tool that lives on -- `bd-sweep` (8 refs), `bd-coretest`
(4), `bd-tool-lint` (3), and single references in `bd-freshcheck`,
`bd-guardcheck`, `bd-cut` and `bd-tool-smoke`, all four of which gate CI or the
release. They stay queued.

`bd-state` is held separately: `tools/build_session_pack.py` genuinely invokes
it (`:128`) and that file carries its own pin test.

NOT PROVED BY bd-equiv, and that is stated rather than glossed. Their corpora
are retired, so the old tools emit zero tokens -- which `bd-equiv` now correctly
grades CANNOT-EVALUATE (@852, @855) instead of issuing a false licence. The
argument is subject-death plus a named replacement, not a mechanical proof.

SALVAGED FIRST at v3.66.857, into `bdtools_sec`: `read_manifest_canon()` and
`stub_reason()`, plus the net-tool budget policy re-homed as a test ratchet.

## Lifecycle at a glance
```
OPEN      bd-boot (budgeted+checkpointed -- re-run until READY) = prestage → install → venv → preflight → state → status → footguns → kbsync
DEV LOOP  bd-band (run tests)  ·  bd-band-derive (what to band)  ·  bd-snapshot (pre-edit)
          bd-lsp (code intel)  ·  bd-render (visual gate)    ·  bd-curl (hit the API)
CUT       bd-precut (predict) → bd-cut (tsc→vitest→build→bump→release→band→verify)
CLOSE     bd-ship = bd-precut → bd-cut → bd-handoff (→ bd-pack)   [one command]
          bd-handoff (repin STATE from zip) → bd-pack (lint+zip version.zip)
DEPLOY    (human: git fetch origin main + git reset --hard origin/main, then the
          post-move steps below)  ->  bd-verify-live (confirm live)
SAFETY    bd-doctor (triage)   ·   bd-rollback   ·   bd-reconcile (tracker hygiene)
```

**Deploy is pure git** (operator-confirmed 2026-07-27): `git fetch origin main &&
git reset --hard origin/main`, then restart. There is no zip overlay and no zip
fallback, so deletions propagate natively -- the orphan-on-disk class is gone.

**But a git deploy only moves FILES. It does not make the running system match them.**
None of the following were ever properties of the overlay, so none of them went away.
Treat this as a condition to satisfy, not a fixed-length checklist:

- `__pycache__/*.pyc` is **not** cleared. `git reset --hard` leaves stale bytecode
  exactly as `unzip -o` did (the v3.66.161 footgun is unchanged).
- Gitignored generated artifacts are **not** refreshed, and `git clean -fd` will not
  remove them either (that needs `-x`). A stale
  `reports/gui_parity_inventory.json` fails the ENTIRE suite. Regenerate, do not delete.
- The service is **not** restarted.
- `frontend/dist/` is **not delivered at all** -- it is gitignored and `git ls-files
  frontend/dist` returns 0 files. `bulk_downloader/app.py` serves a uniform 503 when it
  is missing, so a missing or stale bundle is a silent 503 on the SPA. Rebuild with
  `cd frontend && npm ci && npm run build` whenever SPA source changed.

---

## Open / bootstrap

### `bd-boot` — sandbox · the one-command open (budgeted + checkpointed)
Runs the whole chain `prestage → install → venv → preflight → state → status →
footguns → kbsync` under a wall-clock **budget** (`--budget N`, default 230s <
the ~280s harness limit; `$BD_BOOT_BUDGET`), exiting 0 with a loud `PARTIAL` +
phase ledger BEFORE starting a phase it cannot finish. Completed phases are
checkpointed in `/home/claude/.bd_boot` (markers keyed on the source-zip
identity — a new zip auto-resets; `--fresh` wipes). **Operator instruction:
run `bd-boot` until it prints READY.** `--jobs N` sets prestage parallelism.
Gating unchanged: install/preflight/state halt hard; kbsync halts on a
manifest whose own `generated` field is strictly newer than the pasted PK
(mtime is never trusted — extraction resets it). Resolves zips from
`/home/claude` intake copies first, evicting uploads second.

### `bd-prestage` — sandbox · chunked, validated, parallel pre-stage
Extracts + VALIDATES the pack-embedded kits into `/tmp/bulkdl_staging` before
`bd-install` reads them. **Sentinel-validated**: a kit that passes `unzip -t`
once gets `$STAGING/.ok/<kit>` (its byte size); re-validation is a stat, not a
~92s/sweep decompress. A killed mid-extract never receives a sentinel and a
size mismatch voids one, so the truncation footgun stays closed. `--jobs N`
(default 3, `$BD_PRESTAGE_JOBS`) extracts in parallel waves — all 22 kits cold
in ~109s, one call. `--max N` bounds a pass; `--check` is a cheap sweep;
`--no-cache` rebuilds the kit→pack index (otherwise cached in
`$STAGING/.index` keyed on pack sizes+mtimes). Packs are discovered in
`/home/claude` first (intake copies), uploads second. Resumable.

### `bd-install` — sandbox
Lands the kits and **refreshes** `/home/claude/work` from the highest-version
source zip every run (preserves `frontend/node_modules`; picks the zip by
content + embedded version, `/home/claude` intake copies preferred with a
STRICT tie-break — uploads only wins a strictly newer version). Exports
`UPLOADS=<effective dir>` to `install_bulkdl_kits.sh`, whose `find_kit` trusts
staged kits via the shared `.ok/` sentinels (stat, not testzip). Re-applies
the `kit/` overlay from the newest `BulkDL_next_session_*.zip` (both dirs).
Has a dev-kit handler. No flags.

### `bd-venv` — sandbox · idempotent service-venv provisioner
Provisions `work/venv` (NOT `.venv/`): core + cloak layer, stealth Chromium (146),
websockets, Windows-font aliasing (Carlito/Caladea + fontconfig), a dev pytest
layer, and a working ffmpeg (apt distro build + a `/tmp/media/tools_bin` → `/usr/bin`
PATH shadow, because the static media-kit ffmpeg SIGSEGVs on HLS+HTTPS in-sandbox).
Idempotent — validates flask + cloakbrowser + `resolve_backend` + version and does
only the missing work. Strips `PYTHONPATH` for its dev-layer probe/install (the bd
env's `PYTHONPATH=/tmp/prestaged_site_packages` masks the venv's own packages).

### `bd-preflight` — sandbox
Asserts the work tree matches the source zip byte-for-byte (version, every tracked
file, `node_modules`/lockfile). Run **first** after `bd-install`; hard-fails on a
stale `frontend/src`. Flag: `--determinism` (extra pre-build determinism assert
before a cut). Exit non-zero on divergence.

### `bd-state` — sandbox
Asserts `STATE.json`'s pin matches the zip: **full-zip sha256** (binding identity),
version, file-count, 7 guard SHAs. Flags: `--state <STATE.json>`, `--zip <zip>`
(defaults auto-discover). Exit non-zero on any mismatch.

### `bd-status` — sandbox · informational *(UPDATED @732)*
Reports env + services + tree. Never gates (a red `venv (stash runtime)` is
expected in the sandbox). No required flags. Also reports the OPTIONAL PACKS
(pack_E-H) present/installed state (calls `bd-optpack --brief`).

**@732 — `--json` was SILENTLY SWALLOWED.** It is a bash script; bash ignores unknown args, so
`bd-status --json` printed the ANSI **human** report and any caller parsing it got garbage that
*looked fine to a human*. It has no structured model to emit, so it now **HONESTLY REFUSES**:
exit **2**, message on stderr. *An honest refusal, not a fake success.* Colour is guarded on
`[ -t 1 ]` — pipes, captures and redirects are ANSI-free.

### `bd-optpack` — optional expansion packs (E–H): detect / install / VERIFY
`list|--brief` detect + install-state; `install <E|F|G|H|all>` per-pack recipes
(`G --apt` dpkg-installs incl. pack_G2 dep debs; `F --pip "<pkgs>"` installs
named wheels into the service venv). Extraction restores POSIX modes from zip
metadata, rematerializes symlinks, and exec-restores ELF/`#!` files (naked
`extractall` was the root cause of dead E/H capabilities). pack_E lands in the
BD runtime cache `/home/claude/.cache/ms-playwright` (`BD_PW_CACHE` overrides;
the ambient `PLAYWRIGHT_BROWSERS_PATH=/opt/...` is never trusted) + creates the
webkit injected-bundle symlink (webkit is headful-under-Xvfb only in-sandbox).
pack_H wires `~/rev` to the pack venv (pins match bd-rev) and heals jscpd
(.bin symlink; commander-ESM launcher flag only when node<22). **`verify`**
probes each installed pack's CAPABILITY (17 read-only checks — exec bits,
imports, PATH tools, libzbar, jscpd `--version`, `~/rev` wiring); trust it,
never the install flag.

### `bd-fetch` — sandbox · live dependency fallback
When a pack/dep is missing from uploads, fetch it LIVE instead of hard-failing
(sandbox has egress). Maps a capability name to an install recipe:
`bd-fetch rev` (audit venv via bd-rev), `bd-fetch playwright`, `bd-fetch <pypi>`
(best-effort pip), `bd-fetch npm:<pkg>`, `bd-fetch --check <name>` (report only).
This is the sanctioned fallback behind the cloak-staleness lesson — e.g. a
missing `authlib` wheel becomes a warning + live pull, not a stop.

### `bd-live` — sandbox · headless URL smoke
Drives a real URL headless (chromium) for a quick reachability/render smoke.
Refuses signed URLs. Read-only; for recognizer/capture spot-checks, not a suite.

---

## Dev loop

### `bd-band` — sandbox · run targeted suites *(NEW)*
Runs specific test files through `run_tests.py` with the correct env baked in
(`BD_HOME`/`BD_DISABLE_KEEPALIVE`/`PYTHONPATH`/`PLAYWRIGHT_BROWSERS_PATH`) and a
per-suite timeout, reporting **every** suite before exiting non-zero on any failure.
- `bd-band tests/test_gui_parity.py tests/test_contracts.py`
- Flags: `--work DIR` (default `/home/claude/work`); `--from-zip <zip>` (extract +
  band from a pristine tree — release-grade; default bands the work tree for the
  dev loop); `--timeout N` (per-suite seconds, default 240).
- **Refuses a bare `tests/`** argument (that's the whole-dir hang). Exit 0 = all
  `Failed:0`; 1 = any failure / timeout / missing suite.

### `bd-band-derive` — sandbox · what must I band/regen?
Diffs the work tree vs the source zip and maps each changed file to its required
suites via `TOUCHED_FILE_TO_TEST.md`, flagging route changes (→ regen + G12) and
guard touches (→ declare sha). Emits a ready-to-run `bd-band ...` line.
(Supersedes `bd-touched`, `bd-blast` and `bd-suites`, all retired at rev-702 and
merged into this one diff-derived band engine.)
- Flags: `--file <path>` (NOT `--changed`), `--zip <source.zip>` (default: highest uploaded), `--work DIR`,
  `--map <TOUCHED_FILE_TO_TEST.md>` (default: auto-locate). Advisory; exit 0.

### `bd-snapshot` — sandbox · pre-edit snapshots + diff *(NEW)*
Snapshots files to `patches/originals/<version>/` (write-once per baseline) so you
can diff your session's changes against pristine.
- `bd-snapshot bulk_downloader/app.py frontend/src/routes/Settings.tsx` — snapshot
- Flags: `--diff <file>` (unified diff vs snapshot); `--all-diff` (diff every
  snapshot); `--list`; `--work DIR`. Exit 0; 1 on missing target / no snapshot.

### `bd-render` — sandbox · the visual gate *(NEW)*
Starts the render backend (`spa_serve.py`), waits for its port, runs the both-theme
capture + montage, then **always tears the server down**. Owns the reliable
lifecycle; the capture/montage commands are discovered with overridable defaults.
- `bd-render -- --routes settings,queue` (args after `--` go to capture)
- Flags: `--serve-only`; `--no-montage`; `--port N` (default 5599); `--host`;
  `--out DIR` (default `/home/claude/render_out`); `--wait N` (port wait secs);
  `--serve-cmd / --capture-cmd / --montage-cmd "<cmd>"` (override sub-tools);
  `--baseline-dir <dir>` (name-level compare vs packed montages).
- **Note:** sub-tool flags live in the render harness; if yours differ, override
  `--capture-cmd`/`--montage-cmd`. The server lifecycle is correct regardless.
  Exit 0 = served + capture + montage all 0; 1 = a step failed / server never came up.

### `bd-render-env` — sandbox · gate + repair the render environment *(NEW @715)*
**Run this before any capture.** Every GUI-capture tool (`capture_all`, `capture_gui`,
`render_check`, `gui_audit_kit`) calls a bare `chromium.launch()`, which executes the
**headless shell** build — a *different* artifact from `chromium-<rev>`.
- `SHELL`: the shell build for the rev playwright actually wants. **`bd-sbcap` used to report
  "chromium build resolvable" while headless launch was dead** — it checked the chromium build,
  not the shell. Fixed @715, but the lesson stands: check what `launch()` *executes*.
- `PATH`: the sandbox ships an ambient `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` holding a
  **stale rev**; `bdenv.sh` overrides it. So a tool works under `bd` and dies bare.
- `DIST`: a missing `frontend/dist` yields HTTP 200 and screenshots of an **empty shell** — a
  silent pass. `HARNESS`: the render scripts ship only in read-only `/mnt/project`.
- `bd-render-env` · `--fix` · `--json` · `--selftest`. Exit 1 = render blocked.

### `bd-gui-surface` — sandbox · the complete GUI surface, statically *(NEW @715, CORRECTED @729 + @732)*
Route counts do not describe the GUI. The cockpit is **one** server route rendering its views
**client-side** (`PAGES[p]()` in `cockpit_console.py`); a URL-walk finds 13, and
`enumerate_surfaces.py` walks `url_map`, so it has **never seen them**. BD's subtabs are
`aria-pressed` (13 files), not `role=tab` (1 file).

**"44 views" was FOLKLORE** (the old regex required `data-p` to be the anchor's ONLY attribute,
so every `class="btn"` anchor was invisible — and the selftest ENSHRINED the bug). Views derive
from the **PAGES registry** — the router's own table. Live: **133 renderable · 83 anchors ·
61 redirect aliases · 0 orphans.**

**@732 — the verdict is THREE-VALUED.** "No anchor" is not "unreachable"; it is *unreachable
BY ANCHOR*. The cockpit also reaches views by **tab-consolidation** (v3.66.107 merged
inbox/daily into `priority`, the composite indices into `scores` — nav entries removed ON
PURPOSE, renderers kept so deep-links resolve) and **programmatic tier-landing**
(`_tierLanding()` → `advlanding`/`syslanding`). Modelling only anchors reported **7 dark views
that were all deliberately closed** — and acting on that report reverted a consolidation until
the band stopped it.
- **UNREACHABLE** (`dark_views`) — a real finding. Currently **0**.
- **CLOSED** (`closed_views`) — anchor-less by design, reachable another way. Informational. **7**.
- The consolidation is **verified live in source**, not asserted: delete the `scores` host page
  and its 3 views correctly flip CLOSED → UNREACHABLE.
- `--captured DIR --gate` → exit 1 if a capture sweep missed a surface. Read-only, no browser.

**A false "dark" finding is not a harmless over-count — it is an instruction to break something.**

### `bd-deep-capture` — sandbox · exhaustive GUI capture *(NEW @715)*
Every page, subtab, expanded disclosure, modal, and cockpit view; `full_page`; theme forced
before first paint; self-boots and tears down its own backend.
- Its destructive-label denylist guards **modal prospecting only** — applying it to *navigation*
  ate the cockpit's "Rollback" **view** out of a sweep.
- Subtab state lives in the URL (`useUrlState` → `?status=running`); compare the **pathname**,
  not the full URL, or every subtab is discarded as a navigation.

### CODEX_HANDOFF.md — RETIRED 2026-08-03 *(a second agent-facing contract)*

**The document is gone. Do not recreate it; use `CLAUDE.md` plus SESSION_CARRY 15.15.**

Removed in the same cut: `CODEX_HANDOFF.md` and
`tests/test_codex_handoff_defers_to_claude_md.py`.

WHY, and it is NOT the tracker's reason. TASK_TRACKER was a duplicate
REGISTER. This was a duplicate CONTRACT -- a second document an agent reads
before acting, describing a DIFFERENT MACHINE. It once shipped 14 commands
against a dot-prefixed `venv` that does not exist on this host while CLAUDE.md
said otherwise; a session followed the wrong one and reported seven failures
that were not real.

`test_codex_handoff_defers_to_claude_md.py` existed solely to stop that
recurring -- it failed if the handoff restated any fact CLAUDE.md owns. It is
retired WITH its subject, deliberately: a gate whose whole purpose is to keep a
second contract from contradicting the first is answered more completely by not
having a second contract. Keeping it without the document would be a check that
can no longer encounter its subject, which is worse than no gate.

The 34-task program's open groups (Analysis Task 4 paused, Analysis 5-7,
Governance 1-8, Audit/knowledge/hygiene/static-KB 1-11) are recorded in
SESSION_CARRY 15.15, including the fact that Task 4's frozen review packages no
longer exist -- so resuming it means RE-FREEZING from the current tree, not
verifying against the recorded hashes.

THE LESSON IS KEPT, NOT THE FILE. The `.venv` trap stays in CLAUDE.md section 5
and `LESSONS_LEARNED_v3_66_818.md`. `tests/test_codex_handoff_stays_retired.py`
asserts CLAUDE.md still owns both halves -- that `venv/bin/python` is stated
and that `.venv` is still warned about -- so the retirement cannot quietly
delete the correction along with the error.

### TASK_TRACKER — RETIRED 2026-08-03 *(the ledger, its generators, and two operator tools)*

**The tracker is gone. Do not regenerate it; use `project-knowledge/SESSION_CARRY.md`.**

Removed in the same cut: `TASK_TRACKER.md`, `TASK_TRACKER.xlsx`,
`TASK_TRACKER_DATA.json` (the canonical source -- the other two were views),
`tools/tasktracker_gen.py`, `tools/tasktracker_sync.py`,
`toolchain/bin/bd-tracker-recon`, `toolchain/bin/bd-reconcile`, and four
tests. `bd-pack` lost its `TRACKER_FILES` list and `_tracker_drift()` gate;
`bd-capsweep` lost its `--data` / `--id` completed[] cross-ref;
`bd-freshest`, `tools/render_advanced_kb.py` and
`tools/build_session_pack.py` lost single references.

WHY. It was a SECOND register beside SESSION_CARRY.md, and the two never
referenced each other -- SESSION_CARRY mentioned TASK_TRACKER zero times
while the tracker held eleven open operator-bound rows the session queue
could not see. Two registers that do not know about each other are worse
than one that is merely incomplete, because each looks authoritative alone.
Everything still OPEN was absorbed into SESSION_CARRY 15.15 first. The 283
completed rows were deliberately not copied: git history holds them, and a
completed row is not something anyone needs to read again.

`bd-pack` sanctioned this. Its drift gate said: *"Regenerate ... or formally
kill the tracker and remove it from TRACKER_FILES. Do NOT let it silently
disappear again."* This is the second branch, taken deliberately.

THE ABSENCE IS STILL POLICED, which is the whole point. The tracker vanished
once already at v3.66.700 and stayed gone 48 versions, because
`_tracker_drift()` SKIPPED when its files were absent -- the check policing
the tracker reported CLEAN over its own subject's disappearance. Silence read
as pass. `tests/test_task_tracker_stays_retired.py` replaces that with a gate
that ASSERTS absence, plus a no-executable-references scan over `git
ls-files`. If you are reading this because that test failed, the files came
back; decide deliberately rather than regenerating on reflex.

### `bd-deploy-manifest` — RETIRED 2026-07-28 *(added @718, removed once deploy became git)*

**The tool is gone. Do not re-add it; the failure class it detected cannot occur.**

Removed in the same cut: `toolchain/bin/bd-deploy-manifest`,
`tools/deploy_manifest.py`, `project-knowledge/deploy_manifest.py` and
`tests/test_v3_66_722_deploy_manifest_ships.py`.

*Why it existed.* `unzip -o` overwrote and added; it **never removed**. A file deleted in a
cut kept living on stash, and the graph gates **glob the disk**, so the orphan tripped the
frozen baseline — observed at v3.66.718, when `app_sched_exports.py` (deleted at 716) stayed
on disk and turned three suites RED against a correct release. The tool emitted the `rm`
lines an overlay could not produce for itself, behind a `_NEVER_RM` guard that refused to
propose deleting runtime data (DB, `.env`, secrets, `sites_config.json`).

*Why it is gone.* The box deploys with `git fetch origin main` + `git reset --hard
origin/main` + a restart. That deletes tracked files natively, so a deleted file cannot
survive a deploy and there is no orphan to enumerate. Keeping the tool would have meant
keeping a gate whose subject no longer exists — and a gate that cannot encounter its
subject reports clean, which is worse than not having it.

*If you ever restore a zip-based deploy*, recover the tool from git history
(`git log --diff-filter=D -- tools/deploy_manifest.py`) rather than rewriting it; the
`_NEVER_RM` list was derived from real incidents and is easy to under-specify from memory.

### `bd-parity-scan` — CLI→GUI parity, derived *(fixed @715)*
Its tool glob was `tools/*.py` — **non-recursive** — so every tool under a subdirectory
(`tools/decomp/`, `tools/audit/`, `tools/data/`) was outside the denominator. **208 → 215 tools,
721 → 739 flags.** "192 of 208 dark" was a fraction of the wrong total.

### `bd-surface-census` / `bd-fullsuite` *(NEW)*
`bd-surface-census`: multi-probe enumeration of every env var / config file / flag / module /
test — used to find the layers the config inventory never scanned. `bd-fullsuite`: full-suite
gate.

### `bd-brief` — *(fixed @715)*
**Three route numbers circulate and none stated its unit:** 971 unique paths · 1000 `url_map`
Rule objects · 1012 ROUTE_INDEX rows (**path × method**; 37 paths carry >1 method). All three
are correct — they looked like drift because nobody labelled them. `bd-brief` now reports the
unit. *A number without its unit is not a fact.*

### `bd-band-derive` — *(improved @718)*
New signal: when a cut **deletes a config key**, band every test that **names** the literal. A
test that does `cfg.get("auto_refresh")` without importing the inventory is invisible to a
file-name band — that put `test_v3_66_285` on stash.

### `bd-lsp` — sandbox · offline code intelligence
Wraps jedi + (offline) pyright. `setup` (install wheels, idempotent), `refs <file>
<symbol>`, `defs <file>`, `check <file>…`, `unused <file>`, `who <file> <symbol>`.

### `bd-curl` — sandbox/stash · CSRF-aware API helper
`bd-curl GET /api/health` · `bd-curl POST /api/x '{"k":"v"}'`. Auto-fetches
`/api/csrf` for write verbs. Flag: `--base <url>` (default localhost:5555).
Stdlib-only; prints `<status>\n<body>`; exit 0 if HTTP < 400.

---

## Checks & readiness (NEW — 12 high-ROI advisory tools)

Read-only advisory unless noted; each predicts an on-stash failure *before* a cut,
or removes recurring friction. `bd-ready` aggregates the cut gates.

### `bd-ready` — sandbox · one-shot cut-readiness preflight
Runs guards + version + changelog + derived-docs + import-edges and prints
PASS/FAIL per gate. The "am I safe to `bd-cut`?" command. Never mutates.

### `bd-guardcheck` — sandbox · guard SHA integrity  *(was `bd-guards`, retired)*
Live SHA-256 of the 7 pinned guard files vs `<tree>/guards.json`, the repo-root manifest and
single source of truth (a STATE.json `guards_full_sha256` baseline is the fallback for the
version-pack workflow, via `--state`). Instant "are my guards byte-intact?" --
`bd-guardcheck --tree .`. A `0 ok, 0 drifted, 7 missing` summary or exit 2 means the pins were
**not** verified; do not read it as a pass.

### `bd-versync` — work tree · version consistency  *(was `bd-ver`, retired)*
`__version__` vs the sole `assert __version__ ==` test pin vs the CHANGELOG top
header. `--tree`/`--work` defaults to the work tree (`bdtools_sec.DEFAULT_WORK`),
not a sandbox path.

Pins come from `tools/build_pin_index.py`'s **AST** index, not a text scan --
so a version string in a fixture literal or in prose is structurally not a pin
and needs no allowlist. The old scan-plus-allowlist is retired: it reported
fixture literals as stray pins (cry-wolf), and its denominator silently
excluded whole idioms. Exit 2 / `CANNOT-EVALUATE` when the tree, the pin-index
tool, or any `tests/*.py` cannot be read -- unknown is a third state and it
fails rather than reporting consistent. Teeth: `tests/test_versync_gate.py`
and `bd-versync --selftest` (delegates to `bd-coretest --only bd-versync`).

### `bd-changelog` — sandbox · CHANGELOG entry validity
Top `## v…` header matches `__version__`, entry is ASCII-only, non-empty (the
release-hygiene gates that fail post-deploy).

### `bd-ascii` — sandbox · non-ASCII / emoji scan
Scans the current CHANGELOG entry (or any file) for non-ASCII — the emoji gate
that hit ×3. `bd-ascii <file>`.

### `bd-regen` — sandbox · derived-doc sync (read-only `--check`, `--write` regenerates)
FUNCTION_INDEX / DEPENDENCY_GRAPH / PIN_INDEX / route counts. `--check` (default)
only runs generators with a real `--check` flag (never writes); `--write`
regenerates. Run up front to avoid the post-vitest abort-and-recut.

### `bd-imports` — sandbox · undeclared import edges (`--update` re-freezes)
Wraps `import_graph_gate --check`; lists new edges and reminds to re-freeze in the
same cut (separate from regenerating DEPENDENCY_GRAPH).

### `bd-since` — sandbox · what changed vs the pinned zip
Lists MODIFIED / ADDED / REMOVED source files vs `BulkDownloader_v3_66_*.zip`
(bd-preflight only asserts identity) with per-file band/regen hints. **Largely redundant now
that the repo is under git** -- see `docs/repo/TOOLCHAIN_PORTABILITY.md`; prefer
`git diff --name-status origin/main` for the changed set, and keep bd-since for the per-file
band/regen hints it adds on top.

### `bd-sym` — sandbox · grep by SYMBOL not symptom
Every occurrence site of a symbol/flag grouped by file with a count, so you fix
all N (the @680 `_DISABLED` 1/5 lesson). `bd-sym _DISABLED [--py-only]`. Uses rg,
falls back to a python walk if rg is absent.

### `bd-docstale` — sandbox · PK doc staleness
Scans PK docs' `verified-against: vN` markers, reports how far each is behind
`__version__` (most drift for hundreds of releases). `--behind N` to gate.

### `bd-packs` — sandbox · offline pack health *(UPDATED @732)*
Presence + zip-integrity of pack_A-D + cloak + the optional E-H tier (via
bd-optpack) in one glance — the "is my upload set complete + non-truncated?" check.

**@732 — `--json` now emits real JSON.** It previously ignored the flag and printed the colour
report *with raw ANSI escapes*, which breaks any parser. Colour is guarded on
`sys.stdout.isatty()`, so pipe / capture / `--json` are all clean; TTY stays coloured.
**A control byte in what a parser reads is a denominator bug, not decoration.**

### `bd-bump` — sandbox · atomic 3-part version bump (`--check` default, `--write` applies)
`bd-bump 3.66.N --title "…"` edits `__version__` + the real test pin(s) +
prepends the CHANGELOG entry (re-emitting the previous header) together, and greps
stray pins. `--check` shows the plan; `--write` applies, then run
bd-versync && bd-changelog && build_pin_index.

## Inspect / impact / package (NEW — 15 more tools)

Advisory/read-only unless noted. Answer "what/where/who/impact" without hand-grepping,
and package the close-time artifacts.

### `bd-pin` — dependency pins + duplicate pin-site finder  *(was `bd-pins`, retired)*
`bd-pin <pkg>`: every site that pins it (the duplicate-pin-sites footgun — tighten all N,
guard-test all); `--all` lists every dep from requirements*.txt.

### `bd-envscan` — BD_* env-var opt-ins (env-tranche gate)
Lists every `os.environ.get("BD_*")` / `env.get("BD_*")` — each is an OPEN env var to
config_surface_inventory. A new backend-only flag should use an undeclared `cfg.get()`
key instead. Advisory.

### `bd-route` — resolve a path to its owning blueprint  *(was `bd-routes`, retired)*
`bd-route /api/x` → owning blueprint from url_map-derived ROUTE_INDEX; `--grep <term>`
to search. Decorator-accurate, not text-grep.

### `bd-deps` — blueprint/module dependency inspector
No arg: all blueprints (module/#routes/#providers). `bd-deps <blueprint>`: its
providers+routes. `bd-deps <module.py>`: reverse — which blueprints depend on it.

### `bd-blast` / `bd-suites` — RETIRED at rev-702
Both were merged into `bd-band-derive` (see above), together with `bd-touched`;
`bd-equiv` certified the replacement SUPERSET with 0 regressions before removal.
Neither ships in `bin/`. Use `bd-band-derive --file <path>` for single-file blast
radius, and `--files a b c` for a changed set (`bd-since` produces the set).

### `bd-ssrf` — outbound-fetch sites without an in-file SSRF guard
Flags server-side fetch sites (requests/httpx/urlopen/http_probe/deep_detect) whose file
has no private-ip/metadata guard — triage for the SSRF-REM HIGH. Advisory.

### `bd-secrets` — committed-secret scan
detect-secrets-lite over bulk_downloader/ (private keys, AKIDs, JWTs, generic
secret assigns); test/fixture hits down-weighted. Exit 1 on a high-confidence hit.

### `bd-coverage` — test-coverage gaps
Pretty-prints COVERAGE_GAPS.json (under-tested files/functions/spans).

### `bd-flakes` — hazard/flaky-test reference
The hard hazards (never full-run tests/; test_perf_lab; don't pkill -9)
+ KNOWN_FLAKES.md. "What will bite my band?"
- **Known defect in the tool:** `toolchain/bin/bd-flakes` (lines 5 and 14) hardcodes
  `tests/test_v3_66_146_nav_guard.py`, which **does not exist** -- the real 146 suites are
  `test_v3_66_146_detection_safety.py` and `test_v3_66_146_runtime_gate.py`, and neither is
  listed. So bd-flakes warns about a phantom while the real files go unmentioned. Fix the
  tool in a separate cut; do not band a path it names without confirming the file exists.

### `bd-kb` — search the PK/KB docs
`bd-kb <term>` greps /mnt/project/*.md grouped by doc. "Which doc said X?"

### `bd-doclint` — PK docs missing a verified-against marker
Complements bd-docstale (which measures how stale MARKED docs are) by finding
unmarked ones that can't be staleness-checked at all.

### `bd-zipcheck` — is this release zip shippable?
Against the ZIP (no extract): __version__, 7 guards present + SHA-match the STATE
baseline, CHANGELOG top match + ASCII, file count. The pre-handoff sanity gate
(verify_release --zip on-stash is still the binding one). Exit 1 on any failure.

### `bd-mkbdsuite` — (re)build `bdsuite_v3_66_<N>.zip`
Packages the live `/home/claude/bin` toolchain + install_bdsuite.sh + bdenv.sh (+
CHANGELOG) with the layout the installer expects. Changelog source priority:
`/home/claude/BDSUITE_CHANGELOG.md` first (session-updated wins), then the PK
copy, then `/home/claude/CHANGELOG.md`. Pre-1980-timestamp-safe. Standalone,
and called by `bd-handoff`.

### `bd-mkauditstate` — (re)build `audit_state_v3_66_<N>.zip`
Zips the live `/home/claude/audit_state` ledger if present, else re-stamps the newest
prior audit_state zip (the "unchanged ledger, re-stamped" case). Standalone, and
called by `bd-handoff`.

## Reconciled + new tools (@682 parallel-session merge)

> **`bd-tools` is the live, authoritative index** — run
> `venv/bin/python toolchain/bin/bd-tools --bin toolchain/bin` for the categorized map with
> one-liners (add a `<term>` to filter). It covered ~81 tools when this section was written;
> the tree now carries ~246 `bd-*` -- re-derive rather than quoting either number, and note
> that **without `--bin` the tool prints "0 tools total" and exits 0**. The entries below
> cover what changed in the merge; bd-tools always reflects the installed truth.

**Superseded (retired) — a parallel session had fuller equivalents; use the RHS:**
- `bd-guards` → **`bd-guardcheck`** (7-guard SHA vs STATE; improved with a robust STATE
  resolver that extracts the baseline from the newest version-pack zip).
- `bd-ver` → **`bd-versync`** (3-part version consistency, fixture allowlist).
- `bd-pins` → **`bd-pin`** (all pin sites for a package; `--all` lists every dep).
- `bd-routes` → **`bd-route`** (path→owning-blueprint from ROUTE_INDEX; `--grep` to search).
- `bd-changelog` / `bd-envscan` — replaced with the fuller versions (same names).

**Added — scoping & session (parallel session's uniques):**
- **`bd-capsweep`** — before calling a catalog item OPEN, prove it isn't built anywhere:
  idf-weighted whole-tree capability sweep → LIKELY BUILT / WEAK / ABSENT.
- **`bd-freshest`** — point every scoping pass at the newest authoritative doc; flags
  version-lagging catalogs + stray zips.
- **`bd-session`** — multi-cut session ledger (arc/deploy_queue derived, not hand-typed).
- **`bd-pinscan`** — flags `== N` magnitude/parity assertions that should be `<= N` ceilings.
- **`bd-bandcheck`** — validate a band target list (real files, no known-hang, no
  test-function-as-path, no leak co-band) before running it.
- **`bd-factcheck`** — derive canonical counts (modules/tests/tools/routes) from the tree,
  flag any doc/STATE number that disagrees (the stale-copy-of-derived-fact shape).
- **`bd-intake`** — copy uploaded zips out of the evicting uploads dir, `unzip -t` validate
  NEW copies (`--verify` re-checks present ones), report what's missing from the
  bootstrap set. Skip-identical (same-size dest not re-copied), known non-boot
  uploads (the corpus) reported-not-copied unless `--all`, ENOSPC-truncated
  copies detected + removed.

**Added — audit / coordination / navigation (new this session):**
- **`bd-audit`** — hydrate the audit_state ledger + mine it for promotable tooling.
  `hydrate` unzips the newest audit_state zip (exec bits preserved) + symlinks `review`.
  `analyze` classifies every audit_state/tools/ script by cross-checking BOTH `/home/claude/bin`
  AND the static PK (`/mnt/project`), with a per-script one-line summary, an import + **runtime-coupling**
  portability verdict (stdlib-only = portable; audit-local / dynamic sibling-load /
  shells-out-to-a-sibling-or-audit-venv / third-party = not), and byte-divergence vs any existing copy. Buckets: candidate bd tool
  (bd-named, portable) · candidate static PK (portable audit-program whose siblings are
  already in PK) · overlaps `<tool>` · audit-only/not-portable · already-in-bin · already-in-PK.
  `promote <name>` stages a portable bd-named candidate into bin (refuses non-portable;
  routes audit-program scripts to the PK instead). Default = hydrate + analyze.
- **`bd-parallel`** — coordinate parallel sessions: `claim` (version+item+files manifest to
  carry between chats), `check <claims…>` (detect version collisions + file overlaps before
  they produce conflicting version.zips), `worktree <name>` / `merge-check <name>` (isolated
  source workstreams within a session), `list`.
- **`bd-tools`** — categorized index + search for the whole toolchain (this list's live form).

**Upgraded:** `bd-ready` now runs a 7-gate preflight (guards + version + changelog +
derived-docs + import-edges + derived-fact-counts + equality-pins). All tools accept both
`--work` and `--tree`.

## Reachability + body contracts -- Wave 5 (NEW @723-728, rev-aw..ba)

The 709-722 program asked *"can an operator REACH this endpoint?"*. This wave asks the two
questions that were still missing, and both of them found shipping bugs.

### bd-body-contract  *(and `tools/body_contract.py`, which SHIPS in the release so a test can gate on it)*

**"Does the body satisfy the contract?"** -- the question no gate was asking.

Every gate we owned scored two DEAD CONTROLS as WIRED:

    endpoint_reachability : "does a control reach this endpoint?"    -> yes
    bd-fe-dead-control    : "does this control reach anything?"      -> yes
    test_gui_parity       : "is the route literal in the FE source?" -> yes

724's "Delete ALL jobs" posted `{}` to an endpoint demanding `{urls}` (400 every time, *after* a
typed hard-confirm). 726's "Start import" posted `{}` to an endpoint needing `{text}`, with no
field on the page able to supply it. Both had shipped for months.

It does not INFER the contract, it **EXECUTES** it: resolve each frontend call to the real Flask
rule via `url_map`, replay the body the control ACTUALLY SENDS, read the answer.

**TYPE-AWARE (@727).** `apiPost(path, payload: unknown)` declares nothing useful, but the
*argument expression* at each call site has an inferred type. `frontend/scripts/body_types.mjs`
walks the TS program, asks the checker what each body IS, and synthesizes a type-directed sample.

```
bd-body-contract                  # 123 type-resolved call sites
  OK       accepted by the real endpoint
  KEYS     type-correct key set -> the empty-body class is CLOSED for it
  DEAD     body has NO keys and the endpoint refuses {}   <- pinned at 0
  UNKNOWN  open dict / unresolvable type                  <- NOT A PASS. READ IT.
```

**WHAT IT REFUSES TO CLAIM.** A 400 on a type-correct key set is **not** a dead control. The
synthetic values (`"x"`) are not real site ids or filenames, and a probe **cannot distinguish
"missing key" from "invalid value" when the endpoint reports both identically** --
`/api/queue/v2/cancel` answers *"unknown site_id"* to BOTH. Settling the UNKNOWNs needs **REAL
FIXTURES** (an integration harness). That limit is stated, not papered over.

> It took **SIX** iterations; the first five reported 7, then 99, then 36, then 10, then 9 false
> positives. What killed the bad rule was a **live fact**: it flagged `/api/tools/run` DEAD, and we
> had watched that work at 719. **Verify hits by hand before believing any count.**

### bd-regen-order  *(EXTENDED @725 -- now checks the REACHABILITY RATCHET)*

Regenerates every derived artifact in THE ONE CORRECT ORDER
(`gui_parity -> ROUTE_INDEX -> ENDPOINT_CATALOG -> dependency_graph -> function_index ->
pin_index -> route-count gate`), and since 725 **also checks the reachability ratchet on every
run**.

724 went RED on stash because wiring 7 endpoints dropped `dark` 127->120 while the ledger stayed
pinned at 127 -- and nothing regenerated it. *A ratchet nobody regenerates is a ratchet that only
ever fires where it costs the most.* It has since caught the drift **in the sandbox** on every
wiring cut.

It runs that check under **`work/venv` with PYTHONPATH stripped** (see FG-CENSUS-NEEDS-THE-VENV),
and if the venv is missing it **says it cannot verify and FAILS** rather than falling back to a
python whose missing imports would silently shrink the denominator.

```
bd-regen-order                     # regen + CHECK route_map / import_graph / reachability
bd-regen-order --declare-surface   # an INTENDED route add/remove
bd-regen-order --declare-edges     # an INTENDED import edge
bd-regen-order --declare-reach     # an INTENDED wiring change (re-pins dark_count)
```

The declares are **declarations of intent**, never automatic. The gate must fire first.

### Resolver family fix (@723-726, rev-aw/az) -- bd-state, bd-since, bd-treecheck, bd-guardcheck, bd-zipcheck

All five resolved the current source zip / session pack by globbing **read-only**
`/mnt/user-data/uploads`, which a pin corrected in-session can never reach and which **evicts
mid-session**. Four of them *looked* correct -- they globbed both directories -- but sorted the
**path strings** with `reverse=True`, and `"/mnt..." > "/home..."`, so the stale copy always won
**regardless of version**. `bd-since` diffed the work tree against a four-release-stale zip.

> **The denominator contained the right file. The ORDERING discarded it.** Sort by the VERSION.
> **And when ONE resolver has this bug, SWEEP THE WHOLE TOOLCHAIN.**


## Speed / reliability / robustness (NEW)

Fast health gates, a parallel test runner, and rollback safety.

### `bd-treecheck` — robustness · is the tree structurally sound?
Parses every `.py` (ast), loads every `.json`, confirms critical files aren't
empty — the generalized verify-after-mutation gate that catches corruption (e.g. a
botched edit → SyntaxError) across the whole tree before the slow build chain.
`--changed` (only files changed vs the pinned zip) is the fast inner-loop; `--tests`
adds the test suite; `--py-only` skips json. Exit 1 on the first structural failure.

### `bd-smoke` — reliability · import smoke of the core modules
Imports `bulk_downloader` + the heavy `app` module (+ core modules) via the service
venv with the deps path (`tree:/tmp/prestaged_site_packages`), so a broken import
surfaces in ~2s instead of via a slow band. Catches what unit tests miss (an
import-time error before any test runs).

### `bd-selfcheck` — reliability · verify the toolchain itself
Statically (no execution — running tools like `bd-boot` has side effects) checks
every bd-* tool: valid shebang, executable bit, Python syntax, an entrypoint, and
byte-identity vs the PK bundle. ~0.2s. Run after any toolchain change or at session
start so a broken tool can't silently corrupt work.

### `bd-parband` — speed · run targeted suites in parallel
Runs multiple suites concurrently, each in its own subprocess with its own
`BD_HOME` (mktemp) — so it's faster AND the state-leak co-band hazards can't cross
suites (per-process isolation). Refuses the known hangers, a bare `tests/` dir,
and (@868) any suite path that does not exist — exit 2, nothing dispatched, no
results file written. `--jobs N` `--timeout S`. Writes the run to
`<checkout>/.bd_last_band.json` (`bdtools_sec.DEFAULT_WORK`), override with
`BD_LAST_BAND`. The `/home/claude/` path this line carried until @868 was
zip-era and had never been where the tool writes.

### `bd-retest` — reliability · re-run only the last band's failures
Reads the last `bd-parband` results and re-runs each failed suite N times: passes
on retry ⇒ FLAKE, fails every time ⇒ REAL failure. `--retries N`. Avoids re-running
the whole band to tell a flake from a real break. Exit 2 if the ledger names a
suite that no longer exists (@868): `run_tests.py` broad-runs an unknown path, a
green whole-tree run reads as FLAKE, and that DELETES a real failure. Resolves
the results path identically to `bd-parband` — edit them together.

### `bd-checkpoint` — robustness · wholesale source checkpoint + restore
`save <name>` tars the source dirs; `restore <name>` reverts them in one move;
`diff <name>` lists what changed since; `list`. Cheap insurance before a big
refactor/decomposition (bd-snapshot is per-file; this is the whole source set).

### `bd-timeit` — speed · wall-clock a command
`bd-timeit [--label L] -- <cmd …>` prints runtime + exit code. Find the slow cut
gate (e.g. `bd-timeit -- bd-regen`). Exits with the wrapped command's code.

---

## Egress / SSRF primitives — Wave 0 (NEW @688 rev-f)

The five load-bearing egress tools from `BD_TOOLS_SCOPED_ROADMAP.md` section 4,
plus the shared library they (and every later egress wave) import. All are
stdlib-only, read-only, deterministic + offline by default, ASCII, carry
`--json` + `--work/--tree` + a `--selftest` with mandatory negative controls.
These are toolchain additions — no version bump, no guard touch.

### `bdtools_sec.py` — shared library (not a CLI tool)
URL network-risk classification, the bd-ssrf sink inventory (byte-parity
FETCH/GUARD definitions), the shared `Finding` schema + `--json` emitter, and
STATE helpers that read `guards_full_sha256` — never the 8-char `guards` map
(the bdkit_common false-alarm lesson; the selftest asserts against the REAL
STATE schema: 64-hex shas, 7 guards, short-map trap present).
Self-test: `python3 /home/claude/bin/bdtools_sec.py --selftest`.

### `bd-url-classify` — the egress primitive
Network-risk verdict per URL: loopback / private (RFC1918+ULA) / link-local /
cloud-metadata (`169.254.169.254`, `fd00:ec2::254`, `metadata.google.internal`)
/ reserved / public; flags embedded userinfo (SSRF obfuscation, fail-closed on
non-provably-public hosts); `--resolve-dns` opt-in is DNS-rebind aware (any
private resolution → blocked). `--stdin` for pipelines. Exit 1 on any block —
usable as a gate. Every egress tool calls this instead of re-deriving.

### `bd-path-scan` — unguarded filesystem sinks
Flags `os.path.join` / `Path /` / `open(...,'w')` / `shutil.*` / archive-extract
sinks with no confinement hint (`safe_join`/`is_relative_to`/`commonpath`/
`realpath` etc.) within ±5 lines. Rule `BD-PATH-UNGUARDED`; severity high when
externally-derived input appears in the window. `--fail-on-findings` flips the
exit code. Pure-literal paths are skipped (not traversal).

### `bd-secret-floor` — canonical-redactor floor prover
Auto-detects the canonical redactor (most-imported `redact`/`scrub`/`sanitize`
symbol; override with `--canonical mod:sym`). Secret-bearing output sinks
(logging / log-export writers) in modules off the canonical path →
`BD-REDACT-FLOOR-GAP` (high); ad-hoc `re.sub` secret redaction →
`BD-REDACT-ADHOC` (medium, suppresses the GAP double-count for that module).
Feeds bd-secret-taint / bd-redaction-compiler / bd-scrub-proof (Wave 4+).

### `bd-fetch-policy` — the ONE outbound-fetch policy
`--emit` prints a self-contained `fetch_policy.py` (`safe_fetch`: scheme
allowlist, public-host requirement, per-hop redirect re-validation with a cap,
timeout floor, proxy/VPN hook, fail-closed), **ast.parse-verified before
printing** (the bd-bump lesson). `--check` classifies every fetch sink as
conforming / partial / raw and reports conformance % — the target
`bd-fetch-migration` migrates toward (688 baseline: 0/208).

### `bd-host-guard` — the SSRF-REM triage matrix
Sinks × guard × literal-destination risk × write-route × vpn-gate, sorted
unguarded-highest-risk first — the ranked worklist for the SSRF-REM tail.
Selftest asserts guarded-count **parity with bd-ssrf's own definitions**
(catches double-counting drift; 35/67 on the 688 tree). `--only unguarded`
filters. Fixture caveat: bd-ssrf's guard lexicon matches the literal IPv4
metadata IP inside a fetched URL, so metadata fixtures must use
`fd00:ec2::254`.

---

## Security static audits — Wave 1 (NEW @688 rev-g)

Eight Phase-C-surface audits from roadmap wave 1 (bd-static-ban dropped;
bd-challenge-guard skipped — operator calls). All reuse `bdtools_sec.py`,
`--selftest` with negative controls, `--json`, `--work/--tree`, read-only.

### `bd-redaction-scan` — ad-hoc redaction finder
Enumerates every redaction *operation* keyed on secret vocabulary
(token/secret/auth/csrf/cookie/signature) across log/export/report/clipboard
surfaces and flags the ones not delegating to the canonical helper
(`BD-REDACT-ADHOC`). Complements bd-secret-floor (floor prover).

### `bd-csrf-audit` — mutating-route CSRF coverage
Reads ENDPOINT_CATALOG.md (authoritative), lists every POST/PUT/PATCH/DELETE
marked `CSRF:no`, subtracts the documented bootstrap bypass (`/api/pair/redeem`)
→ `BD-CSRF-UNGUARDED`. `--only unguarded`, `--fail-on-unguarded`. Note: the
`/cockpit/api/*` hits are a catalog-vs-`_check_csrf` discrepancy (PHC-1 routes
them through the same gate) worth a doc/cut look.

### `bd-path-guard` — confinement-helper coverage
Coverage complement to bd-path-scan: discovers the tree's real confinement
helpers (`_is_safe_path`, `_confine_extract`) and reports write-surface modules
that use none (`BD-PATH-NO-CONFINE`).

### `bd-devsurf-audit` — dev/cockpit surface + posture
Enumerates `/api/dev/*` + `/cockpit/api/*`, reads the real `is_dev_mode` to
report default posture (DEFAULT-ON) + kill-switch (`BD_DEV_MODE_DISABLE`) +
env unlocks, and marks mutating routes.

### `bd-defaults-audit` — dangerous defaults
dev-on, `0.0.0.0` bind, wildcard/credentialed CORS, `autoescape=False`,
`verify=False`, `shell=True`; each finding notes whether a mitigation
(kill-switch/re-gate) is documented. Framed against the single-user-LAN model.

### `bd-html-taint` — XSS-sink finder (PY + FE)
`Markup`/`render_template_string`/`autoescape=False`/`.to_html`/`|safe` (PY) and
`innerHTML`/`dangerouslySetInnerHTML` (FE) with a ±4-line sanitizer-hint window;
strips comments to avoid false positives; severity high on non-literal sinks.
`--only high`.

### `bd-report-sanitizer` — canonical report HTML sanitizer + check
`--emit` a stdlib-only allowlist `sanitize_report_html()` (drops script/style,
strips `on*`, blocks `javascript:`/`data:`/`vbscript:` hrefs), ast-verified.
`--check` finds markdown/report render sinks lacking a sanitizer.

### `bd-fetch-migration` — raw-fetch → safe_fetch worklist
Risk-ordered migration plan built on bd-fetch-policy's conformance view:
literal-URL/low-site-count files batch first (5/batch) for small reviewable
cuts. `--batch N`, `--emit-diff <file>` sketches one file's changes. No edits.

---

## Interop readiness -- Wave 2 (NEW @688 rev-h)

Four capability-probing readiness checkers for the Batch B interop blockers.
Each probes the REAL runtime/module (capability, not a presence flag). All
`--selftest` green, `--json`, read-only.

### `bd-yt-dlp-check` -- yt-dlp readiness + safe invocation
Resolves + versions yt-dlp, counts extractors, audits invocation sites for
`shell=True` (`BD-YTDLP-SHELL`). Exit 1 if absent or unsafe. Run under `bd`.

### `bd-jd-check` -- JDownloader connectivity
Loads the app's own `jd_bridge.JDClient.diagnose()` (TCP then `/jd/version`)
so the tool and runtime agree. In-sandbox JD absent = clean not-reachable +
operator hint. `--host/--port`.

### `bd-crx-check` -- MV3 extension audit
Static audit of `extension/manifest.json`: confirms MV3, enumerates + flags
broad permissions (`<all_urls>`/tabs/scripting/cookies/webRequest/debugger...),
verifies every referenced file (service_worker/popup/options/icons) exists.

### `bd-node-plugin-check` -- Node plugin runtime
Probes the real node binary the app would use (`BD_PLUGINS_NODE_BIN` > config
> `node`) and runs the plugin lifecycle contract against a temp fixture
(`--manifest` one-JSON-line + event stdin->stdout round-trip), confirming the
tree's `plugin_node.probe_manifest` agrees. Absent node = clean skip.

---

## Guard fuzz + fixtures -- Wave 3 (NEW @688 rev-i)

Five tools that fuzz the REAL guards (not reimplementations). `--selftest`
green; the two differential fuzzers compute ground truth independently.

### `bd-secret-fixture` -- deterministic fake-secret corpus
jwt/apikey/bearer/cookie/signed_url/csrf/privkey/opaque; every value carries a
visible `FAKEsecret` sentinel. Import-safe. Consumed by canary + fuzz tools.

### `bd-secret-canary` -- plant secrets, verify the real scrubber catches them
Embeds the corpus in json/log/url contexts, runs `tools/capture_scrub`, asserts
no FAKE body survives (`BD-CANARY-LEAK`). 0 leaks / 84 canaries on 688.

### `bd-fuzz-pathguard` -- differential fuzz of `backup._is_safe_path`
Hammers the real guard with traversal payloads (../, encoded, absolute,
symlink, unicode, long) and compares to a realpath+commonpath oracle. Reports
`BD-PATHGUARD-ESCAPE` (guard says safe but escapes) + over-blocks. Proven clean.

### `bd-fuzz-redaction` -- mutation fuzz of the real scrubber
Mutates secret context (nested json / url param / header / base64 / kv-variant)
through `capture_scrub`; `BD-REDACT-FUZZ-LEAK` on survivors. Harness-correctness
is the gate.

### `bd-fuzz-urlguard` -- adversarial battery vs the classifier
26 payloads (IPv6 forms, encoded IPs, userinfo, metadata, mixed-case) with known
verdicts; `BD-URLGUARD-BYPASS` on should-block-classified-allowed. Caught the
127.1/decimal-IP bypass now fixed in bdtools_sec + the emitted safe_fetch.

### `bd-fuzz-import` -- hostile-input fuzz of parse/import endpoints
39 adversarial payloads (malformed/oversized JSON, wrong types, huge ints,
NaN/Infinity, dunder keys, recursion bombs) against the real
`plugins.api_compatible` / `_read_manifest_ast` / `json.loads`. Asserts graceful
failure (`BD-IMPORT-CRASH` on an uncaught crash). Parsers proven robust on 688.

---

## Taint dataflow -- Wave 4 (NEW @688 rev-l)

Five root-cause finders on a shared intra-procedural AST taint engine
(`bdtools_taint.py`): mark a source expr, propagate through assignments/
f-strings/concats, flag when it reaches a sink. Function-local + advisory
(call per-hop for cross-function flows). All `--selftest` green + deterministic.

### `bd-url-taint` -- untrusted URL -> fetch
request.args/json + url params -> network sinks (network receivers only). 32.
### `bd-egress-taint` -- egress settings + URL -> fetch/launch. 89.
### `bd-secret-taint` -- secrets (secrets/vault/cookies) -> exfil (write/jsonify/
log/clipboard). 31. Full-attribute source match (not any `.get`).
### `bd-path-taint` -- untrusted names -> path join + write sinks. 66.
### `bd-template-taint` -- secrets -> template writers/serializers. 11.

`bdtools_taint.py` is the shared library (self-test:
`python3 /home/claude/bin/bdtools_taint.py --selftest`).

---

## netns / VPN egress proof -- Wave 5 (NEW @688 rev-m; CORRECTED @729)

Six F5 launch-routing follow-on tools. pack_G/G2 supply the netns toolchain
(ip/nft/wg/dnsmasq).

**CORRECTED @729 -- netns creation WORKS IN-SANDBOX. It is not stash-only.**
This section (and `bd-netns-proof` itself) used to assert *"netns creation needs
CAP_NET_ADMIN (stash)"*. **That claim was never tested.** `bd-netns-proof` printed
it as a hardcoded literal while probing only two things -- the ns inode and whether
`ip` was on PATH -- neither of which can see whether a netns can be CREATED. A check
whose denominator excludes the question, answering it anyway: the house shape, and it
cost us the capability rather than a RED.

Derived ground truth (after `bd-optpack install G --apt`):

    id -u                 -> 0
    CapEff                -> 000001fffeffffff      (CAP_NET_ADMIN set)
    ip netns add <ns>     -> exit 0
    ns inode              -> 4026532268  vs host 4026531833   (DISTINCT)
    veth into the ns      -> up, addressed
    nft `oifname vX counter` in-ns -> packets 5, bytes 145 after a UDP sendto

So: distinct namespace, iface-scoped nftables policy, and packet counters that
actually move. **F5 netns Phase 2/3 can be validated IN-SANDBOX, not deferred.**
(WireGuard's *kernel module* is still absent -- `wg` the userspace tool installs;
veth stands in for proving iface-scoped egress policy. ICMP is restricted: use a UDP
sendto probe + nft counters.)

### `bd-netns-proof` -- process net-ns evidence. **@729: the create verdict is now
DERIVED, not asserted** -- it attempts a real create in a throwaway ns, verifies a
distinct inode, tears down, and reports **CAPABLE / not-permitted / UNKNOWN**, where
*unknown* is a real third state that fails rather than reading as either yes or no.
`--no-create` for a pure read-only run; `--logic-only` for the comparison logic.
### `bd-netns-launch-proof` -- runtime inode + static unwrapped-launch scan.
### `bd-vpn-proof` / `bd-egress-proof` -- resolve the egress decision via real
vpn_runtime; a vpn_required site resolving direct is a DIRECT LEAK.
### `bd-vpn-required` -- assert fail-closed (proxy OR VPNRequiredError).
### `bd-vpn-egress-scan` -- static: egress functions with no vpn/proxy reference
(BD-VPN-BYPASS; high for control-plane). 116 flagged on 688.

---

## Cut / release

### `bd-precut` — sandbox · predict the cut (advisory) *(UPDATED @732)*
Auto-detects baseline + root and runs `tools/precut_check.py`: changed files,
version-consistency, pin scan, in-sync drift, guard SHAs, predicted regens. Then
`bd-footguns --check`, `bd-ratchet`, `bd-coretest`. Flags: `--baseline <zip>`, `--root <dir>`,
`--json`, `--no-insync`, `--no-envscan`, `--no-coretest`. Predictor only — never gates.

**@732:** `_run_insync()` existed, was documented, had a `--no-insync` flag — and was **CALLED
BY NOTHING** (*a skip flag for a check that never runs is a silencer with no siren attached*).
Now wired, and it **DEFERS** every in-sync suite an active `kind=insync` footgun detector will
run later **in the same invocation** — the tool was running 6 of its 7 in-sync suites TWICE,
same tree, seconds apart. Coverage is **DERIVED from the live registry**: deactivate a footgun
and its suite returns to this step automatically.

**NOTE — `bd-precut` is SELF_REFERENTIAL to `bd-tool-lint`.** It orchestrates the gate battery
(spawns bd-footguns' full fan-out, bd-coretest, bd-ratchet, run_tests), so the lint must not
runtime-probe it: doing so re-runs everything recursively under core contention and its cost
becomes a function of the load the probe itself creates. It is read-only (recursion, not
mutation) — but excluded all the same.

### `bd-cut` — sandbox · the codified finalize *(UPDATED)*
Runs the whole cut: `precut(adv) → tsc → vitest → vite build → regen
gui_parity → [bump + regen PIN/ROUTE] → build_release --skip-tests → extract →
band → verify_release → summary`. Never deploys.
- Flags:
  - `--version V` — new semver (omit to skip the bump = dry typecheck+build).
  - `--changelog MSG` — required with `--version`.
  - `--baseline <zip>` — enables precut + auto `--approved-frontend` derivation.
  - `--suites …` — band suites (default: gui_parity, contracts, parity_method_aware,
    slice4). On a `--version` run, `test_pin_index_in_sync` + `test_kb_golden_questions`
    are **auto-unioned** in (so a bump can't ship a stale PIN_INDEX).
    **FORMAT @729: SPACE-separated `tests/<name>.py` PATHS. Not a comma-joined string,
    and not bare suite names.** A comma string is read as ONE filename, matches nothing in
    the zip, and ABORTs at step 8 with the *misleading* message *"no band suites present in
    the zip -- NO-CUT (check --suites names)"* — after the whole build has already run. That
    costs a full rebuild. (This is the sibling of the known
    test-FUNCTION-passed-as-a-FILE trap, which instead falls back to a broad run → timeout →
    band ABORT.)
  - `--resume-zip <zip>` *(NEW)* — **skip the build; re-run band + verify + summary
    on an existing zip.** For a flaky/missing suite, a suite you just added, or a
    re-verify. (A *source* fix still needs a full re-cut.)
  - `--rm-runtime-db` *(NEW)* — auto-remove `video_hashes.db` /
    `downloader_history.db*` from the tree before build (else bd-cut refuses with the
    exact `rm` — they trip the diff-gate).
  - `--no-build` — stop after inventory; `--work DIR`; `--out DIR`.
- *(NEW)* Step **4b** regenerates `PIN_INDEX.json` + `ROUTE_INDEX.json` after the
  bump (runs even on a resumed partial cut). Hang-prone steps now have timeouts
  (a wedged suite fails its box instead of the session). Exit 0 = CUT-ready
  (band Failed:0 + verify_release PASS); 1 = NO-CUT.

---

## Close

### `bd-ship` — sandbox · one-command close *(NEW)*
The mirror of `bd-boot`: `bd-precut(adv) → bd-cut → bd-handoff(→bd-pack)`, halting
on the first failure (no handoff on a NO-CUT). Derives the built zip path for you.
- Flags: `--version V` + `--changelog MSG` (required); `--baseline <zip>`;
  `--pack-dir DIR` (default `/home/claude/work_pack`); `--out DIR`; `--suites …`;
  `--rm-runtime-db`; `--no-handoff` (stop after the cut if the pack prose isn't ready).
- **Prereq:** author the pack prose first (STATE narrative + `KB_HANDOFF_v3_66_<n>.md`)
  — bd-handoff/bd-pack fix the byte-facts and lint, not your narrative. Never deploys.

### `bd-handoff` — sandbox · repin STATE from the built zip *(now also builds bdsuite + audit_state)*
Mechanically repins the byte-derivable `STATE.json` fields from the zip (full-zip
sha256, file_count, version, 7 guard SHAs, generated_at), archives old `changes_<N>`,
schema-gates, self-checks via `bd-state`, **regenerates `bdsuite_v3_66_<N>.zip` +
`audit_state_v3_66_<N>.zip`** (via bd-mkbdsuite / bd-mkauditstate), then calls `bd-pack`
— so one run produces the whole next-session upload set.
- Flags: `--version V` + `--zip <built.zip>` (required); `--pack-dir`; `--out`;
  `--keep-changes N` (default 10); `--no-pack` (repin + self-check + bdsuite/audit only);
  `--no-bdsuite` / `--no-audit` (skip those artifacts).
- You still author the prose; it fixes only what can be wrong if hand-typed. Exit 0
  = repinned + self-checked + artifacts built + (packed); 1 = a gate failed.

### `bd-pack` — sandbox · lint + zip the next-session pack
Cross-checks the pack vs `STATE.json` and **refuses to zip** on: incomplete required
set, a stale `LIVE = <older>` banner, more than one / an older `KB_HANDOFF`, or
TASK_TRACKER drift. Flags: `--dir <pack-dir>`, `--out <dir>`. Exit 0 = linted +
zipped; 1 = lint failure (nothing zipped).

---

## Deploy / verify

### `bd-verify-live` — **stash** · confirm a deploy landed *(NEW)*
Run after you deploy. Does **not** deploy. Confirms `/api/health` reports the
expected version (catches the stale-bytecode trap) and that the **service venv**
resolves `cloak.resolve_backend() == cloakbrowser` (catches the venv-vs-system-python
trap).
- Flags: `--version V` (required); `--base <url>` (default `http://localhost:5555`);
  `--bd-dir <dir>` (default `~/BulkDownloader`, for the `venv/bin/python` probe);
  `--skip-backend` (health/version only). Stdlib-only. Exit 0 = verified live; 1 =
  version mismatch or wrong backend (prints the cache-clear/restart fix).

---

## In-sandbox test harnesses

These exercise the REAL BD code paths in the sandbox (headless Chromium drives real
sites; the Flask app runs via `app.test_client()`; downloads run against public
sample media). They ride the version.zip `kit/` overlay (self-persisting), are
read-only/gated, and none deploy. **Ethics floor:** public non-adult demos +
published-credential practice sites only; challenge sites are DETECTED and handed
off, never solved; redaction is exercised, not bypassed; registry sites are
off-limits (player-family detection is site-agnostic, so synthetic fixtures suffice).

### `bd-corpus` — sandbox · live recognizer-corpus puller
Renders sanctioned player demos headless, runs the real redactor + residual-scan
gate, then the real recognizer, and pins a verdict. `--group players|auth|challenge`,
`--url`, `--click`, `--all`, `--list`. Multi-step players: `--steps <csv of selectors>`,
`--step-recipe {dashjs-drm|shaka|theoplayer}`, `--list-recipes`. `--challenge-detect`
is detect-only (fails if a challenge is cleared). Off-allowlist URLs are REFUSED.

### `bd-dltest` — sandbox · download-pipeline regression
Drives `multi_conn.download` (direct/progressive) and `hls_downloader.download`
(HLS/DASH, capped by OUTPUT duration via `-t`, not wall-clock) against an allowlist
of public-domain/CC/vendor-official media, then `ffprobe`-verifies codec/dims/
duration + byte==content-length. `--group direct|hls|dash`, `--all`, `--list`.
Off-allowlist REFUSED.

### `bd-runner-nav` — sandbox · multi-step listing → download
Drives the real `/api/scrape_listing` route (server-side fetch → extract video
anchors). `--ssrf-check` asserts BD REFUSES loopback/metadata/private hosts (400).
`--group listings` scrapes allowlisted public HTML listings; `--chain` runs the
full listing → pick a discovered link → download → verify chain. JS-rendered
listings stay browser-extension territory (the route is HTML-heuristic).

### `bd-opv` — sandbox · sandboxable operator-verification suite
Runs the OPV checks that don't need a display/device. Registry entries are
`(id, class, fn)` with classes `sandbox`/`partial`/`gated`; each returns
`(status, detail)`. `--only <id>`, `--include-gated`. Exit non-zero on FAIL.
Sandbox-PASS coverage grows as gated checks are wired to real code paths; the
current PASS set + counts live in STATE (`opv_session`), never here. Many checks
need the advanced capability layer (headed display, netns, tesseract, SMTP/webpush/
Prometheus deps) — run `bd-sbcap` first. bd-opv **re-execs once under the venv
python** if the ambient playwright's chromium build isn't in the cache, so the
browser checks work whether invoked bare or under `bd`.

### `bd-sbcap` — sandbox · advanced capability provisioner *(NEW)*
Idempotent. Sets up the layer the display/netns/notify/soak/render OPV checks need,
on top of `bd-boot`: the headed-display stack (Xvfb :99 + fluxbox + x11vnc(5900) +
websockify/noVNC(6080)), netns tooling (iproute2/nftables/wireguard-tools) + tcpdump,
tesseract-ocr, sqlite3/jq, and the pip deps (aiosmtpd, pywebpush, pyzbar, freezegun,
locust, memray, qrcode, pillow, prometheus-client, pytesseract) into BOTH the venv AND
the prestaged path, plus noVNC + axe-core. `bd-sbcap` = provision; `bd-sbcap --check`
= verify without changing anything. **As of @539 it self-fixes the three gaps that used
to need manual work:** G1 stages prometheus_client + pyzbar into the prestaged path (so
bd-opv's METRICS/QR resolve), G2 stages render_check_sb.py to `~/.sbcap/` (so OPV-RENDER
finds it), G3 symlinks the chromium build headed playwright expects. It also pins
cryptography during the venv install (pywebpush footgun) and fast-paths when the display
stack is already up. Rides the version.zip `kit/` overlay, so it self-persists. For a
no-network provision use `bd_sbcap_offline_pack_v3_66_539.zip` (same fixes). Full detail +
the footgun encyclopedia: `SANDBOX_CAPABILITY_LAYER.md`.

### `bd-novnc` — sandbox · headless element-PICK + DOM-analyzer workbench
`--check` (default) runs OPV-PICK (inject `dom_overlay.picker_script()` → headless
click → `inspect_pick.build_selector` derives a stable selector) and OPV-F2.6
(`selector_playground.evaluate_selectors` + a review-only `pin_candidate`). Neither
needs a display. `--only pick|workbench`. `--serve [--port]` brings up an OPTIONAL
noVNC display for interactive watching (reports honestly if the x11vnc/websockify/
noVNC stack isn't installed; non-fatal).

### `bd-proxy` — sandbox · no-leak gate over the redaction chain
Seeds a capture with every secret kind, runs it through `capture_redact.scrub_headers`
(capture stage) + `capture_artifact_redact.redact_capture` (sink stage), serializes
to disk, and byte-scans for any surviving literal. Default runs the full gate +
documents the sink boundary (the sink trusts upstream `scrub_headers` for raw
auth-header/cookie *values*). `--sink-only` shows that boundary explicitly; `--wire`
adds a mitmproxy wire→scrub check **(install mitmproxy in a THROWAWAY venv — it
mutates the service venv's cryptography)**.

### `bd-vpnlab` — sandbox · VPN kill-switch gate
`KS-PLAN` drives BD's real `vpn_kill_switch_system.plan()`/`available()` (Windows/
netsh; `available()` is False on Linux by design; `plan()` is pure and emits
endpoint+lo+LAN allow + block-all-out). `KS-ENFORCE` stands up a netns + nftables
default-drop egress policy and proves via packet counters that egress DROPS when the
tunnel interface is down (no leak). `--logic-only` runs just the pure half.
Auto-SKIPs if the netns primitives are absent (needs root + `ip`/`nft` +
`/dev/net/tun`; `apt-get install iproute2 nftables wireguard-tools`). Proves the
iface-scoped egress policy, not a live WireGuard handshake.

---

## Safety / maintenance

### `bd-doctor` — sandbox · read-only triage *(NEW)*
One shot: tools on PATH, background services up, work tree + its version,
`prestaged_site_packages`/Flask, Playwright chromium, `STATE.json` readability +
footgun count. Flag: `--work DIR`. Exit 0 = no critical issue; 1 = a critical check
failed (warnings don't fail).

### `bd-rollback` — sandbox · prepare a rollback archive *(NEW)*
Thin wrapper over `tools/rollback.py` with tool + source-zip auto-discovery.
Prepares an archive; does **not** deploy.
- `bd-rollback --archive 3.66.363` (auto `--from` highest zip) · `--from <zip>` ·
  args after `--` pass through to `rollback.py`. Exit = `rollback.py`'s code (2 if
  tool/zip not found).

### `bd-reconcile` — sandbox · tracker hygiene
Mechanizes the safe parts of the task-tracker reconcile: `tasktracker_gen
--audit/--render/--check` + meta-staleness. Default read-only; `--render`
regenerates first. Exit 0 = clean & in-sync; 1 = defect/drift.

---

## The two footguns these close (this session)
- **Stale `PIN_INDEX` after a bump** → `bd-cut` step 4b + auto-banded pin/kb suites.
- **`video_hashes.db` tripping the diff-gate** → `bd-cut` runtime-db preflight
  (`--rm-runtime-db` to auto-clean).
Plus: retry without rebuilding (`bd-cut --resume-zip`), hang protection (per-step
timeouts), and the new dev-loop/visual/deploy-verify front-doors.

---

## Added @v3.66.728 (toolchain session)

### bd-job -- detached job runner  [Dev loop]
THE EXEC LIMIT IS NOT A HARD CEILING. `setsid nohup <cmd> &` with stdio redirected survives
the bash_tool exec boundary -- proven by running the full 618s runtime lint to completion
across calls. Five tools had each grown a bespoke chunk/resume flag (bd-boot, bd-prestage,
bd-cut --skip-fe, bd-deep-capture --start/--count, bd-sbcap) to work around a constraint
that does not bind.

    bd-job start --name X -- <cmd...>    # launch detached, returns immediately
    bd-job status|tail|wait|kill|reap X

UNKNOWN is a third state and it FAILS. `reap` REFUSES to delete a RUNNING job -- the wrapper
writes `rc` into that dir on exit, so deleting it mid-flight makes the outcome permanently
unknowable. (This is not hypothetical: an `rm -rf .bd_jobs` during a 618s lint did exactly
that, and the tool reported UNKNOWN rather than guessing "done".)

### bd-tool-smoke -- does the tool actually RUN?  [Toolchain self-governance]
The check nothing else made. bd-guardcheck -- a RELEASE-GATING guard tool -- shipped at 726
AND 728 with a helper calling `re.findall()` and no `import re`. NameError on every real
invocation. Every existing check passed it:

    bd-tool-lint --gate --no-runtime    PASS "toolchain clean"
    bd-tool-lint --gate (FULL runtime)  PASS "toolchain clean"   <- runs --selftest
    bd-guardcheck --selftest            SELFTEST PASS
    bd-guardcheck --help                clean (argparse short-circuits)
    bd-guardcheck  (real run)           NameError, exit 1        <- ONLY this

The runtime lint's denominator is "what the SELFTEST exercises", not "does the tool WORK".
Static undefined-name pass; no execution, no mutation. `--gate` now blocks bd-mkbdsuite.
`--run` additionally invokes each tool for real: kills PROCESS GROUPS (orphaned grandchildren
otherwise outlive the kill and race the restore), excludes self, snapshots + restores +
VERIFIES the work tree, and reports TIMEOUT as DID-NOT-COMPLETE (unknown) -- never as a crash,
because bd-tool-lint legitimately takes ~10 minutes.

### bd-consumer-graph -- reverse index  [Impact / change]
What NAMES this symbol / module / config key / route / tool.
    bd-consumer-graph <name>          # who names this
    bd-consumer-graph --module X      # BAND view (bd-band-derive Signal 4's primitive)
    bd-consumer-graph --dead-tools    # used / listed-only / orphan
A CATALOG LISTING IS NOT A USE: bd-tools names all 237 tools, so counting it made every tool
"referenced" by construction. Excluding the catalogs: 84 of 241 are LISTED-ONLY. Not proof of
death (an operator may run a tool by hand) -- review candidates, not a deletion list.

### bd-deploy-rehearse -- run the gates against the POST-OVERLAY tree  [Deploy / verify]
718 went RED on stash with a CORRECT release zip: a file deleted at 716 orphaned on disk, and
the graph gates GLOB THE DISK, so the ghost was scanned as live source. This reconstructs the
deployed tree, applies the release the way `unzip -o` did (it NEVER deleted), and runs the
disk-globbing gates on the result. **Superseded under the current git deploy** --
`git reset --hard origin/main` removes deleted files, so no orphan survives to be caught,
and this rehearsal no longer models the real deploy.
    bd-deploy-rehearse --new REL.zip [--prev PREV.zip | --deployed-root DIR]
Exit 3 = would RED on stash. Distinct from bd-deploy-manifest (emits the rm list) and
bd-deploy-proof (post-hoc).

---

## Toolchain corrections @729 -- five gates that reported clean while unable to see

Every one of these was a tool that *truthfully* said OK about a question it was
structurally incapable of asking. They are listed with what they now do instead.

### `bd-tool-lint` -- a silent timeout read exactly like a clean run
`TimeoutExpired` was swallowed into `None` and treated as benign-inconclusive, and
results printed only at the END -- so a run killed mid-flight produced **empty stdout,
which is indistinguishable from success.** (I read that silence as a pass once before
catching it.)

- `--budget N` (default **900s**; the full sweep measures ~420s). **A budget BELOW the
  real cost turns the gate into a permanent false alarm -- just a different way of being
  useless.** I shipped 180s first and it failed the close.
- `--probe-timeout N` (default **90s**) + a `SLOW_TOOLS` map for legitimately heavy gates
  (`bd-verify` 150s, `bd-packs` 120s, `bd-footguns` 120s, `bd-band-derive` 90s, ...). Raise the
  *tool's* timeout, never the global one -- a blanket raise hides a genuinely hung tool behind
  a long wait. **@732: this rule is right and I violated it for four iterations, because
  `SLOW_TOOLS.get(tool, global)` OVERRIDES the global -- so raising `--probe-timeout`
  20→90→150→180 changed NOTHING for the tool that was actually timing out.** Its `SLOW_TOOLS`
  entry was the only lever, and it was pinned at **60s -- below the tool's own 62s cost. A
  permanent false alarm inside the very map that exists to prevent permanent false alarms.**
- **@732 -- THE REFUSAL NOW NAMES THE TOOL** (`TIMED OUT : <tool>`). It previously refused with
  only a COUNT. *Three full re-runs were burned discovering who.* **A gate that refuses without
  naming what tripped it teaches the operator to override the gate instead of fix the tool --
  and an override reflex is how a real finding eventually gets waved through.**
- **@732 -- when a number has to keep growing to make a gate stop complaining, STOP GROWING THE
  NUMBER.** `bd-precut` still timed out at 180s. That was the diagnostic, not a setback: a cost
  that *moves as you raise the budget* is not a slow tool, it is the wrong lever. `bd-precut`
  **orchestrates the gate battery**, so probing it during the lint re-runs everything
  recursively under core contention -- its cost is a function of the load the probe itself
  creates. It belongs in `SELF_REFERENTIAL`, not in a budget map at any value. Moved. The FULL
  runtime gate then passed **honestly, not overridden.**
- Timeouts and budget-skips are tracked as **UNVERIFIED**, printed loudly, and the
  "toolchain clean" line is **suppressed**; `--gate` exits **3**.
- **`SELF_REFERENTIAL` (never-probe) -- see FG-LINT-PROBES-MUTATORS.** The runtime probe
  RUNS each tool with `--json`. It was **spawning `bd-fullsuite`** (the entire 15-20 min
  test suite) on every lint, and -- far worse -- **`bd-install`, which does `rm -rf work/*`
  and re-extracts the pinned zip.** *Linting the toolchain destroyed the venv and reverted
  the work tree.* Now 15 named exclusions (**+`bd-precut` @732 -- recursion, not mutation:
  it is read-only, but it RUNS the linter's siblings**).
- **Full runtime coverage, achieved for the first time, immediately surfaced two real
  defects the silent timeout had been hiding: `bd-status --json` and `bd-packs --json` both
  emit ANSI escapes into their JSON**, which breaks any parser consuming them.

### `bd-evidence` -- the field-name collision (and the check that never ran)
`SHARED_FACTS` compared **raw keys**, which failed in both directions at once:
- **FALSE COLLISION:** `bd-trust-score.score` (release confidence) and
  `bd-agent-scorecard.score` (session quality) are DIFFERENT quantities sharing a key ->
  one bucket -> a false disagreement that would block a close the moment they diverged.
- **MISSED CHECK (worse):** the authoritative trust score arrives under `score`, while
  pack/review report the SAME quantity under `trust_score` -> **different buckets -> never
  compared.** trust=80 vs pack=100 reported clean.

Now a `(view, raw_key) -> canonical_fact` map. **Cross-checked facts 3 -> 8.**
`changed_files` is a **list** in review/notes but an **int count** in scorecard -- split
into two facts, with the `len(list) == count` **relation** asserted explicitly so fixing
the collision did not LOSE a check. Undeclared shared keys are surfaced as an advisory
third state; only 3 are allowlisted as private-by-design, **each with a stated reason --
an allowlist without one is a silencer.**
*(The relation check earned itself on its first outing: it caught `bd-release-note`/
`bd-review-pack` computing `changed_files` over a DIFFERENT DENOMINATOR than the watchdog.)*

### `bd-agent-watchdog` -- SRC-WITHOUT-TEST was two bugs stacked
The global gate (`src_changed and not test_changed`) let **one unrelated test edit silence
the flag for EVERY uncovered source file.** And underneath it: `changed()` called
`sec.iter_py(work)` with the default `subdir="bulk_downloader", include_tests=False`, so
**it never scanned `tests/` at all** -- `test_changed` was **always empty by construction.**
Now per-file stem coverage + both trees scanned. `bd-agent-scorecard`'s `tested` factor (25
pts) reads straight off this, so it finally means something.
**The same blind denominator was then found in `bd-release-note` (and via it
`bd-review-pack`) and fixed. Three tools, one bug.**

### `bd-gui-surface` -- **"44 cockpit views" was FOLKLORE**
The regex required `data-p` to be the **only** attribute on the `<a>`, so every anchor
carrying `class="btn"` / `class="on"` / `data-dl=` was **invisible** -- and the selftest
**ENSHRINED the bug** with a "NEG control" asserting that an attribute-bearing anchor is
not a view. The number was also hardcoded into the tool's own docstring, which is how it
became fact.

Views now derive from the **`PAGES` registry** -- what the router actually dispatches on
(`go(p)` calls `PAGES[p]()`):

| | old | **derived** |
|---|---|---|
| renderable views | 44 | **133** |
| nav anchor keys | -- | 83 |
| REDIRECT aliases | -- | 61 |
| **dark views** | -- | **7** |
| orphan anchors | -- | **0** |

The `>= 40` floor (which would have passed at the broken 44) is now `>= 100` and
`views >= anchors`. **Any parity/coverage percentage computed against 44 was flattering
itself by 3x.** The 7 dark views: `advlanding, complexity, daily, inbox, maturity,
orghealth, syslanding`.

### `bd-body-contract` -- the capability that was never wired in
`ts_calls()` / `probe_typed()` -- the entire type-directed differential probe, written and
documented at 726 -- **were never called by `main()`.** Dead code. **That is where the "66
UNKNOWN" everyone quoted came from: a number produced by a capability nobody was running.**

- `--typed` -- the type-directed differential probe (empty world).
- `--fixtures` -- **replay against a REAL world** (v3.66.729). The sharpest mode.
- `--regen` -- rewrite `tools/BODY_CONTRACT_CALLS.json`, the committed artifact the gate
  reads. **Node regenerates it; the gate never needs node to ENFORCE.** (See
  FG-GATE-DEGRADES-TO-SKIP.)

Verdicts @729: **OK 53 | DEAD 0 | FIXTURE-GAP 9 | HARNESS-FAULT 0 | UNKNOWN 64 (ratchet).**

---

## `FOOTGUNS.json` -- the registry that had never run (@729)

**`bd-footguns` loads an external `FOOTGUNS.json` from the TREE ROOT or ALONGSIDE THE TOOL
(`bin/`) -- and from nowhere else.** The static-PK copy is in neither place, so **the pasted
registry has never once been loaded.** It was documentation wearing a gate's clothes.

Loading it for the first time crashed the tool, and revealed **three latent defects in the
registry itself** -- the same shapes it exists to catalogue:

| entry | defect | now |
|---|---|---|
| `FG-DENOMINATOR-BLAST-RADIUS` | `kind: "grep"` with a **tool-schema body** (`cmd`/`block_on_exit`, no `pattern`) -> **`KeyError('pattern')` on load** | `kind: none` (honest advisory) |
| `FG-RESOLVER-SORTS-BY-PATH` | same | `kind: none` |
| `FG-GATE-DIRTIES-THE-TREE` | `kind: "test"` (**not a kind bd-footguns knows**) + a `::nodeid` in `cmd`. `_run_insync` takes a test **FILE**, so a nodeid resolves to *"skip: harness/test missing"* -- **forever** | `kind: insync`, test = the FILE |

**The third is the project's own documented `test-FUNCTION-mistaken-for-a-FILE` footgun,
living inside the footgun registry.**

**Valid detector kinds are exactly four:** `none` (advisory), `tool` (`cmd` + `block_on_exit`),
`insync` (`test` = a test FILE path), `grep` (`pattern` [+ `root`, `must_not_match`]). Anything
else silently degrades to `skip`.

**`bin/FOOTGUNS.json` now ships in the bdsuite**, so the registry travels with the toolchain and
is actually enforced (21 active footguns; `bd-footguns --check` runs them all).

### @732 -- THE FAN-OUT WAS THE SLOWDOWN

`cmd_check` fanned the detectors out over `ThreadPool(8)` because they are independent
subprocesses and *"only the waiting is shared."* **Measured on the actual box, the parallelism
was a PESSIMIZATION: 77s wall against a 52s SERIAL sum.** The sandbox has **1 core**, and 8
concurrent app-booting subprocesses simply contend — the fan-out *added* 25s.

- Workers now sized to the machine: `min(len, os.cpu_count(), 4)`.
- Detectors submitted **longest-first** by a measured `cost_hint` in `FOOTGUNS.json`
  (`FG-GATE-DIRTIES-THE-TREE` 22s, `FG-CENSUS-NEEDS-THE-VENV` 16s), so the heavy ones are not
  the tail of the schedule.
- Print order unchanged — the report stays byte-identical to the serial one.

**Fan-out is a bet on cores you have. Measure the wall clock against the serial sum before
assuming concurrency bought anything.**

### Every new detector needs a NEGATIVE CONTROL

`FG-LINT-PROBES-MUTATORS`'s first detector was `grep -q '"bd-install"' bd-tool-lint`. It **passed
the negative control** -- because the string still appeared in a **comment**. It asked *"does this
word appear in the file"*, not *"is this tool excluded from probing"*. **A detector's denominator
can miss the question exactly like any other gate's.** It now loads the module and asserts real
**set membership** in `SELF_REFERENTIAL`, and it has been proven to fire:

    remove bd-install from the set -> [VIOLATION] exit 3
    restore it                     -> [PASS]      exit 0

Do this for every detector you add. **A gate you have not watched fail is a gate you are
guessing about.**
