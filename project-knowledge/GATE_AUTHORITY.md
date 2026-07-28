<!-- verified-against: v3.66.276 -->
# GATE_AUTHORITY — guards · in-sync gates · operator live edits (one card)

The single source for "what must match byte-for-byte, what must be regenerated when a
route/function changes, and what a deploy does NOT do for you." Other docs should point here
rather than restate. **Live guard SHA values live in `guards.json`** at the repo root -- the
single source of truth, hashed from the files and read by `bd-guardcheck` by default (so a
declared change updates cleanly). This card names the *files and process*, which don't change
per release. Counts in this card are re-derivable; measure them, don't quote them.

---

## A. The 7 byte-identical release guards
Re-derive all seven before any cut with **`venv/bin/python toolchain/bin/bd-guardcheck --tree
"$PWD"`**, which hashes the files in the tree against `guards.json`. The box deploys by
`git reset --hard origin/main`, so the work tree at `origin/main` **is** the deployed artifact --
hash the tree. A change is allowed only if **declared with its new sha**: `bd-guard-declare
--apply --reason "..."`, then update `guards.json` (per that file's own `_comment`). Git history
is not authorization.

A `BD-GATE-UNRUNNABLE` message or exit 2 means the pins were **NOT** verified -- do not proceed
on it. A zero-in-every-bucket summary (`0 ok, 0 drifted, 0 missing`) is a **failure** signal, not
a pass: until v3.66.818 `bd-guardcheck` printed exactly that on a clean tree and exited 0. The
set:

1. `bulk_downloader/extraction_core.py`
2. `bulk_downloader/session_capture.py`
3. `tools/capture_session.py`
4. `bulk_downloader/dom_capture.py`
5. `bulk_downloader/dom_recorder.py`
6. `bulk_downloader/capture_bodies.py`
7. `tools/build_release.py`  ← joined the set at the 6→7 transition, when it took the in-sync gate logic.

Re-derive (preferred -- reads `guards.json`, reports drifted/missing/unpinned separately):
```
venv/bin/python toolchain/bin/bd-guardcheck --tree "$PWD"
```

Manual fallback only if `bd-guardcheck` itself is unrunnable (run from the tree root; compare
against the FULL digests in `guards.json`, never against the truncated table in CLAUDE.md):
```
for f in bulk_downloader/extraction_core.py bulk_downloader/session_capture.py \
  tools/capture_session.py bulk_downloader/dom_capture.py bulk_downloader/dom_recorder.py \
  bulk_downloader/capture_bodies.py tools/build_release.py; do
  printf "%s  %s\n" "$(sha256sum "$f" | cut -c1-8)" "$f"; done
```

**Do NOT conflate** these 7 byte-identical guards with the **5 ASI-separator checks** in
`test_dom_recorder_asi.py` (card #2) — same word "guard," narrower/different set.

---

## B. The 4 in-sync regen targets + the route-count gate
`build_release.py` **gates on the first three** (prints a diff, exits 1 on drift). Regenerate only
when the relevant surface changed; then re-run each `--check`.

| Target | Tool (regen) | Tracks / scope | When it moves |
|---|---|---|---|
| `ENDPOINT_CATALOG.md` | `tools/build_endpoint_catalog.py` *(needs Flask)* | all routes incl. cockpit/blueprint | any route add/remove |
| `FUNCTION_INDEX.md` | `tools/build_function_index.py` | **only `app.py` + `runner.py`** line numbers | a func added/renamed in those two files; a **new cockpit page usually leaves it UNCHANGED** (confirm, don't assume) |
| `DEPENDENCY_GRAPH.{json,md}` | `tools/dependency_graph.py` *(needs Flask)* | `bulk_downloader/` + `tools/` subset | edges/blueprint change; **full-text scan → a token in a comment can create a false edge** (reword) |
| `reports/gui_parity_inventory.{json,md}` | `tools/gui_parity_inventory.py` *(needs Flask)* | endpoint `spa_wired` flags | a route is wired/unwired in the SPA |
| **G12 route-count** | `tools/check_route_counts.py` | source-decorators == inventory == test-pin | every route add (actions_center N→N+1) |

Regen invocations. **The interpreter is `venv/bin/python`, never bare `python3`** -- `python3` in
the cloud container is 3.11 *without* the project dependencies (Flask included), so the three
"needs Flask" generators either fail or import a different stack than the box runs. There is no
`/tmp/prestaged_site_packages`; `venv` already supplies Flask.
```
BD_DISABLE_KEEPALIVE=1 venv/bin/python tools/build_endpoint_catalog.py
venv/bin/python tools/build_function_index.py
BD_DISABLE_KEEPALIVE=1 venv/bin/python tools/dependency_graph.py
BD_DISABLE_KEEPALIVE=1 venv/bin/python tools/gui_parity_inventory.py
```
Or regenerate everything in the mandated order (CLAUDE.md section 2 requires this before
packaging a change for review):
```
venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```
A **GUI-parity write cut touches all four + G12**. A **frontend-only / one-tool-module** change
that adds no `/api/` literal and no route leaves all of them UNCHANGED — regen nothing; the build
gates confirm it.

**SPA-parity gotcha:** `gui_parity_inventory` marks `spa_wired` by scanning the SPA for **literal
`/api/…` strings**. Build call paths as FULL literals — ``apiPost(`/api/sites/${id}/foo`)`` — NOT
via a concatenated `base` const, or the scanner can't see it and the endpoint stays `spa_unwired`.

---

## C. Operator live-edited files (a NEW hazard, not a retired one)
The box deploys purely by git: `git fetch origin main && git reset --hard origin/main`, then
restart. **`git reset --hard` discards ANY uncommitted local edit, unconditionally. There is no
`-x` exclude.** The old overlay had one (`unzip -o -x <file>` preserved a live-edited file);
git has **no equivalent**. This is a hazard the deploy change *introduced* -- protection that
used to exist and no longer does, not merely stale text.

Affected files -- the two the operator has historically live-edited on `stash`:

- `tools/cockpit_console.py` — the cockpit blueprint endpoints; operator live-edits. **Commit
  (or `git stash`) live edits before deploying or they are lost.** Fixes that must take effect
  still belong in `tools/cockpit_core.py`, with the console endpoint **delegating** to it
  (e.g. `api_novnc` -> `cc.novnc_url()`); confirm the delegation survives after deploy.
- `ENDPOINT_CATALOG.md` — regenerated artifact; a live-cockpit edit here is likewise discarded.
  Regenerate it from source (section B) rather than hand-editing on the box.

Deletions now propagate natively (git removes files the overlay never could), so the *orphan*
class that `bd-deploy-manifest` / `tools/deploy_manifest.py` detect cannot occur on a git deploy.

**What git does NOT do.** A git deploy moves *files*; it does not make the *running system*
match them. None of the following were ever properties of the overlay either, so none of them
went away. Treat this as a **condition set to re-check, not a fixed count** -- verify each
condition applies or does not, rather than trusting the length of the list:

- stale `__pycache__` / `*.pyc` are **NOT** cleared -- `git reset --hard` leaves old bytecode
  exactly as `unzip -o` did (the v3.66.161 footgun, unchanged). Purge them.
- gitignored generated artifacts are **NOT** refreshed, and `git clean -fd` will not remove them
  either (that needs `-x`). A stale `reports/gui_parity_inventory.json` reads as parity drift and
  fails the **entire** suite. The durable fix is to **regenerate, not delete**.
- `frontend/dist/` is **NOT delivered at all**: `git ls-files frontend/dist` returns 0 files and
  `frontend/.gitignore` ignores `dist/`. `bulk_downloader/app.py` serves a uniform **503** when
  the bundle is missing, so a missing or stale bundle is a silent 503 on the SPA. Rebuild
  whenever SPA source changed.
- the service is **NOT** restarted.

Always:
```
cd ~/BulkDownloader && git fetch origin main && git reset --hard origin/main
#   ^ discards uncommitted local edits (cockpit_console.py, ENDPOINT_CATALOG.md) -- commit first
find ~/BulkDownloader -name __pycache__ -type d -prune -exec rm -rf {} +
find ~/BulkDownloader -name '*.pyc' -delete    # load-bearing: stale .pyc runs old bytecode
cd ~/BulkDownloader/frontend && npm ci && npm run build   # if SPA source changed: dist/ is NOT in git
cd ~/BulkDownloader && BD_DISABLE_KEEPALIVE=1 venv/bin/python tools/gui_parity_inventory.py
#   ^ gitignored artifact: git will neither refresh nor clean it; a stale copy fails the suite
sudo systemctl restart bulkdownloader
curl -s localhost:5555/api/health              # CONFIRM the new version before trusting anything
```

---

## D. One-line summary
- **Guards (7):** byte-identical in the work tree via `bd-guardcheck --tree "$PWD"`; declare changes with sha. Baseline in `guards.json`.
- **In-sync (4 + G12):** regen only the surfaces you touched, with `venv/bin/python`; build gates the first three.
- **Operator live edits (2):** `git reset --hard` discards them -- commit before deploy; confirm `cockpit_core` delegation.
- **After every deploy:** git moved files, nothing else. Clear pycache; regenerate gitignored artifacts; rebuild `frontend/dist` if SPA source changed; restart; confirm `/api/health`. Re-check the condition list in section C rather than trusting its length.
