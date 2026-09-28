"""Regression test for BH-BANDSCAF: band derives from staged index / wt-new stops writing untracked test scaffold.

Defect: wt-new writes an untracked tests/test_<slug>_scope.py scaffold. bd-worker-band.sh
derived band from worktree filesystem without filtering for staged/committed status,
shipping a band referencing an untracked file that does not exist in the temp commit,
causing remote pytest to abort with rc=5 UNKNOWN ("file or directory not found").
"""

import os
import subprocess
import sys

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE_BAND = os.environ.get("BD_BH_BANDSCAF_BAND_CANDIDATE", "")
CANDIDATE_OPS = os.environ.get("BD_BH_BANDSCAF_OPS_CANDIDATE", "")

pytestmark = pytest.mark.skipif(
    not (CANDIDATE_BAND or CANDIDATE_OPS),
    reason="candidate opt-in required (BD_BH_BANDSCAF_BAND_CANDIDATE or BD_BH_BANDSCAF_OPS_CANDIDATE)",
)


def test_wt_new_no_untracked_scaffold():
    """wt-new candidate must not create an untracked tests/test_<slug>_scope.py stub."""
    script = CANDIDATE_OPS
    if not script:
        pytest.skip("BD_BH_BANDSCAF_OPS_CANDIDATE not set")
    assert os.path.isfile(script), f"candidate script {script} missing"

    # Read the script AST / text to verify the stub creation is removed
    with open(script, encoding="utf-8") as f:
        content = f.read()

    assert "BD_GATE_SCOPE" not in content, (
        "candidate operations.py must not hardcode BD_GATE_SCOPE stub creation"
    )
    assert "_scope.py" not in content, (
        "candidate operations.py must not write untracked _scope.py test stub"
    )


def test_worker_band_filters_untracked_scaffold():
    """bd-worker-band.sh candidate must filter out untracked test files from the band."""
    script = CANDIDATE_BAND
    if not script:
        pytest.skip("BD_BH_BANDSCAF_BAND_CANDIDATE not set")
    assert os.path.isfile(script), f"candidate script {script} missing"

    with open(script, encoding="utf-8") as f:
        content = f.read()

    # Verify the filtering logic exists in the candidate
    assert "git ls-files --error-unmatch" in content, (
        "candidate bd-worker-band.sh must filter band against git tracked/staged index"
    )
    assert "git cat-file -e" in content, (
        "candidate bd-worker-band.sh must verify band suites exist in temp commit sha"
    )


def run(tmp_path, explicit, deleted=False):
    if not CANDIDATE_BAND:
        pytest.skip('BD_BH_BANDSCAF_BAND_CANDIDATE not set')
    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    git('init')
    git('config', 'user.name', 'test')
    git('config', 'user.email', 'test@example.com')
    (tmp_path/'tests').mkdir()
    tracked=tmp_path/'tests/test_tracked.py'
    tracked.write_text('def test_ok(): pass\n')
    git('add', 'tests/test_tracked.py')
    git('-c', 'core.hooksPath=/dev/null', 'commit', '-m', 'fixture')
    tracked.write_text('def test_ok(): assert True\n')
    git('add', 'tests/test_tracked.py')
    (tmp_path/'tests/test_scope.py').write_text('BD_GATE_SCOPE="module"\n')
    (tmp_path/'venv/bin').mkdir(parents=True)
    (tmp_path/'venv/bin/python').symlink_to(sys.executable)
    (tmp_path/'toolchain/bin').mkdir(parents=True)
    (tmp_path/'toolchain/bin/bd-band-derive').write_text('print("tests/test_tracked.py\\ntests/test_scope.py")\n')
    harness=tmp_path/'harness'
    harness.mkdir()
    (harness/'bd-band-remote.sh').write_text('#!/bin/bash\necho UNEXPECTED_REMOTE; exit 17\n')
    if deleted:
        tracked.unlink()
    env=dict(os.environ, BD_HARNESS_DIR=str(harness), BD_WORKER_BAND_DRY_RUN='0' if deleted else '1', BD_WORKER_BAND_NO_RATCHETS='1')
    return subprocess.run(['bash',CANDIDATE_BAND,str(tmp_path),*explicit],env=env,text=True,capture_output=True,check=False)


def test_derived_untracked_is_filtered(tmp_path):
    r=run(tmp_path, [])
    assert r.returncode==0, r.stderr
    assert 'tests/test_tracked.py' in r.stdout
    assert 'tests/test_scope.py' not in r.stdout, 'BANDSCAF_UNTRACKED_SELECTED'


@pytest.mark.parametrize("selector", ["tests/test_required.py", "tests/test_scope.py"])
def test_explicit_missing_test_must_not_disappear(tmp_path, selector):
    r=run(tmp_path, [selector])
    assert r.returncode == 2, 'BANDSCAF_EXPLICIT_TEST_SILENTLY_DROPPED: '+r.stdout
    assert 'explicit test not in index' in r.stderr


@pytest.mark.parametrize("selector", ["tests/test_tracked.py", "tests/test_tracked.py::test_ok"])
def test_explicit_tracked_test_retained(tmp_path, selector):
    r=run(tmp_path, [selector])
    assert r.returncode == 0
    assert 'tests/test_tracked.py' in r.stdout


def test_explicit_test_deleted_before_commit_refuses(tmp_path):
    r=run(tmp_path, ['tests/test_tracked.py'], deleted=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert 'explicit test not in band commit' in r.stderr
    assert 'UNEXPECTED_REMOTE' not in r.stdout
