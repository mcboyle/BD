"""Row 794: bd-precut must not call an unrun detector clean.

Rebased onto 6add188b (2026-09-15): row 790 landed the same property in
bd-precut first, so this file asserts it with the landed tool's wording
(ran_parts, "exited rc=N", "metric ratchet (bd-ratchet not found)")."""
from __future__ import annotations
import importlib.machinery
import os as _os
import subprocess as _subprocess
from pathlib import Path
import pytest
BD_GATE_SCOPE = "repo-wide"
REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-precut"
def _load(): return importlib.machinery.SourceFileLoader("bd_precut_row794", str(TOOL)).load_module()
def _minimal_release_tree(root):
    (root / "bulk_downloader").mkdir(); (root / "tests").mkdir()
    (root / "bulk_downloader" / "__init__.py").write_text('__version__ = "3.66.1542"\n')
    (root / "tests" / "test_settings_center_slice4.py").write_text('assert __version__ == "3.66.1542"\n')
    (root / "CHANGELOG.md").write_text("## v3.66.1542\n\n")
def test_skipped_footguns_and_ratchets_are_unknown_not_clean(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"; root.mkdir(); _minimal_release_tree(root); module = _load(); real = module.os.path.isfile
    monkeypatch.setattr(module.os.path, "isfile", lambda path: False if str(path).endswith(("/bd-footguns", "/bd-ratchet")) else real(path))
    monkeypatch.setattr(module, "_auto_baseline", lambda _root: None)
    monkeypatch.setattr(module, "_derive_baseline", lambda _root, _tmp: (None, "test baseline"))
    monkeypatch.setattr(module, "_repo_wide_marker_census", lambda _root: {"error": "test census"})
    assert module.main(["--root", str(root), "--no-coretest"]) == 0
    output = capsys.readouterr().out
    assert "footguns clean" not in output; assert "metric ratchets clean" not in output
    assert "footgun registry (bd-footguns not found)" in output; assert "metric ratchet (bd-ratchet not found)" in output
def test_all_present_passing_detectors_keep_the_clean_claim_construction():
    text = TOOL.read_text(encoding="utf-8")
    assert 'ran_parts.append("footguns clean")' in text; assert 'ran_parts.append("metric ratchets clean")' in text; assert '", ".join(ran_parts)' in text
    assert "sys.exit(main())" in text


@pytest.mark.parametrize(
    "footguns_rc,ratchet_rc,footguns_clean_expected,ratchet_clean_expected,expect_footguns_unknown,expect_ratchet_unknown",
    [
        (0, 0, True, True, False, False),
        (1, 0, False, True, True, False),
        (0, 2, True, False, False, True),
    ],
)
def test_nonzero_detector_rc_is_reported_unknown_not_absorbed_into_clean(
    tmp_path, monkeypatch, capsys,
    footguns_rc, ratchet_rc, footguns_clean_expected, ratchet_clean_expected,
    expect_footguns_unknown, expect_ratchet_unknown,
):
    """The row's own subject: bd-footguns/bd-ratchet exiting nonzero (but not
    the blocking rc=3) must show up as UNKNOWN in the RESULT line, never as
    "clean". Drives the REAL subprocess.run call site (module.subprocess and
    module._sp both resolve to the same stdlib module object) rather than
    the tool's own source text, so a mutant that flips the rc==0/rc!=0
    branches or renames the RAN marker is actually caught."""
    root = tmp_path / "root"; root.mkdir(); _minimal_release_tree(root)
    module = _load()
    real_isfile = module.os.path.isfile

    def _fake_isfile(path):
        if str(path).endswith(("/bd-footguns", "/bd-ratchet")):
            return True
        return real_isfile(path)

    def _fake_run(cmd, timeout=None):
        name = _os.path.basename(cmd[1])
        if name == "bd-footguns":
            return _subprocess.CompletedProcess(cmd, footguns_rc)
        if name == "bd-ratchet":
            return _subprocess.CompletedProcess(cmd, ratchet_rc)
        raise AssertionError(f"unexpected subprocess.run call: {cmd!r}")

    monkeypatch.setattr(module.os.path, "isfile", _fake_isfile)
    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    monkeypatch.setattr(module, "_auto_baseline", lambda _root: None)
    monkeypatch.setattr(module, "_derive_baseline", lambda _root, _tmp: (None, "test baseline"))
    monkeypatch.setattr(module, "_repo_wide_marker_census", lambda _root: {"error": "test census"})

    assert module.main(["--root", str(root), "--no-coretest"]) == 0
    output = capsys.readouterr().out

    assert ("footguns clean" in output) == footguns_clean_expected
    assert ("metric ratchets clean" in output) == ratchet_clean_expected
    assert ("footgun registry exited rc=%d" % footguns_rc in output) == expect_footguns_unknown
    assert ("metric ratchet exited rc=%d" % ratchet_rc in output) == expect_ratchet_unknown
