import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_W2_C14_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_W2_C14") != "1" or not CANDIDATE,
    reason="candidate opt-in required (BD_TEST_O1698_W2_C14=1 and BD_O1698_W2_C14_CANDIDATE)",
)


@pytest.fixture
def candidate_path():
    p = Path(CANDIDATE)
    assert p.is_absolute() and p.is_file(), f"candidate missing: {CANDIDATE}"
    assert os.access(p, os.X_OK), f"candidate not executable: {CANDIDATE}"
    return p


def make_400_line_python_file(path: Path) -> None:
    lines = []
    for i in range(1, 200):
        lines.append(f"# line {i}\n")
    # Lines 200-230:
    lines.append("def target_func():\n")
    for i in range(201, 230):
        lines.append(f"    v_{i} = {i}\n")
    lines.append("    return 42\n")  # line 230
    for i in range(231, 401):
        lines.append(f"# line {i}\n")
    path.write_text("".join(lines))


def make_git_repo_with_diff(tmp_path: Path, edit_target_func: bool = True, include_non_python: bool = False):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Worker"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "worker@example.com"], cwd=repo_dir, check=True)

    py_file = repo_dir / "module_under_test.py"
    make_400_line_python_file(py_file)
    subprocess.run(["git", "add", "module_under_test.py"], cwd=repo_dir, check=True)

    if include_non_python:
        doc_file = repo_dir / "README.md"
        doc_file.write_text("# Old Readme\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)

    subprocess.run(["git", "commit", "-m", "base commit"], cwd=repo_dir, check=True, capture_output=True)
    base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True).stdout.strip()

    # Now make the edit inside target_func (line 215)
    if edit_target_func:
        text = py_file.read_text()
        text = text.replace("    v_215 = 215\n", "    v_215 = 999  # edited\n")
        py_file.write_text(text)

    if include_non_python:
        doc_file = repo_dir / "README.md"
        doc_file.write_text("# Updated Readme with diff\nNew line added.\n")

    # Write a DONE.md in repo_dir
    done_md = repo_dir / "DONE.md"
    done_md.write_text(
        f"VERDICT: PATCH\n"
        f"BASE: {base_sha}\n"
        f"SEAT: bd-worker-test worker\n"
        f"ROW: test-c14-row\n"
    )

    # Write a transcript
    transcript_file = tmp_path / "transcript.jsonl"
    tool_entry = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": str(py_file.resolve())},
                }
            ]
        },
    }
    transcript_file.write_text(json.dumps(tool_entry) + "\n")

    return repo_dir, base_sha, transcript_file, py_file


