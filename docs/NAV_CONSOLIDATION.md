# NAV_CONSOLIDATION — historical information-architecture consolidation

Status: **SHIPPED; refreshed against v3.66.817.** This document was referenced by
`app_cockpit_home.py` since the center pages first shipped but never existed —
the consolidation pass it described was deferred wave after wave. The
v3.66.199 MAX audit (`FINDING_orphaned_pages_v3_66_199.md`) found the result:
from `/`, exactly one page was reachable — `/` itself. Every other surface
(the entire D3 SPA, the cockpit console, all 23 center pages, framework,
fleet) was typed-URL only, and 13 of 26 D3 routes had no inbound link even
inside the SPA. The sections describing the v3.66.200 wiring pass are retained
as history; the current disposition is summarized below.

## Current surfaces

| Surface | Path | Role |
|---|---|---|
| D3 SPA | `/` | Primary day-to-day downloader UI; root-flipped at v3.66.203 |
| Compatibility shims | `/m`, `/m/ops`, `/m2/*` | Deep-link-preserving redirects to the root SPA |
| Cockpit console | `/cockpit` | Single-page operator console (capture / report / autopilot workflows) |
| Center pages | `/cockpit/settings`, `/cockpit/reports`, `/cockpit/template-manager`, and related routes | Server-rendered, mostly read-only or confirm-gated operator pages; the old `/cockpit/home` landing is retired while `/api/cockpit/nav` remains the navigation source |
| Framework / Fleet | `/framework/`, `/fleet/` | Read-only report dashboards |

The legacy shell and `/legacy` route are retired. `/legacy` is deliberately
reserved so it returns 404 rather than falling through to SPA HTML.

## The hub model (v3.66.200 pass; historical)

The original rule was: **every page must be reachable by clicks from `/`.**
After the root flip, React navigation owns SPA reachability. The current
external-console contract is explicit in `frontend/src/lib/navGroups.ts`:
`/framework`, `/fleet`, and `/cockpit` must remain `external: true` anchors.

> **RETIRED AT v3.66.344, AND THIS SECTION DESCRIBED IT AS LIVE FOR 772 MORE
> RELEASES.** The three bullets below are kept as the historical record of the
> v3.66.3xx arrangement; they are **not** current. The server-rendered
> `/cockpit/home` landing page is gone — `app_cockpit_home.py` serves only
> `/api/cockpit/nav`, which remains the nav source of truth, and the SPA at `/`
> plus the consolidated cockpit console replace the hub. The surviving cockpit
> pages are `/cockpit/reports` and `/cockpit/settings`.
>
> This paragraph is why the dead breadcrumb survived: three served pages carried
> `← Cockpit Home` pointing at a 404, and the document a reader would check
> described that as intended behaviour. Corrected at v3.66.1117, backlog row 113.
> Note which gate could not see it — `docs/**.md` is outside both freshness
> gates (row 106), and `nav_reachability.py` crawls INBOUND reachability, so a
> link pointing OUT at a dead route is outside the denominator of the check
> built for dead links.

- ~~`/` (legacy shell) gained a normal-flow **Consoles footer**: → `/m2/`,
  `/cockpit/home`, `/cockpit`, `/framework/`, `/fleet/`.~~ *(historical)*
- ~~`/cockpit/home` is the **hub** for everything cockpit-side. Its NAV now
  includes Actions, Settings (+secrets), Consoles (framework / fleet / both
  main UIs) on top of the original Templates / Monitoring / Reports / Dev
  groups.~~ *(historical — the page is retired)*
- ~~Every center shell page carries a **← Cockpit Home** breadcrumb
  (actions `_PAGE_HEAD` covers all 10 actions pages).~~ *(historical — those
  breadcrumbs now point at `/`)*
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

## Former Plan 3 disposition

The root flip and legacy retirement listed here have shipped. The remaining
items are independent design choices, not unfinished prerequisites of this
historical consolidation:

1. **Shared cockpit chrome** module across console + centers.
2. **Dedup duplicate surfaces**: server `/cockpit/template-manager` vs SPA
   `/templates`; cockpit actions pages vs SPA parity pages — pick canonical,
   redirect the other.
3. Fold framework / fleet into cockpit home groups.
