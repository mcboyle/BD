"""A configured download_dir that does not exist fails the box, and nothing made it.

MEASURED 2026-08-14, and I caused it. `sites_config.json` was copied from test5
to four hosts to give them a real site. It carries
`download_dir = /home/mboyle/d` -- a HOST-LOCAL path that existed on test5 and
test4 and nowhere else. The next capture round went 2/6:

    CAPTURE VERDICT: FAIL - selftest exit=1
      FAIL  disk_space: /home/mboyle/d: can't check
            (FileNotFoundError: No such file or directory: '/home/mboyle/d')

Unit was 15942 pass / 0 fail on all six and live had 0 fail everywhere. The only
thing wrong was a directory.

NOTHING CREATES IT. Not `provision_test_host.sh`, not `install_linux.sh`, not
the app: `download_dir` is read in at least four places
(`admission.py`, `app_capacity.py`, `alerts_engine.py`, `app_health.py`) and
created in none. So any host given a real site config fails the same way, and
the failure names a PATH rather than the cause -- an operator reads
"FileNotFoundError" and has to work backwards to "the config I installed refers
to a directory that only exists on the machine I copied it from".

WHY DEPLOY AND NOT THE PROVISIONER. Provisioning runs ONCE, on a bare host,
before any operator config exists -- a fresh host has an empty 2-byte
sites_config.json, so there is nothing to create and the provisioner cannot help.
The failure arises when a config is installed LATER, which is exactly what
happened. Deploy runs every time and is the step whose job is "make this host
ready to serve"; a service that cannot write its downloads is not ready.

WHY NOT HARDCODE THE PATH. `/home/mboyle/d` is one operator's directory and this
repo is public. The fix reads whatever the host's own config names, so it is
correct for any deployment and leaks nothing.

WHY A scripts/lib/ FRAGMENT. Same reason heartbeat.sh, tree_state.sh and
capture_run_dir.sh were extracted: inline in deploy.sh the only thing a test
could do is grep for a string, and a source check cannot tell a mkdir that RUNS
from one that is written down. These tests EXECUTE it.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile

# Its subject is one shell fragment and deploy.sh's wiring to it.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib" / "download_dirs.sh"
DEPLOY = REPO / "scripts" / "deploy.sh"


def _run(config_path, extra="") -> subprocess.CompletedProcess:
    """Source the fragment and call it, the way deploy.sh will."""
    script = (
        f'set -u; . "{LIB}"; '
        f'bd_ensure_download_dirs "$(command -v python3)" "{config_path}" {extra}'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=60)


def _write_config(tmp: pathlib.Path, dirs) -> pathlib.Path:
    sites = [{"name": f"s{i}", "download_dir": str(d)} for i, d in enumerate(dirs)]
    p = tmp / "sites_config.json"
    p.write_text(json.dumps(sites))
    return p


def test_the_fragment_exists_and_parses():
    """PRECONDITION -- without it every assertion below is vacuous."""
    assert LIB.is_file(), (
        f"no {LIB.relative_to(REPO)}. A configured download_dir that does not "
        "exist fails the box selftest and nothing creates it.")
    r = subprocess.run(["bash", "-n", str(LIB)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_it_creates_a_configured_directory_that_is_missing():
    """THE DEFECT, driven rather than grepped."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        want = tmp / "downloads" / "nested"
        cfg = _write_config(tmp, [want])
        assert not want.exists(), "fixture precondition: the dir must be absent"

        r = _run(cfg)
        assert r.returncode == 0, f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
        assert want.is_dir(), (
            f"the configured download_dir was not created: {want}\n{r.stdout}")


def test_an_existing_directory_is_left_alone_with_its_contents():
    """Idempotent, and never destructive. deploy runs this every time."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        want = tmp / "downloads"
        want.mkdir()
        keeper = want / "already-here.mp4"
        keeper.write_text("payload")
        cfg = _write_config(tmp, [want])

        r = _run(cfg)
        assert r.returncode == 0, r.stderr
        assert keeper.read_text() == "payload", (
            "an existing download_dir had its contents disturbed -- this runs "
            "on every deploy, against directories holding real downloads")


def test_several_sites_each_get_their_directory():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        wants = [tmp / "a", tmp / "b" / "deep", tmp / "c"]
        cfg = _write_config(tmp, wants)
        r = _run(cfg)
        assert r.returncode == 0, r.stderr
        missing = [str(w) for w in wants if not w.is_dir()]
        assert not missing, f"not created: {missing}\n{r.stdout}"


def test_a_missing_config_is_a_no_op_and_not_an_error():
    """A fresh host has no sites_config.json. That must not fail a deploy."""
    with tempfile.TemporaryDirectory() as td:
        r = _run(pathlib.Path(td) / "does_not_exist.json")
        assert r.returncode == 0, (
            "a host with no site config must deploy cleanly; this is the "
            f"normal state of a freshly provisioned box\n{r.stderr}")


def test_an_empty_or_malformed_config_is_a_no_op_and_not_an_error():
    """The four hosts carried a 2-byte config before this session."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for body in ("{}", "[]", "not json at all"):
            p = tmp / "sites_config.json"
            p.write_text(body)
            r = _run(p)
            assert r.returncode == 0, (
                f"a config of {body!r} must not fail the deploy: {r.stderr}")


def test_a_blank_download_dir_is_skipped_not_created_as_cwd():
    """The empty string is the common case and must not become a directory.

    Several config keys default to "" (watch_folder, storage_tier_dir,
    ytdlp_archive_path all did on the real config). Passing "" to mkdir -p
    would create something surprising or fail; skipping is the only correct
    answer.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        p = tmp / "sites_config.json"
        p.write_text(json.dumps([{"name": "s", "download_dir": ""},
                                 {"name": "t"}]))
        before = sorted(x.name for x in tmp.iterdir())
        r = _run(p)
        assert r.returncode == 0, r.stderr
        after = sorted(x.name for x in tmp.iterdir())
        assert before == after, (
            f"a blank download_dir created something: {set(after) - set(before)}")


def test_deploy_sh_actually_calls_it_before_starting_the_service():
    """Wiring, asserted over comment-stripped code.

    A fragment nothing calls is a fragment that fixes nothing, and the call has
    to land BEFORE the service starts -- a service that comes up unable to write
    its downloads is the state this exists to prevent.
    """
    import sys
    sys.path.insert(0, str(REPO / "tests"))
    from shell_source import shell_code_only

    code = shell_code_only(DEPLOY)
    assert "download_dirs.sh" in code, (
        "deploy.sh does not source scripts/lib/download_dirs.sh")
    assert "bd_ensure_download_dirs" in code, (
        "deploy.sh sources the fragment but never calls it")

    # ANCHOR ON THE STEP MARKER, NOT ON THE FIRST TEXTUAL MATCH. The first
    # `systemctl start bulkdownloader` in this file is inside die()'s recovery
    # path ("the unit was STOPPED at step 8 -- attempting restart"), which sits
    # near the top and is not the deploy's start step at all. A first-match
    # comparison therefore fails a CORRECT wiring -- which is exactly what it
    # did on the first run of this test. CLAUDE.md section 1: a predicate over
    # the wrong part of the subject is worse than a grep, because it looks
    # rigorous.
    call_at = code.index("bd_ensure_download_dirs")
    start_step_at = code.index("STEP=11")
    assert call_at < start_step_at, (
        "the download dirs are ensured at or after step 11, where the service "
        "starts. The service would come up unable to write its downloads, "
        "which is the state this exists to prevent.")
