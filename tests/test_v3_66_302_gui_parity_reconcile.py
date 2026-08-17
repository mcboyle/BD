"""Current GUI-parity generator and release-artifact reconciliation.

The inventory generator must contain the two live routes. When a release artifact is present,
it must also match a fresh generator run. Clean source trees do not carry the
ignored reports artifact; the release gate requires it before packaging.

The specific reconciliation remains pinned without pinning a brittle total.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SHIPPED_INVENTORY = _REPO / "reports" / "gui_parity_inventory.json"
# The generator runs in a child process because importing it mutates sys.path.
# Keeping that mutation out of pytest prevents later bare imports from resolving
# against an unrelated tools/*.py module.

# Two current routes whose inventory membership is directly enforced.
_NEW_293_ROUTES = {"sites.api_template_capture_cancel", "cockpit.api_capture_goto"}


@pytest.fixture
def generated_inventory_path(tmp_path):
    """Materialize the ignored parity report in an isolated output directory."""
    outdir = tmp_path / "reports"
    result = subprocess.run(
        [
            sys.executable,
            str(_REPO / "tools" / "gui_parity_inventory.py"),
            "--root",
            str(_REPO),
            "--outdir",
            str(outdir),
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"parity generator exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    path = outdir / "gui_parity_inventory.json"
    assert path.is_file()
    return path


def _shipped_names(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    return {it["name"] for it in d["items"]}, d


def test_generated_inventory_includes_new_293_routes(generated_inventory_path):
    names, _ = _shipped_names(generated_inventory_path)
    missing = sorted(_NEW_293_ROUTES - names)
    assert not missing, f"generated inventory missing 293 routes: {missing}"


def test_shipped_inventory_matches_live_regen_itemset(generated_inventory_path):
    """A present release artifact must match a fresh generator item-set."""
    if not _SHIPPED_INVENTORY.is_file():
        pytest.skip(
            "release parity artifact absent; packaging requires it via "
            "tools/check_route_counts.py"
        )
    shipped, _ = _shipped_names(_SHIPPED_INVENTORY)
    regen, _ = _shipped_names(generated_inventory_path)
    only_shipped = sorted(shipped - regen)
    only_regen = sorted(regen - shipped)
    assert not only_shipped and not only_regen, (
        f"inventory drift — only-shipped={only_shipped} only-regen={only_regen}")


def test_generated_blueprint_counts_unchanged(generated_inventory_path):
    """G12 invariant: the 3 write-blueprint counts must be unchanged by the
    reconciliation (the 9 new items are NOT on data_layer/report_center/
    actions_center, so G12 stays green)."""
    names, _ = _shipped_names(generated_inventory_path)
    c = {"data_layer.": 0, "report_center.": 0, "actions_center.": 0}
    for n in names:
        for k in c:
            if n.startswith(k):
                c[k] += 1
    assert c == {"data_layer.": 15, "report_center.": 9, "actions_center.": 0}, c
