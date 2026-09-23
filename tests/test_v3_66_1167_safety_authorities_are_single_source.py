"""Cut 7: one machine authority per safety concept, with independent gates."""

from __future__ import annotations

import copy
import functools
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest

from ci_workflow_model import enabled_guard_lanes, guard_lanes


BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
GUARDS = {
    "bulk_downloader/extraction_core.py",
    "bulk_downloader/session_capture.py",
    "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py",
    "bulk_downloader/capture_bodies.py",
    "tools/capture_session.py",
    "tools/build_release.py",
}


def _strict_json(text: str):
    def reject_duplicates(pairs):
        out = {}
        for key, value in pairs:
            assert key not in out, f"duplicate JSON key: {key}"
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=reject_duplicates)


def test_duplicate_machine_authority_keys_are_rejected():
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        _strict_json('{"guards": {"same": "first", "same": "second"}}')
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        _strict_json('{"invariants": {"I0001": {}, "I0001": {}}}')
    with pytest.raises(AssertionError, match="duplicate JSON key"):
        _strict_json('{"footguns": [], "footguns": []}')


def _tracked() -> set[str]:
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    paths = {item.decode() for item in raw.split(b"\0") if item}
    assert len(paths) > 1000
    return paths


def test_guards_json_is_the_only_embedded_guard_hash_authority():
    registry = _strict_json((ROOT / "guards.json").read_text())
    assert registry["schema"] == "bd-guards/1"
    assert set(registry["guards"]) == GUARDS
    pins = set(registry["guards"].values())
    assert all(re.fullmatch(r"[0-9a-f]{64}", pin) for pin in pins)

    for rel in ("CLAUDE.md", ".github/workflows/ci.yml", "scripts/cloud-setup.sh"):
        text = (ROOT / rel).read_text()
        leaked = sorted(pin[:16] for pin in pins if pin[:16] in text)
        assert not leaked, f"{rel} duplicates guard pins: {leaked}"
    # Row 186. This used to be `assert "toolchain/bin/bd-guardcheck --tree" in
    # ci` -- a substring standing in for a scheduling property it cannot see.
    # `if: false`, `continue-on-error`, a narrowed pull_request trigger, the run
    # line moved into a YAML comment and `echo '...'` all leave that substring
    # exactly where it was. The parse below asks the question the substring was
    # a proxy for: will GitHub run this step, and will a nonzero exit from
    # bd-guardcheck fail the job? Whether the running command actually detects a
    # tampered guard is the other half, and it is executed rather than parsed in
    # tests/test_v3_66_1234_ci_really_executes_the_guard_lane.py.
    #
    # Both messages below are REQUIRED, not stylistic: 1233 loads this module by
    # path, without pytest's assertion rewriting, so a bare assert would raise
    # AssertionError('') and its `match="bd-guardcheck"` clause could then be
    # satisfied by nothing -- or, worse, by an unrelated assertion above.
    lanes = guard_lanes(ROOT)
    assert lanes, (
        "no step in .github/workflows/ci.yml runs bd-guardcheck, so an "
        "unannounced edit to a release guard reaches main unchallenged"
    )
    enabled = enabled_guard_lanes(ROOT)
    assert enabled, (
        "CI declares a bd-guardcheck step that will not run or will not fail "
        "the job: " + "; ".join(lane.describe() for lane in lanes)
    )


