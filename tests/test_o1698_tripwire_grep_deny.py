"""O1698 T21/T22 -> T23: bd-tripwire-hook.py refuses a Grep/Glob TOOL call rooted at or above a fleet root.

The Grep tool walked harness-work recursively (ugrep 140+ s, iowait 22%); T22 only judges Bash commands. Each
refusal sits beside its allowed twin, driven through the hook's real stdin JSON contract (PreToolUse event).
Opt-in: BD_O1698_TRIPWIRE_GREP_DENY_CANDIDATE=<abs path to the candidate hook>, or BD_TEST_O1698_TRIPWIRE_GREP_DENY=1
(defaults to the FIX candidate). RED = env pointing at the live/.pre copy.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
FIX = "/home/mboyle/bd-persist/harness-work/FIX/o1698-tripwire-grep-deny/bd-tripwire-hook.py"
CANDIDATE = os.environ.get("BD_O1698_TRIPWIRE_GREP_DENY_CANDIDATE") or (
    FIX if os.environ.get("BD_TEST_O1698_TRIPWIRE_GREP_DENY") == "1" else "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

HOME = "/home/mboyle"
CUT = "/home/mboyle/bd-local-wt/some-cut"


@pytest.fixture
def hook(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_file(), candidate
    env = {k: v for k, v in os.environ.items() if k not in ("BD_SEARCH_ROOTS", "BD_TOOL_SEARCH_EXTRA_ROOTS")}
    env.update(HOME=HOME, LC_ALL="C", BD_TRIPWIRE_LOG=str(tmp_path / "tripwire.log"))

    def _run(tool, tool_input, cwd=HOME, **extra_env):
        ev = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": cwd}
        return subprocess.run(["python3", str(candidate)], input=json.dumps(ev), capture_output=True,
                              text=True, env=dict(env, **extra_env), check=False, timeout=30)
    return _run


# (tool, tool_input, cwd) refused  |  its allowed twin
PAIRS = [
    pytest.param(("Grep", {"pattern": "x", "path": "/home/mboyle/bd-persist"}, HOME),
                 ("Grep", {"pattern": "x", "path": CUT}, HOME), id="grep-bd-persist-vs-one-cut"),
    pytest.param(("Grep", {"pattern": "x"}, HOME),
                 ("Grep", {"pattern": "x"}, CUT), id="grep-no-path-cwd-home-vs-cwd-cut"),
    pytest.param(("Grep", {"pattern": "x", "path": "/home"}, CUT),
                 ("Grep", {"pattern": "x", "path": "/home/other"}, CUT), id="grep-parent-of-roots-vs-sibling"),
    pytest.param(("Grep", {"pattern": "x", "path": "/"}, CUT),
                 ("Grep", {"pattern": "x", "path": "/tmp"}, CUT), id="grep-filesystem-root-vs-tmp"),
    pytest.param(("Grep", {"pattern": "x", "path": "../bd-cuts"}, "/home/mboyle/bd-persist"),
                 ("Grep", {"pattern": "x", "path": "harness"}, "/home/mboyle/bd-persist"), id="grep-relative-path"),
    pytest.param(("Grep", {"pattern": "x", "path": "/home/mboyle/bd-codex-briefs"}, CUT),
                 ("Grep", {"pattern": "x", "path": "/home/mboyle/bd-codex-briefs/COMMON.md"}, CUT),
                 id="grep-codex-briefs-vs-named-file"),
    pytest.param(("Grep", {"pattern": "x", "path": "/home/mboyle/bd-cuts", "glob": "cut/one/**"}, CUT),
                 ("Grep", {"pattern": "x", "path": "/home/mboyle/bd-cuts/cut/one"}, CUT), id="glob-filter-does-not-bound"),
    pytest.param(("Glob", {"pattern": "**/*.md", "path": "/home/mboyle/bd-persist"}, CUT),
                 ("Glob", {"pattern": "**/*.md", "path": "/home/mboyle/bd-persist/harness"}, CUT), id="glob-tool-path"),
    pytest.param(("Glob", {"pattern": "/home/mboyle/bd-local-wt/**/*.py"}, CUT),
                 ("Glob", {"pattern": "/home/mboyle/bd-local-wt/some-cut/**/*.py"}, CUT), id="glob-tool-absolute-pattern"),
    # lens r1 F1: POSIX normpath keeps a leading '//', so //home/mboyle compared unequal to /home/mboyle
    pytest.param(("Grep", {"pattern": "x", "path": "//home/mboyle"}, CUT),
                 ("Grep", {"pattern": "x", "path": "//home/mboyle/bd-local-wt/some-cut"}, CUT), id="grep-double-slash"),
    pytest.param(("Glob", {"pattern": "//home/mboyle/bd-persist/**/*.md"}, CUT),
                 ("Glob", {"pattern": "//home/mboyle/bd-persist/harness/*.sh"}, CUT), id="glob-double-slash"),
    # lens r1 N1: a relative Glob prefix can climb above path
    pytest.param(("Glob", {"pattern": "../../**/*.md"}, CUT),
                 ("Glob", {"pattern": "src/**/*.py"}, CUT), id="glob-relative-climb"),
]


@pytest.mark.parametrize("refused,allowed", PAIRS)
def test_t23_refuses_root_search_and_allows_its_bounded_twin(hook, refused, allowed):
    r = hook(*refused)
    assert r.returncode == 2, (refused, r.returncode, r.stderr)
    assert "T23" in r.stderr and "path to ONE cut or ONE subdir" in r.stderr, r.stderr
    a = hook(*allowed)
    assert a.returncode == 0 and a.stderr == "", (allowed, a.returncode, a.stderr)


def test_t23_refusal_is_logged(hook, tmp_path):
    assert hook("Grep", {"pattern": "x", "path": "/home/mboyle/bd-persist"}).returncode == 2
    assert "T23 Grep over /home/mboyle/bd-persist" in (tmp_path / "tripwire.log").read_text()


def test_odd_shapes_fail_open(hook):
    assert hook("Grep", {"pattern": "x", "path": 7}).returncode == 0
    assert hook("Grep", "not-a-dict").returncode == 0


def test_symlink_to_a_root_is_the_root(hook, tmp_path):
    root, other = tmp_path / "root", tmp_path / "other"
    root.mkdir(); other.mkdir()
    (tmp_path / "link").symlink_to(root)
    roots = {"BD_SEARCH_ROOTS": str(root)}
    r = hook("Grep", {"pattern": "x", "path": str(tmp_path / "link")}, CUT, **roots)
    assert r.returncode == 2 and "T23" in r.stderr, r.stderr
    assert hook("Grep", {"pattern": "x", "path": str(other)}, CUT, **roots).returncode == 0


def test_bash_double_slash_is_t22(hook):
    r = hook("Bash", {"command": "grep -r x //home/mboyle/bd-persist"})
    assert r.returncode == 2 and "T22" in r.stderr, r.stderr
    assert hook("Bash", {"command": "grep -r x //home/mboyle/bd-persist/harness"}).returncode == 0


def test_bash_search_is_still_t22(hook):
    r = hook("Bash", {"command": "grep -r x /home/mboyle"})
    assert r.returncode == 2 and "T22" in r.stderr and "T23" not in r.stderr, r.stderr


def test_nul_in_a_path_never_crashes_the_hook(hook):
    # B1 lens r2: os.path.realpath raises ValueError on an embedded NUL; a traceback (rc 1) is a non-blocking hook
    # error, so every other rule in the same call (here T1) was skipped. The path is judged on its normpath instead.
    r = hook("Bash", {"command": "git add . ; grep -r x /tmp/a\x00b"})
    assert r.returncode == 2 and "T1" in r.stderr and "Traceback" not in r.stderr, r.stderr
    r = hook("Grep", {"pattern": "x", "path": "/home/mboyle/a\x00b"}, CUT)
    assert r.returncode == 0 and "Traceback" not in r.stderr, r.stderr
    r = hook("Grep", {"pattern": "x", "path": "/home/mboyle/bd-persist/../a\x00b/.."}, CUT)
    assert r.returncode == 2 and "T23" in r.stderr and "Traceback" not in r.stderr, r.stderr


def test_selftest_passes():
    r = subprocess.run(["python3", CANDIDATE, "--selftest"], capture_output=True, text=True, check=False, timeout=60,
                       env=dict(os.environ, LC_ALL="C", BD_TRIPWIRE_LOG=os.devnull))
    assert r.returncode == 0 and "selftest OK" in r.stdout, (r.stdout, r.stderr)
