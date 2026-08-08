"""v3.66.940 -- the `.env` loader applied every key it found, not the declared set.

THE ASYMMETRY. `bulk_downloader/app_envfile_editor.py` allow-lists what it will
WRITE: "Writable keys are allow-listed to _envfile.EDITOR_KEYS -- the endpoint
can't be used [to write anything else]". The READER had no such restriction.
`load_envfile` walked `parse_envfile(text).items()` and `os.environ`-seeded
every pair, whatever the key was.

MEASURED at v3.66.939, with a `.env` at `$BD_ENVFILE` and a fresh interpreter
importing `bulk_downloader`:

    BD_PORT              = '6000'                          (an editor key)
    PATH                 = '/tmp/hijack'                   NOT an editor key
    LD_PRELOAD           = '/tmp/evil.so'                  NOT an editor key
    PYTHONPATH           = '/tmp/inject'                   NOT an editor key
    HTTPS_PROXY          = 'http://attacker.invalid:8080'  NOT an editor key
    BD_SECRETS_FILE      = '/tmp/fake_vault.json'          NOT an editor key
    TOTALLY_ARBITRARY    = '1'                             NOT an editor key

All seven applied. `LD_PRELOAD` and `PYTHONPATH` change what code the process
loads, and they were being set from a file at import time, before any other
import in the package runs.

PRECISION ABOUT `PATH`, because the first draft of this docstring overstated
it. The seed is `setdefault`, so a variable the service ALREADY has is never
overwritten -- and `PATH` is essentially always set, which is why the
subprocess test below finds `LD_PRELOAD`, `PYTHONPATH`, `HTTPS_PROXY` and an
arbitrary key leaking but NOT `PATH`. The measurement above popped `PATH` by
hand, which is not the ordinary case. The exposure is real for the many
variables a service does not normally carry; for `PATH` it needs a unit file
with a cleared environment. Stating both halves rather than the scarier one.

WHY THAT FILE IS REACHABLE. `_candidate_paths()` tries `$BD_ENVFILE`, then
`cwd/.env`, then `~/BulkDownloader/.env`. On the deploy host the systemd unit
sets `WorkingDirectory=APP_DIR` and the install directory IS `~/BulkDownloader`,
so the last two are the same path -- and that path is the GUI env-editor's
persistence target. No test fixture chdir reaches it, which is why nothing in
the suite noticed.

THE FIX IS THE SET THAT ALREADY EXISTS. `EDITOR_KEY_NAMES` is the canonical
editable set, kept honest by test_v3_66_504_envfile_editor, which re-derives
`_DEPLOY_ONLY` from source and asserts the two match -- so a new deploy var
cannot silently escape the editor, and now cannot silently escape this gate
either. Introducing a SECOND hardcoded list here would be the denominator drift
CLAUDE.md section 8 warns about, so the allow-list is asserted to BE that set
rather than to resemble it.

A SKIPPED KEY IS REPORTED, NOT DROPPED IN SILENCE. An operator who hand-adds a
line to `.env` and gets no effect and no message has to read source to find out
why. Silence is the shape section 0 is about, even when the silent behaviour is
the safe one.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from bulk_downloader import _envfile as EF  # noqa: E402

# Zero-entropy stand-ins, per CLAUDE.md section 7: a corpus value in a test that
# asserts about credentials or hijack paths must be an obvious repeat, never a
# realistic-looking string. The obvious "improvement" is to make these look
# real; do not.
_ROGUE = {
    "PATH": "/tmp/" + "x" * 8,
    "LD_PRELOAD": "/tmp/" + "y" * 8 + ".so",
    "PYTHONPATH": "/tmp/" + "z" * 8,
    "HTTPS_PROXY": "http://" + "h" * 8 + ".invalid:8080",
    "TOTALLY_ARBITRARY": "1",
}


def _write_env(pairs: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / ".env"
    p.write_text("".join(f"{k}={v}\n" for k, v in pairs.items()), encoding="utf-8")
    return p


@pytest.fixture
def clean_env(monkeypatch):
    """Pop every key under test.

    CLAUDE.md section 0: a harness that varies an environment variable must POP
    it, not merely refrain from setting it -- the parent's value is part of the
    denominator, and `setdefault` semantics mean an inherited value would make
    every assertion below pass for the wrong reason.
    """
    keys = list(_ROGUE) + list(EF.EDITOR_KEY_NAMES) + ["BD_ENVFILE"]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch

    # @945: POPPING ON ENTRY IS NECESSARY AND NOT SUFFICIENT. The code under
    # test writes `os.environ[k] = v` directly, and monkeypatch can only undo
    # what it RECORDED -- a key it deleted while already absent records nothing,
    # so `undo()` leaves the writer's value in place. That leaked
    # BD_INSTALL_DIR="v3" (index 3 of EDITOR_KEY_NAMES) into the whole session
    # and produced register item 34's four failures four files away.
    #
    # This teardown runs BEFORE monkeypatch's own undo -- monkeypatch is set up
    # first as a dependency, so it tears down last -- which is what makes the
    # order correct: we remove the writer's values, then undo restores whatever
    # the environment genuinely had.
    for k in keys:
        os.environ.pop(k, None)


# ── the defect ───────────────────────────────────────────────────────────────

def test_a_key_outside_the_declared_set_is_not_seeded(clean_env):
    """RED on pristine: all five rogue keys reached os.environ."""
    envpath = _write_env({**_ROGUE, "BD_PORT": "6000"})
    clean_env.setenv("BD_ENVFILE", str(envpath))
    EF.load_envfile()

    leaked = {k: os.environ.get(k) for k in _ROGUE if k in os.environ}
    assert not leaked, (
        f"load_envfile seeded key(s) that are not in the declared editable set: "
        f"{sorted(leaked)}. PATH, LD_PRELOAD and PYTHONPATH change what code "
        f"the process loads, and this runs at import before anything else in "
        f"the package.")


def test_the_declared_keys_are_still_seeded(clean_env):
    """The regression guard. Over-narrowing is as bad as not narrowing."""
    envpath = _write_env({"BD_PORT": "6000", "BD_HOST": "127.0.0.1"})
    clean_env.setenv("BD_ENVFILE", str(envpath))
    applied = EF.load_envfile()
    assert os.environ.get("BD_PORT") == "6000", "an editor key was not seeded"
    assert os.environ.get("BD_HOST") == "127.0.0.1", "an editor key was not seeded"
    assert applied == 2, f"expected 2 keys applied, got {applied}"


def test_every_declared_key_can_be_seeded(clean_env):
    """All of them, not a sample.

    A fix that allow-listed a handful would pass the test above and quietly
    break the rest of the editor's surface.
    """
    pairs = {k: f"v{i}" for i, k in enumerate(EF.EDITOR_KEY_NAMES)}
    envpath = _write_env(pairs)
    clean_env.setenv("BD_ENVFILE", str(envpath))
    EF.load_envfile()
    missing = [k for k in pairs if os.environ.get(k) != pairs[k]]
    assert not missing, (
        f"declared editable key(s) that load_envfile refused to seed: {missing}")


def test_real_env_still_wins_over_the_file(clean_env):
    """setdefault semantics, unchanged. The systemd unit must outrank `.env`."""
    envpath = _write_env({"BD_PORT": "6000"})
    clean_env.setenv("BD_ENVFILE", str(envpath))
    clean_env.setenv("BD_PORT", "5555")
    EF.load_envfile()
    assert os.environ.get("BD_PORT") == "5555", (
        "the `.env` overrode the real environment; the unit file must win")


def test_a_skipped_key_is_reported(clean_env, capsys):
    """Silence is the section 0 shape even when the silent behaviour is safe."""
    envpath = _write_env({**_ROGUE, "BD_PORT": "6000"})
    clean_env.setenv("BD_ENVFILE", str(envpath))
    EF.load_envfile()
    err = capsys.readouterr().err
    for k in _ROGUE:
        assert k in err, (
            f"{k} was silently dropped from the `.env`. An operator who adds a "
            f"line and gets neither effect nor message has to read source to "
            f"find out why. stderr was {err!r}")


def test_nothing_is_reported_when_the_file_is_clean(clean_env, capsys):
    """Over-sensitivity is a soundness bug too: a warning on every ordinary
    boot gets tuned out, and then the one that matters is invisible."""
    envpath = _write_env({"BD_PORT": "6000", "BD_HOST": "127.0.0.1"})
    clean_env.setenv("BD_ENVFILE", str(envpath))
    EF.load_envfile()
    assert capsys.readouterr().err.strip() == "", (
        "load_envfile warned about a `.env` containing only declared keys")


# ── the allow-list must be the existing set, not a second copy ───────────────

def test_the_allow_list_is_exactly_the_editor_key_set():
    """Two lists of the same thing drift, and the copy nobody reads is the one
    that rots (CLAUDE.md section 8). test_v3_66_504_envfile_editor already
    re-derives EDITOR_KEY_NAMES from source, so binding to it inherits that
    guarantee instead of restating it."""
    allowed = getattr(EF, "SEEDABLE_KEYS", None)
    assert allowed is not None, (
        "no SEEDABLE_KEYS on _envfile -- the allow-list must be a named, "
        "inspectable object, not an expression buried in load_envfile")
    assert set(allowed) == set(EF.EDITOR_KEY_NAMES), (
        f"the seed allow-list and the editor key set disagree: "
        f"only-in-allow-list={sorted(set(allowed) - set(EF.EDITOR_KEY_NAMES))}, "
        f"only-in-editor={sorted(set(EF.EDITOR_KEY_NAMES) - set(allowed))}")


def test_the_allow_list_is_not_empty():
    """Every assertion above is vacuous over an empty allow-list -- and an
    empty one would also disable the loader entirely while every 'rogue key is
    not seeded' test went green."""
    assert EF.EDITOR_KEY_NAMES, "the editor key set is empty"
    assert getattr(EF, "SEEDABLE_KEYS", ()), "the seed allow-list is empty"


