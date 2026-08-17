<!-- verified-against: v3.66.805 -->
# BD_README — the BulkDownloader operating manual

The single "how it all actually works" doc, for two readers:
- **a future Claude session** bootstrapping from this static PK, and
- **Matt** (sole dev/operator), as the operational reference.

It is comprehensive on purpose: the full lifecycle, every tool that matters, the
flags (`--skip-fe`, `--no-pack`, `--check`, …), and the traps. When a detail lives
in another doc, this points there. If this doc and the source ever disagree,
**the source tree wins** — re-derive.

---

## 0. TL;DR of the loop

```
UPLOAD SET  →  bootstrap chain  →  work (read-only free / changes gated)
            →  cut-readiness (bd-ready)  →  bd-cut  →  band the right tests
            →  present the release zip  →  [STOP: wait for Matt's stash GREEN]
            →  bd-handoff (repins STATE + builds bdsuite + audit_state)  →  bd-pack
```
`bd-tools` prints the live categorized index of the whole toolchain at any time.

---

## 1. Golden rules (non-negotiable)

1. **Memory is stale-by-construction for BD *state*.** Version numbers, guard SHAs,
   slice letters, parity/file counts live ONLY in `STATE.json` (per-session
   `version.zip`) + the newest `KB_HANDOFF_v3_66_<n>.md`. Durable *lessons* live in
   `KB_JUDGMENT.md` + `PROJECT_OPERATING_INSTRUCTIONS.md`. Source is ground truth,
   re-derived every session. Never store version-specific ephemera to memory.
2. **Gated approval.** Read-only / planning / analysis is free. Anything that
   changes runtime, build, version, a guard, or cuts a release needs an explicit
   per-task go. Terse directives ("go", "cut", "1", a file upload) = full
   authorization *within the established scope*. "hold"/"wait" = stop immediately.
3. **Claude never deploys.** Claude works only in `/home/claude`. Matt deploys on
   stash. The binding confirmation is `capture.sh --workers=180` returning **GREEN**.
   *(Note v3.66.805: the 805 stash-green was reported as `--workers=60` —
   12466 / 12391 pass / 0 fail / 75 skip. The worker count is operator practice and
   varies; treat the GREEN, not the number, as the binding fact.)*
4. **Close order is fixed** (see §11): build + verify + present the zip → **STOP**
   for Matt's stash GREEN → `bd-handoff` → `bd-pack`. Never handoff before GREEN.
5. **Never run the whole `tests/` dir** — it hangs (see §9).
6. **The 7 guard files stay byte-identical** unless Matt declares a new SHA (§8).
7. **Report honestly.** Results-first, no aspirational docs, divergence stated
   candidly. The work tree and stash can diverge (Matt overlays files himself).

---

## 2. Players & layout

- **stash** — the headless host Matt runs. `mboyle@10.0.70.20`, app at
  `~/BulkDownloader`, systemd `bulkdownloader.service`, localhost:5555.
- **sandbox** — Claude's Linux box. Key dirs:
  - `/home/claude/work` — the extracted source tree (the SERVICE venv is
    `work/venv`, **not** `.venv`).
  - `/home/claude/bin` — installed toolchain (`install_bdsuite.sh` lands tools here
    + symlinks into `/usr/local/bin`).
  - `/mnt/user-data/uploads` — **READ-ONLY and EVICTS files mid-session.** Copy
    everything out FIRST (`bd-intake`).
  - `/mnt/project` — the static PK (this bundle, read-only in-session).
  - `/mnt/user-data/outputs` — where deliverables are surfaced (`present_files`).
