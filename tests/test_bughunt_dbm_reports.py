"""deep-bench-misc reports-fix: F5 F14-F21 (O1789 bughunt FINDINGS.md).

One test per finding; each carries a positive control so a passing assert is
not a probe that can never fire.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import builder_gap_report as BGR  # noqa: E402
import capture_quality_report as CQR  # noqa: E402
import environment_report as ENV  # noqa: E402
import install_report as INS  # noqa: E402
import kb_staleness_report as KBS  # noqa: E402
import offline_pack_report as OPR  # noqa: E402
import redaction_dial_report as RDR  # noqa: E402
import report_core as RC  # noqa: E402
import technical_debt_report as TDR  # noqa: E402
import template_drift_report as TDRIFT  # noqa: E402
import template_health_report as THR  # noqa: E402
import test_runtime_report as TRR  # noqa: E402


def _wj(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


# -- F5: redaction_dial_report argv + repo-root import ------------------------
def test_f5_unknown_arg_is_usage_error_not_silent_pass(monkeypatch, capsys):
    calls = []

    def reduced(*a, **k):
        calls.append(1)
        return {"profile": {}, "defaults": {}, "deltas": [], "reduced_redaction": True}
    monkeypatch.setattr(RDR, "build_report", reduced)
    assert RDR.main(["--check"]) == 1          # control: the gate fires
    for typo in ("--chek", "-check", "--bogus"):
        with pytest.raises(SystemExit) as ei:
            RDR.main([typo])
        assert ei.value.code == 2, typo
        assert "unrecognized arguments" in capsys.readouterr().err
    calls.clear()
    with pytest.raises(SystemExit) as ei:
        RDR.main(["-h"])
    assert ei.value.code == 0 and "usage" in capsys.readouterr().out
    assert calls == []                          # help never reports a verdict


def test_f5_documented_invocation_imports_from_repo_root(tmp_path):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
    r = subprocess.run([sys.executable, "-I", "-B", "tools/redaction_dial_report.py", "--json"],
                       cwd=str(_ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    assert "reduced_redaction" in json.loads(r.stdout)


# -- F14: markdown table cells -------------------------------------------------
def _bare_pipes(line):
    return len(re.findall(r"(?<!\\)\|", line))


def test_f14_table_escapes_cells_and_rejects_short_aligns():
    assert RC.table(["a", "b"], [[1, 2]]) == ["| a | b |", "|---|---|", "| 1 | 2 |"]
    out = RC.table(["a"], [["x|y"], ["l1\nl2"]])
    assert len(out) == 4                        # newline did not split a row
    assert all(_bare_pipes(line) == 2 for line in out)
    with pytest.raises(ValueError, match="1 aligns for 3 headers"):
        RC.table(["a", "b", "c"], [], aligns=["---"])


def test_f14_template_health_row_keeps_column_count():
    row = {"dir": "drafts", "host": "pipe|host.example\nx", "status": "draft",
           "completeness_score": 1, "trigger_present": True, "row_selectors_present": False,
           "api_block_present": False, "resolutions_present": False,
           "network_patterns_present": False, "promotion_ready": False,
           "blocked_terms": ["a|b"]}
    d = {"root": ".", "per_template": [row, dict(row, host="plain.example", blocked_terms=[])],
         "summary": {"total": 2, "counts": {}, "gate_ready_rate": 0,
                     "completeness": {"mean": 1, "min": 1, "max": 1}},
         "warnings": {}, "drift_current": {}, "drift_history": {"note": "n"}, "sanity": []}
    lines = THR._md(d).splitlines()
    hdr = next(i for i, ln in enumerate(lines) if ln.startswith("| dir |"))
    rows = lines[hdr + 2:hdr + 4]
    assert "plain.example" in rows[1]           # control row rendered
    assert [_bare_pipes(r) for r in rows] == [_bare_pipes(lines[hdr])] * 2
    assert lines[hdr + 4] == ""                 # no stray split line


# -- F15: capture_quality_report ----------------------------------------------
def test_f15_coverage_snapshots_and_dir_dedupe(tmp_path):
    cap = tmp_path / "captures"
    _wj(cap / "capture_1.json", {"rrweb": [1], "snapdom": True, "snapshots": [1, 2, 3]})
    (cap / "capture_2.json").write_text("{corrupt", encoding="utf-8")
    _wj(cap / "capture_3.json", [1, 2])
    (cap / "a.wacz").write_bytes(b"x")
    m = CQR._scan_payloads(str(tmp_path), ["captures"])
    assert m["capture_json"] == 1 and m["unreadable_json"] == 2
    assert m["rrweb_coverage"] == 1.0 and m["snapdom_coverage"] == 1.0
    assert m["snapshots"] == 3
    # CLI dir dedupe on a clean root (a list-root capture also trips the unowned
    # capture_analytics statistics pass, outside this cut)
    clean = tmp_path / "clean"
    _wj(clean / "captures" / "capture_1.json", {"rrweb": [1]})
    (clean / "captures" / "a.wacz").write_bytes(b"x")
    out = tmp_path / "out"
    assert CQR.main(["--root", str(clean), "--outdir", str(out)]) == 0
    assert "- WACZ: 1" in (out / "capture_analytics.md").read_text("utf-8")  # control
    assert CQR.main(["--root", str(clean), "--captures-dir", "captures",
                     "--captures-dir", "./captures/", "--outdir", str(out)]) == 0
    assert "- WACZ: 1" in (out / "capture_analytics.md").read_text("utf-8")


# -- F16: test_runtime_report --summary ---------------------------------------
def test_f16_explicit_summary_must_be_readable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(TRR.TI, "inventory", lambda r: {"test_files": 1, "total_tests": 1, "largest": []})
    monkeypatch.setattr(TRR.TC, "catalog", lambda r: {"with_test_match": 0, "modules": 0, "gap_candidates": []})
    monkeypatch.setattr(TRR, "_skip_baseline", lambda r: 0)
    out = str(tmp_path / "out")
    good = tmp_path / "good.txt"
    good.write_text("Total: 10 | Passed: 10 | Failed: 0 | Skipped: 0\n")
    assert TRR.main(["--summary", str(good), "--outdir", out]) == 0
    assert "total 10, passed 10" in (tmp_path / "out" / "test_health.md").read_text("utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("nothing here\n")
    latin = tmp_path / "latin.txt"
    latin.write_bytes(b"run \xff\nTotal: 5 | Passed: 4 | Failed: 1 | Skipped: 0\n")
    assert TRR.main(["--summary", str(latin), "--outdir", out]) == 0
    assert "total 5, passed 4" in (tmp_path / "out" / "test_health.md").read_text("utf-8")
    capsys.readouterr()
    for p in (tmp_path / "absent.txt", bad):
        assert TRR.main(["--summary", str(p), "--outdir", out]) == 2
        assert "error:" in capsys.readouterr().err
    assert TRR.main(["--outdir", out]) == 0     # no --summary: still a report
    assert "no SUMMARY.txt provided" in (tmp_path / "out" / "test_health.md").read_text("utf-8")


# -- F17: template_drift_report shapes ----------------------------------------
def test_f17_malformed_template_is_usage_error(tmp_path, capsys):
    gold = {"host": "x.example", "selectors": {"download": {"row_selectors": ["a"]}},
            "resolutions": ["1080p", "720p"], "api": {"base": "b"}}
    g = _wj(tmp_path / "gold.json", gold)
    assert TDRIFT.main([_wj(tmp_path / "same.json", gold), "--gold", g]) == 0   # control
    assert TDRIFT.main([_wj(tmp_path / "drift.json", dict(gold, resolutions=["1080p"])),
                        "--gold", g]) == 1                                       # control
    bad = [[], None, 123, {"selectors": ["x"]}, {"api": ["x"]},
           {"selectors": {"download": ["x"]}}, {"resolutions": "1080p"}]
    for i, doc in enumerate(bad):
        capsys.readouterr()
        assert TDRIFT.main([_wj(tmp_path / f"c{i}.json", doc), "--gold", g]) == 2, doc
        assert "malformed candidate" in capsys.readouterr().err
        assert TDRIFT.main([_wj(tmp_path / "same.json", gold), "--gold",
                            _wj(tmp_path / f"g{i}.json", doc)]) == 2, doc
        assert "malformed gold" in capsys.readouterr().err


# -- F18: builder_gap_report usage/IO -----------------------------------------
_GOLD = _ROOT / "templates" / "reviewed" / "app.reptyle.com.template.json"


def test_f18_bad_inputs_exit_2(tmp_path, monkeypatch, capsys):
    corrupt = tmp_path / "corrupt.wacz"
    corrupt.write_bytes(b"not a zip")
    assert BGR.main(["--gold", str(_GOLD), "--capture", str(corrupt)]) == 2
    assert "error:" in capsys.readouterr().err
    for i, doc in enumerate(([], {"host": "x", "selectors": ["x"]},
                             {"host": "x", "api": ["x"]},
                             {"host": "x", "selectors": {"download": ["x"]}})):
        assert BGR.main(["--gold", _wj(tmp_path / f"g{i}.json", doc)]) == 2, doc
        assert "expected an object" in capsys.readouterr().err
    monkeypatch.setattr(BGR, "_ROOT", tmp_path)
    assert BGR.main([]) == 2
    assert "no reviewed gold" in capsys.readouterr().err


def test_f18_valid_gold_control():
    assert BGR.main(["--gold", str(_GOLD), "--json"]) == 0


# -- F19: environment_report ---------------------------------------------------
def test_f19_environment_inputs(tmp_path, monkeypatch, capsys):
    assert "--mode" not in ENV.__doc__          # no flag argparse would reject
    fe = tmp_path / "frontend" / "package.json"
    _wj(fe, {"version": "1", "dependencies": {"a": "1"}})
    assert ENV._npm_deps(str(tmp_path))["dependencies"] == {"a": "1"}   # control
    for doc in ([], None):
        _wj(fe, doc)
        assert ENV._npm_deps(str(tmp_path)) == {}
        assert __import__("dependency_report").main(["--root", str(tmp_path)]) == 0
    _wj(fe, {"dependencies": [1, 2], "devDependencies": "x"})
    md = ENV._md(ENV.report(str(tmp_path)))
    assert "## Frontend dependencies" in md
    home = tmp_path / "home"
    pw = home / ".cache" / "ms-playwright"
    (pw / "chromium-1").mkdir(parents=True)
    other = tmp_path / "pw2"
    (other / "firefox-1").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(other))
    assert len(ENV._browser_assets()) == 2      # control: two real dirs
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(pw))
    assert ENV._browser_assets() == [str(pw / "chromium-1")]


# -- F20: offline_pack_report / install_report refs ---------------------------
def test_f20_exempt_refs_and_install_summary(tmp_path, capsys):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "ok.sh").write_text("")
    (tmp_path / "install.sh").write_text(
        "bash ./scripts/ok.sh\nbash scripts/missing.sh\ncp /tmp/foo.txt /mnt/bar.sh .\n"
        "curl -O https://host.example/pkg/a.zip\n")
    refs = OPR.report(str(tmp_path))["unresolved_refs"]
    assert refs == [{"script": "install.sh", "ref": "scripts/missing.sh"}]
    assert INS.main(["--root", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["unresolved_refs"] == 1


# -- F21: technical_debt_report / kb_staleness_report --------------------------
def test_f21_debt_self_count_and_bad_root(tmp_path, capsys):
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copy(_ROOT / "tools" / "technical_debt_report.py", tools)
    (tools / "x.py").write_text("# TODO real debt\n")
    d = TDR.scan(str(tmp_path))
    assert d["marker_total"] == 1 and d["marker_hits"][0]["file"] == os.path.join("tools", "x.py")
    out = str(tmp_path / "out")
    assert TDR.main(["--root", str(tmp_path), "--outdir", out]) == 0           # control
    assert KBS.main(["--root", str(tmp_path)]) == 0                             # control
    missing = str(tmp_path / "nope")
    capsys.readouterr()
    assert TDR.main(["--root", missing, "--outdir", out]) == 2
    assert "error:" in capsys.readouterr().err
    assert KBS.main(["--root", missing]) == 2
    assert "error:" in capsys.readouterr().err