def test_footgun_runtime_and_package_share_one_43_row_registry(tmp_path):
    # 43 = the 42 rows adjudicated at v3.66.1167 + FG-IMPORT-REWRITTEN-TO-DODGE-THE-GATE
    # (the diff-scoped import-dodge detector). Bump this pin with every registry row.
    tracked = _tracked()
    canonical = "FOOTGUNS.json"
    assert canonical in tracked
    assert "project-knowledge/FOOTGUNS.json" not in tracked
    payload = _strict_json((ROOT / canonical).read_text())
    rows = payload["footguns"]
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)) == 43
    assert {"FG-OVERLAY-ORPHAN-ON-DELETE", "FG-SPA-WIRING-ROUTE-INDEX"} <= set(ids)
    allowed_status = {"active", "retired"}
    allowed_severity = {"blocking", "advisory"}
    assert all(row["status"] in allowed_status for row in rows)
    assert all(row["severity"] in allowed_severity for row in rows)
    retired_defaults = re.compile(r"/home/claude|/mnt/user-data/uploads", re.I)
    assert not [
        row["id"]
        for row in rows
        if row["status"] == "active"
        and retired_defaults.search(json.dumps({key: row.get(key) for key in ("rule", "fix", "detector")}))
    ]
    tool = (ROOT / "toolchain/bin/bd-footguns").read_text()
    assert "SEED = [" not in tool

    proc = subprocess.run(
        [str(ROOT / "toolchain/bin/bd-footguns"), "--tree", str(ROOT), "--list", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert {row["id"] for row in json.loads(proc.stdout)} == set(ids)

    installed = tmp_path / "installed" / "bin"
    installed.mkdir(parents=True)
    (installed / "bd-footguns").write_bytes((ROOT / "toolchain/bin/bd-footguns").read_bytes())
    (installed / "bd-footguns").chmod(0o755)
    (installed / "bdtools_sec.py").write_bytes((ROOT / "toolchain/bin/bdtools_sec.py").read_bytes())
    duplicate = b'{"footguns": [], "footguns": []}\n'
    (installed / "FOOTGUNS.json").write_bytes(duplicate)
    rejected_runtime = subprocess.run(
        [str(installed / "bd-footguns"), "--tree", str(installed.parent), "--list", "--json"],
        capture_output=True, text=True,
    )
    assert rejected_runtime.returncode != 0
    (installed / "FOOTGUNS.json").write_text('{"footguns": []}\n')
    empty_runtime = subprocess.run(
        [str(installed / "bd-footguns"), "--tree", str(installed.parent), "--list", "--json"],
        capture_output=True, text=True,
    )
    assert empty_runtime.returncode != 0
    (installed / "FOOTGUNS.json").write_bytes((ROOT / canonical).read_bytes())
    restored = subprocess.run(
        [str(installed / "bd-footguns"), "--tree", str(installed.parent), "--list", "--json"],
        capture_output=True, text=True,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert {row["id"] for row in json.loads(restored.stdout)} == set(ids)


@functools.cache
def _footgun_tool():
    """bd-footguns loaded in process for its registry loader (it is a script, not a module)."""
    loader = importlib.machinery.SourceFileLoader("bd_footguns_1167", str(ROOT / "toolchain/bin/bd-footguns"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    saved = list(sys.path)   # the tool prepends its own directory to sys.path on import
    try:
        loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _footgun_rows():
    return _strict_json((ROOT / "FOOTGUNS.json").read_text())["footguns"]


def _footgun_registry_loads(tmp_path, rows) -> bool:
    (tmp_path / "FOOTGUNS.json").write_text(json.dumps({"footguns": rows}))
    try:
        _footgun_tool()._load_registry(str(tmp_path))
    except ValueError:
        return False
    return True


def test_a_footgun_row_missing_any_key_every_row_carries_is_refused(tmp_path):
    """H105: this gate checked the value domains (id, status, severity) but not the ROW SCHEMA, so
    FOOTGUNS.json with one row's `detector` dropped loaded and the gate passed (bd-lens-L2,
    2026-09-07). The required set is derived, never hand-listed: the keys every real row carries.
    Each is dropped from one active and one retired row, and each row's detector is replaced by a
    non-object; the runtime loader must refuse every fixture. The real registry loading is the
    negative control, so the refusal is not simply refusing everything."""
    rows = _footgun_rows()
    shared = sorted(set.intersection(*(set(row) for row in rows)))
    assert {"id", "status", "detector"} <= set(shared), shared
    assert _footgun_registry_loads(tmp_path, rows), "negative control: the real registry loads"
    samples = [next(i for i, row in enumerate(rows) if row["status"] == status)
               for status in ("active", "retired")]
    fixtures = []
    for i in samples:
        label = f"{rows[i]['id']} ({rows[i]['status']})"
        for key in shared:
            broken = copy.deepcopy(rows)
            del broken[i][key]
            fixtures.append((f"{label} without {key!r}", broken))
        for bad in (None, "", "tests/test_x.py", [], {}):
            broken = copy.deepcopy(rows)
            broken[i]["detector"] = bad
            fixtures.append((f"{label} with detector {bad!r}", broken))
    loaded = [name for name, broken in fixtures if _footgun_registry_loads(tmp_path, broken)]
    assert loaded == [], f"H105: bd-footguns loaded rows that are not a legal state: {loaded}"


def test_bd_footguns_declares_exactly_the_keys_every_row_carries():
    """H105 / M70: the required set is declared once, in the tool that enforces it, and pinned to the
    artefact, so a key added to every row, or one some row no longer carries, moves this pin instead
    of leaving a check named for the whole row asserting over part of it."""
    shared = set.intersection(*(set(row) for row in _footgun_rows()))
    declared = set(getattr(_footgun_tool(), "REQUIRED_ROW_KEYS", ()))
    assert declared == shared, (
        f"bd-footguns REQUIRED_ROW_KEYS {sorted(declared)} != the keys every FOOTGUNS.json row "
        f"carries {sorted(shared)}")


def test_defect_catalog_is_an_exact_view_of_the_executable_detector_set():
    expected = {f"DP-{number:02d}" for number in range(1, 19)}
    source = (ROOT / "tools/defect_patterns.py").read_text()
    packaged = (ROOT / "toolchain/bin/bd-defect-scan").read_text()
    catalog = (ROOT / "project-knowledge/DEFECT_PATTERN_CATALOG.md").read_text()
    detector_block = lambda text: text.split("DETECTORS = {", 1)[1].split("CORPUS_GATED", 1)[0]
    source_ids = re.findall(r'"(DP-\d\d)"\s*:', detector_block(source))
    packaged_ids = re.findall(r'"(DP-\d\d)"\s*:', detector_block(packaged))
    catalog_ids = re.findall(r"^### (DP-\d\d)\b", catalog, re.M)
    assert len(source_ids) == len(set(source_ids)) == 18 and set(source_ids) == expected
    assert len(packaged_ids) == len(set(packaged_ids)) == 18 and set(packaged_ids) == expected
    assert len(catalog_ids) == len(set(catalog_ids)) == 18 and set(catalog_ids) == expected
    assert "[PLANNED]" not in catalog
    assert "generated view" in catalog.lower()


def test_invariants_json_is_the_only_12_row_invariant_authority():
    tracked = _tracked()
    assert "INVARIANTS.json" in tracked
    assert "project-knowledge/INVARIANTS.json" not in tracked
    payload = _strict_json((ROOT / "INVARIANTS.json").read_text())
    assert payload["schema"] == 1
    assert set(payload["invariants"]) == {f"I{i:04d}" for i in range(1, 12)} | {
        "I-CAP01-rec-url-shape"
    }
    assert {row["status"] for row in payload["invariants"].values()} == {"GUARDED"}
    assert payload["_meta"]["version_context"] == "v3.66.1167"
    source = (ROOT / "tools/invariants.py").read_text()
    assert "INVARIANTS = {" not in source
    assert "/home/claude" not in source
    generated = subprocess.run(
        ["python3", str(ROOT / "tools/invariants.py"), "--check", "--root", str(ROOT)],
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr


def test_architecture_map_is_the_only_live_human_authored_map():
    tracked = _tracked()
    assert "project-knowledge/ARCHITECTURE_MAP.md" in tracked
    retired = ("project-knowledge/DANGER_MAPv2.md", "project-knowledge/GATE_AUTHORITY.md")
    for rel in retired:
        assert rel not in tracked
        assert not os.path.lexists(ROOT / rel)
    current = (
        "CLAUDE.md",
        "project-knowledge/0_INDEX.md",
        "project-knowledge/KB_ACTIVE_INDEX.md",
        "project-knowledge/README_KB.md",
        "project-knowledge/ADVANCED_PROJECT_KNOWLEDGE.md",
        "scripts/deploy.sh",
        "project-knowledge/DECOMP_HAZARD_REGISTER.md",
        "project-knowledge/CODE_INTELLIGENCE_ARCHITECTURE.md",
        "project-knowledge/KB_SYNC_WORKFLOW.md",
        "project-knowledge/SANDBOX.md",
        "project-knowledge/TOUCHED_FILE_TO_TEST.md",
        "project-knowledge/README.md",
    )
    offenders = []
    for rel in current:
        text = (ROOT / rel).read_text(errors="ignore")
        if "DANGER_MAPv2.md" in text or "GATE_AUTHORITY.md" in text:
            offenders.append(rel)
    assert not offenders, f"live readers still route to retired maps: {offenders}"