def test_a_400_line_fixture_covered_slice_bytes_less_than_whole(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # Fake ctx_slice stub returning target_func
    stub_file = tmp_path / "ratf_stub.py"
    stub_file.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health':\n"
        "        return json.dumps({'status': 'ok', 'service': 'tree-sitter-ast-server'})\n"
        "    return json.dumps({\n"
        "        'status': 'ok',\n"
        "        'symbol': 'target_func',\n"
        "        'symbol_def': 'def target_func():\\n    # stubbed 30 line func\\n    return 84\\n',\n"
        "        'start_line': 200,\n"
        "        'end_line': 230,\n"
        "        'file_path': file_path\n"
        "    })\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    cmd = [
        sys.executable,
        str(candidate_path),
        "score",
        "test-c14-row",
        "--wt",
        str(repo_dir),
        "--transcript",
        str(transcript_file),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"score failed: {res.stdout}\n{res.stderr}"

    score_tsv = state_dir / "shadow" / "c14" / "score.tsv"
    assert score_tsv.exists(), "score.tsv was not created"
    content = score_tsv.read_text()
    lines = [ln for ln in content.strip().splitlines() if ln]
    assert len(lines) >= 1
    # row file whole_bytes slice_bytes hunks_covered/hunks status
    parts = lines[0].split("\t")
    assert parts[0] == "test-c14-row"
    whole_bytes = int(parts[2])
    slice_bytes = int(parts[3])
    coverage = parts[4]
    status = parts[5]

    assert whole_bytes > 0, "whole_bytes must be > 0"
    assert slice_bytes < whole_bytes, f"slice_bytes ({slice_bytes}) must be < whole_bytes ({whole_bytes})"
    assert coverage == "1/1", f"hunks covered must be 1/1, got {coverage}"
    assert status == "COVERED"


def test_b_stub_slice_returns_different_function_flags_0_1(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # Stub returning a different function
    stub_file = tmp_path / "ratf_stub_diff.py"
    stub_file.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health':\n"
        "        return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({\n"
        "        'status': 'ok',\n"
        "        'symbol': 'completely_different_function',\n"
        "        'symbol_def': 'def completely_different_function():\\n    pass\\n',\n"
        "        'start_line': 10,\n"
        "        'end_line': 20,\n"
        "        'file_path': file_path\n"
        "    })\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    cmd = [
        sys.executable,
        str(candidate_path),
        "score",
        "test-c14-row",
        "--wt",
        str(repo_dir),
        "--transcript",
        str(transcript_file),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"score failed: {res.stdout}\n{res.stderr}"

    score_tsv = state_dir / "shadow" / "c14" / "score.tsv"
    assert score_tsv.exists()
    content = score_tsv.read_text()
    lines = [ln for ln in content.strip().splitlines() if ln]
    parts = lines[0].split("\t")
    coverage = parts[4]
    status = parts[5]

    assert coverage == "0/1", f"hunks covered must be 0/1 for wrong function, got {coverage}"
    assert status in ("UNCOVERED", "MISSED", "PARTIAL")


def test_c_stub_raising_or_refused_is_could_not_look_exit_0(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # Stub raising connection error
    stub_file = tmp_path / "ratf_stub_raise.py"
    stub_file.write_text(
        "import urllib.error\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    raise urllib.error.URLError('Connection refused to :8095')\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    cmd = [
        sys.executable,
        str(candidate_path),
        "score",
        "test-c14-row",
        "--wt",
        str(repo_dir),
        "--transcript",
        str(transcript_file),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"must exit 0 on could-not-look, got {res.returncode}: {res.stderr}"

    score_tsv = state_dir / "shadow" / "c14" / "score.tsv"
    assert score_tsv.exists()
    content = score_tsv.read_text()
    lines = [ln for ln in content.strip().splitlines() if ln]
    parts = lines[0].split("\t")
    status = parts[5]
    assert status == "COULD-NOT-LOOK", f"status must be COULD-NOT-LOOK, got {status}"


def test_d_non_python_file_in_diff_is_skipped_lang(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(
        tmp_path, edit_target_func=True, include_non_python=True
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    stub_file = tmp_path / "ratf_stub.py"
    stub_file.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health': return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({'status': 'ok', 'symbol': 'target_func', 'symbol_def': 'def target_func(): pass\\n', 'start_line': 200, 'end_line': 230, 'file_path': file_path})\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    cmd = [
        sys.executable,
        str(candidate_path),
        "score",
        "test-c14-row",
        "--wt",
        str(repo_dir),
        "--transcript",
        str(transcript_file),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0

    score_tsv = state_dir / "shadow" / "c14" / "score.tsv"
    content = score_tsv.read_text()
    lines = [ln for ln in content.strip().splitlines() if ln]
    readme_row = None
    for line in lines:
        if "README.md" in line:
            readme_row = line.split("\t")
            break
    assert readme_row is not None, "README.md not found in score.tsv"
    assert readme_row[5] == "SKIPPED-LANG"
    assert readme_row[4] == "0/0" or "0" in readme_row[4]


def test_e_no_writes_outside_state_shadow_c14(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    stub_file = tmp_path / "ratf_stub.py"
    stub_file.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health': return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({'status': 'ok', 'symbol': 'target_func', 'symbol_def': 'def target_func(): pass\\n', 'start_line': 200, 'end_line': 230, 'file_path': file_path})\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    cmd = [
        sys.executable,
        str(candidate_path),
        "score",
        "test-c14-row",
        "--wt",
        str(repo_dir),
        "--transcript",
        str(transcript_file),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0

    shadow_c14 = (state_dir / "shadow" / "c14").resolve()
    for root, _, files in os.walk(state_dir):
        for f in files:
            p = (Path(root) / f).resolve()
            assert str(p).startswith(str(shadow_c14)), f"Wrote outside state/shadow/c14: {p}"


def test_f_hub_gate_exits_75_on_high_load_or_iowait(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"
    hub_samples = state_dir / "hub-samples"
    hub_samples.mkdir(parents=True, exist_ok=True)
    sample_file = hub_samples / "2026-10-03.tsv"
    # Header then high load sample
    sample_file.write_text("utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\n2026-10-03T08:00:00Z\t15.50\t2400\t0\t1.0\t30.0\n")

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env.pop("BD_SKIP_HUB_GATE", None)

    cmd = [
        sys.executable,
        str(candidate_path),
        "score",
        "test-c14-row",
        "--wt",
        str(repo_dir),
        "--transcript",
        str(transcript_file),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 75, f"hub gate must exit 75, got {res.returncode}: {res.stdout}\n{res.stderr}"


def test_g_probe_log_records_both_ports(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    stub_file = tmp_path / "ratf_stub.py"
    stub_file.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health': return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({'status': 'ok', 'symbol': 'target_func', 'symbol_def': 'def target_func(): pass\\n', 'start_line': 200, 'end_line': 230, 'file_path': file_path})\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    cmd = [
        sys.executable,
        str(candidate_path),
        "score",
        "test-c14-row",
        "--wt",
        str(repo_dir),
        "--transcript",
        str(transcript_file),
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0

    probe_log = state_dir / "shadow" / "c14" / "probe.log"
    assert probe_log.exists(), "probe.log not found"
    log_content = probe_log.read_text()
    assert ":8095 health" in log_content
    assert "control closed port :8097" in log_content


def test_h_install_log_selection_requires_end_installed_no_rollback(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    stub_file = tmp_path / "ratf_stub.py"
    stub_file.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health': return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({'status': 'ok', 'symbol': 'target_func', 'symbol_def': 'def target_func(): pass\\n', 'start_line': 200, 'end_line': 230, 'file_path': file_path})\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    # 1. Positive log: END INSTALLED
    pos_log = tmp_path / "pos.log"
    pos_log.write_text(f"2026-10-03T00:00:00Z CUT 1 END INSTALLED test-c14-row wt={repo_dir}\n")
    res_pos = subprocess.run([
        sys.executable, str(candidate_path), "score", "test-c14-row",
        "--install-log", str(pos_log), "--transcript", str(transcript_file)
    ], env=env, capture_output=True, text=True)
    assert res_pos.returncode == 0
    tsv_content = (state_dir / "shadow" / "c14" / "score.tsv").read_text()
    assert "COVERED" in tsv_content

    # 2. Begin-only log: START only
    begin_log = tmp_path / "begin.log"
    begin_log.write_text(f"2026-10-03T00:00:00Z CUT 1 START test-c14-row wt={repo_dir}\n")
    state_dir_begin = tmp_path / "state_begin"
    env["BD_STATE_DIR"] = str(state_dir_begin)
    res_begin = subprocess.run([
        sys.executable, str(candidate_path), "score", "test-c14-row",
        "--install-log", str(begin_log), "--transcript", str(transcript_file)
    ], env=env, capture_output=True, text=True)
    assert res_begin.returncode == 0
    tsv_begin = (state_dir_begin / "shadow" / "c14" / "score.tsv").read_text()
    assert "COULD-NOT-LOOK" in tsv_begin

    # 3. Rollback log: ROLLED BACK
    rb_log = tmp_path / "rollback.log"
    rb_log.write_text(f"2026-10-03T00:00:00Z CUT 1 START test-c14-row wt={repo_dir}\n2026-10-03T00:00:01Z CUT 1 END test-c14-row ROLLED BACK\n")
    state_dir_rb = tmp_path / "state_rb"
    env["BD_STATE_DIR"] = str(state_dir_rb)
    res_rb = subprocess.run([
        sys.executable, str(candidate_path), "score", "test-c14-row",
        "--install-log", str(rb_log), "--transcript", str(transcript_file)
    ], env=env, capture_output=True, text=True)
    assert res_rb.returncode == 0
    tsv_rb = (state_dir_rb / "shadow" / "c14" / "score.tsv").read_text()
    assert "COULD-NOT-LOOK" in tsv_rb


def test_i_slice_error_or_not_found_status_is_could_not_look(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)

    # Stub returning status: error
    stub_file_err = tmp_path / "ratf_stub_err.py"
    stub_file_err.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health': return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({'status': 'error', 'message': 'backend refused symbol'})\n"
    )

    state_dir_err = tmp_path / "state_err"
    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir_err)
    env["BD_RATF_STUB"] = str(stub_file_err)
    env["BD_SKIP_HUB_GATE"] = "1"

    res = subprocess.run([
        sys.executable, str(candidate_path), "score", "test-c14-row",
        "--wt", str(repo_dir), "--transcript", str(transcript_file)
    ], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    tsv_err = (state_dir_err / "shadow" / "c14" / "score.tsv").read_text()
    assert "COULD-NOT-LOOK" in tsv_err

    # Stub returning status: not_found
    stub_file_nf = tmp_path / "ratf_stub_nf.py"
    stub_file_nf.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health': return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({'status': 'not_found', 'symbol': 'target_func'})\n"
    )

    state_dir_nf = tmp_path / "state_nf"
    env["BD_STATE_DIR"] = str(state_dir_nf)
    env["BD_RATF_STUB"] = str(stub_file_nf)

    res = subprocess.run([
        sys.executable, str(candidate_path), "score", "test-c14-row",
        "--wt", str(repo_dir), "--transcript", str(transcript_file)
    ], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    tsv_nf = (state_dir_nf / "shadow" / "c14" / "score.tsv").read_text()
    assert "COULD-NOT-LOOK" in tsv_nf


def test_j_missing_transcript_is_could_not_look(candidate_path, tmp_path):
    repo_dir, base_sha, transcript_file, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state"

    stub_file = tmp_path / "ratf_stub.py"
    stub_file.write_text(
        "import json\n"
        "def ctx_slice(code='', pattern='', lang='python', endpoint='', timeout=15, skeleton=False, symbol='', file_path=''):\n"
        "    if endpoint == 'health': return json.dumps({'status': 'ok'})\n"
        "    return json.dumps({'status': 'ok', 'symbol': 'target_func', 'symbol_def': 'def target_func(): pass\\n', 'start_line': 200, 'end_line': 230, 'file_path': file_path})\n"
    )

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_RATF_STUB"] = str(stub_file)
    env["BD_SKIP_HUB_GATE"] = "1"

    nonexistent_transcript = tmp_path / "does_not_exist.jsonl"
    res = subprocess.run([
        sys.executable, str(candidate_path), "score", "test-c14-row",
        "--wt", str(repo_dir), "--transcript", str(nonexistent_transcript)
    ], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    tsv_content = (state_dir / "shadow" / "c14" / "score.tsv").read_text()
    assert "COULD-NOT-LOOK" in tsv_content


def test_k_zero_whole_denominator_summary_is_drop(candidate_path, tmp_path):
    state_dir = tmp_path / "state"
    c14_dir = state_dir / "shadow" / "c14"
    c14_dir.mkdir(parents=True, exist_ok=True)
    score_tsv = c14_dir / "score.tsv"
    score_tsv.write_text("".join(f"row{i}\tf.py\t0\t50\t1/1\tCOVERED\n" for i in range(10)))

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    res = subprocess.run([
        sys.executable, str(candidate_path), "summary"
    ], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "SIGNAL: DROP" in res.stdout


def test_l_after_24h_summary_evaluates_signal(candidate_path, tmp_path):
    state_dir = tmp_path / "state"
    c14_dir = state_dir / "shadow" / "c14"
    c14_dir.mkdir(parents=True, exist_ok=True)
    score_tsv = c14_dir / "score.tsv"
    score_tsv.write_text("row0\tf.py\t1000\t50\t1/1\tCOVERED\n")
    os.utime(score_tsv, (0, 0))

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    res = subprocess.run([
        sys.executable, str(candidate_path), "summary"
    ], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "SIGNAL: KEEP" in res.stdout


def test_m_pm_ruling_clock_and_window_controls(candidate_path, tmp_path):
    from datetime import datetime, timezone

    # 1. Test: 9 cuts aged 25 h -> window CLOSED, n=9, verdict computed
    state_9_25h = tmp_path / "state_9_25h"
    c14_dir = state_9_25h / "shadow" / "c14"
    c14_dir.mkdir(parents=True, exist_ok=True)
    t_25h = datetime.now(timezone.utc).timestamp() - (25 * 3600)
    iso_25h = datetime.fromtimestamp(t_25h, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (c14_dir / "CLOCK").write_text(f"{iso_25h}\n")
    tsv_9 = "".join(f"row{i}\tf.py\t1000\t50\t1/1\tCOVERED\n" for i in range(9))
    (c14_dir / "score.tsv").write_text(tsv_9)

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_9_25h)
    res_9 = subprocess.run([
        sys.executable, str(candidate_path), "summary"
    ], env=env, capture_output=True, text=True)
    assert res_9.returncode == 0
    assert "WINDOW: CLOSED (cuts=9)" in res_9.stdout
    assert "SIGNAL: KEEP" in res_9.stdout

    # 2. Paired control: 10 cuts at 2 h -> CLOSED n=10
    state_10_2h = tmp_path / "state_10_2h"
    c14_dir_10 = state_10_2h / "shadow" / "c14"
    c14_dir_10.mkdir(parents=True, exist_ok=True)
    t_2h = datetime.now(timezone.utc).timestamp() - (2 * 3600)
    iso_2h = datetime.fromtimestamp(t_2h, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (c14_dir_10 / "CLOCK").write_text(f"{iso_2h}\n")
    tsv_10 = "".join(f"row{i}\tf.py\t1000\t50\t1/1\tCOVERED\n" for i in range(10))
    (c14_dir_10 / "score.tsv").write_text(tsv_10)

    env["BD_STATE_DIR"] = str(state_10_2h)
    res_10 = subprocess.run([
        sys.executable, str(candidate_path), "summary"
    ], env=env, capture_output=True, text=True)
    assert res_10.returncode == 0
    assert "WINDOW: CLOSED (cuts=10)" in res_10.stdout
    assert "SIGNAL: KEEP" in res_10.stdout

    # 3. Paired control: 3 cuts at 2 h -> OPEN
    state_3_2h = tmp_path / "state_3_2h"
    c14_dir_3 = state_3_2h / "shadow" / "c14"
    c14_dir_3.mkdir(parents=True, exist_ok=True)
    (c14_dir_3 / "CLOCK").write_text(f"{iso_2h}\n")
    tsv_3 = "".join(f"row{i}\tf.py\t1000\t50\t1/1\tCOVERED\n" for i in range(3))
    (c14_dir_3 / "score.tsv").write_text(tsv_3)

    env["BD_STATE_DIR"] = str(state_3_2h)
    res_3 = subprocess.run([
        sys.executable, str(candidate_path), "summary"
    ], env=env, capture_output=True, text=True)
    assert res_3.returncode == 0
    assert "WINDOW: OPEN (cuts=3)" in res_3.stdout
    assert "SIGNAL: PENDING" in res_3.stdout


def test_n_selection_negative_status_and_terminal_markers(candidate_path, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("c14_cand", candidate_path)
    cand = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cand)

    fixture = tmp_path / "existing-dir"
    fixture.mkdir()
    missing = tmp_path / "absent-dir"
    log = tmp_path / "INSTALL-LOG.md"

    cases = [
        ("installed", f"CUT 1 END INSTALLED test-c14-row wt={fixture}\n", True),
        ("begin_only", f"CUT 1 START test-c14-row wt={fixture}\n", False),
        ("rolled_back", f"CUT 1 START test-c14-row wt={fixture}\nCUT 1 END test-c14-row ROLLED BACK\n", False),
        ("unrelated", f"CUT 1 END INSTALLED another-row wt={fixture}\n", False),
        ("split_positive", f"2026-10-03T03:39:29Z CUT 10 START test-c14-row r4 wt={fixture}\n2026-10-03T03:43:55Z CUT 10 INSTALLED /some/path sha\n2026-10-03T03:43:55Z CUT 10 END\n", True),
        ("end_positive", f"2026-10-03T04:49:06Z CUT 22 START test-c14-row wt={fixture}\n2026-10-03T04:49:06Z CUT 22 END test-c14-row r2 INSTALLED.\n", True),
        ("not_installed", f"CUT 1 END NOT INSTALLED test-c14-row wt={fixture}\n", False),
        ("uninstalled", f"CUT 1 END UNINSTALLED test-c14-row wt={fixture}\n", False),
        ("never_installed", f"CUT 1 END NEVER INSTALLED test-c14-row wt={fixture}\n", False),
        ("not_yet_installed", f"CUT 1 END NOT YET INSTALLED test-c14-row wt={fixture}\n", False),
        ("status_not_installed", f"CUT 1 END status=NOT_INSTALLED test-c14-row wt={fixture}\n", False),
        ("installed_false", f"CUT 1 END INSTALLED=false test-c14-row wt={fixture}\n", False),
        ("pending_substring_end", f"CUT 1 PENDING INSTALLED test-c14-row wt={fixture}\n", False),
        ("extend_substring_end", f"CUT 1 EXTEND INSTALLED test-c14-row wt={fixture}\n", False),
        ("split_not_installed", f"CUT 1 START test-c14-row wt={fixture}\nCUT 1 END NOT INSTALLED test-c14-row\n", False),
        ("standalone_not_installed", f"END NOT INSTALLED test-c14-row wt={fixture}\n", False),
        ("row_prefix", f"CUT 1 END INSTALLED test-c14-row-other wt={fixture}\n", False),
        ("missing_directory", f"CUT 1 END INSTALLED test-c14-row wt={missing}\n", False),
        ("empty_log", "", False),
    ]

    for name, text, expected in cases:
        log.write_text(text)
        got = cand.locate_worktree_from_install_log(log, "test-c14-row")
        assert (got is not None) == expected, f"{name}: got {got}, expected {expected}"


def test_o_unreadable_transcript_reports_could_not_look(candidate_path, tmp_path):
    repo_dir, base_sha, _, py_file = make_git_repo_with_diff(tmp_path)
    state_dir = tmp_path / "state_unreadable"
    state_dir.mkdir(parents=True, exist_ok=True)

    # 1. Directory transcript
    dir_trans = tmp_path / "dir_transcript"
    dir_trans.mkdir()

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_dir)
    env["BD_SKIP_HUB_GATE"] = "1"
    res = subprocess.run([
        sys.executable, str(candidate_path), "score", "test-row-dir",
        "--wt", str(repo_dir),
        "--transcript", str(dir_trans),
        "--state-dir", str(state_dir),
    ], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "COULD-NOT-LOOK" in res.stdout

    # 2. Mode 000 transcript
    m0 = tmp_path / "mode000.jsonl"
    m0.write_text('{"type": "message"}\n')
    m0.chmod(0)
    try:
        res2 = subprocess.run([
            sys.executable, str(candidate_path), "score", "test-row-m0",
            "--wt", str(repo_dir),
            "--transcript", str(m0),
            "--state-dir", str(state_dir),
        ], env=env, capture_output=True, text=True)
        assert res2.returncode == 0
        assert "COULD-NOT-LOOK" in res2.stdout
    finally:
        m0.chmod(0o644)


def test_p_clock_and_signal_without_score_tsv(candidate_path, tmp_path):
    from datetime import datetime, timezone

    # 1. Absent score.tsv + CLOCK 2h -> OPEN (cuts=0), PENDING
    state_2h = tmp_path / "state_no_tsv_2h"
    c14_2h = state_2h / "shadow" / "c14"
    c14_2h.mkdir(parents=True, exist_ok=True)
    t_2h = datetime.now(timezone.utc).timestamp() - 7200
    iso_2h = datetime.fromtimestamp(t_2h, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (c14_2h / "CLOCK").write_text(f"{iso_2h}\n")

    env = os.environ.copy()
    env["BD_STATE_DIR"] = str(state_2h)
    res_2h = subprocess.run([
        sys.executable, str(candidate_path), "summary"
    ], env=env, capture_output=True, text=True)
    assert res_2h.returncode == 0
    assert "WINDOW: OPEN (cuts=0)" in res_2h.stdout
    assert "SIGNAL: PENDING" in res_2h.stdout

    # 2. Absent score.tsv + CLOCK 25h -> CLOSED (cuts=0), DROP
    state_25h = tmp_path / "state_no_tsv_25h"
    c14_25h = state_25h / "shadow" / "c14"
    c14_25h.mkdir(parents=True, exist_ok=True)
    t_25h = datetime.now(timezone.utc).timestamp() - 90000
    iso_25h = datetime.fromtimestamp(t_25h, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (c14_25h / "CLOCK").write_text(f"{iso_25h}\n")

    env["BD_STATE_DIR"] = str(state_25h)
    res_25h = subprocess.run([
        sys.executable, str(candidate_path), "summary"
    ], env=env, capture_output=True, text=True)
    assert res_25h.returncode == 0
    assert "WINDOW: CLOSED (cuts=0)" in res_25h.stdout
    assert "SIGNAL: DROP" in res_25h.stdout


def run_coverage_case(tmp_path, candidate_path, name, before, after, response_dict):
    import importlib.util
    import io
    import contextlib
    from unittest.mock import patch

    spec = importlib.util.spec_from_file_location("c14_cov", candidate_path)
    cand = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cand)

    env_git = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env_git["LC_ALL"] = "C"
    env_git.update(
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.com",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )

    case_dir = tmp_path / name
    case_dir.mkdir(parents=True, exist_ok=True)
    wt = case_dir / "wt"
    wt.mkdir()
    subprocess.check_output(["git", "-C", str(wt), "init", "-q"], env=env_git)
    f = wt / "f.py"
    f.write_text(before)
    subprocess.check_output(["git", "-C", str(wt), "add", "f.py"], env=env_git)
    subprocess.check_output(["git", "-C", str(wt), "commit", "-q", "-m", "init"], env=env_git)
    base = subprocess.check_output(["git", "-C", str(wt), "rev-parse", "HEAD"], env=env_git, text=True).strip()
    f.write_text(after)
    (wt / "DONE.md").write_text(f"VERDICT: PATCH\nBASE: {base}\nSEAT: test-fixture\n")
    transcript = case_dir / "transcript.jsonl"
    transcript.write_text(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": str(f)}}]}}) + "\n")
    state = case_dir / "state"

    with contextlib.redirect_stdout(io.StringIO()), patch.dict(os.environ, {"BD_SKIP_HUB_GATE": "1"}), patch.object(cand, "probe_health_and_control", return_value=(200, 0)), patch.object(cand, "get_ctx_slice_fn", return_value=lambda **kwargs: response_dict):
        rc = cand.score_cut(f"ind-{name}", wt, transcript, state, case_dir / "absent-install-log")
    assert rc == 0
    row = (state / "shadow" / "c14" / "score.tsv").read_text().strip()
    fields = row.split("\t")
    return fields[4], fields[5]


def test_q_coverage_merged_edits_omitted_vs_unchanged_context(candidate_path, tmp_path):
    long_src = "def first():\n" + "".join(f"    x{i} = {i}\n" for i in range(1, 17)) + "    return x8\n\n\n\n\n\ndef second():\n    return 3\n"
    first_slice = dict(status="ok", symbol="first", symbol_def="\n".join(long_src.splitlines()[:18]) + "\n", start_line=1, end_line=18)
    second_slice = dict(status="ok", symbol="second", symbol_def="def second():\n    return 3\n", start_line=24, end_line=25)
    short_src = "def first():\n    x = 1\n    y = 2\n    return x + y\n\ndef second():\n    return 3\n"
    short_slice = dict(status="ok", symbol="second", symbol_def="def second():\n    return 3\n", start_line=6, end_line=7)

    # 1. Contained-positive: 1/1 COVERED
    assert run_coverage_case(tmp_path, candidate_path, "contained-positive", long_src, long_src.replace("x8 = 8", "x8 = 80"), first_slice) == ("1/1", "COVERED")

    # 2. Opposite-symbol-control: 0/1 UNCOVERED
    assert run_coverage_case(tmp_path, candidate_path, "opposite-symbol-control", long_src, long_src.replace("x8 = 8", "x8 = 80"), second_slice) == ("0/1", "UNCOVERED")

    # 3. Merged-edits-omitted: 0/1 UNCOVERED (line 2 outside slice 6-7)
    assert run_coverage_case(tmp_path, candidate_path, "merged-edits-omitted", short_src, short_src.replace("x = 1", "x = 5").replace("return 3", "return 9"), short_slice) == ("0/1", "UNCOVERED")

    # 4. Unchanged-context-control: 1/1 COVERED (line 7 inside slice 6-7, context 4-5 outside)
    assert run_coverage_case(tmp_path, candidate_path, "unchanged-context-control", short_src, short_src.replace("return 3", "return 9"), short_slice) == ("1/1", "COVERED")




@pytest.mark.parametrize("terminal", ["PENDING", "status=PENDING", "FAILED"])
def test_r_split_terminal_failure_is_not_installed(candidate_path, tmp_path, terminal):
    import importlib.util

    spec = importlib.util.spec_from_file_location("c14_terminal", candidate_path)
    cand = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cand)
    fixture = tmp_path / "existing-dir"
    fixture.mkdir()
    log = tmp_path / "INSTALL-LOG.md"
    prefix = f"CUT 1 START test-c14-row wt={fixture}\nCUT 1 INSTALLED /some/path sha\n"
    log.write_text(prefix + "CUT 1 END\n")
    assert cand.locate_worktree_from_install_log(log, "test-c14-row") == fixture
    log.write_text(prefix + f"CUT 1 END {terminal}\n")
    assert fixture.is_dir() and terminal in log.read_text()
    assert cand.locate_worktree_from_install_log(log, "test-c14-row") is None, (
        f"E1 terminal END {terminal} admitted as INSTALLED"
    )


def run_current_coordinate_case(candidate_path, tmp_path, kind, expected):
    import ast
    import contextlib
    import importlib.util
    import io
    from unittest.mock import patch

    spec = importlib.util.spec_from_file_location("c14_coordinates", candidate_path)
    cand = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cand)
    before = (
        "def first():\n"
        + "".join(f"    x{i} = {i}\n" for i in range(1, 17))
        + "    return x8\n" + "\n" * 5 + "def second():\n"
        + "".join(f"    y{i} = {i}\n" for i in range(1, 8)) + "    return y7\n"
    )
    wt = tmp_path / "wt"
    wt.mkdir()
    f = wt / "f.py"
    f.write_text(before)
    env_git = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env_git.update(
        GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.com",
        GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
    )

    def git(*args):
        return subprocess.check_output(["git", "-C", str(wt), *args], env=env_git, text=True)

    git("init", "-q")
    git("add", "f.py")
    git("commit", "-q", "-m", "coordinate fixture base")
    base = git("rev-parse", "HEAD").strip()
    after = before.replace("y6 = 6", "y6 = 60")
    if kind == "same_count_both":
        after = after.replace("x4 = 4", "x4 = 40")
    elif kind == "insert_before_later":
        after = after.replace("    x4 = 4\n", "    x4 = 40\n" + "".join(f"    z{i} = {i}\n" for i in range(40)))
    f.write_text(after)
    assert "y6 = 60" in git("diff", base)
    (wt / "DONE.md").write_text(f"BASE: {base}\nSEAT: test-fixture\n")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": str(f)}}
    ]}}) + "\n")
    nodes = {n.name: n for n in ast.walk(ast.parse(after)) if isinstance(n, ast.FunctionDef)}
    calls = []

    def slicer(**kwargs):
        calls.append(kwargs["symbol"])
        node = nodes["first" if kind == "wrong_symbol" else kwargs["symbol"]]
        return dict(status="ok", symbol=node.name, symbol_def=ast.get_source_segment(after, node),
                    start_line=node.lineno, end_line=node.end_lineno)

    state = tmp_path / "state"
    with contextlib.redirect_stdout(io.StringIO()), patch.object(cand, "check_hub_gate"), patch.object(cand, "probe_health_and_control", return_value=(200, 0)), patch.object(cand, "get_ctx_slice_fn", return_value=slicer):
        rc = cand.score_cut(f"coordinate-{kind}", wt, transcript, state, tmp_path / "absent-log")
    assert rc == 0 and len(calls) == (2 if kind in ("same_count_both", "insert_before_later") else 1)
    fields = (state / "shadow" / "c14" / "score.tsv").read_text().strip().split("\t")
    assert tuple(fields[4:6]) == expected, f"E2 current-coordinate coverage: {fields[4:6]}, expected {expected}"


@pytest.mark.parametrize("kind,expected", [
    ("same_count_both", ("2/2", "COVERED")),
    ("insert_before_later", ("2/2", "COVERED")),
    ("later_only", ("1/1", "COVERED")),
    ("wrong_symbol", ("0/1", "UNCOVERED")),
])
def test_s_current_slice_coordinates_cover_current_edits(candidate_path, tmp_path, kind, expected):
    run_current_coordinate_case(candidate_path, tmp_path, kind, expected)


def _locate(candidate_path, tmp_path, text, row="test-c14-row"):
    import importlib.util

    spec = importlib.util.spec_from_file_location("c14_e1_r5", candidate_path)
    cand = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cand)
    log = tmp_path / "INSTALL-LOG.md"
    log.write_text(text)
    return cand.locate_worktree_from_install_log(log, row)


@pytest.mark.parametrize("tail", [
    "CUT 1 END FAILED: tests red\n",
    "CUT 1 END PENDING: PM decision\n",
    "CUT 1 END result=FAILED\n",
    "CUT 1 END PENDING-APPLY\n",
    "CUT 1 END\nCUT 1 status=FAILED\n",
    "CUT 1 END (FAILED)\n",
])
def test_t_word_colon_and_late_terminal_failure_is_not_installed(candidate_path, tmp_path, tail):
    fixture = tmp_path / "existing-dir"
    fixture.mkdir()
    prefix = f"CUT 1 START test-c14-row wt={fixture}\nCUT 1 INSTALLED /some/path sha\n"
    assert _locate(candidate_path, tmp_path, prefix + "CUT 1 END\n") == fixture
    assert _locate(candidate_path, tmp_path, prefix + tail) is None, f"E1 terminal {tail!r} admitted as INSTALLED"


def test_t_real_install_log_shapes_stay_selected(candidate_path, tmp_path):
    fixture = tmp_path / "existing-dir"
    fixture.mkdir()
    cut18 = (
        f"2026-10-03T04:16:57Z CUT 18 START o1698-launcher-receipt r1 install agent; wt={fixture}\n"
        "2026-10-03T04:18:57Z CUT 18 PRECOND verdict head1=BOARD; DONE line1=PATCH; NOTE actual additions=25 "
        "(DONE/verdict say 24; 25th is the blank line after the END marker)\n"
        "2026-10-03T04:19:45Z CUT 18 COPY harness/bd-launch-role.sh 6629aa3c installed (atomic .new+mv, cmp ok, mode 775)\n"
        "2026-10-03T04:21:10Z CUT 18 ADAPTER live operations.py 8be2c682 grep -c RESULT = 0 -> live adapter does NOT parse "
        "the receipt; only pending FIX/o1698-adapter-host-placement/scripts/operations.py e2982d61 does (5 hits).\n"
        "2026-10-03T04:21:10Z CUT 18 INSTALLED bd-launch-role.sh 6629aa3c .pre=20261003T041945Z\n"
        "2026-10-03T04:21:10Z CUT 18 END\n"
    )
    assert _locate(candidate_path, tmp_path, cut18, "o1698-launcher-receipt") == fixture
    cut34 = (
        f"2026-10-03T06:00:00Z CUT 34 START o1698-c2-indexed-search-default wt={fixture}\n"
        "2026-10-03T06:04:39Z CUT 34 END o1698-c2-indexed-search-default INSTALLED bd-search-census.py new->febad91a, "
        "bd-rag/corpus2.py 4cefee5c->5c9f7f2a (.pre-20261003T060401Z); optional crons/landing hooks NOT wired "
        "(PM decision pending)\n"
    )
    assert _locate(candidate_path, tmp_path, cut34, "o1698-c2-indexed-search-default") == fixture
