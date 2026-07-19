"""Drift + correctness test for DEPENDENCY_GRAPH.{json,md} (A3).

Two jobs:

  1. DRIFT — regenerate both artifacts in-process and diff against the
     on-disk copies. Any discrepancy fails the suite; since
     `tools/build_release.py` runs this suite (and a dedicated --check
     gate) before emitting the zip, a stale graph blocks the release.
     Recovery: `python tools/dependency_graph.py` at the repo root.

  2. CORRECTNESS — the graph is only worth gating on if it does NOT
     reproduce the three undercounts that made the prior reference graph
     (dependency_inventory.py) misleading. These assertions are the teeth:

       P1  every blueprint-defining package module is detected (3 use the
           `Blueprint(...) if Flask else None` ternary that defeats naive
           ast.Call detection); count is derived from source, not pinned, and
           the ternary-decorated API blueprints have routes > 0.
       P2  internal edge count reflects `from . import X` edges (naive
           drops them: 267 vs the real ~689), and db.py — the true top
           hotspot — has its real in-degree (~62, not 12).
       P3  config writers detected via verb_noun method calls + alias
           (naive bare-verb regex misses `vpn_config.update_tunnel_config`).

  3. RECONCILIATION — the blueprint graph's vpn route count must be in the
     same ballpark as the catalog's /api/vpn lines. A large divergence
     means a programmatic registration (add_url_rule) the AST missed.

Uses `assert ..., msg` (the custom run_tests.py runner's pytest stub does
not implement pytest.fail) — same convention as test_function_index_in_sync.
"""
import re
import sys
from pathlib import Path

import pytest  # noqa: F401  (harmless under real pytest + the custom runner)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.dependency_graph import (  # noqa: E402
    build, render_json, render_md, _GRAPH_VERSION,
    _json_path, _md_path,
)

_G = build(_REPO_ROOT)


def test_json_in_sync():
    path = _json_path(_REPO_ROOT)
    assert path.exists(), (
        f"{path.name} is missing. Run `python tools/dependency_graph.py`.")
    on_disk = path.read_text(encoding="utf-8")
    regen = render_json(_G)
    assert on_disk == regen, (
        f"{path.name} drift — run `python tools/dependency_graph.py` and "
        f"commit the diff.")


def test_md_in_sync():
    path = _md_path(_REPO_ROOT)
    assert path.exists(), (
        f"{path.name} is missing. Run `python tools/dependency_graph.py`.")
    on_disk = path.read_text(encoding="utf-8")
    regen = render_md(_G)
    assert on_disk == regen, (
        f"{path.name} drift — run `python tools/dependency_graph.py` and "
        f"commit the diff.")


def test_graph_version_pinned():
    assert _G["graph_version"] == _GRAPH_VERSION
    assert _GRAPH_VERSION == "1", (
        "graph schema version changed — bump intentionally and update this pin.")


# ── correctness teeth (the reason the tool replaces the naive graph) ──

def _expected_blueprint_count():
    """Ground truth, derived from the source tree (no magic literal): the count
    of `bulk_downloader/*.py` modules that instantiate a `Blueprint(...)`,
    including the `Blueprint(...) if Flask else None` ternary form. Adding or
    removing a blueprint module moves this in lockstep with the graph, so the
    assertion can never go stale on its own — that stale-literal failure is what
    reached the post-deploy suite at v3.66.177."""
    pkg = _REPO_ROOT / "bulk_downloader"
    return sum(
        1 for p in pkg.glob("*.py")
        if re.search(r"\bBlueprint\s*\(", p.read_text(encoding="utf-8"))
    )


def test_p1_all_blueprints_detected():
    n = len(_G["blueprint"])
    expected = _expected_blueprint_count()
    assert n == expected, (
        f"graph detected {n} blueprints, but {expected} package modules define "
        f"a Blueprint(...). The 3 ternary-guarded API blueprints "
        f"(vpn/widgets/captcha) are the ones a naive ast.Call finder drops: "
        f"n < expected ⇒ detection regressed; n > expected ⇒ the graph is "
        f"crediting a blueprint not backed by a package module.")


def test_p1_ternary_decorated_blueprints_have_routes():
    for bp in ("vpn_api", "widgets_api"):
        rc = _G["blueprint"].get(bp, {}).get("route_count", 0)
        assert rc > 0, (
            f"{bp} reports {rc} routes — its `@bp.route(...) if bp else "
            f"(lambda f:f)` ternary decorators were not unwrapped.")


def test_p2_from_dot_import_edges_counted():
    ec = _G["package"]["edge_count"]
    assert ec >= 600, (
        f"internal edges = {ec} (expected ~689). `from . import X` edges "
        f"are likely being dropped — the exact undercount this tool fixes.")


def test_p2_db_is_top_hotspot():
    db_in = len(_G["package"]["in"].get("bulk_downloader/db.py", []))
    assert db_in >= 50, (
        f"db.py in-degree = {db_in} (expected ~62). The naive graph reported "
        f"12 by missing `from . import db` — this is the load-bearing "
        f"correction for refactor-blast-radius reasoning.")


def test_p3_config_writers_detected():
    vw = len(_G["config"]["vpn_config"]["writers"])
    assert vw > 0, (
        "vpn_config has 0 writer modules — the verb_noun + alias detection "
        "missed `vpn_config.update_tunnel_config(...)` etc.")


def test_reconcile_vpn_routes_against_catalog():
    cat = _REPO_ROOT / "ENDPOINT_CATALOG.md"
    if not cat.exists():
        return  # catalog absent in this tree slice — skip reconciliation
    ct = cat.read_text(encoding="utf-8", errors="replace")
    vpn_cat = len(re.findall(r"^(GET|POST|PUT|DELETE|PATCH)\s+/api/vpn/", ct, re.M))
    vpn_g = _G["blueprint"].get("vpn_api", {}).get("route_count", 0)
    assert vpn_g > 0 and vpn_g >= vpn_cat * 0.5, (
        f"vpn blueprint routes ({vpn_g}) diverge sharply from catalog "
        f"/api/vpn lines ({vpn_cat}); a programmatic add_url_rule "
        f"registration may be unaccounted for.")
