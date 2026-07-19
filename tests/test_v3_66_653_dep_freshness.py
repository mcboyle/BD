"""v3.66.653 -- S3.2: dependency-freshness advisory scanner (POS-2).

tools/dep_freshness.py reports drifted pins / unpinned / missing packages across
requirements*.txt vs installed versions. Advisory only -- never auto-bumps.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parent.parent / "tools" / "dep_freshness.py"
_spec = importlib.util.spec_from_file_location("dep_freshness", _p)
df = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(df)


def test_parse_requirement_line():
    assert df.parse_requirement_line("flask>=3.0,<4.0") == {
        "name": "flask", "specifier": ">=3.0,<4.0", "raw": "flask>=3.0,<4.0"}
    assert df.parse_requirement_line("# a comment") is None
    assert df.parse_requirement_line("-r base.txt") is None
    assert df.parse_requirement_line("git+https://x/y.git") is None
    assert df.parse_requirement_line("requests")["specifier"] == ""
    # trailing inline comment stripped from the specifier
    assert df.parse_requirement_line("playwright>=1.45,<2.0  # pin")["specifier"] \
        == ">=1.45,<2.0"


def test_check_freshness_flags_drift_unpinned_missing_ok():
    reqs = {"requirements.txt": (
        "flask>=3.0,<4.0\n"        # ok
        "playwright>=1.45,<2.0\n"  # drift (installed 2.5)
        "requests\n"               # unpinned
        "ghostpkg==1.0\n"          # missing
    )}
    installed = {"flask": "3.1.0", "playwright": "2.5.0", "requests": "2.31.0"}
    rep = df.check_freshness(reqs, installed)
    assert rep["checked"] == 4, rep
    assert rep["ok"] == 1, rep
    drifted = {d["name"] for d in rep["drifted"]}
    assert drifted == {"playwright"}, rep
    assert {u["name"] for u in rep["unpinned"]} == {"requests"}, rep
    assert {m["name"] for m in rep["missing"]} == {"ghostpkg"}, rep


def test_satisfies_comparator_no_packaging_dep():
    # Self-contained comparator (no `packaging`): the exact range pins in the repo.
    assert df._satisfies("3.1.0", ">=3.0,<4.0") is True
    assert df._satisfies("4.0.0", ">=3.0,<4.0") is False
    assert df._satisfies("1.61.0", ">=1.45,<2.0") is True
    assert df._satisfies("2.5.0", ">=1.45,<2.0") is False
    assert df._satisfies("2.0.0", "==2.0") is True
    assert df._satisfies("2.0.1", "==2.0") is False
    assert df._satisfies("1.5", "!=1.5") is False
    assert df._satisfies("1.6", ">1.5") is True


def test_no_packaging_import_in_source():
    # Guard the regression: the deploy venv lacks `packaging`, so the tool must not
    # import it (the failure that took 654 red on stash).
    src = _p.read_text(encoding="utf-8")
    assert "import packaging" not in src and "from packaging" not in src


def test_check_freshness_name_canonicalization():
    # Underscore/case variants of the same distribution match.
    reqs = {"r.txt": "My_Pkg>=1.0\n"}
    installed = {"my-pkg": "1.2"}
    rep = df.check_freshness(reqs, installed)
    assert rep["ok"] == 1 and not rep["drifted"] and not rep["missing"], rep


def test_build_report_over_real_repo_runs():
    # Smoke: the real repo's requirements parse + check without raising.
    rep = df.build_report()
    assert rep["checked"] > 0
    assert set(rep) >= {"checked", "ok", "drifted", "unpinned", "missing", "errors"}


def test_main_check_exit_code_on_drift(capsys=None):
    # --check returns 1 only when drift is present; report path returns 0.
    import types
    rep_drift = {"checked": 1, "ok": 0, "drifted": [
        {"file": "r", "name": "x", "specifier": ">=2", "installed": "1"}],
        "unpinned": [], "missing": [], "errors": []}
    orig = df.build_report
    df.build_report = lambda root=None: rep_drift
    try:
        assert df.main(["--check"]) == 1
        rep_drift["drifted"] = []
        assert df.main(["--check"]) == 0
    finally:
        df.build_report = orig
