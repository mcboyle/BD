import concurrent.futures
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_BAND_FETCHHEAD_RACE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _script(name):
    path = Path(CANDIDATE) / name
    assert path.is_file() and os.access(path, os.X_OK), f"FETCHHEAD: invalid candidate {path}"
    return path.read_text()


def _between(source, start, end):
    assert source.count(start) == 1, f"FETCHHEAD: executable seam {start!r} count != 1"
    begin = source.index(start)
    finish = source.index(end, begin)
    return source[begin:finish]


def _fetch_code():
    return _between(
        _script("bd-band-remote.sh"),
        'git -C "$repo" fetch -q "$mirror" "$BAND_REF"',
        'wt="$HOME/.bd-bands/$SHA.$MODE.$slot"',
    )


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", *args],
        cwd=cwd, capture_output=True, text=True, check=True, timeout=20,
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    shas = []
    for value in ("A", "B"):
        (source / "value").write_text(value)
        _git(source, "add", "--", "value")
        _git(source, "commit", "-qm", value)
        shas.append(_git(source, "rev-parse", "HEAD"))
    assert len(set(shas)) == 2, "FETCHHEAD: fixture needs two distinct commits"
    mirror = tmp_path / "mirror.git"
    _git(source, "clone", "-q", "--bare", str(source), str(mirror))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for sha in shas:
        _git(mirror, "cat-file", "-e", f"{sha}^{{commit}}")
    return repo, mirror, shas


def _run_fetch(repos, sha, ref=None, **extra):
    repo, mirror, _ = repos
    return subprocess.run(
        ["bash", "-c", _fetch_code()],
        env={**os.environ, "repo": str(repo), "mirror": str(mirror),
             "BAND_REF": ref or sha, "SHA": sha, **extra},
        capture_output=True, text=True, timeout=25, check=False,
    )


def _band_result(remote_rc):
    source = _script("bd-band-remote.sh")
    code = _between(source, '  case "$remote_rc" in\n', '\ndone\n')
    final = source[source.index('\necho "band-order(unavailable):'):]
    return subprocess.run(
        ["bash", "-c", 'for ip in fixture; do\n' + code + '\ndone\n' + final],
        env={**os.environ, "remote_rc": str(remote_rc)},
        capture_output=True, text=True, timeout=10, check=False,
    )


def test_concurrent_fetches_verify_their_own_commit(repos, tmp_path):
    sync = tmp_path / "sync"
    sync.mkdir()
    for name in ("ready", "release"):
        os.mkfifo(sync / name)
    shim = tmp_path / "bin"
    shim.mkdir()
    real_git = shutil.which("git")
    assert real_git, "FETCHHEAD: real git required"
    git_shim = shim / "git"
    git_shim.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "fetch = 'fetch' in args\n"
        "sync = Path(os.environ['SYNC'])\n"
        "run = os.environ['RUN']\n"
        "if fetch and run == 'B':\n"
        "    with (sync / 'ready').open() as f: assert f.read() == 'A'\n"
        "rc = subprocess.run([os.environ['REAL_GIT'], *args]).returncode\n"
        "if fetch and rc == 0:\n"
        "    if run == 'A':\n"
        "        with (sync / 'ready').open('w') as f: f.write('A')\n"
        "        with (sync / 'release').open() as f: assert f.read() == 'B'\n"
        "    else:\n"
        "        with (sync / 'release').open('w') as f: f.write('B')\n"
        "sys.exit(rc)\n"
    )
    git_shim.chmod(0o755)
    extra = {"SYNC": str(sync), "REAL_GIT": real_git, "PATH": f"{shim}:{os.environ['PATH']}"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(_run_fetch, repos, sha, RUN=run, **extra)
                for sha, run in zip(repos[2], ("A", "B"))]
        results = [job.result(timeout=30) for job in jobs]
    repo, _, shas = repos
    for sha in shas:
        _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    assert _git(repo, "rev-parse", "FETCH_HEAD") == shas[1], "FETCHHEAD: overwrite precondition absent"
    outer_codes = [_band_result(r.returncode).returncode for r in results]
    assert [r.returncode for r in results] == [0, 0], (
        f"FETCHHEAD-RACE: both commits present, concurrent fetch return codes "
        f"{[r.returncode for r in results]}, outer band codes {outer_codes}"
    )
    assert outer_codes == [0, 0], f"FETCHHEAD-OUTER: wanted [0, 0], got {outer_codes}"


def test_single_fetch_passes(repos):
    result = _run_fetch(repos, repos[2][0])
    assert result.returncode == 0, f"FETCHHEAD-PASS: {result.stderr}"
    assert _band_result(result.returncode).returncode == 0


def test_missing_wanted_commit_still_refuses(repos):
    missing = "0" * 40
    result = _run_fetch(repos, missing, ref=repos[2][0])
    assert result.returncode == 81, f"FETCHHEAD-MISSING: expected rc81, got {result.returncode}"
    repo, _, _ = repos
    absent = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{missing}^{{commit}}"],
                            capture_output=True, text=True, check=False)
    assert absent.returncode != 0 and "Not a valid object name" in absent.stderr
    outer = _band_result(result.returncode)
    assert outer.returncode == 64, f"FETCHHEAD-MISSING-OUTER: expected rc64, got {outer.returncode}"
    assert "the fetched object is not the candidate SHA" in outer.stdout
    assert "REMOTE-UNAVAILABLE:" in outer.stdout


