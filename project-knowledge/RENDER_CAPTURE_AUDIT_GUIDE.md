<!-- verified-against: v3.66.510 -->
# Guide: SPA render engine + full GUI capture & audit (definitive)

**Version:** v3.66.510 · supersedes the v3.66.502 guide.
**Audience:** a future session asked to render the GUI and/or audit the
GUI/CLI/config surface from scratch.
**Companion:** `gui_audit_kit/` — eight tested scripts that collapse this whole
guide into one command. **Read §0 + §1 first; everything after is the why + the
manual path.**

---

## 0. TL;DR — two entry points

The hardest parts of this work (finding *hidden* surfaces, computing orphans
correctly, **judging coverage honestly**, capturing both themes, keeping the
numbers honest) are scripted and **tested against the live tree**. Don't
re-derive by hand. Don't trust a substring grep of the bundle (see §4.5).

```
# from $BD_WORK, after a green bd-boot:

# (A) ONE verdict, fast — answers "is the GUI surface healthy, yes/no?" (~12s, no browser):
bd python3 gui_audit_kit/gui_gate.py                  # -> reports/gui_gate_verdict.json, exit 0/1
bd python3 gui_audit_kit/gui_gate.py --capture        # + both-theme sweep + JS-error gate (slow)

# (B) FULL artifacts (JSONs + screenshots + montage), verbose:
bd bash gui_audit_kit/run_gui_audit.sh                # full
bd bash gui_audit_kit/run_gui_audit.sh --fast         # structural + coverage only — seconds
```

**Use (A) `gui_gate.py` as the pre-cut gate.** It collapses discoverability +
coverage + render_check (+ optional capture) into a single PASS/FAIL on the
*invariants* (§7). **Use (B) `run_gui_audit.sh`** when you want the screenshots +
montage + every JSON to eyeball.

If `gui_audit_kit/` isn't attached, §8 has the raw primitives to rebuild it.

---

## 1. The toolkit (use this)

All resolve the current repository by default; use `$BD_WORK` / `--root` to
override); wrap in `bd` for Flask+PATH. Steps that don't screenshot need **no
server**.

| Script | Server? | One-liner |
|---|---|---|
| `gui_gate.py` (new) | self-boots render | ONE verdict on the version-stable invariants; emits `reports/gui_gate_verdict.json`; exit 0/1 |
| `coverage_reconcile.py` (new) | regens inventories | trustworthy coverage verdict — reconciles the dist grep against the authoritative `operator_facing_unwired` gate |
| `audit_snapshot.py` | no | every headline number → one JSON + `--baseline` drift diff |
| `enumerate_surfaces.py` | no | classified page census from the live url_map; flags hidden-from-SPA pages |
| `discoverability_audit.py` | no | reachability + orphans; handles the object-literal `to:`/`href:` trap |
| `capture_gui.py` | self-boots | both-theme screenshots, SPA **+ hidden surfaces**, seeds a site, manifest, guaranteed teardown |
| `montage.py` (new) | no | manifest-driven contact sheets from `capture_gui`'s output (replaces the stale `/mnt/project/build_montage.py`) |
| `run_gui_audit.sh` | — | orchestrates all of the above in order, with a real exit code |

### 1.1 `gui_gate.py` — the one-command verdict *(self-boots render only)*
```
bd python3 gui_audit_kit/gui_gate.py [--capture] [--strict-dist]
```
Runs discoverability → coverage_reconcile (regens the parity + config
inventories) → render_check → (optional) capture, then prints a single PASS/FAIL
and writes `reports/gui_gate_verdict.json`. **Gates only on invariants** (§7), so
it never goes stale on routine count drift. `--capture` adds the screenshot sweep
and the 0-JS-errors gate.

