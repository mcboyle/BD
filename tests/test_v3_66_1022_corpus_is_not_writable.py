"""@1022. A test wrote into the TRACKED corpus, and it failed a box capture.

`tests/test_v3_66_13_phase2_p10_bdctl_dd_diff.py` mutated
`tests/fixtures/deep_detect/01_hls_master/meta.json` in place -- a tracked file
-- and restored it in a `finally:`. `tests/test_v3_66_13_phase2_p2_snapshot_
replay.py` re-reads that same `meta.json` from disk on every call, resolving
each snapshot URL against its `base_url`. Both files are in the capture's
PARALLEL lane, so on the box's 88-worker run any read landing inside the
perturb-to-restore window saw a base_url nobody expected:

    - "url": "https://cdn.example.test/stream/1080p.m3u8"
    + "url": "https://different.example.test/1080p.m3u8"

That is the capture at 213fa81, and the victim passes alone in 0.37s -- it
correctly detected the corpus changing underneath it.

CONCURRENCY-DEPENDENT, NOT ORDER-DEPENDENT. Running polluter-then-victim in one
process passes, because the `finally:` restores before the victim starts. It
takes genuine overlap, which is why it surfaces on the box and essentially
never in a container, and why no amount of re-running proves it fixed.

THE `finally:` IS ALSO A RESIDUE HAZARD, and that half is worse. A worker
killed mid-window -- timeout, SIGKILL, an orchestrator reaping a slow job --
never runs it, and the tracked file stays mutated on disk. The next run then
fails deterministically, on a dirty tree, for a reason that looks nothing like
its cause. CLAUDE.md section 6 records exactly this shape for bd-mutate.

WHY A HASH CHECK ALONE WOULD NOT BE A TEST. Measured: every meta.json
round-trips byte-identically through `json.dumps(indent=2) + "\\n"`, and a full
pristine P10 run leaves the corpus hash IDENTICAL. So "the corpus is unchanged
after the suite" is GREEN on pristine source in the happy path -- it catches
only the crash case. The discriminating assertion is where the WRITE lands, and
that is what the tests below assert.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_TRACKED = REPO / "tests" / "fixtures" / "deep_detect"
_P10 = REPO / "tests" / "test_v3_66_13_phase2_p10_bdctl_dd_diff.py"
_REPLAY = REPO / "tools" / "dd-replay.py"


def _load_replay():
    """dd-replay, loaded the way bdctl loads it: fresh, by path.

    bdctl.py does spec_from_file_location + exec_module on EVERY dd-diff
    invocation, so the module global is re-evaluated each time. That is why an
    env var reaches it and a monkeypatch of `replay.CORPUS_DIR` does not -- the
    test never holds the instance bdctl builds mid-call.
    """
    spec = importlib.util.spec_from_file_location("dd_replay_probe", _REPLAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _corpus_sha() -> str:
    h = hashlib.sha256()
    for p in sorted(_TRACKED.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(_TRACKED).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


# ── the override exists and points where it is told ───────────────

def test_the_env_override_reaches_the_fixture_enumeration(tmp_path, monkeypatch):
    """RED on pristine: dd-replay hardwired CORPUS_DIR, so a copy could not be
    pointed at and the only writable corpus was the tracked one."""
    dst = tmp_path / "deep_detect"
    shutil.copytree(_TRACKED, dst)
    (dst / "99_sentinel").mkdir()
    (dst / "99_sentinel" / "meta.json").write_text(
        json.dumps({"base_url": "https://sentinel.example.test/"}) + "\n")
    monkeypatch.setenv("DD_REPLAY_CORPUS", str(dst))

    replay = _load_replay()
    assert replay.CORPUS_DIR == dst, (
        "dd-replay ignored DD_REPLAY_CORPUS: %r" % (replay.CORPUS_DIR,))
    names = [d.name for d in replay._list_fixtures(None)]
    assert "99_sentinel" in names, (
        "the enumeration did not follow the override; it listed %r" % names)


def test_the_default_is_the_tracked_corpus_when_the_var_is_POPPED(monkeypatch):
    """POPPED, not merely unset by this test. CLAUDE.md section 0: a harness
    that inherits the parent's environment cannot test the absence of a
    variable -- and the P10 fixture sets this one for every test in its file."""
    monkeypatch.delenv("DD_REPLAY_CORPUS", raising=False)
    replay = _load_replay()
    assert replay.CORPUS_DIR == _TRACKED, replay.CORPUS_DIR
    assert len(replay._list_fixtures(None)) >= 5


def test_an_EMPTY_value_falls_back_instead_of_resolving_to_dot(monkeypatch):
    """`os.environ.get(k) or default`, never `os.environ.get(k, default)`: an
    exported-but-empty var would otherwise make the corpus Path("") -> the
    process CWD, and the enumeration would silently find nothing."""
    monkeypatch.setenv("DD_REPLAY_CORPUS", "")
    replay = _load_replay()
    assert replay.CORPUS_DIR == _TRACKED, (
        "an empty DD_REPLAY_CORPUS resolved to %r instead of falling back"
        % (replay.CORPUS_DIR,))


# ── the write lands on the copy, not on the tracked corpus ────────

def _p10_module():
    spec = importlib.util.spec_from_file_location("p10_probe", _P10)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_perturb_helper_writes_to_the_COPY_not_the_tracked_corpus(
        tmp_path, monkeypatch):
    """THE DEFECT, asserted where it actually is: the destination of the write.

    A "corpus unchanged after the suite" check would pass on pristine source,
    because the finally: restores. This calls the helper and does NOT restore,
    then asks which file moved.
    """
    dst = tmp_path / "deep_detect"
    shutil.copytree(_TRACKED, dst)
    monkeypatch.setenv("DD_REPLAY_CORPUS", str(dst))

    before = _corpus_sha()
    p10 = _p10_module()
    p10._perturb_first_fixture("https://written-by-a-test.example/")

    assert _corpus_sha() == before, (
        "the perturb helper wrote into the TRACKED corpus. That is the race "
        "that failed the capture at 213fa81, and -- because the restore is a "
        "finally: -- it is also what a SIGKILLed worker leaves behind on disk")

    moved = json.loads((sorted(dst.iterdir())[0] / "meta.json").read_text())
    assert moved["base_url"] == "https://written-by-a-test.example/", (
        "nothing was written to the copy either, so this test proved nothing "
        "about where the write goes: %r" % moved)


def test_no_test_in_the_p10_file_can_reach_the_tracked_corpus(tmp_path):
    """The autouse fixture is the protection, so assert it is autouse.

    A future test in that file must not have to remember to ask for the copy --
    the tracked corpus being writable at all is the defect, not any one call
    site forgetting.
    """
    import ast
    tree = ast.parse(_P10.read_text(encoding="utf-8"))
    fix = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_corpus_copy":
            fix = n
    assert fix is not None, "the corpus-copy fixture is gone"
    decos = [ast.unparse(d) for d in fix.decorator_list]
    assert any("autouse=True" in d for d in decos), (
        "the corpus-copy fixture is no longer autouse, so a new test in that "
        "file would write the tracked corpus again: %r" % decos)


def test_the_tracked_corpus_is_clean_right_now():
    """Backstop for the crash case the hash check DOES cover: if a previous
    run died mid-window, this fails loudly instead of the next reader
    diffing against a URL nobody wrote."""
    import subprocess
    out = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "--",
         "tests/fixtures/deep_detect"],
        capture_output=True, text=True).stdout.strip()
    assert out == "", (
        "the tracked deep_detect corpus is modified on disk -- a run died "
        "inside a perturb window and its finally: never fired:\n%s" % out)