# ── the real path: package import ────────────────────────────────────────────

def test_importing_the_package_does_not_seed_a_rogue_key():
    """End-to-end, in a fresh interpreter, through the import that actually
    runs it.

    `bulk_downloader/__init__.py:31` calls load_envfile() at module scope, so
    the in-process tests above exercise the function while THIS one exercises
    the path an operator is actually on. A subprocess because the seeding
    already happened in this interpreter -- CLAUDE.md section 0's rule that a
    harness must control the ambient state it is testing, not inherit it.
    """
    envpath = _write_env({**_ROGUE, "BD_PORT": "6000"})

    # env= IS THE POINT, and omitting it is the trap CLAUDE.md section 0 names:
    # a subprocess harness that inherits os.environ cannot test what the child's
    # environment contains, because the parent's value answers first. The first
    # draft of this test omitted it and "failed" by reporting the RUNNER's PATH
    # as a leak -- a true-looking red for the wrong reason.
    #
    # PATH is kept rather than popped: an interpreter launched with no PATH is a
    # different experiment. The assertion is therefore that PATH is not the
    # rogue VALUE, which is the actual question, instead of that PATH is absent.
    child = dict(os.environ)
    for k in list(_ROGUE) + ["BD_PORT"]:
        child.pop(k, None)
    child["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    child["BD_ENVFILE"] = str(envpath)
    child["BD_DISABLE_KEEPALIVE"] = "1"

    probe = (
        "import os, sys, json\n"
        "sys.path.insert(0, %r)\n"
        "import bulk_downloader\n"
        "print(json.dumps({k: os.environ.get(k) for k in %r}))\n"
        % (str(_REPO), list(_ROGUE) + ["BD_PORT"])
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                       text=True, cwd=str(_REPO), timeout=180, env=child)
    assert r.returncode == 0, f"probe failed: {r.stdout}\n{r.stderr}"
    import json
    seen = json.loads(r.stdout.strip().splitlines()[-1])

    leaked = {k: v for k, v in seen.items()
              if k in _ROGUE and v is not None and v == _ROGUE[k]}
    assert not leaked, (
        f"importing bulk_downloader seeded rogue key(s) from a `.env`: "
        f"{sorted(leaked)}. This is the live path -- on the deploy host that "
        f"file is the GUI env-editor's target inside the install directory.")
    assert seen.get("BD_PORT") == "6000", (
        "the declared key was not seeded through the real import path, so the "
        "fix over-narrowed and this test would have passed vacuously")
