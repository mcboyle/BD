# NAV_CONSOLIDATION — information architecture for BulkDownloader's UI surfaces

Status: v3.66.200 (wiring pass shipped). This document was referenced by
`app_cockpit_home.py` since the center pages first shipped but never existed —
the consolidation pass it described was deferred wave after wave. The
v3.66.199 MAX audit (`FINDING_orphaned_pages_v3_66_199.md`) found the result:
from `/`, exactly one page was reachable — `/` itself. Every other surface
(the entire D3 SPA, the cockpit console, all 23 center pages, framework,
fleet) was typed-URL only, and 13 of 26 D3 routes had no inbound link even
inside the SPA.

## The three frontends (unchanged by this pass)

| Surface | Path | Role |
|---|---|---|
| Legacy shell | `/` | Primary day-to-day downloader UI (default root) |
| D3 SPA | `/m2/` | React successor UI; `/m`, `/m/ops` 302 here |
| Cockpit console | `/cockpit` | Single-page operator console (capture / report / autopilot workflows) |
| Center pages | `/cockpit/home`, `/cockpit/actions`, `/cockpit/settings`, `/cockpit/reports`, `/cockpit/monitoring`, `/cockpit/template-manager` | Server-rendered, mostly read-only or confirm-gated operator pages |
| Framework / Fleet | `/framework/`, `/fleet/` | Read-only report dashboards |

## The hub model (this pass)

One rule: **every page must be reachable by clicks from `/`.**

- `/` (legacy shell) gained a normal-flow **Consoles footer**: → `/m2/`,
  `/cockpit/home`, `/cockpit`, `/framework/`, `/fleet/`.
- `/cockpit/home` is the **hub** for everything cockpit-side. Its NAV now
  includes Actions, Settings (+secrets), Consoles (framework / fleet / both
  main UIs) on top of the original Templates / Monitoring / Reports / Dev
  groups.
- Every center shell page carries a **← Cockpit Home** breadcrumb
  (actions `_PAGE_HEAD` covers all 10 actions pages).
- The cockpit **console sidebar** gained a real-href "Centers" navsec.
  ⚠ `tools/cockpit_console.py` is deploy-excluded (live stash edits exist);
  the change ships in-tree **and** as a standalone merge diff.
- The **settings center index** now links `secrets` and the per-site editor
  pattern (`/cockpit/settings/site/<sid>`), which previously appeared only as
  code-text.
- D3 SPA: the tab bar is contract-frozen at 5 entries
  (`test_d3_u3_bottom_tab_bar_has_all_5_tabs`), so overflow lives in two
  places: **Settings → "Tools & operations"** (canonical, organized:
  operations / data / network / automation) and the **⌘K CommandPalette
  "Tools" group** (full route list). `SiteDetail` links its two per-site
  action pages (`actions`, `payload-actions`) beside the candidate inspector.

## The gate (prevents recurrence)

`tools/nav_reachability.py` + `tests/test_nav_reachability.py`:

- **Server side** — boots the real app, BFS-crawls rendered `<a href>` links
  from `/` with the Flask test client, and fails if any GET page rule in
  `url_map` is unreachable. Param rules (`/cockpit/settings/site/<sid>`,
  `/framework/report/<name>`) pass via an inbound href-prefix on a reachable
  page or a source-level reference. `/m`, `/m/ops` are documented redirect
  shims (302 → `/m2`). `/m2` is allowed to answer 503 when `frontend/dist`
  is absent.
- **SPA side** — statically parses `App.tsx` routes and scans
  `frontend/src` for inbound nav targets (`to=`, `navigate(`, `go(`);
  every route needs at least one inbound link outside its own file.

The gate lives as a **test**, not inside `build_release.py` (guard file) and
not as a bd-cut script change — it runs in the cut band and in the on-stash
full suite.

Root causes this closes (from the 199 finding): additive-only waves with a
deferred consolidation that never ran; a parity metric (`spa_wired`) that
measures endpoint fetch literals rather than page reachability; three
frontends with zero cross-links; a frozen tab bar with no overflow surface.

## Deferred — Plan 3 (maximalist, on the Roadmap)

Not in scope here, recorded for a future attended slice:

1. **Root flip**: `/` serves the D3 SPA, legacy moves to `/legacy` —
   completes the stalled D4 retirement. Requires verifying the CSRF
   bootstrap currently living in the legacy `index()` handler, full in-sync
   regen (endpoint catalog, parity inventory, dependency graph, G12, pin
   sweep), and operator click-through.
2. **Shared cockpit chrome** module across console + centers.
3. **Dedup duplicate surfaces**: server `/cockpit/template-manager` vs SPA
   `/templates`; cockpit actions pages vs SPA parity pages — pick canonical,
   redirect the other.
4. Fold framework / fleet into cockpit home groups.
