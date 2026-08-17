"""Cut 9: OpenAPI has one live producer and retired surfaces stay gone."""

import importlib.util
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BD_GATE_SCOPE = "repo-wide"

RETIRED_ROOTS = {
    "tools/deploy_overlay.sh",
    "tools/templates_snapshot_baseline.json.tmp",
    "tools/generate_handoff_manifest.py",
    "tools/bd_chatbot_bridge.py",
    "scripts/bulkdl-review-refresh.service",
    "scripts/bulkdl-review-refresh.timer",
    "spa",
}
RETIRED_TOKENS = {
    "deploy_overlay.sh",
    "templates_snapshot_baseline.json.tmp",
    "generate_handoff_manifest.py",
    "bd_chatbot_bridge.py",
    "bulkdl-review-refresh",
    "ResolutionPicker",
}


def _tracked() -> set[str]:
    run = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
        timeout=15)
    paths = {p.decode() for p in run.stdout.split(b"\0") if p}
    assert len(paths) > 1000
    return paths


def _app():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader.app import app
    return app


def _tool_module():
    path = ROOT / "tools/build_openapi.py"
    spec = importlib.util.spec_from_file_location("cut9_build_openapi", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_route_cli_and_package_share_one_exact_versioned_spec(tmp_path):
    from bulk_downloader import __version__
    from bulk_downloader import openapi_spec

    live_response = _app().test_client().get("/api/openapi.json")
    assert live_response.status_code == 200
    live = live_response.get_json()
    assert live["info"]["version"] == __version__
    assert len(live["paths"]) > 900

    run = subprocess.run(
        [sys.executable, "tools/build_openapi.py", "--stdout"],
        cwd=ROOT, env={**os.environ, "BD_DISABLE_KEEPALIVE": "1"},
        text=True, capture_output=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == live

    out = tmp_path / "openapi.json"
    write = subprocess.run(
        [sys.executable, "tools/build_openapi.py", "--out", str(out)],
        cwd=ROOT, env={**os.environ, "BD_DISABLE_KEEPALIVE": "1"},
        text=True, capture_output=True, timeout=60)
    assert write.returncode == 0, write.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == live

    tool = _tool_module()
    assert tool._dependencies()[1] is openapi_spec.generate


def test_cli_import_failure_is_an_actionable_refusal(tmp_path):
    isolated = tmp_path / "build_openapi.py"
    isolated.write_bytes((ROOT / "tools/build_openapi.py").read_bytes())

    run = subprocess.run(
        [sys.executable, str(isolated), "--stdout"], cwd=tmp_path,
        text=True, capture_output=True, timeout=15)

    assert run.returncode == 2
    assert "REFUSED: could not export OpenAPI" in run.stderr
    assert "Traceback" not in run.stderr


def test_canonical_generation_is_repeatable_and_does_not_mutate_metadata():
    from bulk_downloader import __version__, openapi_spec

    before = copy.deepcopy(openapi_spec.ROUTE_META)
    first = openapi_spec.generate(_app())
    second = openapi_spec.generate(_app())

    assert first == second
    assert first["info"]["version"] == __version__
    assert openapi_spec.ROUTE_META == before


def test_checked_in_openapi_is_absent_and_cockpit_links_to_live_route():
    tracked = _tracked()
    assert "openapi.json" not in tracked
    assert not os.path.lexists(ROOT / "openapi.json")

    from bulk_downloader.app_cockpit_home import NAV
    items = [item for group in NAV for item in group["items"]
             if item["label"] == "OpenAPI export"]
    assert items == [{
        "label": "OpenAPI export",
        "path": "/api/openapi.json",
        "kind": "page",
        "note": "OpenAPI 3.1 generated from the live route map",
    }]


def test_retired_residue_is_untracked_and_physically_absent():
    tracked = _tracked()
    bad = []
    for root in sorted(RETIRED_ROOTS):
        if (root in tracked or any(path.startswith(root + "/") for path in tracked)
                or os.path.lexists(ROOT / root)):
            bad.append(root)
    assert not bad, f"retired Cut 9 residue returned: {bad}"


def test_no_current_tracked_reader_names_a_retired_surface():
    historical = {"CHANGELOG.md", "docs/repo/DOC_HYGIENE_AUDIT_v3_66_811.md"}
    this_test = Path(__file__).relative_to(ROOT).as_posix()
    offenders = []
    for path in sorted(_tracked() - historical - {this_test}):
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = sorted(token for token in RETIRED_TOKENS if token in text)
        if hits:
            offenders.append((path, hits))
    assert not offenders, f"current reader names retired Cut 9 surface: {offenders}"


@pytest.mark.parametrize("path", ["SANDBOX.md", "project-knowledge/SANDBOX.md"])
def test_current_sandbox_guidance_does_not_restore_retired_spa(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "BulkDownloader/spa/node_modules" not in text
    assert "| `spa` | standalone" not in text


def test_release_walk_contains_dynamic_producer_but_no_static_or_retired_copy():
    from tools import build_release

    excluded, _ = build_release._load_exclusions(ROOT)
    members = {path.relative_to(ROOT).as_posix()
               for path in build_release._walk_tree(ROOT, excluded)}
    assert "bulk_downloader/openapi_spec.py" in members
    assert "tools/build_openapi.py" in members
    assert "openapi.json" not in members
    for root in RETIRED_ROOTS:
        assert root not in members
        assert not any(path.startswith(root + "/") for path in members)