### 1.2 `coverage_reconcile.py` — trustworthy coverage *(regenerates inventories)*
```
bd python3 gui_audit_kit/coverage_reconcile.py            # regen + verdict
bd python3 gui_audit_kit/coverage_reconcile.py --no-regen # reuse fresh reports/*.json (faster)
bd python3 gui_audit_kit/coverage_reconcile.py --strict-dist  # SPA-dist divergence -> hard fail
```
Reads the authoritative gate (`operator_facing_unwired`) and **reconciles** the
crude dist grep into: endpoints confirmed-in-dist vs advisory candidates (cockpit/
`${suffix}` routes); config literal-in-dist vs schema/indirect-benign vs real-gap.
This is what makes the "43 config / 70 endpoint missing" scare numbers
**disappear** (they're false negatives — see §4.5). Verdict JSON →
`reports/coverage_reconcile.json`, exit 0/1. **@510:** unwired 0, 210/212 write
routes confirmed (naive collapse would false-flag 60), config 44 literal / 105
schema-indirect / **0 real-gap**, 2 advisory candidates.

### 1.3 `audit_snapshot.py` — every headline number, one JSON, + drift diff *(no server)*
```
bd python3 gui_audit_kit/audit_snapshot.py --run-inventories
bd python3 gui_audit_kit/audit_snapshot.py --baseline reports/audit_snapshot.PREV.json
```
Re-derives live (never trusts a doc) and `--baseline` prints a recursive delta.
**@510:** routes 952/374/749/181, config 174, env 80, parity 1152 (179/21/952),
unwired **0**.

### 1.4 `enumerate_surfaces.py` — classified page census *(no server)*
Walks the live `url_map`, classifies every GET page, flags **hidden-from-SPA**
pages a nav-walk misses. **@510: 19 hidden-from-SPA pages.**

### 1.5 `discoverability_audit.py` — reachability + orphans *(no server)*
Computes App.tsx routes vs the reachable union (nav + tabs + palette + literal
links + param families); flags orphans. **@510: SPA orphans 0**, bridges
`/cockpit` + `/cockpit/review`, non-SPA orphans `/metrics` + extension. See §4.

### 1.6 `capture_gui.py` — both-theme capture incl. hidden surfaces *(self-boots)*
```
bd python3 gui_audit_kit/capture_gui.py --out "$BD_OUT/shots"   # full
bd python3 gui_audit_kit/capture_gui.py --dry-run                  # list targets, no server
```
Boots its **own** backend (own session → guaranteed teardown), seeds a site,
captures every SPA static route **plus the hidden surfaces** in light + dark,
records a real JS-error count per shot, writes `manifest.json` (dict schema:
`{base,count,shots:[{path,slug,kind,theme,file,http,errors}]}`). **@510: 88 shots
(44 light + 44 dark), 0 JS errors.**

### 1.7 `montage.py` — manifest-driven contact sheets *(no server)*
```
bd python3 gui_audit_kit/montage.py --cap "$BD_OUT/shots" --out /mnt/user-data/outputs
```
Tiles `capture_gui`'s shots into `montage_{light,dark}_{spa,hidden}.png`. Reads
the **dict-manifest** (the old `/mnt/project/build_montage.py` expected a
list-manifest and crashed — that was the orchestrator's montage gap).

### 1.8 `run_gui_audit.sh` — the orchestrator
Sequences 1.3→1.5 (no server) → coverage_reconcile → capture → render_check +
montage. `--fast` stops after the structural + coverage JSONs. Never leaves a port
held; exits non-zero if any gate fails.

---

## 2. Prerequisites & bootstrap
- [ ] `bash /mnt/project/setup.sh` → `bd-boot` **green** (tree at `$BD_WORK`
  == source zip, version matches). The render backend imports
  `bulk_downloader.app` from the work tree — a stale tree renders the wrong
  version, and every number is then wrong. Run `bd-preflight` + `bd-state` first.
- [ ] Playwright + Chromium: `PLAYWRIGHT_BROWSERS_PATH=${XDG_CACHE_HOME:-$HOME/.cache}/ms-playwright`.
  Launch **`headless=True`** — no `DISPLAY`/Xvfb needed.
- [ ] `bd <cmd>` injects Flask + PATH + services. `bash_tool` is **dash** (fresh
  shell per call, no auto-env).

---

## 3. The render backend — lifecycle deep dive
`spa_serve.py` serves the built `frontend/dist` on `127.0.0.1:5599` with **all
writable state redirected to a throwaway temp dir** (`BD_HOME` + `BD_INSTALL_DIR`
+ `chdir`), so no `downloader_history.db` / `video_hashes.db` / `*-wal` leaks into
the work tree — that isolation is the entire reason the launcher exists. The dist
root resolves package-relative, so the `chdir` doesn't affect serving.

**Boot timing.** The server does FTS-optimize + integrity-check + migrations +
blueprint registration (~2-3 s) **before** it binds. Probing `:5599` immediately
returns `000` — **poll for up to ~12 s**, don't assume failure on the first miss.

**Detachment is the footgun, not the boot.** Each `bash_tool` call is an ephemeral
shell; a shell-backgrounded (`&`) server is reaped when the call ends (you'll see
`-1`). Two robust patterns:
1. **Let `capture_gui` / `render_check` / `gui_gate` own it** (preferred) —
   they use `subprocess(..., start_new_session=True)` and do boot→use→teardown
   **inside one process/one call**, so nothing lingers.
2. **`bd-render --serve-only`** — owns the lifecycle from a long-lived parent.

Manual one-call boot+probe (when you must drive it yourself):
```
cd "$BD_WORK"
( setsid env PYTHONPATH=$PWD BD_DISABLE_KEEPALIVE=1 \
    PLAYWRIGHT_BROWSERS_PATH="${XDG_CACHE_HOME:-$HOME/.cache}/ms-playwright" \
    python3 /mnt/project/spa_serve.py >/tmp/spa.log 2>&1 < /dev/null & )
for i in $(seq 1 24); do curl -sf -o /dev/null http://127.0.0.1:5599/ && break; sleep 0.5; done
```
Teardown: `pkill -f spa_serve.py` (or let the owning tool do it).

---

## 4. Discoverability methodology (and the trap)
The question: *for every surface, is there an in-app path, or is it URL-only?*
Build a reachable set and subtract.

**Reachable union** = `navGroups.ts` nav menu ∪ primary sidebar tabs ∪
CommandPalette ∪ every literal `<Link to=>`/`href`/`navigate()` ∪ template-literal
param families (`to={`/sites/${id}/inspect`}`).

**The trap that cost time (twice).** Navigation is emitted in **two syntaxes**:
- JSX attribute: `to="/sites"` / `href="/x"` — matched by `to=`/`href=`.
- **Object literal**: `{ to: "/cockpit/review" }` — colon, **not** equals.
`navGroups.ts` (the whole nav menu) and the SPA→cockpit bridge use the
**object-literal** form. A naive `grep 'to='` misses them and reports false
orphans. `discoverability_audit.py` matches `to:`/`href:` *and* `to=`/`href=`.

**"Linked" ≠ "mentioned".** Match link *forms* (`to:`/`href:`/`navigate(`), not
substrings, or hint text (`framework_reports` in a Settings placeholder) is wrongly
counted as reachable.

**Result @510:** SPA orphans **0** (all client routes reachable), bridges
`/cockpit` + `/cockpit/review`, non-SPA orphans `/metrics` + browser extension.
(The 502 baseline's `/framework/` and `/fleet/` are now linked from src.)

---

## 4.5. Coverage methodology — **a dist grep cannot judge coverage** (the @510 lesson)
The naive instinct (what the retired `verify_gui.py` did): grep the built
`frontend/dist/assets/*.js` for each config key / endpoint path; "not found" =
"no GUI control". **This is wrong and produces alarming false negatives.** Three
independent reasons a present control is invisible to a dist grep:

1. **Template-literal / `${suffix}` interpolation.** The SPA builds routes as
   `` `/api/sites/${id}/${suffix}` `` with `suffix:"manual_login"` /
   `"captcha/test"`, and `` `/api/vpn/tunnels/${id}/${action}` ``. So the literal
   `/api/sites//login` (param collapsed to empty `//`) and even `/manual_login`
   (leading slash) **never appear** — though the base `/api/sites/` and the bare
   `manual_login` do. A naive matcher false-flags every param route.
2. **Cockpit routes live in a different bundle.** `kind=cockpit_api` endpoints
   (e.g. `/api/playground/test`) are wired in the **cockpit** UI, served from the
   blueprint — **not** in `frontend/dist` (the SPA bundle). Grepping the SPA dist
   for them always "misses".
3. **Schema-driven config.** The Settings Center / store-raw raw editor (@507)
   renders fields from a **fetched schema**, so keys like `qb_password`,
   `plex_token`, `vpn.*`, `sched_*` are never literals in the bundle — yet they're
   fully editable.

**The number that actually governs coverage** is `operator_facing_unwired` from
`tools/gui_parity_inventory.py` (`counts.gui_gated_endpoints.operator_facing_unwired`).
It accounts for **all** surfaces (SPA + cockpit + schema). **It is 0 at 510** and
is the only coverage figure to gate on.

`coverage_reconcile.py` operationalizes this: it reads the gate, then reconciles
the dist grep with **param-aware fragment matching** (longest distinctive
fragment, matched with-or-without leading slash) so the false-flag count drops
from 60 to 0 real. Remaining mismatches are classified **advisory** (cockpit/
`${suffix}` — benign, the gate confirms covered), not failures. Config keys absent
as literals are classified **schema/indirect-benign** (the gate proves no
operator-facing field is unwired). Use `--strict-dist` only if you explicitly want
SPA-dist divergence candidates to hard-fail (you usually don't).

**Bottom line:** gate on `operator_facing_unwired`. Treat any "missing from the
bundle" claim as a false-negative candidate until reconciled against the gate.

---

## 5. Capture methodology
- **Enumerate, don't hand-list.** SPA routes from App.tsx (`capture_gui` parses
  them); page surfaces from the url_map (`enumerate_surfaces`). Hand lists rot.
- **Hidden surfaces are explicit.** `/framework/`, `/fleet/`, `/cockpit*`,
  `/cockpit/settings/site/<id>`, mobile aliases — captured by URL. `/metrics` is
  Prometheus **text**: `curl` it, don't screenshot.
- **Both themes** = toggle `.dark` on `documentElement` (and `body`), then wait,
  then shoot. (Cockpit pages keep their own dark shell regardless — expected.)
- **JS errors are the real signal.** Attach a `pageerror` listener per shot; a
  non-zero count is a regression. (@510: 0 errors across 88 shots.)
- **Seed a site** (`POST /api/sites`) so site-scoped routes render with data.
- **`networkidle` waits forever on SSE pages.** Cap the wait at ≤2.5 s.

---

## 6. Structural inventories — what each number is
- **Routes** from the url_map: total / mutating / `/api` / `/cockpit`.
- **Config** via `tools/config_surface_inventory.py` → 174 settings; env-startup
  vars are the `BD_*` subset (80).
- **Parity** via `tools/gui_parity_inventory.py` → 1152 items, `by_gui_support`
  none/partial/full, and the gate **`operator_facing_unwired`** (the figure that
  matters — **0** at 510). The supporting split: `spa_unwired` 23,
  `non_spa_surface_unwired` 8, `dev_internal` 16.
- **Caveats:** `spa_wired_total` jitters run-to-run (set ordering) — gate on
  `operator_facing_unwired`. Tool "CLI" counts are heuristic (~141-144).

---

## 7. Verification checklist
Easiest: `bd python3 gui_audit_kit/gui_gate.py [--capture]` — it checks all of
these and emits one verdict. The invariants it gates on:
- [ ] **`operator_facing_unwired == 0`** (coverage_reconcile / gui_parity) — the one that matters.
- [ ] **coverage_reconcile verdict == PASS** (dist discrepancies reconciled to benign).
- [ ] **SPA orphans == 0** (every client route reachable in-app).
- [ ] **`render_check` 30/30, exit 0** (cockpit computed-layout squish gate; gate on `$?`).
- [ ] **0 JS errors** across both themes (`--capture`; from `manifest.json`).

Informational (printed, never gates): route/config/parity/env totals (drift is
expected), non-SPA orphans + bridges (URL-only-by-design), coverage advisory
candidates, `/api/health` version (confirm it before trusting any shot or number).

---

## 8. Manual primitives (if `gui_audit_kit/` isn't attached)
**Enumerate page routes:**
```
bd python3 - <<'PY'
import os,sys,tempfile; sys.path.insert(0,".")
os.environ.setdefault("BD_HOME",tempfile.mkdtemp()); os.environ.setdefault("BD_DISABLE_KEEPALIVE","1")
from bulk_downloader.app import app
for r in sorted({x.rule for x in app.url_map.iter_rules()
   if "GET" in (x.methods or set()) and "/api/" not in x.rule}):
    print(r)
PY
```
**The coverage gate (the only one to trust):**
```
bd python3 tools/gui_parity_inventory.py && \
bd python3 -c "import json;print(json.load(open('reports/gui_parity_inventory.json'))['counts']['gui_gated_endpoints']['operator_facing_unwired'])"
```
**Nav menu (object-literal form):** `grep -oE 'to:\s*"[^"]+"' frontend/src/lib/navGroups.ts`
**Capture one route both themes:** attach `pg.on("pageerror", …)`, toggle `.dark`
on `documentElement`, `full_page=True`.

---

## 9. Footgun encyclopedia
| Symptom | Cause | Fix |
|---|---|---|
| coverage tool reports "43 config / 70 endpoints MISSING" | substring grep of minified dist; param `${suffix}` + cockpit-bundle + schema-driven fields aren't literals | **don't grep the bundle for coverage** — gate on `operator_facing_unwired`; use `coverage_reconcile.py` |
| a route "absent from dist" but it's wired | it's `kind=cockpit_api` (cockpit bundle, not `frontend/dist`) **or** built via `${suffix}`/`${action}` | classify advisory, not a gap; the gate already counts it covered |
| montage step crashes | `/mnt/project/build_montage.py` expects a **list**-manifest; `capture_gui` writes a **dict**-manifest | use `gui_audit_kit/montage.py` (dict-manifest native) |
| gate "passes" but you changed counts | you gated on a count total (drift) instead of an invariant | gate on `operator_facing_unwired` / orphans / render / JS-errors (what `gui_gate.py` does) |
| `mkdir -p a/{x,y}` made ONE dir `{x,y}` | `bash_tool` is **dash**, no brace expansion | explicit dirs, or `bd bash -c '…'` |
| call returns **-1**, server gone next call | shell-backgrounded (`&`) process reaped at call end | use `capture_gui`/`render_check`/`gui_gate` (self-boot) or do boot+use in **one** call |
| `:5599` returns `000` right after boot | server still doing FTS/migrations (~3 s) before bind | poll up to ~12 s |
| route renders the wrong version | stale `$BD_WORK` | `bd-install` + `bd-preflight` + `bd-state` before serving |
| false orphans (`/schedules`, `/cockpit/review`) | grepped JSX `to=`, missed object-literal `to:`/`href:` | match both forms — `discoverability_audit` does |
| `/framework/` counted as reachable | substring match on hint text, not a link | match link *forms*, not substrings |
| site-scoped pages empty | no seeded site | `POST /api/sites` first |
| dark shots look light | toggled wrong node | `.dark` on `documentElement` **and** `body`, then wait |
| sweep crawls / times out | `networkidle` waits on SSE connections | cap the wait at ≤2.5 s |
| `/metrics` shot blank | it's Prometheus **text** | `curl` it |
| backend check finds wrong backend on stash | used system `python` | on stash use `venv/bin/python`; in sandbox put the tree on `sys.path` |
| `spa_wired_total` differs between runs | set ordering in the scanner | gate on `operator_facing_unwired` (stable 0) |

---

## 10. Baseline @ v3.66.510 (diff against these)
routes 952 (374 mut · 749 api · 181 cockpit) · config 174 · env 80 · tools 180
(~141 CLI) · parity 1152 (none 179 / partial 21 / full 952) ·
**operator_facing_unwired 0** (spa_unwired 23 / non_spa_surface_unwired 8 /
dev_internal 16) · page-surface kinds: cockpit-page 13, cockpit-param 1, framework
2, fleet 1, machine 2, mobile-alias 7, asset 6 · **19 hidden-from-SPA pages** ·
App.tsx 35 static + 5 param routes, reachable union 40 · **SPA orphans 0** ·
SPA→cockpit bridges 2 (`/cockpit`, `/cockpit/review`) · non-SPA orphans 2
(`/metrics`, extension) · render gate **30/30** · capture **88 shots** (44 light +
44 dark), **0 JS errors** · coverage: write-wired 212, **210 confirmed-in-dist**
(naive collapse false-flags 60), config 44 literal / 105 schema-indirect / **0
real-gap**, 2 advisory candidates (`/api/playground/test`, `/api/tags/for_many`).

---

## 11. Annotated session recipe
```
bash /mnt/project/setup.sh && bd-preflight && bd-state && bd-status   # 1. bootstrap — GREEN + version
bd python3 gui_audit_kit/gui_gate.py                                  # 2. ONE verdict on the invariants (fast)
#    -> reports/gui_gate_verdict.json ; exit 0 = healthy
bd bash gui_audit_kit/run_gui_audit.sh                                # 3. (optional) full artifacts: JSONs + shots + montage
#    everything self-tears-down; outputs in reports/ + the screenshot dir + montage_*.png
```
For a pre-cut hook, step 2 alone is the gate. Step 3 is for when you want to *see*
the surface (screenshots + contact sheets), not just gate it.
