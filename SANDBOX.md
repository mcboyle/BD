# BulkDL Claude Sandbox — canonical reference

The single source of truth for working in the BulkDL sandbox.
Supersedes `SANDBOX_NOTES.md`, `SANDBOX_ENVIRONMENT_MAP.md`, and the
"Sandbox runtime" section of every handoff doc. Those become
pointers to this file.

Last verified: v3.66.159 (deploy label 159.1; version string stays 3.66.159 per the
semver contract). The live work is the template pipeline (151–158) + its cockpit
integration (158), the Reptyle first-enable (now DONE/validated), and the 159.1
additive slice (cockpit capture UX, GUI-parity Phase 3, extraction_core
characterization). See §12 for v3.66.159 capture/diagnostic footguns. Targeted suites
green via the `run_tests.py` harness; the full `tests/` run still HANGS at
`test_perf_lab.py` in-sandbox (perf/threading; unrelated). See §11 for the current
architecture, the pipeline,
the release/build workflow, and the footguns. For where session context comes
from see §0.5 "Continuity"; for current project state read
`KB_HANDOFF_v3_66_158.md`; for how to work here read
`PROJECT_OPERATING_INSTRUCTIONS.md`.

---

## 0. The 30-second bootstrap

**Convenience layer (preferred):** the bdkit ships three wrappers on `$PATH`
(`/usr/local/bin` → `/home/claude/bin`): **`bd-install`** (bootstraps kits +
extracts the source zip — expect 20/20 kits), **`bd <cmd>`** (runs a command with
the full env + background services loaded — replaces the export block below), and
**`bd-status`** (health check). A fresh session is just `bash /mnt/project/setup.sh`
→ `bd-install` → `bd-status`. See `bdkit_HANDOFF.md` + `BDKIT_FIXES.md`.

**Under the hood** (what `bd` loads; use directly only if the wrappers are absent):

```bash
# Once per session, before anything else:
bash /mnt/user-data/uploads/install_bulkdl_kits.sh
cd /home/claude/work && unzip -q /mnt/user-data/uploads/BulkDownloader_v*.zip
```