def test_failed_fetch_still_refuses(repos):
    result = _run_fetch(repos, repos[2][0], ref="refs/heads/missing-fixture-ref")
    assert result.returncode == 80, f"FETCHHEAD-FETCH-FAIL: expected rc80, got {result.returncode}"
    outer = _band_result(result.returncode)
    assert outer.returncode == 64
    assert "the candidate ref did not arrive from its mirror" in outer.stdout


def _status(tmp_path, summary, rc=0):
    log = tmp_path / "summary.log"
    log.write_text(summary)
    code = _between(_script("bd-worker-precut.sh"), '\nSTATUS="UNKNOWN"\n',
                    'staged_files=$(git -C "$WT" diff --cached --name-only')
    result = subprocess.run(
        ["bash", "-c", code + '\nprintf "STATUS=%s passed=%s failed=%s skipped=%s errors=%s\\n" "$STATUS" "$PASSED" "$FAILED" "$SKIPPED" "$ERRORS"'],
        env={**os.environ, "LOG": str(log), "rc": str(rc)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, f"BAND-STATUS: seam failed: {result.stderr}"
    return result.stdout.strip()


@pytest.mark.parametrize("summary,rc,expected", [
    ("================ 7 skipped in 0.01s ================\n", 0,
     "STATUS=EMPTY passed=0 failed=0 skipped=7 errors=0"),
    ("================ 2 passed in 0.01s ================\n", 0,
     "STATUS=COMPLETED passed=2 failed=0 skipped=0 errors=0"),
    ("================ 2 passed, 7 skipped in 0.01s ================\n", 0,
     "STATUS=COMPLETED passed=2 failed=0 skipped=7 errors=0"),
    ("================ 1 failed, 7 skipped in 0.01s ================\n", 1,
     "STATUS=COMPLETED passed=0 failed=1 skipped=7 errors=0"),
    ("================ 1 error, 7 skipped in 0.01s ================\n", 1,
     "STATUS=COMPLETED passed=0 failed=0 skipped=7 errors=1"),
    ("================ 7 skipped, 1 warning in 0.01s ================\n", 0,
     "STATUS=EMPTY passed=0 failed=0 skipped=7 errors=0"),
    ("================ 7 skipped, 1 xfailed in 0.01s ================\n", 0,
     "STATUS=COMPLETED passed=0 failed=0 skipped=7 errors=0"),
    (("tests/test_case.py::test_counts[2 passed-1 failed-1 error] SKIPPED\n"
      "================ 7 skipped in 0.01s ================\n"), 0,
     "STATUS=EMPTY passed=0 failed=0 skipped=7 errors=0"),
    ("2 passed in 0.01s\n", 0,
     "STATUS=COMPLETED passed=2 failed=0 skipped=0 errors=0"),
    ("", 0, "STATUS=UNKNOWN passed=0 failed=0 skipped=0 errors=0"),
])
def test_status_uses_executed_counts(tmp_path, summary, rc, expected):
    observed = _status(tmp_path, summary, rc)
    assert observed == expected, f"BAND-EMPTY: expected {expected!r}, got {observed!r}"


@pytest.mark.parametrize("tail,rc,expected", [
    ("================ 7 skipped in 0.01s ================", "0", "EMPTY"),
    ("================ 2 passed, 7 skipped in 0.01s ================", "0", "COMPLETED"),
    ("================ 1 failed, 7 skipped in 0.01s ================", "1", "COMPLETED"),
    ("================ 7 skipped, 1 warning in 0.01s ================", "0", "EMPTY"),
    ("================ 7 skipped, 1 xfailed in 0.01s ================", "0", "COMPLETED"),
    (("tests/test_case.py::test_counts[================ 7 skipped in 0.01s ================] SKIPPED\n"
      "================ 2 passed, 7 skipped in 0.01s ================"), "0", "COMPLETED"),
    (("================ 7 skipped in 0.01s ================\n"
      "================ 2 passed in 0.01s ================"), "0", "COMPLETED"),
    ("7 skipped in 0.01s", "0", "EMPTY"),
    ("", "", "UNKNOWN"),
])
def test_attach_reports_empty_too(tail, rc, expected):
    code = _between(_script("bd-worker-precut.sh"), '\n  ATTACH_STATUS="COMPLETED"\n',
                    '  # Report failing node ids')
    result = subprocess.run(
        ["bash", "-c", code + '\nprintf "%s\\n" "$ATTACH_STATUS"'],
        env={**os.environ, "RC_VAL": rc, "TAIL_OUTPUT": tail},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, f"BAND-ATTACH: seam failed: {result.stderr}"
    assert result.stdout.strip() == expected, f"BAND-ATTACH-EMPTY: expected {expected}, got {result.stdout.strip()}"
