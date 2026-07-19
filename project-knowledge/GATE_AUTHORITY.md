<!-- verified-against: v3.66.276 -->
# GATE_AUTHORITY — guards · in-sync gates · deploy-excluded (one card)

The single source for "what must match byte-for-byte, what must be regenerated when a
route/function changes, and what must NOT be overlaid on deploy." Other docs should point here
rather than restate. **Live SHA/route-count *values* live in STATE.json** (so a declared change
updates cleanly) — this card names the *files and process*, which don't change per release.

---

## A. The 7 byte-identical release guards
Re-derive all seven **from the EXTRACTED zip** (never the work tree) before any cut; they must be
byte-identical to the STATE baseline. A change is allowed only if **declared with its new sha**
(updates the STATE baseline). The set:

1. `bulk_downloader/extraction_core.py`
2. `bulk_downloader/session_capture.py`
3. `tools/capture_session.py`
4. `bulk_downloader/dom_capture.py`
5. `bulk_downloader/dom_recorder.py`
6. `bulk_downloader/capture_bodies.py`
7. `tools/build_release.py`  ← joined the set at the 6→7 transition, when it took the in-sync gate logic.

Re-derive (from an extracted zip dir):
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

Regen invocations (Flask via prestaged path):
```
PYTHONPATH=/tmp/prestaged_site_packages BD_DISABLE_KEEPALIVE=1 python3 tools/build_endpoint_catalog.py
python3 tools/build_function_index.py
PYTHONPATH=/tmp/prestaged_site_packages BD_DISABLE_KEEPALIVE=1 python3 tools/dependency_graph.py
PYTHONPATH=/tmp/prestaged_site_packages BD_DISABLE_KEEPALIVE=1 python3 tools/gui_parity_inventory.py
```
A **GUI-parity write cut touches all four + G12**. A **frontend-only / one-tool-module** change
that adds no `/api/` literal and no route leaves all of them UNCHANGED — regen nothing; the build
gates confirm it.

**SPA-parity gotcha:** `gui_parity_inventory` marks `spa_wired` by scanning the SPA for **literal
`/api/…` strings**. Build call paths as FULL literals — ``apiPost(`/api/sites/${id}/foo`)`` — NOT
via a concatenated `base` const, or the scanner can't see it and the endpoint stays `spa_unwired`.

---

## C. Deploy-excluded (merge, not overlay)
A bare `unzip -o` of the full release zip would clobber these operator-live-edited files. **Exclude
them on overlay** (`-x`) if the operator has live edits, and re-confirm the delegation noted below:

- `tools/cockpit_console.py` — the cockpit blueprint endpoints; operator live-edits. Fixes that
  must take effect are placed in `tools/cockpit_core.py` (which overlays normally) and the
  console endpoint **delegates** to it (e.g. `api_novnc` → `cc.novnc_url()`). Confirm the
  delegation survives after deploy.
- `ENDPOINT_CATALOG.md` — regenerated artifact; exclude if the live cockpit was edited.

A clean full-zip overlay is safe **only when the cut changed neither of these**. Always:
```
cd ~/BulkDownloader && unzip -o <zip>          # add -x tools/cockpit_console.py ENDPOINT_CATALOG.md if live-edited
find ~/BulkDownloader -name __pycache__ -type d -prune -exec rm -rf {} +
find ~/BulkDownloader -name '*.pyc' -delete    # load-bearing: stale .pyc runs old bytecode
sudo systemctl restart bulkdownloader
curl -s localhost:5555/api/health              # CONFIRM the new version before trusting anything
```

---

## D. One-line summary
- **Guards (7):** byte-identical from the extracted zip; declare changes with sha. Baseline in STATE.
- **In-sync (4 + G12):** regen only the surfaces you touched; build gates the first three.
- **Deploy-excluded (2):** never overlay-clobber; confirm `cockpit_core` delegation; clear pycache; confirm `/api/health`.