- **Stack** — Flask + Playwright + React/TypeScript. `authlib>=1.3,<2.0` (OIDC),
  `cryptography` held `<46`.

  > **VERSIONS CORRECTED v3.66.805 — the previous line said "node v22.22.2; ffmpeg is
  > the distro build (6.1.1 via apt)". Measured:**
  > - **node**: sandbox **v20.18.0**, stash **v18.19.1** (operator-reported). *Neither
  >   is 22.x.* `frontend/package.json` pins `engines.node >= 18.0.0`, which both
  >   satisfy; Vite 5 / Vitest 2 accept 18. Do not pin ahead of stash.
  > - **ffmpeg**: apt **6.1.1** IS installed at `/usr/bin/ffmpeg`, but PATH resolves
  >   `ffmpeg` to `/tmp/media/tools_bin/ffmpeg` — the **static 7.0.2 johnvansickle
  >   build**, i.e. exactly the binary this doc warns SEGFAULTs on HLS+HTTPS
  >   in-sandbox. **The apt shadow `bd-venv` is meant to install was NOT in effect at
  >   805.** Verify with `command -v ffmpeg` before any HLS work; if it resolves to
  >   `/tmp/media/tools_bin`, put `/usr/bin` ahead of it or re-run `bd-venv`.

---

## 3. Per-session bootstrap (exact chain)

0. **`setup.sh` FAILS by design — do not run it.**
1. **Intake first.** `bd-intake` copies uploaded zips out of the evicting uploads
   dir, `unzip -t`-validates them, and reports what's missing from the set. (Or copy
   manually to `/home/claude/`.) "Save and wait" = copy everything present, report
   what's missing, hold without bootstrapping until the full set arrives.
2. **Install the toolchain.** Unzip `bdsuite_v3_66_<n>.zip` and run
   `install_bdsuite.sh` (globs `bin/*` → symlinks). Create symlinks by hand if they
   don't land.
3. **The chain:** run `bd-boot` and **re-run it until it prints READY.**
   It is budgeted (~230s per call, under the harness limit) + checkpointed
   (`/home/claude/.bd_boot`), so each call finishes what it can, exits 0 with a
   loud `PARTIAL` + phase ledger, and the next call resumes. Internally:
   `prestage → install → venv → preflight → state → status → footguns → kbsync`.
   - No manual prestage loop and no expected timeout-kill anymore. `--fresh`
     forces a full re-run; `--jobs N` sets extraction parallelism.
   - Staged kits are sentinel-validated (`$STAGING/.ok/`) — re-validation is a
     stat, not an `unzip -t` sweep; the truncation guarantee is preserved.
   - `bd-venv` provisions `work/venv` + the cloak stealth browser from the cloak
     pack. If the cloak pack predates a `requirements.txt` change it can fail
     offline (e.g. authlib) — `bd-fetch` live-fetches the missing wheel.
   - **GUARD:** bootstrap halts if no `KB_HANDOFF_v3_66_*.md` is in the version pack.
4. **Sanity:** `bd-doctor` (read-only triage), `bd-tools` (see the toolchain map).

---

## 4. The upload set

**Core (always):** 4 install packs `pack_A–D.zip` · `bd_cloak_pack_v3_66_<n>.zip`
(venv consumable — ships CORE+cloak wheels, so a `requirements.txt` change can
stale it, not just `requirements-cloak.txt`) · the source zip · `version.zip`
(`BulkDL_next_session_*` — STATE + handoff + planning docs) · `bdsuite_v3_66_<n>.zip`
· `audit_state_v3_66_<n>.zip`.

- **pack_D** = `bulkdl_dev_kit` (pytest + pyinstaller wheelhouse). Kit→pack
  distribution is arbitrary — don't assume "pack_B = chromium".
