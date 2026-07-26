#!/usr/bin/env python3
"""G12 gate: route-count / inventory-freshness consistency check.

Closes the class of defect that shipped in v3.66.176 and was only caught by
the full on-stash suite *after* deploy: a blueprint gains routes, but a
version-coupled count pin (and/or the parity inventory) is not updated in the
same change, so the focused release band passes while the real suite goes red.

Before the count checks, the gate regenerates the full GUI-parity inventory in
an isolated child process and requires the shipped and live item-name sets to
match. This catches drift in non-route items such as CLI tools as well as routes.

The gate then cross-checks THREE independent sources and fails the build if any
of them disagree:

  1. SOURCE (ground truth) — the number of `@<bp>.route(...)` decorators in
     the blueprint module, and the length of the report-center SECTIONS list.
  2. INVENTORY — the count of `reports/gui_parity_inventory.json` items whose
     `name` is prefixed with the blueprint (`data_layer.` / `report_center.`).
     A mismatch here means the inventory is STALE (route added, inventory not
     regenerated) — the "stale denominator" failure.
  3. TEST PIN — the integer literals pinned in
     `tests/test_wave2_backlog.py` (the data-layer / report-center
     register_routes() counts and the SECTIONS length). A mismatch here is the
     exact failure that 176 shipped.

Flask-free and fast (~static parse only), so it can run as a build gate without
importing the app or spawning a browser. Mirrors the --check convention of the
other gate tools (build_endpoint_catalog.py, dependency_graph.py, etc.).

Usage:
    python tools/check_route_counts.py --check     # exit 0 = consistent, 1 = drift
    python tools/check_route_counts.py             # same, prints the table either way

Exit codes:
    0 — all three sources agree for both blueprints
    1 — drift detected (details printed) OR a required source file is missing
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _root() -> Path:
    # tools/<this>.py  ->  repo root is parent of tools/
    return Path(__file__).resolve().parent.parent


def _count_route_decorators(module_path: Path, bp_name: str) -> int:
    """Count `@<bp_name>.route(...)` decorators on top-level functions.

    This is exactly what each blueprint's register_routes() returns at
    runtime (one url_map rule per decorated view, all single-method GETs).
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"), str(module_path))
    n = 0
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # match `bp_name.route(...)`
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "route"
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == bp_name
            ):
                n += 1
    return n


