"""ROUTE_INDEX in-sync + invariant gate (KB Tier-A / A2).

ROUTE_INDEX.json is a generated join: app.url_map (the route source of truth, via
build_endpoint_catalog._import_app) × reports/gui_parity_inventory.json (the spa_wired
truth). It exists to kill audit-by-grep ("does actions_center own /api/library?") and to
back the `what-pins` / `can-i-retire` queries. Like ENDPOINT_CATALOG, a silent regen-miss
must fail the build, not rot for three releases.

Grain: one entry per (method, path) — the codebase's canonical route identity (cf.
test_parity_method_aware) — because spa_wired differs by method on the same path
(GET /api/shares is wired, POST /api/shares is not).

Gating choice: the in-sync diff compares the STABLE projection (everything EXCEPT `line`).
`line` is regenerated fresh and shipped current-as-of-cut, but is NOT equality-gated — so
this gate fires on a real route change (added / removed / renamed / re-blueprinted / wiring
flip), never on an unrelated line shift elsewhere in app.py. (Anchor on symbol, not line.)

Custom-runner friendly: BD_DISABLE_KEEPALIVE before import; zero-arg tests; repo root from
__file__; Flask is in prestaged_site_packages so the app import works in-runner.
"""
import os
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ROUTE_INDEX = ROOT / "ROUTE_INDEX.json"

# Fields gated for equality (the stable projection — `line` deliberately excluded).
_STABLE = ("method", "path", "blueprint", "endpoint", "file", "csrf",
           "spa_wired", "operator_facing", "kind")


def _stable_projection(routes):
    return [{k: r.get(k) for k in _STABLE} for r in routes]


def _load_committed():
    assert ROUTE_INDEX.exists(), \
        "ROUTE_INDEX.json missing — run `python tools/build_route_index.py`"
    return json.loads(ROUTE_INDEX.read_text(encoding="utf-8"))


@pytest.fixture
def generated_parity_path(tmp_path):
    """Materialize the ignored parity input without mutating the source tree."""
    saved_path = list(sys.path)
    outdir = tmp_path / "reports"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gui_parity_inventory.py"),
            "--root",
            str(ROOT),
            "--outdir",
            str(outdir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"parity generator exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert sys.path == saved_path, "parity generation leaked import paths"
    path = outdir / "gui_parity_inventory.json"
    assert path.is_file()
    return path


def _regen(parity_path):
    import importlib
    import tools.build_route_index as bri
    importlib.reload(bri)
    return bri.build_index(parity_path=parity_path)


def test_route_index_exists_and_parses():
    d = _load_committed()
    assert isinstance(d, dict) and "routes" in d and isinstance(d["routes"], list)
    assert d.get("schema_version"), "ROUTE_INDEX.json must carry a schema_version"


def test_route_index_in_sync(generated_parity_path):
    """Regenerate in-process; the STABLE projection must match the committed file."""
    committed = _load_committed()
    regen = _regen(generated_parity_path)
    assert _stable_projection(regen["routes"]) == _stable_projection(committed["routes"]), (
        "ROUTE_INDEX.json is stale — regenerate with `python tools/build_route_index.py` "
        "(a route was added/removed/renamed/re-blueprinted or its spa_wiring flipped)"
    )


def test_every_entry_has_required_fields():
    d = _load_committed()
    for r in d["routes"]:
        for k in ("method", "path", "blueprint", "endpoint", "csrf", "spa_wired", "kind"):
            assert k in r, f"route entry missing '{k}': {r.get('path')}"
        assert r["method"] and r["path"].startswith("/"), r


def test_identity_is_unique_method_path():
    d = _load_committed()
    seen = set()
    for r in d["routes"]:
        key = (r["method"], r["path"])
        assert key not in seen, f"duplicate (method,path): {key}"
        seen.add(key)


def test_spa_wired_join_is_faithful(generated_parity_path):
    """The join must neither drop nor invent rows: ROUTE_INDEX's spa_wired
    (method,path) set must equal the gui_parity items' spa_wired set expanded
    per-method. (This is grain-correct — parity's `spa_wired_total` *field* is an
    item-grain count where a `GET|POST /x` item counts once, so it legitimately
    differs from the (method,path) grain by the number of multi-method wired routes;
    set equality is the real faithfulness invariant.)"""
    d = _load_committed()
    parity = json.loads(generated_parity_path.read_text(encoding="utf-8"))
    parity_wired = set()
    for it in parity.get("items", []):
        if it.get("kind") in ("cockpit_api", "gui_api", "cockpit_page", "gui_page") \
                and it.get("spa_wired"):
            ce = it.get("command_or_endpoint", "").split(" ", 1)
            if len(ce) == 2:
                for m in ce[0].split("|"):
                    parity_wired.add((m, ce[1]))
    index_wired = {(r["method"], r["path"]) for r in d["routes"] if r.get("spa_wired")}
    assert index_wired == parity_wired, (
        f"join not faithful: only-in-index={sorted(index_wired - parity_wired)}, "
        f"only-in-parity={sorted(parity_wired - index_wired)}"
    )


def test_no_api_route_classified_as_page():
    """Invariant the Phase-4 plan violated: an /api/* path is never a page."""
    d = _load_committed()
    for r in d["routes"]:
        if "/api/" in r["path"] or r["path"].endswith("/api"):
            assert r["kind"] == "api", f"{r['path']} is /api/* but kind={r['kind']}"


def test_external_file_detection_is_venv_layout_independent():
    """Regression for the v3.66.355 on-stash in-sync break. Stash's venv lives INSIDE
    the repo (~/BulkDownloader/venv/.../site-packages), so Flask's /static view resolves
    UNDER the repo root; a bare relative_to() would emit a venv path the sandbox (venv
    outside the repo) never produces, failing the gate on stash only. A site-packages
    file must classify as external regardless of venv nesting — proven here without
    needing stash, closing the gap the sandbox band could not see."""
    import importlib
    import tools.build_route_index as bri
    importlib.reload(bri)
    # stash layout: venv nested in the repo
    assert bri._is_external(
        Path("/home/mboyle/BulkDownloader/venv/lib/python3.12/site-packages/flask/app.py"),
        Path("/home/mboyle/BulkDownloader")) is True
    # sandbox layout: deps outside the repo
    assert bri._is_external(
        Path("/tmp/prestaged_site_packages/flask/app.py"),
        Path("/home/claude/work")) is True
    # our own source is NOT external
    assert bri._is_external(
        Path("/home/claude/work/bulk_downloader/app.py"),
        Path("/home/claude/work")) is False


def test_source_locations_use_posix_separators_on_every_host():
    """ROUTE_INDEX file fields are stable repository IDs, not native paths."""
    import importlib
    import tools.build_route_index as bri
    importlib.reload(bri)
    file_rel, line = bri._src_loc(
        test_source_locations_use_posix_separators_on_every_host, ROOT)
    assert file_rel == "tests/test_route_index_in_sync.py"
    assert isinstance(line, int) and line > 0