- **Skip for read-only audit sessions:** the cloak/sbcap packs.
- **Optional expansion tier `pack_E–H`** (install-on-demand; `bd-install` indexes
  them but has NO handler → **zero bootstrap cost until used**). Managed by
  **`bd-optpack`**:
  - `bd-optpack list` / `--brief` — detect + installed-state.
  - `pack_E` browsers (playwright firefox+webkit; base ships chromium only) →
    `bd-optpack install E` (extracts into `$PLAYWRIGHT_BROWSERS_PATH`).
  - `pack_F` pyext wheels (gallery-dl/streamlink/m3u8/lxml/…) →
    `bd-optpack install F --pip`.
  - `pack_G` system debs (wireguard/nftables/aria2/jellyfin/…) →
    `bd-optpack install G --apt` (dpkg -i as root).
  - `pack_H` audit venv (semgrep/bandit/vulture/… + a11y stack) →
    `bd-optpack install H`.
- **`bd-packs`** = presence + zip-integrity of every pack in one glance.

---

## 5. The toolchain (by category, with the flags that matter)

`bd-tools` is the live index; `bd-tools <term>` filters. Read-only unless noted.

### Bootstrap / provisioning
`bd-boot` (re-run until READY) · `bd-prestage` · `bd-install` · `bd-venv` · `bd-preflight` ·
`bd-state` · `bd-status` · `bd-doctor` · `bd-intake` · `bd-optpack` · `bd-fetch`
(live wheel fetch) · `bd-packs`.

### Cut-readiness / gates  — run before every cut
| tool | what | key flags |
|---|---|---|
| **bd-ready** | 7-gate preflight (aggregates the rest) | — (advisory, never mutates) |
| bd-guardcheck | 7 guard SHAs vs the repo-root `guards.json` baseline | `--guards <path>` to override; `--state` is the legacy version-pack fallback |
| bd-versync | 3-part version consistency (fixture-aware) | — |
| bd-changelog | top entry matches version + ASCII + non-empty | — |
| bd-ascii | non-ASCII/emoji scan (the gate that hit ×3) | `bd-ascii <file>` |
| bd-pinscan | `== N` count/parity pins that should be `<= N` | — |
| bd-bandcheck | validate a band list (real files/no hang/no test-fn-path/no leak co-band) | pass the band list |
| bd-imports | undeclared import edges vs frozen baseline | `--update` re-freezes (SAME cut) |
| bd-regen | derived-doc sync | **`--check` (default, READ-ONLY)** · `--write` regenerates |
| bd-factcheck | canonical counts from tree vs doc/STATE numbers | — |

### Inspect / search
bd-route (`bd-route /api/x` → owning blueprint; `--grep`) · bd-deps (blueprint
graph; `bd-deps <module.py>` reverse) · bd-pin (`bd-pin <pkg>` all sites; `--all`) ·
bd-envscan (BD_* env-var opt-ins) · bd-sym (`bd-sym <symbol> [--py-only]`, all N
sites) · bd-kb (`bd-kb <term>` over PK docs) · bd-capsweep (prove a capability
isn't already built: LIKELY BUILT/WEAK/ABSENT) · bd-freshest (newest authoritative
doc + stray-zip flags).

### Impact / change
bd-since (work tree vs pinned zip: MODIFIED/ADDED/REMOVED + band/regen hints) ·
bd-band-derive (`bd-band-derive --file <path>` → tests + blueprint dependents +
regen/guard rules; `--files a b c` for a changed set). Supersedes the
retired bd-blast / bd-suites / bd-touched (merged at rev-702). For the changed
set itself, pipe from `bd-since`.

### Quality / security / docs
bd-ssrf (fetch sites w/o an in-file SSRF guard) · bd-secrets (committed-secret scan;
exit 1 on high-confidence) · bd-coverage (COVERAGE_GAPS.json) · bd-flakes (hazards +
KNOWN_FLAKES) · bd-docstale (`verified-against` markers vs current; `--behind N`) ·
bd-doclint (docs missing a marker).

### Dev loop
bd-precut (predicts the cut) · bd-cut (**`--skip-fe`** backend-only) · bd-band ·
bd-bump (`bd-bump 3.66.N --title "…"` → **`--check` default**, `--write` applies).