Then at the top of **every** subsequent `bash_tool` call (state
doesn't persist between calls):

```bash
export PATH=/tmp/tools_bin:/tmp/media/tools_bin:/home/claude/.local/node/bin:$PATH
export PYTHONPATH="/tmp/prestaged_site_packages:${PYTHONPATH:-}"
export BD_HOME=/home/claude/bd_home
export BD_DISABLE_KEEPALIVE=1
export DISPLAY=:99
export PLAYWRIGHT_BROWSERS_PATH=/home/claude/.cache/ms-playwright
export GTK_ROOT=/home/claude/.local/gtk
export LD_LIBRARY_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
export GI_TYPELIB_PATH="$GTK_ROOT/usr/lib/x86_64-linux-gnu/girepository-1.0:${GI_TYPELIB_PATH:-}"
export XDG_DATA_DIRS="$GTK_ROOT/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
Xvfb :99 -screen 0 1024x768x24 >/tmp/xvfb.log 2>&1 &
sleep 1
```

The installer prints this block verbatim at the end of its run for
easy copy-paste.

---

## 0.5 Continuity — where session context comes from

Long sessions are compacted; the **transcript + journal** system is the record,
not per-release handoff docs (the old `v*_handoff.md` convention is retired —
the files of that name in outputs, 138–149, are legacy).

1. **`/mnt/transcripts/journal.txt`** — catalog. Read the tail; each entry names a
   transcript file + a one-paragraph summary.
2. **The newest transcript** in `/mnt/transcripts/` — the full detailed record
   (module APIs, paths, line numbers, decisions). They are **large** → read
   incrementally (grep/sed/`view` ranges), never whole.
3. **`KB_HANDOFF_v3_66_158.md`** — distilled current state (refresh per release).
4. **This file** — env + footguns.

A compaction summary at the top of a conversation is a faithful snapshot, but the
transcript wins if they differ, and **source code is the final ground truth** over
any doc.

---

## 1. The sandbox

**Process / OS:**
- Ubuntu 24.04 LTS, kernel 6.8.
- `nproc == 1`. Everything is serial. Custom test harness's
  `--workers` collapses to 1.
- `/tmp` and `/home/claude/` persist across `bash_tool` calls.
  **Processes (`&`, `nohup`, `setsid`) do NOT persist.**

**Network:**
- **Disabled.** No `pip install`, no `npm install`, no outbound HTTP.
  Everything must come from kits.

**Disk:**
- ~7–10 GB free at start.
- The installer extracts kits one at a time and cleans up staging.
- Ollama is the elephant — 3.3 GB transient + 3.6 GB extracted.
  Install ollama **first** if you need it; the other kits eat ~3 GB
  combined.

**Filesystems:**
| Path | Purpose | Writable? | Persists? |
|---|---|---|---|
| `/mnt/user-data/uploads/` | What you uploaded (zips, source, docs) | **read-only** | session-only |
| `/mnt/project/` | Project files mirrored from Claude.ai | **read-only** | yes, edited via UI |
| `/home/claude/` | Your scratch | yes | yes, across bash_tool calls |
| `/tmp/` | Volatile scratch | yes | yes, across bash_tool calls |
| `/mnt/user-data/outputs/` | Files visible back to user | yes | until session ends |

---

## 2. Kits — what they install and where

The installer (`install_bulkdl_kits.sh`) finds each kit either from a
direct upload OR from inside a pack. Direct uploads win — see
footgun #1.

| Kit | Pack | Installs to | Persistent? |
|---|---|---|---|
| `core` | A | `/tmp/prestaged_site_packages/` (pytest + 121 deps), `/tmp/tools_bin/` (rg, jq, fd, sqlite3, litecli, etc.) | files ✅ |
| `venv` | A | `/home/claude/work/venv/` (pre-built Python venv) | ✅ |
| `datastores` | A | `/home/claude/datastores_kit/tools_bin/` (redis, postgres binaries) | ✅ |
| `chromium` | B | `/home/claude/.cache/ms-playwright/` (Chromium rev 1223) | ✅ |
| `media` | B | `/tmp/media/tools_bin/` (ffmpeg, ffprobe — 7.0.2 static) | ✅ |
| `gtk` | B | `/home/claude/.local/gtk/` (GTK 3 + GIR). **Also launches Xvfb on :99 — dies between calls** | files ✅ / Xvfb ❌ |
| `optional` | C | `/tmp/opt/` (yt-dlp, scrapling, apprise, site-specific extractor APIs) | ✅ |
| `frontend` | C | `/home/claude/work/frontend/` (full React `node_modules`, ~344 packages including testing-library). **Creates BulkDownloader/frontend/node_modules + BulkDownloader/spa/node_modules symlinks** | ✅ |
| `node` | C | `/home/claude/.local/node/` (Node 20.18.0 + npm 10.8.2) | ✅ |
| `rsuite` | C | `/home/claude/rsuite_kit/` (standalone rsuite 5.71.0 + React 19 in own tree) | ✅ |
| `pypy` | C | `/home/claude/pypy_kit/pypy/bin/pypy3` | ✅ |
| `bdhome` | C | `/home/claude/bd_home/` (sample BD_HOME with `app_config.json`, small DB) | ✅ |
| `apprise` | C | `/home/claude/apprise_kit/` + **fake webhook receiver on :8765 — dies between calls** | files ✅ / receiver ❌ |
| `mocks` | C | `/home/claude/mocks_kit/` + **Plex (:32400), Jellyfin (:8096), Stash (:9999) — die between calls** | files ✅ / servers ❌ |
| `tools` | C | `/tmp/tools_kit/tools_bin/` (rg, jq, fd, sqlite3, litecli) | ✅ |
| `profiling` | C | `/tmp/profiling_kit/` (py-spy, scalene, line-profiler) | ✅ |
| `supervisord` | C | `/home/claude/supervisord_kit/` | ✅ |
| `recordings` | C | `/home/claude/recordings_kit/` (vcrpy cassettes) | ✅ |
| `webproxy` | C | `/home/claude/webproxy_kit/` (Caddy) | ✅ |
| `lsp` | C | `/home/claude/lsp_kit/` (pylsp, pyright wheels) | ✅ |
| `precommit` | C | `/home/claude/precommit_kit/` (pre-commit + config) | ✅ |
| `ollama` | reassembled from 7 parts | `/home/claude/ollama_kit/` (~3.6 GB extracted; `ollama serve` on :11434 — dies between calls) | files ✅ / serve ❌ |
| `spa` | standalone (rare) | `/home/claude/spa/` (placeholder vite scaffold; frontend kit covers real use) | ✅ |

**Pack sizes:**
- pack_A: ~395M (core + venv + datastores)
- pack_B: ~413M (chromium + media + gtk)
- pack_C: ~492M (the other 15 kits including frontend + rsuite + testing-library)
- ollama_part_0..6 + checksum: ~3.4 GB total

---

## 3. The runtime env block — per-variable

| Var | Value | Why | Failure if missing |
|---|---|---|---|
| `PATH` | `/tmp/tools_bin:/tmp/media/tools_bin:/home/claude/.local/node/bin:$PATH` | Tool binaries from kits | `command not found: rg / ffmpeg / node` |
| `PYTHONPATH` | `/tmp/prestaged_site_packages:${PYTHONPATH:-}` | pytest + flask + yt-dlp + 121 other deps live here, NOT in system site-packages | `ModuleNotFoundError: No module named 'pytest'` |
| `BD_HOME` | `/home/claude/bd_home` | App reads `app_config.json`, `downloader_history.db`, etc. from here | App falls back to `cwd`, writes to source tree, poisons next test |
| `BD_DISABLE_KEEPALIVE` | `1` | Stops `bulk_downloader.app` from spinning 14 background threads on import (drops to 5) | Order-dependent test flakes; +29 failures across full suite per §6 |
| `DISPLAY` | `:99` | Xvfb display number for headed GUI tests | `Can't connect to display ":99"` (also needs Xvfb running) |
| `PLAYWRIGHT_BROWSERS_PATH` | `/home/claude/.cache/ms-playwright` | Where chromium kit extracts the browser | `Executable doesn't exist at /opt/pw-browsers/...` |
| `GTK_ROOT` | `/home/claude/.local/gtk` | Root of extracted GTK userspace tree | (used by the three below) |
| `LD_LIBRARY_PATH` | `$GTK_ROOT/usr/lib/x86_64-linux-gnu:...` | Find `libgtk-3.so` and friends | `cannot open shared object file: libgtk-3.so` |
| `GI_TYPELIB_PATH` | `$GTK_ROOT/usr/lib/x86_64-linux-gnu/girepository-1.0:...` | GObject Introspection bindings | `Namespace Gtk not available` |
| `XDG_DATA_DIRS` | `$GTK_ROOT/usr/share:/usr/local/share:/usr/share` | GTK icons, themes, mime data | Tray icons render as blank squares |

**`BD_INSTALL_DIR` is NOT in this block.** It's a per-test variable
that points to a tmpdir; setting it session-globally breaks tests
that expect a clean install location. Tests set it themselves.

---

## 4. Minimal env per task

The full block is fine for everything but pays Xvfb startup (~1 s).
For shorter shells:

| Task | PATH | PYTHONPATH | BD_HOME | BD_DISABLE_KEEPALIVE | DISPLAY | PLAYWRIGHT_BROWSERS_PATH | GTK_* | Xvfb |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `python3 -c "import bulk_downloader"` | | ✓ | | | | | | |
| `python3 -m pytest --version` | | ✓ | | | | | | |
| Most `pytest tests/...` files | | ✓ | ✓ | ✓ | | | | |
| Tests importing `tray_app` | | ✓ | ✓ | ✓ | ✓ | | ✓ | ✓ |
| Playwright headless | | ✓ | ✓ | ✓ | | ✓ | | |
| Playwright headed | | ✓ | ✓ | ✓ | ✓ | ✓ | | ✓ |
| `node` / `npm` / `vite` / `vitest` | ✓ | | | | | | | |
| `ffmpeg` / `rg` / `jq` | ✓ | | | | | | | |
| `bash install_bulkdl_kits.sh` | (it sets its own) | | | | | | | |

**Playwright headless skips the GUI stack entirely** — most BulkDL
deep_detect tests use `headless=True` and don't need DISPLAY or Xvfb,
just `PLAYWRIGHT_BROWSERS_PATH`.

---

## 5. What dies between calls + how to relaunch

`bash_tool` tears down the foreground process tree at the end of
every call. `setsid`, `nohup`, `disown`, double-forking — none of
them preserve children across calls.

| Service | Port | Relaunch in current shell |
|---|---|---|
| Xvfb | :99 | `Xvfb :99 -screen 0 1024x768x24 >/tmp/xvfb.log 2>&1 & sleep 1` |
| Fake webhook | :8765 | `python3 /home/claude/apprise_kit/bin/fake_webhook_server.py &` |
| Mock Plex / Jellyfin / Stash | :32400 / :8096 / :9999 | `bash /home/claude/mocks_kit/bin/start_all_mocks.sh` |
| Ollama | :11434 | `OLLAMA_MODELS=/home/claude/ollama_kit/models /home/claude/ollama_kit/bin/ollama serve &` |
| Flask (BulkDL UI) | :5555 | within-call only: `cd ~/work/BulkDownloader && python3 downloader_ui.py` |

The canonical bootstrap block (§0) launches Xvfb unconditionally
because it's cheap (~1 s) and tray_app tests are common. Mocks and
webhook receivers — relaunch on demand.

**Alternative pattern: skip the HTTP layer.** For `deep_detect`,
call `bulk_downloader.deep_detect.deep_detect()` directly instead of
POSTing to `/api/dev/deep_detect`. Faster, no process-lifetime
problem, and the HTTP wrapper is thin.

**For tests that genuinely need a live Flask:** use the
`_BDServerThread` pattern from `tests/test_v3_53_phase6.py` —
werkzeug `make_server` + `shutdown()` runs within a single
`bash_tool` call.

---

## 6. Running the full test suite

**Per-file / per-suite (the current default):** the lightweight harness is
`run_tests.py` (a minimal pytest-compatible runner; pytest 8.4.2 is also present
in `prestaged_site_packages`). Recent work runs each suite as:

```bash
timeout 90 env BD_HOME=$(mktemp -d) BD_DISABLE_KEEPALIVE=1 \
  PYTHONPATH=/tmp/prestaged_site_packages \
  PLAYWRIGHT_BROWSERS_PATH=/home/claude/.cache/ms-playwright \
  python3 run_tests.py tests/<file>.py
```

The harness **chdirs to a fresh temp dir per run** (tests derive the repo root
from `Path(__file__).resolve().parent.parent` and insert it on `sys.path`) and
does **not** inject pytest builtins: no `tmp_path` (use `tempfile.mkdtemp` +
`shutil.rmtree`), `monkeypatch` is unreliable (restore module globals in
`try/finally`), test functions take zero args. `prestaged_site_packages` has
Flask, so the Flask test client works in-harness. **`run_tests.py tests/` (whole
dir) HANGS at `test_perf_lab.py`** — run targeted files in small batches, and
don't `pkill -9 run_tests` (broad pattern can take the tool shell down).
`test_v3_66_146_nav_guard` times out (>200s) in-sandbox (known, not a
regression). Network is off → live browser/noVNC launches aren't runtime-testable
here.

For a **full-suite** sweep, partition with pytest (cross-file pollution is real):

**The single-shot `pytest tests/` blows past the bash_tool ~5 min
wall-clock cap.** The suite is 5097 tests across 255 files at
`nproc=1`, which takes 7–9 min total.

**Partition the suite.** Four sizes verified, same source tree,
only the chunk boundary changes:

| Partition | Chunks × size | Passed | Failed | Wall-clock |
|---|---|---:|---:|---:|
| 5-chunk | 5 × ~51 files | 5045 | 50 | 8m 30s |
| 6-phase | 6 × ~42 files | 5056 | 39 | 7m 55s |
| **10-phase** | **10 × ~25 files** | **5074** | **21** | **7m 47s** |
| 20-phase | 20 × ~13 files | 5074 | 21 | 7m 30s |

**Twenty-nine failures vanish going from 5-chunk to 10-phase.** Same
tests, same code, same environment. The difference is cross-file
pollution: smaller chunks mean polluter and victim land in different
Python processes and can't reach each other.

**10- and 20-phase produce identical results — that's the failure
floor.** Going smaller doesn't help further: the remaining
failures cluster by module family (sibling files like
`test_dev_suite_tier1b.py` sort alphabetically next to
`test_dev_suite*.py`, so they land in the same partition no
matter how small). 20-phase is slightly faster total walltime and
has the most per-phase headroom under the bash_tool cap (worst
phase 138 s vs. ~5 min cap); 10-phase is fewer commands. Either
is fine.

**10-phase recipe:**

```bash
cd /home/claude/work/BulkDownloader
ls tests/test_*.py | sort > /tmp/all_tests.txt
split -d -n l/10 /tmp/all_tests.txt /tmp/phase_   # produces phase_00..09
for i in 0 1 2 3 4 5 6 7 8 9; do
    TESTS=$(cat /tmp/phase_0${i#0} 2>/dev/null || cat /tmp/phase_$i)
    TESTS=$(echo "$TESTS" | grep -v test_extension_live | tr '\n' ' ')
    python3 -m pytest $TESTS --tb=line --no-header -q --timeout=60 \
        -p no:cacheprovider > /tmp/phase_$i.log 2>&1
done
```

`test_extension_live.py` is filtered out — needs a real Chromium
extension context the sandbox can't provide.

**Triage of the 21 failures at 10-phase:**

| File | In batch | Alone | Verdict |
|---|---:|---:|---|
| `test_dev_suite_tier1b.py` | 6 | 10/10 | Cross-file pollution |
| `test_v3_62_2_login_fallback.py` | 4 | 4/4 | Cross-file pollution |
| `test_phases_195_199.py` | 8 | 0/8 | In-isolation (shipped-DB family) |
| `test_macro_replay.py` | 1 | 0/1 | In-isolation |
| `test_perf_lab.py` | 1 | 0/1 | In-isolation |
| `test_u15_config_import.py` | 1 | 0/1 | In-isolation |

**Theoretical floor at 10-phase ≈ 11 failures.** Eleven are real
in-isolation bugs (partition won't help); ten are cross-file
pollution survivors that smaller chunks would catch.

**Phase distribution at 20-phase:** All 21 failures cluster into
just 6 of 20 phases (01, 03, 04, 08, 18). The other 14 phases
pass 100% clean. If you want a fast partial re-run, hitting only
those 6 dirty phases covers every known failing test in <4 min.

---

## 7. Error → missing variable map

When something breaks, this maps the symptom to the cause:

| Error | Missing |
|---|---|
| `No module named 'pytest'` | PYTHONPATH |
| `No module named 'yt_dlp'` / `'flask'` / `'requests'` | PYTHONPATH |
| `command not found: rg` / `ffmpeg` / `node` | PATH |
| `Can't connect to display ":99"` | Xvfb not running OR DISPLAY unset |
| `Namespace Gtk not available` | GI_TYPELIB_PATH |
| `cannot open shared object file: libgtk-3.so` | LD_LIBRARY_PATH |
| `Executable doesn't exist at /opt/pw-browsers/...` | PLAYWRIGHT_BROWSERS_PATH |
| `Looks like you launched a headed browser without having a XServer running` | Xvfb not running |
| Test imports succeed but stdout has background log lines | BD_DISABLE_KEEPALIVE |
| Tests pass alone, fail in batch with DB-row-count surprises | BD_HOME unset OR test missing chdir (see v3.66.8 #5) |
| `vite: not found` / `vitest: not found` | frontend `node_modules` symlink (installer creates it; only fails on stale tree) |
| `Server already running for display 99` | Stale `/tmp/.X99-lock` — `rm` it |
| `ERR_REQUIRE_ESM` from `html-encoding-sniffer` at jsdom boot | Old frontend kit (pre-v3.66.7 fix); rebuild from latest |

---

## 8. Footguns that have actually bitten

1. **Stale standalone kit upload overriding fresh pack contents.**
   `find_kit` prefers direct uploads. If you uploaded
   `bulkdl_frontend_kit.zip` separately AND it's older than the one
   inside pack_C, the old one silently wins. Fix: `unzip -l` both,
   pick the one with the newer mtime / more packages, or delete the
   standalone before re-running the installer.

2. **Xvfb dies mid-session.** Symptom: tests importing
   `bulk_downloader.tray_app` fail with `Can't connect to display
   ":99"`. Files are still on disk; only the process is gone.
   Relaunch from the bootstrap block (§0).

3. **Re-running the installer wipes `/home/claude/work/frontend/`.**
   Anything you've added (custom packages, edits) is gone. Always
   patch the kit's build on stash and re-extract — don't hand-edit
   the live tree.

4. **`downloader_ui.py` writes to `downloader_history.db` at the
   source tree root.** Each Flask smoke grows this DB. Tests that
   depend on a clean DB will then fail. Either `chdir` into a
   tmpdir before booting, or use a fresh extract of the source zip.

5. **`pack_C.zip` is the heavy one (~492 MB).** If a re-upload
   truncates, `unzip -p` returns a tiny stub. Validate with
   `python3 -m zipfile -l pack_C.zip` (more lenient than `unzip`
   for stored-format Zip64).

6. **Pre-v3.66.7 frontend kits don't have testing-library.** If
   `npm test` fails with `Cannot find module '@testing-library/react'`,
   the kit is stale. Rebuild from current build script.

---

## 9. Where things are documented

| Topic | Authority |
|---|---|
| Env vars (this doc), partitioning, footguns | **`SANDBOX.md` (this file)** |
| Template pipeline schemas (draft/candidate/reviewed) | `SCHEMAS.md` |
| Project scope, ethics, in-code guardrails | `PROJECT_CHARTER.md` |
| Project goals / direction | `PROJECT_GOALS.md` |
| Kit build process on stash | `build_bulkdl_kits.sh` source + comments |
| Per-kit install logic | `install_bulkdl_kits.sh` source + comments |
| BulkDL invariants and load-bearing lines | `DANGER_MAP.md` + `INV_TAGS.md` + `# INV-` inline tags |
| What's open / current state | `KB_HANDOFF_v3_66_158.md` + the newest transcript |
| How to work in the project | `PROJECT_OPERATING_INSTRUCTIONS.md` |
| Session record / history | `/mnt/transcripts/` + `journal.txt` (see §0.5) |

The KB handoff describes **what's true now**; it should not re-document setup. If
a doc re-explains env vars, point it at this file instead.

---

## 10. Quick verification after bootstrap

Paste this after the env block:

```bash
# All should print versions, no errors
which rg jq fd sqlite3 ffmpeg node npm python3
python3 -c "import flask, yt_dlp, playwright, apprise, scrapling; print('python deps OK')"
node -p "require('/home/claude/work/frontend/node_modules/@testing-library/react/package.json').version"
node -p "require('/home/claude/work/frontend/node_modules/html-encoding-sniffer/package.json').version"
# ^ should print 16.x and 4.0.0 respectively

# Sanity: Xvfb is up
xdpyinfo -display :99 >/dev/null 2>&1 && echo "Xvfb OK" || echo "Xvfb DEAD"

# Sanity: BD_HOME and friends are set
env | grep -E "^(BD_|PYTHONPATH|PATH|DISPLAY|PLAYWRIGHT_|GTK_)" | sort
```

If all those pass: you're ready. If any fail, cross-reference the
error map in §7.

---

## 11. Current architecture + release workflow (v3.66.158)

The live work is the **template pipeline** (151–158) and its **cockpit
integration** (158). The earlier detection-safety / dry-run / backend stack
(146–148) is still in source (see CHANGELOG for those module APIs).

### 11.1 The template pipeline (capture → build → normalize → review → promote)

Chain CLIs are stdlib-only (plain `python3`; venv only for app/Flask):

- **Capture** — `tools/capture_session.py --url … --title <label> --out
  <dir>/<label>.wacz [--autofill]`. Headless/noVNC (153): non-TTY keeps the
  browser alive and polls for a sentinel — `touch <out_dir>/FINISH` to save,
  `CANCEL` to discard (`--max-seconds` default 1500, `--finish-file`).
- **Build** — `tools/build_template_from_wacz.py <wacz>` → rich draft
  `templates/drafts/<host>.template-draft.json` (schema
  `bulk_downloader.template_draft.v1`, `draft_requires_review`). Parses HLS/DASH
  manifest bodies for the resolution ladder (154); records
  `network_discovery.observed_api_hosts` as a non-authoritative review hint (157).
- **Normalize** — `tools/normalize_template_draft.py <draft>` →
  `templates/review_candidates/<host>.candidate.json` (schema
  `review_candidate.v1`, `review_ready`|`draft_review_required`, **never enabled**).
- **Review (human)** — add the `api{base}` block + modal-scoped
  `download.row_selectors`; verify ladder; check selector drift vs the gold.
- **Promote** — `tools/promote_template.py <candidate> --enable` →
  `templates/reviewed/<host>.template.json` (`enabled`). Gates on BAD_TERMS,
  blocking lint, and presence of `download.{trigger|row_selectors}` + non-empty
  `resolutions`.

**Runtime consumes** reviewed templates via `bulk_downloader/template_registry.py`
(`load_templates` requires `status=="enabled"` + host match; dirs
`templates/reviewed` + `templates/enabled`; `find_template_for_url`) and
`bulk_downloader/template_assist.py` (`template_to_learned_download`,
`build_api_url` → None if no `api` block, `preferred_resolutions`). Normalizer is
`bulk_downloader/template_normalize.py` (`normalize_draft`). Linter
`bulk_downloader/selector_lint.py` (156: quality/player menus no longer mis-flagged
as nav chrome). Nav-rejection `bulk_downloader/candidate_filter.py`; runtime gate
`bulk_downloader/runner.py :: gate_candidate_url`.

**Gold reference:** `templates/reviewed/app.reptyle.com.template.json` (enabled).
`api{base: https://api2.reptyle.com/api/v1, …}`, login/player/quality/download
selectors, 12 modal-scoped `row_selectors` (`[role="dialog"]` + `.ant-modal`),
ladder `[2160…240]`. **Back it up before any promote** — promote writes to that
exact path. Turnkey on-stash regeneration: `REPTYLE_CAPTURE_RUNBOOK_v3_66_158.md`.
The three schemas (draft / review-candidate / reviewed) are documented
field-by-field, extracted from source, in `SCHEMAS.md`.

### 11.2 Cockpit (158 integration)

Cockpit is a server-rendered SPA from the raw `_PAGE` string in
`tools/cockpit_console.py` (blueprint `cockpit`, `/cockpit`); task/capture backend
in `tools/cockpit_core.py`; read-only template-health data in
`tools/cockpit_templates.py`. 158 added (all additive, endpoints tested):
`POST /api/captures/finish` (`cockpit_core.finish_capture` writes FINISH/CANCEL),
`POST /api/captures/normalize` (in-process build+normalize → review candidate),
`GET /api/review-candidates` (lists candidates + a `promote_cmd`). UI: running
capture tasks show finish/discard, succeeded ones show "build template"; the
template-review page lists review candidates. **Promotion stays CLI** (protects the
gold + the never-auto-enable posture). noVNC capture + button click-throughs are
host-verified, not sandbox-testable.

### 11.3 Release / build workflow

Per-release build script `/tmp/build_<N>.sh` (copy the previous, `sed
's/<prev>/<N>/g'`, adjust STAGE/OUT/verification echoes). It unions the 137 base
zip path-list with a work-tree walk (`bulk_downloader tests tools docs kb
live_tests extension frontend/src frontend/dist scripts templates` + root
`*.md`/`*.txt`; excludes `__pycache__`/`.pyc`/`node_modules`/`venv`); **tree wins**.
Produces a **flat-layout** zip (~7.9M, ~1078 files; `bulk_downloader/` at top
level). Expected **3 MISSING** = stale 137 dist hashes (`index-CipdEztE.css`,
`index-D7fSG1ui.js[.map]`) — the tree ships the live hashes. `run_tests.py` ships
in the base zip.

**Release checklist (in order):**
1. Change + tests green.
2. Bump `__version__` in `bulk_downloader/__init__.py` (**line 26**).
3. If you added/renamed a function in `app.py` or `runner.py`, regenerate
   `FUNCTION_INDEX.md` (`python tools/build_function_index.py`) — it tracks only
   those two files' line numbers (cockpit/package modules are not tracked).
4. If you added a Flask route, regenerate `ENDPOINT_CATALOG.md`
   (`PYTHONPATH=/tmp/prestaged_site_packages python3 tools/build_endpoint_catalog.py`
   — needs Flask; **does** include cockpit blueprint routes).
5. Add a `CHANGELOG.md` entry (contract test needs the current version + matching
   health). Prepending: anchor the `str_replace` on the **previous** version's
   `##` header and re-emit it (dropped 3× historically).
6. Build: `sh /tmp/build_<N>.sh`.
7. **Verify from the EXTRACTED/built zip** (ground truth): version, CHANGELOG top,
   new test files, any new routes in the catalog, then run the new suite +
   `test_function_index` + `test_endpoint_catalog` + `test_contracts` + key
   regression.

### 11.4 Deploy

- Overlay update: `unzip -o <zip>` over `~/BulkDownloader` + `sudo systemctl
  restart bulkdownloader.service`. Exclude `tools/cockpit_console.py
  ENDPOINT_CATALOG.md` if Matt has live cockpit edits. **Matt also overlays files
  himself** → tree and stash can diverge; report divergence candidly.
- Fresh install: unzip the full tree + restart. No exclude needed. (158 is being
  deployed as a fresh install — that's why its cockpit/frontend changes ship in
  the full tree.)

### 11.5 Known-deferred (need a real browser / pending input)

- Item #1 live reptyle capture + promotion to gold — operator step on stash via
  the runbook; validates the pipeline end-to-end and catches DOM drift.
- Resolution under-count is fixed (154) but only recovers the ladder when the
  manifest body was captured — re-running build on an old capture gains the ladder
  only if its `.m3u8`/`.mpd` was recorded.
- Cockpit UI click-throughs (finish/discard/build-template buttons) and noVNC
  capture are host-verified, not sandbox-testable.

---

## 12. v3.66.159 capture / diagnostic footguns (live-Reptyle session)

Hard-won from the first real authenticated Reptyle capture. These bit repeatedly;
read before any live capture or readiness-gate work.

- **`export DISPLAY=:99` in your shell before capturing.** Headed CloakBrowser dies
  on launch without a display (`TargetClosedError`). The service has it via the
  systemd drop-in `/etc/systemd/system/bulkdownloader.service.d/10-display.conf`, but
  an interactive shell does **not** inherit it. Symptom when missing: cloak swallows
  the TargetClosedError, falls back to sync Playwright, which then crashes with a
  misleading "Sync API inside the asyncio loop" error. The asyncio message is a
  **red herring** — the real cause is the missing display. (`cloak.open_persistent_context`
  masking the true error is a known rough edge; surfacing it is a tracked fix.)
- **Run `capture_session.py` in the FOREGROUND (no `&`) on the TTY ENTER-to-save
  path.** Backgrounding it means it can't read your ENTER; the browser eventually
  closes → cloak fallback → crash. The noVNC/cockpit path uses the FINISH-file
  sentinel instead (`touch <out_dir>/FINISH`), which is the right path for headless.
- **The WACZ is written only after you complete the session** (press ENTER / touch
  FINISH). An aborted run yields **no file** — don't run the readiness gate against a
  glob that matched nothing (`IsADirectoryError` on `.`).
- **Template listing endpoint is `/api/template_manager`, NOT `/api/templates`.**
  The latter returns `[]` and looks like "no templates loaded." `template_manager`
  globs `templates/reviewed/*.template.json` fresh per call (no restart needed to see
  a newly-enabled file). Reviewed-vs-enabled lives under `reviewed[].enabled`.
- **fMP4/DASH segments vs the classifier.** Cloudflare Stream serves fMP4/DASH
  (`.../video/240/seg_1.mp4`, `init.mp4`, `.m4s`), which `netlog_classify` counts as
  *direct media*, NOT segments (it keys segments on HLS `.ts`). So a fully-streamed
  capture can read `segment_stream: 0` in a naive count. `tools/workflow_diagnostic.py`
  now recognizes fMP4/DASH via `_fmp4_dash_segments` (diagnostic-only; production
  classifier deliberately unchanged). If you write new readiness logic, count both.
- **DOM can be legitimately 0 for an iframe player.** Reptyle's player is in a
  Cloudflare Stream **iframe**; `context.on("page")` (the multi-tab recorder fix)
  catches new tabs/popups but **not iframes**, so `dom_log` stays empty. For an
  **api-driven** template (download = API/cachefly call, not a DOM click) this does
  **not** block the build — API ladder + manifest + segments are sufficient signal.
  Iframe-level DOM binding is a separate, larger fix; only needed for selector-driven
  sites.
- **Persistent authenticated capture without the CLI:** the cockpit Capture form now
  exposes a **Profile dir** field (159.1). Reuse the same profile path across runs to
  keep the login; the path is confined under the captures root. (CLI equivalent:
  `--profile-dir <dir> --autofill`.)
- **Building a template from a thin (0-DOM) capture regresses a good gold.** If a
  capture has no DOM, `build_template_from_wacz` derives no selectors and produces a
  low-confidence, api-base-`None` draft. Do **not** promote it over a selector-rich
  enabled gold — diff the candidate against `…template.json.bak` first; the gold wins.
