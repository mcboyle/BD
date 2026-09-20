"""Row 660: read-only-looking gates must not rewrite generated artifacts."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
FOOTGUNS = ROOT / "toolchain" / "bin" / "bd-footguns"
PRECUT = ROOT / "toolchain" / "bin" / "bd-precut"
BDTOOLS_SEC = ROOT / "toolchain" / "bin" / "bdtools_sec.py"
REGEN_ORDER = ROOT / "toolchain" / "bin" / "bd-regen-order"
BUILD_PIN_INDEX = ROOT / "tools" / "build_pin_index.py"
ENDPOINT_TOOL = ROOT / "tools" / "endpoint_reachability.py"
REACHABILITY_LEDGER = ROOT / "reports" / "endpoint_reachability.json"
ARTIFACTS = (ROOT / "PIN_INDEX.json", ROOT / "FUNCTION_INDEX.md")
DETECTOR = "FG-CENSUS-NEEDS-THE-VENV"
_PIN_TEST = '__version__ = "1.0.0"\n\ndef test_v():\n    assert __version__ == "1.0.0"\n'


def _snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def _load_regen_order():
    loader = importlib.machinery.SourceFileLoader("row660_regen_order", str(REGEN_ORDER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _load_footguns():
    loader = importlib.machinery.SourceFileLoader("row660_footguns", str(FOOTGUNS))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _pin_tree(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    (work / "tools").mkdir(parents=True)
    (work / "tests").mkdir()
    shutil.copy(BUILD_PIN_INDEX, work / "tools" / "build_pin_index.py")
    pin_test = work / "tests" / "test_pin.py"
    pin_test.write_text(_PIN_TEST, encoding="utf-8")
    subprocess.run(
        [sys.executable, str(work / "tools" / "build_pin_index.py")],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    pin_test.write_text(_PIN_TEST.replace("1.0.0", "2.0.0"), encoding="utf-8")
    return work


def _detector_row(footguns, tree: Path) -> dict:
    matches = [
        item
        for item in footguns._load_registry(str(tree))
        if item.get("id") == DETECTOR
    ]
    assert len(matches) == 1
    return matches[0]


def _census_tree(tmp_path: Path, *, measurable: bool = False) -> Path:
    work = tmp_path / ("measurable" if measurable else "unmeasurable")
    (work / "tools").mkdir(parents=True)
    (work / "reports").mkdir()
    shutil.copy(ENDPOINT_TOOL, work / "tools" / ENDPOINT_TOOL.name)
    shutil.copy(REACHABILITY_LEDGER, work / "reports" / REACHABILITY_LEDGER.name)
    row = _detector_row(_load_footguns(), ROOT)
    (work / "FOOTGUNS.json").write_text(
        json.dumps({"footguns": [row]}), encoding="utf-8"
    )
    (work / "bulk_downloader").mkdir()
    (work / "bulk_downloader" / "__init__.py").write_text(
        '__version__ = "1.0.0"\n', encoding="utf-8"
    )
    (work / "tests").mkdir()
    (work / "tests" / "test_settings_center_slice4.py").write_text(
        'def test_v():\n    assert __version__ == "1.0.0"\n', encoding="utf-8"
    )
    (work / "CHANGELOG.md").write_text(
        "## v1.0.0 - fixture release\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    if measurable:
        ledger = json.loads(REACHABILITY_LEDGER.read_text(encoding="utf-8"))
        total = ledger.get("endpoint_count", len(ledger["classified"]))
        python = work / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(
            "#!/bin/sh\nprintf '%s\\n' "
            + repr(json.dumps({"dark": ledger["dark_count"], "endpoints": total}))
            + "\n",
            encoding="utf-8",
        )
        python.chmod(0o755)
    return work


def _precut_copy(work: Path) -> Path:
    bindir = work / "toolchain" / "bin"
    bindir.mkdir(parents=True)
    for source in (PRECUT, FOOTGUNS, REGEN_ORDER, BDTOOLS_SEC):
        shutil.copy(source, bindir / source.name)
    return bindir / PRECUT.name


def _copy_tracked_tree(work: Path) -> Path:
    """Copy the INDEX's file set (git ls-files), never an os.walk of the live
    working directory: under -n, tests/__pycache__/*.pyc.<pid> temp files appear
    and vanish between scandir and copy2, and shutil.copytree raises shutil.Error."""
    listed = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    names = [name for name in listed.decode("utf-8").split("\0") if name]
    assert len(names) > 0, "precondition: git ls-files listed nothing"
    copied = 0
    for rel in names:
        src = ROOT / rel
        if not src.is_file():
            continue
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
        copied += 1
    assert copied > 0
    return work


def test_footgun_gate_does_not_rewrite_the_tree_it_checks() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", *(p.name for p in ARTIFACTS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    before = {path.name: _snapshot(path) for path in ARTIFACTS}

    assert tracked.returncode == 0, tracked.stderr
    assert len(before) == 2
    assert all(payload for payload, _mtime_ns in before.values())

    footguns = _load_footguns()
    verdict, detail = footguns._check_one(_detector_row(footguns, ROOT), str(ROOT))

    assert verdict == "pass", detail
    changed = [
        path.name for path in ARTIFACTS if _snapshot(path) != before[path.name]
    ]
    assert changed == [], (
        f"gate rewrote {len(changed)} tracked generated artifact(s): {changed}"
    )


def test_unmeasurable_reachability_blocks_the_registry_and_precut(tmp_path: Path) -> None:
    work = _census_tree(tmp_path)
    assert (work / "FOOTGUNS.json").is_file()
    assert (work / "tools" / ENDPOINT_TOOL.name).is_file()
    assert (work / "reports" / REACHABILITY_LEDGER.name).is_file()
    assert not (work / "venv" / "bin" / "python").exists()

    footguns = _load_footguns()
    verdict, detail = footguns._check_one(_detector_row(footguns, work), str(work))
    precut = _precut_copy(work)
    assert precut.is_file()
    result = subprocess.run(
        [
            sys.executable,
            str(precut),
            "--gate",
            "--root",
            str(work),
            "--no-insync",
            "--no-envscan",
            "--no-coretest",
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    result_lines = [line for line in output.splitlines() if line.startswith("RESULT:")]
    fired = output.count(f"[VIOLATION] {DETECTOR}")

    assert (verdict, fired, len(result_lines), result.returncode) == (
        "violation",
        1,
        1,
        3,
    ), f"registry={verdict}: {detail}; precut rc={result.returncode}\n{output}"
    assert "delegate could not evaluate" in detail
    assert "footgun violation(s) -- see bd-footguns output above" in result_lines[0]


def test_measurable_reachability_does_not_fire_the_unknown_guard(tmp_path: Path) -> None:
    work = _census_tree(tmp_path, measurable=True)
    python = work / "venv" / "bin" / "python"
    assert python.is_file() and os.access(python, os.X_OK)

    footguns = _load_footguns()
    verdict, detail = footguns._check_one(_detector_row(footguns, work), str(work))

    assert verdict == "pass", detail


def test_reachability_check_is_read_only_and_measures_a_nonzero_denominator() -> None:
    before = {path.name: _snapshot(path) for path in ARTIFACTS}
    result = subprocess.run(
        [
            sys.executable,
            str(REGEN_ORDER),
            "--check-reachability",
            "--work",
            str(ROOT),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    measured = re.findall(r"endpoints=(\d+), dark=(\d+)", output)
    ledger = json.loads(REACHABILITY_LEDGER.read_text(encoding="utf-8"))

    assert result.returncode == 0, output
    assert len(measured) == 1, output
    assert type(ledger["endpoint_count"]) is int and ledger["endpoint_count"] > 0
    assert int(measured[0][0]) == ledger["endpoint_count"]
    assert {path.name: _snapshot(path) for path in ARTIFACTS} == before


def test_endpoint_disappearance_is_drift_even_when_dark_total_holds(monkeypatch) -> None:
    ledger = json.loads(REACHABILITY_LEDGER.read_text(encoding="utf-8"))
    total = ledger["endpoint_count"]
    dark = ledger["dark_count"]
    assert type(total) is int and type(dark) is int and total > dark >= 0
    regen = _load_regen_order()
    monkeypatch.setattr(
        regen,
        "_run_venv",
        lambda _work, _argv: (
            0,
            json.dumps({"endpoints": total - 1, "dark": dark}) + "\n",
        ),
    )

    good, detail = regen.check_reach(str(ROOT))

    assert good is False
    assert detail.startswith(
        f"endpoints={total - 1} but the ledger pins {total} -- an endpoint appeared or vanished"
    )


def test_tree_copy_takes_the_index_not_the_live_working_directory(tmp_path: Path) -> None:
    ghost_dir = ROOT / "tests" / "__pycache__"
    ghost_dir.mkdir(exist_ok=True)
    ghost = ghost_dir / "row660_ghost.cpython-312.pyc.999999"
    ghost.write_bytes(b"\x00")
    try:
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", str(ghost)],
            capture_output=True,
        )
        assert tracked.returncode != 0, "precondition: the ghost must be untracked"
        work = _copy_tracked_tree(tmp_path / "work")
    finally:
        ghost.unlink()
    assert (work / "reports" / "endpoint_reachability.json").is_file()
    assert (work / "tools" / ENDPOINT_TOOL.name).is_file()
    assert not (work / "tests" / "__pycache__").exists()
    assert not (work / ".git").exists() and not (work / "venv").exists()


def test_footgun_detector_still_refuses_real_reachability_drift(tmp_path: Path) -> None:
    work = _copy_tracked_tree(tmp_path / "work")
    ledger = work / "reports" / "endpoint_reachability.json"
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    original_dark = payload["dark_count"]
    assert type(original_dark) is int and original_dark > 0
    payload["dark_count"] = original_dark + 1
    ledger.write_text(json.dumps(payload), encoding="utf-8")

    footguns = _load_footguns()
    matches = [
        item
        for item in footguns._load_registry(str(work))
        if item.get("id") == DETECTOR
    ]
    assert len(matches) == 1

    verdict, detail = footguns._check_one(matches[0], str(work))

    assert verdict == "violation", detail


def test_zero_reachability_denominator_is_unknown(monkeypatch) -> None:
    regen = _load_regen_order()
    monkeypatch.setattr(
        regen,
        "_run_venv",
        lambda _work, _argv: (0, '{"endpoints": 0, "dark": 0}\n'),
    )

    good, detail = regen.check_reach(str(ROOT))

    assert good is None
    assert detail == "UNKNOWN reachability: endpoint denominator is zero"


def test_update_writes_endpoint_count_measured_from_the_same_tree(tmp_path: Path) -> None:
    work = _copy_tracked_tree(tmp_path / "work")
    (work / "venv").symlink_to(ROOT / "venv", target_is_directory=True)
    copied_tool = work / "tools" / ENDPOINT_TOOL.name
    copied_ledger = work / "reports" / REACHABILITY_LEDGER.name
    copied_regen = work / "toolchain" / "bin" / REGEN_ORDER.name
    assert copied_tool.is_file() and copied_ledger.is_file() and copied_regen.is_file()
    assert (work / "venv" / "bin" / "python").is_file()

    update = subprocess.run(
        [sys.executable, str(copied_tool), "--update", "--root", str(work)],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=120,
    )
    written = json.loads(copied_ledger.read_text(encoding="utf-8"))
    check = subprocess.run(
        [
            sys.executable,
            str(copied_regen),
            "--check-reachability",
            "--work",
            str(work),
        ],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = check.stdout + check.stderr
    measurements = re.findall(r"endpoints=(\d+), dark=(\d+)", output)

    assert update.returncode == 0, update.stdout + update.stderr
    assert update.stdout.count("pinned endpoint_count=") == 1, update.stdout
    assert check.returncode == 0, output
    assert len(measurements) == 1, output
    assert type(written["endpoint_count"]) is int
    assert written["endpoint_count"] > 0
    assert int(measurements[0][0]) == written["endpoint_count"]


def test_invalid_reachability_measurements_are_unknown(monkeypatch) -> None:
    regen = _load_regen_order()
    measurements = (
        {"endpoints": "not-an-integer", "dark": 0},
        {"endpoints": 3, "dark": 4},
    )
    observed = []

    for measurement in measurements:
        monkeypatch.setattr(
            regen,
            "_run_venv",
            lambda _work, _argv, value=measurement: (0, json.dumps(value) + "\n"),
        )
        observed.append(regen.check_reach(str(ROOT)))

    assert len(observed) == 2
    assert observed == [
        (None, "UNKNOWN reachability counts: endpoint/dark counts are not valid integers"),
        (None, "UNKNOWN reachability counts: endpoint/dark counts are not valid integers"),
    ]


def test_explicit_regeneration_still_updates_one_real_drift(
    tmp_path: Path, monkeypatch
) -> None:
    work = _pin_tree(tmp_path)
    pin = work / "PIN_INDEX.json"
    assert pin.is_file()
    before = pin.read_bytes()
    regen = _load_regen_order()
    monkeypatch.setattr(
        regen,
        "CHAIN",
        [("PIN_INDEX", ["tools/build_pin_index.py"], "explicit regeneration")],
    )
    monkeypatch.setattr(regen, "VERIFY", [])
    monkeypatch.setattr(regen, "check_census_expiry", lambda _work: (True, "current"))
    monkeypatch.setattr(regen, "check_reach", lambda _work: (True, "in sync"))
    monkeypatch.setattr(sys, "argv", ["bd-regen-order", "--work", str(work)])

    assert len(regen.CHAIN) == 1
    assert regen.main() == 0
    after = pin.read_bytes()
    pins = [
        item
        for item in json.loads(after)["pins"]
        if item["file"] == "tests/test_pin.py"
    ]
    assert after != before
    assert len(pins) == 1
    assert pins[0]["value"] == "2.0.0"


def test_transform_control_only_imports_the_regenerator() -> None:
    """Mutation transform control: deliberately asserts no reachability behavior."""
    _load_regen_order()