### Close / package
bd-ship (bd-precut → bd-cut → bd-handoff → bd-pack, one command) · bd-handoff ·
bd-pack · bd-kb-sync (stage static-PK update) ·
bd-mkbdsuite · bd-mkauditstate · bd-zipcheck (is a release zip shippable, no
extract) · bd-repin-dist (FE-rebuilding cuts; **`--skip-fe`** backend-only).

### Deploy / verify
bd-verify-live (confirm a deploy landed on stash) · bd-rollback · bd-reconcile.

### Sandbox harness
bd-rev (audit venv) · bd-live (headless URL smoke) · bd-dltest · bd-runner-nav ·
bd-corpus · bd-recognizer-drift · bd-fixture-serve · bd-opv · bd-novnc · bd-proxy ·
bd-vpnlab · bd-sbcap.

### Audit / coordination / navigation
bd-audit (§14) · bd-parallel (§15) · bd-tools (the index).

---

## 6. The cut / release lifecycle

1. **RED-first TDD.** Prove the new tests FAIL on pristine source before writing
   any implementation. (See `superpowers:test-driven-development`.)
2. **Pre-cut checklist** (do this BEFORE the first `bd-cut` on any cut that adds/
   renames a module or shifts function line numbers — `build_release` runs these
   gates only AFTER the ~3-min tsc+vitest chain and ABORTS on stale, so doing it up
   front avoids the re-cut):
   - `bd-ready` — one shot for guards + version + changelog + derived-docs +
     import-edges + fact-counts + equality-pins.
   - If red on derived docs: `bd-regen --write` (FUNCTION_INDEX, DEPENDENCY_GRAPH,
     PIN_INDEX, route counts). Import edge added? `bd-imports --update` **in the
     same cut** (separate from regenerating DEPENDENCY_GRAPH).
