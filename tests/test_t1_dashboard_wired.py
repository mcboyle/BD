"""T1 dashboard wiring is exercised by the real React route and API hooks."""

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

from tests.frontend_vitest import build_manifest, run_vitest


BD_GATE_SCOPE = "repo-wide"

_ROOT = Path(__file__).resolve().parents[1]


def _load_capacity_module():
    """Load the response producer without adding a package import-graph edge."""
    path = _ROOT / "bulk_downloader" / "capacity.py"
    spec = importlib.util.spec_from_file_location("_row410_capacity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@patch.dict(os.environ, {}, clear=False)
def test_t1_dashboard_runtime_contract():
    # 7, not 5: the endpoint set is DERIVED from the hook rather than
    # hand-maintained, one test guards that derivation against being vacuous,
    # and row410 adds positive/negative capacity transport controls.
    spec = "src/routes/Dashboard.wired.test.tsx"

    # Build the JSON crossing the Python/React boundary through the production
    # aggregator. Exact nonzero calls prove every report branch contributed;
    # the Vitest process then renders this receipt instead of a hand-maintained
    # frontend-only shape.
    capacity = _load_capacity_module()
    calls = []
    disk = {
        "free_gb": 8,
        "rate_gb_per_day": 2,
        "runway_days": 4,
        "runway_label": "days",
        "confidence": "medium",
        "lookback_hours": 168,
    }
    queue = {
        "pending": 3,
        "running": 1,
        "failed": 0,
        "completions_per_hour": 2,
        "eta_hours": 1.5,
        "confidence": "medium",
    }
    bottleneck = {
        "bottleneck": "running",
        "detail": "1/2 workers active",
        "severity": 0,
    }

    def disk_forecast(download_dir):
        calls.append(("disk", download_dir))
        return disk

    def queue_forecast(runners):
        calls.append(("queue", tuple(runners)))
        return queue

    def bottleneck_hint(runners, download_dir):
        calls.append(("bottleneck", tuple(runners), download_dir))
        return bottleneck

    with (
        patch.object(capacity, "disk_forecast", disk_forecast),
        patch.object(capacity, "queue_forecast", queue_forecast),
        patch.object(capacity, "bottleneck_hint", bottleneck_hint),
    ):
        backend_receipt = capacity.capacity_report(
            {"row410-site": object()}, "/row410-downloads"
        )
    assert calls == [
        ("disk", "/row410-downloads"),
        ("queue", ("row410-site",)),
        ("bottleneck", ("row410-site",), "/row410-downloads"),
    ]
    assert backend_receipt["disk"] == disk
    assert backend_receipt["queue"] == queue
    assert backend_receipt["bottleneck"] == bottleneck
    assert isinstance(backend_receipt["generated_at"], float)
    os.environ["BD_CAPACITY_RECEIPT"] = json.dumps(backend_receipt)
    receipt = run_vitest(spec, expected_tests=7)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": 7,
        "tests_collected": 7,
    }
    assert receipt == expected, (
        "Vitest delegation evidence missing or mismatched for Dashboard: "
        f"expected={expected!r}, observed={receipt!r}"
    )
    manifest = build_manifest()
    dashboard = manifest.get("src/routes/Dashboard.tsx")
    assert isinstance(dashboard, dict), "Dashboard missing from Vite manifest"
    assert dashboard.get("isDynamicEntry") is True, (
        "Dashboard must remain a lazy, separately built route"
    )
