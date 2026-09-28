"""P5 fail-open class, opusb-003: bd-cache-ttl-guard.py must never report OK (rc 0) over input it could not read.

BD_P5_FAILOPEN_OPUSB003_CANDIDATE = opt-in guard under test. Each case builds a scratch
root, runs the guard with --no-proc and a fixed --now, and checks status and rc.
Fail-closed: an unreadable, unparseable or unplaceable input is COULD NOT LOOK -> UNKNOWN rc 3, never OK and never
a traceback (rc 1 is the ALERT code). Positive controls: clean 1h data -> OK rc 0; a 5m session -> ALERT rc 1.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = 'module'
CANDIDATE = os.environ.get('BD_P5_FAILOPEN_OPUSB003_CANDIDATE', '')
pytestmark = pytest.mark.skipif(not CANDIDATE, reason='candidate opt-in required')
GUARD = Path(CANDIDATE)


@pytest.fixture(autouse=True)
def candidate_available():
    assert GUARD.is_file() and os.access(GUARD, os.X_OK), 'candidate unavailable'

NOW = '2026-09-28T12:00:00Z'
TS = '2026-09-28T11:50:00Z'


def line(mid, w1h, w5m, ts=TS):
    return json.dumps({'type': 'assistant', 'timestamp': ts, 'message': {
        'id': mid, 'model': 'claude-opus', 'usage': {'cache_creation': {
            'ephemeral_1h_input_tokens': w1h, 'ephemeral_5m_input_tokens': w5m}}}},
        separators=(',', ':')) + '\n'


def session(root, name, text, slug='p'):
    d = root / 'projects' / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{name}.jsonl'
    f.write_text(text)
    return f


def run(*roots, extra=()):
    args = [sys.executable, str(GUARD), '--now', NOW, '--no-proc', *extra]
    for r in roots:
        args += ['--root', str(r)]
    p = subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)
    status = [ln for ln in p.stdout.splitlines() if ln.startswith('TTL-GUARD ')]
    return p.returncode, (status[-1] if status else ''), p.stdout + p.stderr


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / '.claude-x'
    session(r, 'good', line('g1', 60000, 0))  # a clean, judged 1h session: on its own -> OK
    return r


def assert_unknown(res, why):
    rc, status, out = res
    assert rc == 3 and 'status=UNKNOWN' in status, f'{why}: fail-open, rc={rc} {status!r}\n{out[-600:]}'
    assert 'COULD NOT LOOK' in out, f'{why}: no COULD NOT LOOK line\n{out[-600:]}'
    assert 'Traceback' not in out


def test_positive_control_ok(root):
    rc, status, _ = run(root)
    assert rc == 0 and 'status=OK' in status


def test_positive_control_alert(root):
    session(root, 'fb', line('f1', 0, 90000))
    rc, status, _ = run(root)
    assert rc == 1 and 'status=ALERT' in status


def test_zoneless_timestamps_hide_a_5m_session(root):
    session(root, 'fb', ''.join(line(f'f{i}', 0, 30000, ts='2026-09-28T11:50:00') for i in range(3)))
    assert_unknown(run(root), 'zone-less timestamps')


def test_corrupt_usage_lines_hide_a_5m_session(root):
    bad = line('f1', 0, 90000)[:-40]  # the 5m response, truncated mid-file
    session(root, 'fb', bad + '\n' + line('f2', 1000, 0))
    assert_unknown(run(root), 'corrupt mid-file line')


def test_non_numeric_usage_is_not_a_crash(root):
    session(root, 'fb', line('f1', 'lots', 90000))
    assert_unknown(run(root), 'non-numeric token count')


def test_non_object_message_is_not_a_crash(root):
    session(root, 'fb', '{"type":"assistant","timestamp":"' + TS + '","message":"ephemeral_5m"}\n')
    assert_unknown(run(root), 'message is not an object')


def test_unsearchable_project_dir(root):
    f = session(root, 'fb', line('f1', 0, 90000), slug='locked')
    f.parent.chmod(0o400)  # listable, not searchable: stat() of its files fails
    try:
        assert_unknown(run(root), 'unsearchable project dir')
    finally:
        f.parent.chmod(0o700)


def test_unlistable_project_dir(root):
    f = session(root, 'fb', line('f1', 0, 90000), slug='hidden')
    f.parent.chmod(0o000)
    try:
        assert_unknown(run(root), 'unlistable project dir')
    finally:
        f.parent.chmod(0o700)


def test_explicit_root_without_projects(root, tmp_path):
    assert_unknown(run(root, tmp_path / 'typo'), '--root without projects/')


def test_partial_final_line_is_a_live_append_not_a_failure(root):
    # a transcript being written: its last line has no newline yet; the whole lines still count
    session(root, 'live', line('l1', 60000, 0) + line('l2', 60000, 0)[:-30])
    rc, status, out = run(root)
    assert rc == 0 and 'status=OK' in status and 'COULD NOT LOOK' not in out, out[-600:]


def test_unmatched_lines_are_not_counted(root):
    # user lines and non-usage assistant lines are outside the prefilter and must not raise CNL
    session(root, 'mix', '{"type":"user","message":"hi"}\nnot json at all\n' + line('m1', 60000, 0))
    rc, status, out = run(root)
    assert rc == 0 and 'status=OK' in status, out[-600:]


def test_non_object_settings_env_is_not_a_crash(root):
    # settings.json env as a list: no per-line guard covers it, so only main()'s catch keeps rc off the ALERT code
    (root / 'settings.json').write_text('{"env": ["CLAUDE_CODE_DISABLE_1H_CACHE=1"]}')
    assert_unknown(run(root), 'settings env is not an object')


@pytest.mark.parametrize("mode", [0o000, 0o100])
def test_unreadable_projects_root_is_unknown(tmp_path, mode):
    assert os.geteuid() != 0, "permission probe requires non-root"
    good = tmp_path / "good"
    blocked = tmp_path / "blocked"
    session(good, "one", line("a", 60000, 0))
    f = session(blocked, "five", line("b", 0, 90000))
    assert f.read_text() and run(good, blocked)[0] == 1, "POSITIVE: hidden 5m input must alert when readable"
    projects = blocked / "projects"
    projects.chmod(mode)
    try:
        assert not os.access(projects, os.R_OK), "fixture did not deny directory enumeration"
        assert_unknown(run(good, blocked), "PROJECTS_ROOT_PERMISSION_FAIL_OPEN")
    finally:
        projects.chmod(0o700)


def test_readable_roots_preserve_alert(tmp_path):
    good = tmp_path / "good"
    session(good, "one", line("a", 0, 90000))
    rc, status, out = run(good)
    assert rc == 1 and "status=ALERT" in status, out


def test_config_crash_class_keeps_session_alert(root):
    # lens R1 (A2-A): a non-object settings env must not erase a real 5m-fallback ALERT from the session scan
    session(root, 'fb', line('f1', 0, 90000))
    (root / 'settings.json').write_text('{"env": ["X=1"]}')
    rc, status, out = run(root)
    assert rc == 1 and 'status=ALERT' in status, f'ALERT_MASKED_BY_CONFIG: rc={rc} {status!r}\n{out[-600:]}'
    assert 'ttl-5m-fallback' in out and 'COULD NOT LOOK' in out, out[-600:]


def test_non_object_env_keeps_prompt_cache_ttl_alert(root):
    (root / 'settings.json').write_text('{"promptCacheTtl": "5m", "env": "X=1"}')
    rc, _, out = run(root)
    assert rc == 1 and 'promptCacheTtl=5m' in out and 'COULD NOT LOOK' in out, out[-600:]
