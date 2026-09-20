"""Test precut ratchet advisory behavior under O733.

TASK (harness-work/FIX/FIX-precut-ratchet-advisory-O733.md):
toolchain/bin/bd-precut BLOCKS on bd-ratchet rc3 ("metric ratchet regressed").
O733 (operator 2026-09-15): ratchet ceilings are ADVISORY at every tier --
DP/coupling/host-baseline drift never blocks.
Change: on bd-ratchet rc3 print the delta line, write <wt>/.review/RATCHET-ADVISORY.md
(metric, baseline, value, delta) and CONTINUE (rc0 for that gate); keep every other
gate as is. Env BD_RATCHET_BLOCK=1 restores blocking.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
PRECUT_PATH = REPO / "toolchain" / "bin" / "bd-precut"

FLOOR = (
    "tests/test_v3_66_1184_mutation_specs_are_tracked.py",
    "tests/test_row357_mutant_anchors_are_not_fragile.py",
    "tests/test_row473_register_tree_containment.py",
    "tests/test_v3_66_1222_every_budget_is_subordinate_to_its_bound.py",
    "tests/test_v3_66_1197_ambient_locale_into_subprocess.py",
    "tests/test_import_graph_no_new_edges.py",
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",
)


def _load_precut():
    loader = SourceFileLoader("bd_precut_under_test", str(PRECUT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_ratchet(tmp_path: Path) -> Path:
    script = tmp_path / "fake-ratchet"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--check' in sys.argv:\n"
        "    print('ratchet check vs baseline:')\n"
        "    print('  [REGRESSED] routes_unwired_operator          2 <= 0')\n"
        "    print()\n"
        "    print('BLOCKED -- 1 metric(s) regressed:')\n"
        "    print('  * routes_unwired_operator: rose 0 -> 2')\n"
        "    sys.exit(3)\n"
        "sys.exit(0)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "bulk_downloader" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (root / "tests").mkdir()
    (root / "tests" / "test_settings_center_slice4.py").write_text('assert __version__ == "1.2.3"\n')
    (root / "CHANGELOG.md").write_text("## v1.2.3\n\nrelease\n")
    (root / "PIN_INDEX.json").write_text('{"version": "1.2.3"}\n')
    (root / "tools").mkdir()
    (root / "tools" / "precut_check.py").write_text("raise SystemExit(9)\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\ntimeout-minutes: 30\n")
    for rel in FLOOR:
        (root / rel).write_text("def test_gate(): pass\n")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    return root


def _drive_gate(mod, root: Path, fake_ratchet: Path) -> tuple[int | None, str | None]:
    """Drive the ratchet gate function, or simulate base behavior if not yet refactored."""
    gate_fn = getattr(mod, "_run_ratchet_gate", None)
    if gate_fn is not None:
        return gate_fn(str(root), rex=str(fake_ratchet))
    cp = subprocess.run([sys.executable, str(fake_ratchet), "--check", "--tree", str(root)])
    rc = cp.returncode
    if rc == 3:
        return 3, "metric ratchet regressed -- see bd-ratchet output above"
    return rc, None


def _drive_main(mod, monkeypatch, tmp_path: Path, capsys, fake_ratchet: Path):
    root = _fixture_root(tmp_path)
    baseline = tmp_path / "baseline.zip"
    baseline.write_bytes(b"nonempty baseline fixture")
    real_run = subprocess.run
    real_isfile = mod.os.path.isfile

    def fake_isfile(path):
        base = mod.os.path.basename(str(path))
        if base == "bd-ratchet":
            return True
        return real_isfile(path)

    def selective_run(cmd, **kwargs):
        argv = [str(x) for x in cmd]
        if argv[:1] == ["git"]:
            return real_run(cmd, **kwargs)
        if any(x.endswith("bd-ratchet") for x in argv):
            return real_run([sys.executable, str(fake_ratchet), *argv[2:]], **kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mod, "_auto_baseline", lambda _root: None)
    monkeypatch.setattr(mod, "_derive_baseline", lambda _r, _t: (str(baseline), None))
    monkeypatch.setattr(mod.subprocess, "run", selective_run)
    monkeypatch.setattr(mod.os.path, "isfile", fake_isfile)
    rc = mod.main(["--root", str(root), "--gate", "--no-coretest"])
    out = capsys.readouterr().out
    return rc, out, root


def test_ratchet_rc3_is_advisory_and_writes_note(monkeypatch, tmp_path: Path, fake_ratchet: Path, capsys):
    monkeypatch.delenv("BD_RATCHET_BLOCK", raising=False)
    mod = _load_precut()
    root = tmp_path / "wt"
    root.mkdir()

    rc, err = _drive_gate(mod, root, fake_ratchet)

    # Asserts advisory: gate function returns 0 and reports advisory message
    assert rc == 0, f"Expected rc 0 (advisory continue) on ratchet rc3, got rc={rc} ({err})"
    assert err is not None and "ADVISORY" in err

    # Asserts note written at <wt>/.review/RATCHET-ADVISORY.md with metric, baseline, value, delta
    advisory_path = root / ".review" / "RATCHET-ADVISORY.md"
    assert advisory_path.is_file(), f"Expected {advisory_path} to be created"
    content = advisory_path.read_text()
    assert "metric: routes_unwired_operator" in content
    assert "baseline: 0" in content
    assert "value: 2" in content
    assert "delta: +2" in content

    # Asserts delta line was printed
    captured = capsys.readouterr()
    assert "routes_unwired_operator: rose 0 -> 2" in captured.out


def test_ratchet_rc3_blocks_when_env_set(monkeypatch, tmp_path: Path, fake_ratchet: Path):
    monkeypatch.setenv("BD_RATCHET_BLOCK", "1")
    mod = _load_precut()
    root = tmp_path / "wt"
    root.mkdir()

    rc, err = _drive_gate(mod, root, fake_ratchet)

    # Asserts rc3 preserved under BD_RATCHET_BLOCK=1
    assert rc == 3
    assert err is not None
    assert "metric ratchet regressed" in err


def test_main_ratchet_rc3_is_advisory_in_result_line(monkeypatch, tmp_path: Path, fake_ratchet: Path, capsys):
    """E1/E2 probe: driving main() asserts the RESULT line names the regression as ADVISORY."""
    monkeypatch.delenv("BD_RATCHET_BLOCK", raising=False)
    mod = _load_precut()

    rc, out, root = _drive_main(mod, monkeypatch, tmp_path, capsys, fake_ratchet)

    assert rc == 0, out
    assert "[advisory ratchet]" in out
    assert "routes_unwired_operator: rose 0 -> 2" in out
    advisory_path = root / ".review" / "RATCHET-ADVISORY.md"
    assert advisory_path.is_file(), f"Expected {advisory_path} to be created"

    # Crucial E1 assertion: does not launder regression into "metric ratchets clean"
    result_line = out.split("RESULT:")[-1].split("\n")[0]
    assert "metric ratchets clean" not in result_line, f"Laundered regression in RESULT line: {result_line}"
    assert "metric ratchet regressed -- ADVISORY, see .review/RATCHET-ADVISORY.md" in result_line, (
        f"Expected advisory in RESULT line, got: {result_line}"
    )


def test_main_ratchet_rc3_blocks_when_env_set(monkeypatch, tmp_path: Path, fake_ratchet: Path, capsys):
    """E1/E2 probe: driving main() with BD_RATCHET_BLOCK=1 blocks with rc 3 and NOT CUT-READY."""
    monkeypatch.setenv("BD_RATCHET_BLOCK", "1")
    mod = _load_precut()

    rc, out, _ = _drive_main(mod, monkeypatch, tmp_path, capsys, fake_ratchet)

    assert rc == 3, out
    assert "RESULT: NOT CUT-READY" in out
    assert "metric ratchet regressed" in out