3. **Version bump** (§7).
4. **`bd-cut`** — backend-only cuts pass **`--skip-fe`**. FE-changing cuts run the
   full SPA build (see §7 note) and then **`bd-repin-dist`** (its output zip is the
   one to pin + deploy; don't re-run after the SHA is pinned).
5. **Band the right tests** (§9) — use `bd-band-derive` to pick, `bd-bandcheck`
   to validate the list, `bd-band` to run. Never the full dir.
6. **Close** (§11).

---

## 7. Version bump (3-part, lands together or not at all)

Use **`bd-bump 3.66.N --title "…"`** (`--check` shows the plan; `--write` applies):
1. `bulk_downloader/__init__.py` `__version__`
2. the real test pin — `assert __version__ == "3.66.N"` in
   `tests/test_settings_center_slice4.py` (fixture version strings in
   `test_scan_version_pins_fixture` / `release_hygiene_gates` / `build_release_f02`
   / `precut_check` / `build_session_pack` are NOT pins — allowlist-excluded).
3. `CHANGELOG.md` — prepend `## v3.66.N - …`, **ASCII-only** (the on-stash gate
   rejects emoji/non-ASCII), anchored on the previous `## v…` header.

Then `venv/bin/python tools/build_pin_index.py`. Verify with
`bd-versync && bd-changelog`.

> **SPA build note:** for FE-changing cuts, `build_release.py --prebuild-spa --out
> <dir>` — the `--prebuild-spa` flag runs the full vitest suite internally (it can
> time out; that's the flag doing the whole FE chain). Backend-only cuts use
> `--skip-fe` and never need `bd-repin-dist`.

---

## 8. Guard discipline

Seven files are byte-pinned in `guards.json` at the repo root (the single source of
truth, hashed from the files) and must stay identical unless Matt explicitly
declares a new SHA:
`bulk_downloader/extraction_core.py`, `session_capture.py`, `dom_capture.py`,
`dom_recorder.py`, `capture_bodies.py`, `tools/capture_session.py`,
`tools/build_release.py`.

- **`bd-guardcheck`** — live SHA vs the `guards.json` baseline (a drift is a release
  blocker). Instant "are my guards intact?" A summary that is zero in every bucket
  (`0 ok, 0 drifted, 7 missing`) is a failure signal, not a pass -- the gate could
  not see the files it certifies.
- **`bd-guard-declare`** — declare an intentional guard change + bump the pinned SHA.

---

## 9. Test discipline

- **NEVER run the whole `tests/` dir** — it hangs (`test_perf_lab.py`).
  **Don't `pkill -9`** — let timeouts expire.
- Use targeted suites / the per-cut consumer family in small batches. Pick with
  `bd-band-derive`; validate the list with **`bd-bandcheck`**; run with
  `bd-band`.
- **Sandbox test runner incantation:**
  ```
  timeout 90 env BD_HOME=$(mktemp -d) BD_DISABLE_KEEPALIVE=1 \
    PYTHONPATH=/tmp/prestaged_site_packages \
    PLAYWRIGHT_BROWSERS_PATH=/home/claude/.cache/ms-playwright \
    python3 run_tests.py tests/<file>
  ```
- **Band-naming trap:** `test_spa_wired_join_is_faithful` is a FUNCTION inside
  `tests/test_route_index_in_sync.py`, not a file — passing it as a path
  makes the runner fall back to a broad run → timeout → abort. Band the FILE.
- `test_phases_195_199` leaks `BD_INSTALL_DIR` in single-boot bands — don't co-band
  with `test_cut8_schedules`.
- The **binding** full-suite gate is on-stash via `capture.sh` (~3 min) — Matt runs
  it himself.

---

## 10. Derived artifacts & regen triggers

Regenerate (generators support `--check`; `bd-regen` wraps them):
- `FUNCTION_INDEX` — `tools/build_function_index.py`
- `DEPENDENCY_GRAPH.json` — `tools/dependency_graph.py` (also stales on an import
  *removal* even when edge count is flat; function-local imports DO register edges)
- `ENDPOINT_CATALOG` + G12 route counts — `tools/check_route_counts.py` (if a route
  changed; a `data_layer` route add must update BOTH `test_wave2_backlog` AND
  `test_v3_66_302_gui_parity_reconcile`)
- `PIN_INDEX` — `tools/build_pin_index.py`
- import-graph FROZEN baseline — `tools/decomp/import_graph_gate.py --update`
  (**separate** from regenerating DEPENDENCY_GRAPH; band
  `tests/test_import_graph_no_new_edges.py`)

`bd-regen --check` is read-only (only runs generators that have a real `--check`);
`bd-regen --write` regenerates. `bd-since` shows what changed vs the pinned zip.

---

## 11. The close sequence (fixed order)

1. **Build + verify + present the release zip.** `verify_release --zip` — gate on
   the **true `$?`, never through a pipe**. `bd-zipcheck <zip>` for a fast local
   "is it shippable?" (version, 7 guards vs `guards.json`, CHANGELOG) before you
   hand it over.
2. **STOP. Wait for Matt's stash test + deploy confirmation (`capture.sh
   --workers=180` GREEN).** Do not proceed on your own.
3. **`bd-handoff --version 3.66.N --zip <built.zip>`** — mechanically repins the
   byte-derivable STATE fields (full-zip sha256, file_count, version, the 7 guard
   SHAs, generated_at), drops any stale `KB_HANDOFF` (newest-only), self-checks via
   `bd-state`, **and regenerates `bdsuite_v3_66_<N>.zip` + `audit_state_v3_66_<N>.zip`**
   so one run yields the whole upload set. Flags: `--no-pack` (repin + self-check +
   artifacts only), `--no-bdsuite` / `--no-audit` (skip those), `--kb-dir <dir>`
   (stage a paste-ready static-PK update via `bd-kb-sync`). *You still author the
   prose (deploy_status, validation, next, footguns) into STATE + KB_HANDOFF by hand.*
4. **`bd-pack --dir <pack-dir> --out <dir>`** — lints (newest-handoff / stale-banner
   / tracker-drift) and **refuses to zip** on any lint failure, else zips the
   `version.zip`. `bd-ship` runs precut → cut → handoff → pack as one command.

---

## 12. Deploy (Matt's side)

Git: in `~/BulkDownloader`, `git fetch origin main && git reset --hard origin/main`,
then `sudo systemctl restart bulkdownloader.service`. **Deletions propagate
natively** -- no `rm` list is needed, and the overlay orphan class (what
`bd-deploy-manifest` / `tools/deploy_manifest.py` were built to catch) cannot
occur. Note that `git reset --hard` also discards any uncommitted operator edit
on the box.

**Moving the files is not the same as making the running system match them.**
None of the following were ever properties of the overlay, so none of them went
away with it. Treat this as a condition to check, not a count to memorize -- the
list can grow:

- `__pycache__` / `*.pyc` are **NOT** cleared. `git reset --hard` leaves stale
  bytecode exactly as `unzip -o` did, so Python can serve the OLD version of a
  file that plainly reads the new one on disk (see `FG-STALE-PYCACHE-AFTER-OVERLAY`
  in section 18). Clear pycache after every deploy.
- Gitignored generated artifacts are **NOT** refreshed, and `git clean -fd` will
  not remove them either -- that needs `-x`. A stale
  `reports/gui_parity_inventory.json` reads as parity drift and fails the ENTIRE
  suite. The durable fix is to **regenerate, not delete**.
- The service is **NOT** restarted by the fetch.
- `frontend/dist/` is **NOT delivered at all**: `git ls-files frontend/dist`
  returns nothing and `frontend/.gitignore` ignores `dist/`. `bulk_downloader/app.py`
  serves a uniform 503 when the bundle is missing, so a missing or stale bundle is
  a **silent 503 on the SPA** rather than a loud failure. Rebuild with
  `cd frontend && npm ci && npm run build` whenever SPA source changed.

Confirmation = `capture.sh --workers=180` returning GREEN.
`bd-verify-live` confirms the deploy landed. The version pack may carry a `kit/`
overlay so tool changes win at boot (if absent, updated tools ride the PK + bdsuite).

---

## 13. Packs deep-dive → see §4 + `bd-optpack --brief` + `bd-packs`.

---

## 14. Audit state — `bd-audit`

The `audit_state` zip (audit ledger + witnesses/ + tools/) is uploaded but not
auto-hydrated.
- **`bd-audit hydrate`** — unzip newest `audit_state_*.zip` → `/home/claude/audit_state`
  (+ symlink `review`), exec bits preserved, idempotent.
- **`bd-audit analyze`** — classify every `audit_state/tools/` script by cross-checking
  BOTH `/home/claude/bin` AND the static PK (`/mnt/project`), with a per-script
  summary, an import + **runtime-coupling** portability verdict (stdlib-only vs
  shells-out-to-siblings / audit-venv / third-party), and byte-divergence vs any
  existing copy. Buckets: candidate bd tool · candidate static PK · overlaps
  `<tool>` · audit-only · already-in-bin · already-in-PK.
- **`bd-audit promote <name>`** — stages a portable, self-contained, bd-named
  candidate into bin (refuses coupled/non-portable ones; routes audit-program
  scripts to the PK instead).
- Default (`bd-audit`) = hydrate + analyze.

The audit battery (`bd-scan.py` orchestrator + `defect_patterns.py`, `l0_extract.py`,
`graph_build.py`, `risk_score.py`, `consumer_agreement.py`, `staleness.py`,
`verify_audit.py`, `seed_review_state.py`, `gen_batch_kickoffs.py`, …) lives in the
static PK and runs against the `~/rev` throwaway venv (`bd-rev` / `pack_H`).

---

## 15. Parallel sessions — `bd-parallel`

Sandboxes are isolated (no shared filesystem), so cross-chat coordination **routes
through Matt** — but the collisions that actually hurt are preventable:
- **`bd-parallel claim --version 3.66.N --item "…" --files a.py,b.py`** — writes a
  claim manifest (session id + version + item + files) to carry into other chats.
- **`bd-parallel check <claim.json …>`** — flags two chats cutting the same version,
  or editing overlapping files, before they produce conflicting `version.zip`s.
  Exit 1 on a hard (version) collision.
- **`bd-parallel worktree <name>` / `merge-check <name>`** — isolated source
  workstreams *within* one session (for multiple independent cuts), and the diff
  back.

---

## 16. Memory & PK model

- **Memory** (across-session): personalized, but STALE for BD state by construction
  and updated in the background. Never the source of truth for version/guards/counts.
- **Static PK** (`/mnt/project`, this bundle): the always-on cache of durable docs
  (this README, `KB_JUDGMENT.md`, `PROJECT_OPERATING_INSTRUCTIONS.md`,
  `BD_TOOLCHAIN_REFERENCE.md`, `Manifest.md`, `SANDBOX_CAPABILITY_LAYER.md`, the
  audit battery, …). When these change mid-session they must be re-pasted —
  `bd-handoff --kb-dir` stages a paste-ready update via `bd-kb-sync`. The live
  `/mnt/project` only reflects a change once Matt overlays the new
  `bd_project_files.zip`.
- **Version pack** (`BulkDL_next_session_*`): carries per-session `STATE.json` +
  the newest `KB_HANDOFF` + planning docs. This is where mutable state lives.
- **Compaction tell:** a duplicate user message = a dropped turn-tail. On each
  occurrence: note it, keep a per-session count, re-verify actual tree/STATE/output
  state (nothing double-applied/double-prepended, any missing surface step done),
  then continue without re-asking. Record the total in `KB_HANDOFF` at close.

---

## 17. Shell & environment gotchas

- `bash_tool` is **`/bin/sh` (dash)**: fresh shell per call, no arrays / brace
  expansion / process substitution — wrap bash-isms in `bd bash -c "…"`.
- **The interpreter is `venv/bin/python`, never bare `python3` or `python`.** Both
  resolve to the container's 3.11 without project dependencies; `venv` is 3.12 and
  is what the box and CI run. There is no `.venv` -- a command naming it exits 127
  and the caller silently falls back to 3.11. A full test band was once measured on
  3.11 and reported seven failures that did not exist.
- Backend/import checks use the SERVICE venv **`venv/bin/python`** (not `.venv`) —
  system python makes `resolve_backend()` falsely report playwright.
- **`bd` sets `PYTHONPATH=/tmp/prestaged_site_packages`** (which ships pytest), so
  `venv/bin/python -c "import pytest"` under the `bd` env is a FALSE POSITIVE. To
  test the venv's own site-packages / install into it: **`env -u PYTHONPATH
  venv/bin/python …`**.
- Backend-only feature flags: use an undeclared **`cfg.get("key")`** site-cfg key,
  NOT a `BD_*` env var (env-var opt-ins trip the env-tranche gate; `bd-envscan`
  finds them).
- All bd tools accept **both `--work` and `--tree`** for the tree path.

---

## 18. Footgun taxonomy

`KB_JUDGMENT.md` §1 is the failure-shape registry — read it. `FOOTGUNS.json` (**40
entries — measured v3.66.805; this line previously said 19**) is the *mechanical*
registry, enforced by `bd-footguns --check` and `bd-precut --gate`.
Most shapes now have a tool that immunizes them: stale-copy-of-derived-fact →
`bd-factcheck`; string-grep-not-decorator → `bd-route`; equality-pin-whack-a-mole →
`bd-pinscan`; fixture-looks-like-a-pin → `bd-versync`; env-var-opt-in →
`bd-envscan`; duplicate-pin-sites → `bd-pin`; test-function-mistaken-for-file →
`bd-bandcheck`; scope-from-oldest-catalog → `bd-freshest`; catalog-id-not-shipped-
module → `bd-capsweep`; guard byte-drift → `bd-guardcheck`; CHANGELOG-emoji →
`bd-changelog`/`bd-ascii`; core-req-stales-cloak-pack → watch `requirements.txt`;
optional-pack-invisible-to-tools → `bd-optpack`.

### Added @729 — three that BITE, and one law

- **`FG-LINT-PROBES-MUTATORS`** — **the destructive one.** `bd-tool-lint`'s runtime phase
  RUNS every tool with `--json`. `bd-install` does `rm -rf work/*` and re-extracts the
  **pinned** zip. So **linting the toolchain destroyed the service venv and silently
  REVERTED THE WORK TREE mid-cut** — twice in one session. *A lint must not detonate the
  thing it is linting.* 14 mutators are now never-probed. **If your tree or venv vanishes
  and you did not run `bd-install`, check what ran a runtime lint.**
- **`FG-STALE-PYCACHE-AFTER-OVERLAY`** — after `unzip -o` into a live tree, the resident
  `.pyc` can be NEWER than the restored `.py`, so **Python serves the OLD version of a file
  that plainly reads the new one on disk.** `__init__.py` said `3.66.729`; the import
  returned `3.66.728`. Clear pycache after ANY overlay-extract (this is why the stash deploy
  has always done it — the sandbox needs it for the identical reason).
- **`FG-GATE-DEGRADES-TO-SKIP`** — **a skip reads as green.** A gate that shells out to
  anything absent from the RELEASE ZIP (node/`node_modules`, network, an optional pack) will
  silently self-disable exactly where it matters — in the band and on stash. Fix: make the
  input a **committed derived artifact** (the ROUTE_INDEX pattern) so the gate ALWAYS RUNS;
  the external tool regenerates, it never enforces. **Always run a new gate's test with
  `pytest -rs` in the EXTRACTED ZIP before believing it.**

**The law all three are instances of:** *a skip is not a pass; a timeout is not a pass; an
unknown is not a pass. A gate that degrades to silence when it cannot see is worse than no
gate, because it also consumes the attention that would have gone to checking.*

---

## 19. Cheatsheet

```
# bootstrap
bd-intake; install_bdsuite.sh
bd-boot        # re-run until it prints READY (budgeted+checkpointed full chain)
bd-tools

# before a cut
bd-ready                       # 7-gate preflight
bd-regen --write               # if derived docs drifted
bd-imports --update            # if you added an import edge (same cut)
bd-bump 3.66.N --title "…" --write ; venv/bin/python tools/build_pin_index.py

# cut
bd-cut [--skip-fe]             # backend-only -> --skip-fe
bd-repin-dist                  # FE-changing cuts only
bd-since ; bd-band-derive --files <changed> ; bd-bandcheck <list> ; bd-band <files>

# close (only AFTER Matt's stash GREEN)
bd-zipcheck <zip>              # local shippable check
bd-handoff --version 3.66.N --zip <zip>   # +bdsuite +audit_state ; --no-pack/--no-bdsuite/--no-audit
bd-pack --dir <pack> --out <out>          # or: bd-ship for the whole close

# situational
bd-optpack list ; bd-optpack install H     # audit venv, etc.
bd-audit ; bd-audit promote <name>
bd-parallel claim --version 3.66.N --item "…" ; bd-parallel check *.json
```

---

*Authoritative pointers: `KB_JUDGMENT.md` (§1 failure taxonomy) ·
`PROJECT_OPERATING_INSTRUCTIONS.md` · `BD_TOOLCHAIN_REFERENCE.md` (full per-tool
reference) · `SANDBOX_CAPABILITY_LAYER.md`
(what the sandbox can/can't do). When any doc disagrees with the source tree,
the source wins.*
