"""O1826 C37 -- the cockpit's capture_batch argv must parse in capture_batch.

The cockpit launched tools/capture_batch.py with ``--targets <file>``, a flag
capture_batch's parser never defines, so argparse exited 2 on every run. The
real flag is ``--jobs <file>`` (one ``name|url`` per line). These tests feed the
argv the cockpit builds to capture_batch's own parser, so a flag drift on
either side fails here.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_CAPTURES_ROOT", str(tmp_path / "cap"))
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(tmp_path / "rep"))
    monkeypatch.setenv("BD_COCKPIT_TASKS", str(tmp_path / "task"))
    (tmp_path / "cap").mkdir()
    (tmp_path / "rep").mkdir()
    (tmp_path / "task").mkdir()
    yield


@pytest.fixture
def batch():
    path = _ROOT / "tools/capture_batch.py"
    spec = importlib.util.spec_from_file_location("capture_batch_c37_subject", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _targets(lines):
    tf = cc.captures_root() / "targets.txt"
    tf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tf


def _out():
    out = cc.tasks_root() / "o"
    out.mkdir(parents=True, exist_ok=True)
    return out


def test_capture_batch_argv_parses_in_capture_batch(batch):
    tf = _targets(["alpha|https://a.example/x", "beta|https://b.example/y"])
    out = _out()
    argv = cc.build_invocation("capture", "capture_batch",
                               {"targets_file": str(tf)}, out)
    assert argv[1] == str(cc._ROOT / "tools/capture_batch.py")
    args = batch._build_parser().parse_args(argv[2:])
    assert args.jobs == str(tf.resolve())
    assert args.out_dir == str(out)


def test_capture_batch_argv_names_jobs_exactly_once(batch):
    tf = _targets(["alpha|https://a.example/x"])
    argv = cc.build_invocation("capture", "capture_batch",
                               {"targets_file": str(tf)}, _out())
    assert argv.count("--jobs") == 1
    assert argv.count("--targets") == 0


def test_targets_file_lines_become_capture_batch_jobs(batch):
    # the targets file the cockpit validates is read as capture_batch jobs
    tf = _targets(["# header", "", "alpha|https://a.example/x",
                   "beta | https://b.example/y"])
    argv = cc.build_invocation("capture", "capture_batch",
                               {"targets_file": str(tf)}, _out())
    jobs = batch._parse_jobs(batch._build_parser().parse_args(argv[2:]))
    assert jobs == [("alpha", "https://a.example/x"),
                    ("beta", "https://b.example/y")]


def test_capture_batch_still_refuses_unconfined_targets():
    with pytest.raises(cc.ValidationError):
        cc.build_invocation("capture", "capture_batch",
                            {"targets_file": "/etc/hosts"}, _out())


def test_other_capture_argvs_unchanged():
    out = _out()
    cap = cc.captures_root()
    (cap / "b.wacz").write_text("b", encoding="utf-8")
    (cap / "p.wacz").write_text("p", encoding="utf-8")
    py = cc._py()
    assert cc.build_invocation("capture", "offline_capture_analyze",
                               {"baseline": "b.wacz", "perturbed": "p.wacz"},
                               out) == [
        py, str(cc._ROOT / "tools/offline_capture_analyze.py"),
        "--baseline", str(cap / "b.wacz"), "--perturbed", str(cap / "p.wacz"),
        "--axis", "player_config", "--out", str(out)]
    assert cc.build_invocation("capture", "autopilot", {}, out) == [
        py, str(cc._ROOT / "tools/operator_layer.py"), "autopilot",
        str(cap), "--out-dir", str(out)]
    assert cc.build_invocation("capture", "capture_session",
                               {"url": "https://a.example/x"}, out) == [
        py, str(cc._ROOT / "tools/capture_session.py"),
        "--url", "https://a.example/x", "--title", "capture",
        "--out", str(out / "capture.wacz"),
        "--url-memory-file", str(out / "capture_url_memory.json")]
