# Framework dashboard (read-only in-GUI report viewer)

Sleek, read-only Flask blueprint that renders the recognition-only framework's generated
reports — operator cockpit, executive summary, health, risk, audit, calibration, etc. — in a
single-page dashboard. It reads the JSON/Markdown artifacts the analysis tools produce and
renders them. It has NO control surface: no capture trigger, no remote-browser control, no
command dispatch, no analysis, no writes.

## Integrate into the existing app
    from tools.framework_dashboard import bp as framework_bp
    app.register_blueprint(framework_bp)
Set `BD_FRAMEWORK_REPORTS` to the directory holding the generated reports, then browse to
`/framework`. After registering, regenerate ENDPOINT_CATALOG.md (it adds read-only GET routes
under /framework).

## Or run standalone (local viewing)
    BD_FRAMEWORK_REPORTS=./reports python3 tools/framework_dashboard.py   # serves 127.0.0.1:8770

## Routes (all read-only GET)
- `/framework`                 — cockpit overview (maturity, debt, risks, fragile sites, stale evidence)
- `/framework/report/<name>`   — render any .md (to HTML) or .json (pretty-printed) report under the root
- `/framework/api/cockpit.json`— JSON passthrough of the cockpit for a custom frontend

## Security / posture
- Path-traversal guarded: report names are resolved strictly under the reports root (verified).
- Read-only: serves files; issues no commands; recognition-only posture unchanged.

## Preview
`preview/cockpit_preview.html` and `preview/report_preview.html` are static renders of the UI
(generated from the example reports) so you can see the look without running the server. Links
in the static preview are non-functional; run the blueprint for the live, navigable version.

## Not included (by design / on request)
Capture-task triggering and a noVNC view are NOT part of this read-only dashboard. A control
surface for your OWN authorized capture sessions can be added as a separate, explicitly-scoped
piece. A command-and-control ("C2") site for controlling sessions/machines that are not your
own authorized ones is out of scope and will not be built.

---

## Multi-server fleet view (framework_fleet.py)

Read-only overview across several BD servers you own on your own network. Each node already
exposes the read-only `/framework/api/cockpit.json`; the fleet view fetches that from each
node and rolls it up — worst maturity across the fleet, summed validation debt, total review
workload, fragile sites and high risks tagged by node, and per-node reachability.

Integrate:
    from tools.framework_fleet import bp as fleet_bp
    app.register_blueprint(fleet_bp)
Point `BD_FLEET_NODES` at a JSON file of your nodes (see `nodes.example.json`), then browse to
`/fleet`. Or run standalone: `BD_FLEET_NODES=./nodes.json python3 tools/framework_fleet.py`.

Routes (read-only GET): `/fleet` (overview), `/fleet/api/summary.json` (aggregate JSON).

This is fleet **monitoring** — it reads status from your own servers. It issues no commands
and dispatches no tasks; it is not a control channel. Keep each node's /framework routes
behind the app's existing auth; the per-node token (if set) is sent as a Bearer header.
Preview: `preview/fleet_preview.html`.