def _sections_len(module_path: Path) -> int:
    """Length of the module-level SECTIONS = [...] list literal."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"), str(module_path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "SECTIONS":
                    if isinstance(node.value, ast.List):
                        return len(node.value.elts)
    raise ValueError(f"SECTIONS list not found in {module_path}")


def _inventory_counts(inv_path: Path) -> dict[str, int]:
    data = json.loads(inv_path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    out: dict[str, int] = {"data_layer.": 0, "report_center.": 0}
    for it in items:
        name = str(it.get("name", ""))
        for pfx in out:
            if name.startswith(pfx):
                out[pfx] += 1
    return out


def _inventory_names(data: dict) -> set[str]:
    return {
        str(item.get("name", ""))
        for item in data.get("items", [])
        if item.get("name")
    }


def _live_inventory(root: Path) -> dict:
    """Generate the current inventory in a child process without tree writes."""
    with tempfile.TemporaryDirectory(prefix="bd_gui_parity_check_") as tmp:
        outdir = Path(tmp)
        result = subprocess.run(
            [
                sys.executable,
                str(root / "tools" / "gui_parity_inventory.py"),
                "--root",
                str(root),
                "--outdir",
                str(outdir),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gui parity generator exited {result.returncode}: "
                f"{result.stderr[-1000:]}"
            )
        return json.loads(
            (outdir / "gui_parity_inventory.json").read_text(encoding="utf-8")
        )


def _test_pins(test_path: Path) -> dict[str, int]:
    """Extract the three integer pins from test_wave2_backlog.py by pattern.

    Deliberately literal so the gate breaks loudly if the test is restructured
    (forcing a human to re-point this gate, which is the desired behavior).
    """
    text = test_path.read_text(encoding="utf-8")
    pins: dict[str, int] = {}
    pats = {
        "data_layer": r"register_routes\(app\)\s*==\s*(\d+)",
        "report_center": r"register_routes\(Flask\(__name__\)\)\s*==\s*(\d+)",
        "sections": r"len\(secs\[\"sections\"\]\)\s*==\s*(\d+)",
    }
    for key, pat in pats.items():
        m = re.search(pat, text)
        if not m:
            raise ValueError(
                f"could not find the {key} pin in {test_path.name} "
                f"(pattern: {pat}) — gate needs re-pointing"
            )
        pins[key] = int(m.group(1))
    return pins


def _blueprint_counts(root: Path) -> tuple[int, int]:
    """(source, graph-artifact) blueprint counts, for the build-time freshness
    row added in v3.66.178.

    source  — `bulk_downloader/*.py` modules that instantiate `Blueprint(...)`
              (includes the ternary form); the same ground truth the dep-graph
              test self-anchors on.
    graph   — len of the `blueprint` map in the on-disk DEPENDENCY_GRAPH.json.

    Light by design: a glob + one JSON read, no app import / graph rebuild. A
    mismatch means a blueprint module was added/removed without regenerating the
    dependency graph — i.e. exactly the v3.66.177 stale-pin class, caught here
    at build start instead of in the post-deploy suite.
    """
    bd = root / "bulk_downloader"
    src = sum(
        1 for p in bd.glob("*.py")
        if re.search(r"\bBlueprint\s*\(", p.read_text(encoding="utf-8"))
    )
    gpath = root / "DEPENDENCY_GRAPH.json"
    graph = len(json.loads(gpath.read_text(encoding="utf-8")).get("blueprint", {})) \
        if gpath.exists() else -1
    return src, graph


def run(root: Path) -> int:
    bd = root / "bulk_downloader"
    data_mod = bd / "app_data_layer.py"
    rc_mod = bd / "app_report_center.py"
    inv = root / "reports" / "gui_parity_inventory.json"
    test = root / "tests" / "test_wave2_backlog.py"

    missing = [p for p in (data_mod, rc_mod, inv, test)
               if not p.exists()]
    if missing:
        for p in missing:
            print(f"check_route_counts: MISSING required file: {p}", file=sys.stderr)
        return 1

    try:
        shipped_inventory = json.loads(inv.read_text(encoding="utf-8"))
        live_inventory = _live_inventory(root)
    except Exception as exc:
        print(
            f"GUI-PARITY GATE FAIL: could not compare shipped inventory: {exc}",
            file=sys.stderr,
        )
        return 1
    shipped_names = _inventory_names(shipped_inventory)
    live_names = _inventory_names(live_inventory)
    only_shipped = sorted(shipped_names - live_names)
    only_live = sorted(live_names - shipped_names)
    if only_shipped or only_live:
        print(
            "GUI-PARITY GATE FAIL: shipped item-set differs from live generator.",
            file=sys.stderr,
        )
        print(f"  only shipped: {only_shipped}", file=sys.stderr)
        print(f"  only live: {only_live}", file=sys.stderr)
        return 1

    src_data = _count_route_decorators(data_mod, "data_layer_bp")
    src_rc = _count_route_decorators(rc_mod, "report_center_bp")
    src_sections = _sections_len(rc_mod)

    inv_counts = _inventory_counts(inv)
    inv_data = inv_counts["data_layer."]
    inv_rc = inv_counts["report_center."]

    pins = _test_pins(test)

    bp_src, bp_graph = _blueprint_counts(root)

    rows = [
        # label, source, inventory, test-pin
        ("data_layer routes", src_data, inv_data, pins["data_layer"]),
        ("report_center routes", src_rc, inv_rc, pins["report_center"]),
        ("report_center SECTIONS", src_sections, None, pins["sections"]),
        ("blueprint count", bp_src, bp_graph, None),
    ]

    drift = []
    print(f"{'check':<26} {'source':>7} {'inventory':>10} {'test-pin':>9}  status")
    print("-" * 70)
    for label, s, i, p in rows:
        vals = [v for v in (s, i, p) if v is not None]
        ok = len(set(vals)) == 1
        if not ok:
            drift.append(label)
        istr = "-" if i is None else str(i)
        pstr = "-" if p is None else str(p)
        print(f"{label:<26} {s:>7} {istr:>10} {pstr:>9}  {'OK' if ok else 'DRIFT'}")

    print("-" * 70)
    if drift:
        print(f"ROUTE-COUNT GATE FAIL: drift in {', '.join(drift)}.")
        print("Fix: update the stale source(s) so all sources agree — typically")
        print("  (a) regenerate reports/gui_parity_inventory.* (tools/gui_parity_inventory.py), and/or")
        print("  (b) re-pin tests/test_wave2_backlog.py to the live route counts, and/or")
        print("  (c) regenerate DEPENDENCY_GRAPH.json (tools/dependency_graph.py) for a blueprint-count drift.")
        return 1
    print("ROUTE-COUNT GATE OK: source == inventory == test-pin for all gated blueprints.")
    return 0


def main(argv: list[str]) -> int:
    # --check is accepted for parity with the other gate tools; behavior is
    # identical with or without it (this tool only ever checks).
    return run(_root())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
