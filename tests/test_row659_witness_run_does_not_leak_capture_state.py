"""ROW 659: the CAPTURE READ PATH survives an in-process witness run.

SCOPE -- READ THIS BEFORE TRUSTING THIS FILE.  This gate pins EXACTLY TWO
process-global resources, and only those two:

  1. `bulk_downloader.capture_bodies.bodies_enabled` (the module attribute), and
  2. the `BD_CAPTURE_BODIES` environment seed,

across BOTH in-process seams that execute a witness suite
(`tools/witness_drift.py::snapshot` and `tools/run_witnesses.py`).  It does NOT
assert the general contract "a witness restores everything it mutates".  Three
other unrestored mutations survive this gate by construction and are OWED to a
follow-up row, not covered here -- see OWED TO THE INTEGRATOR in this cut's
DONE.md for their file:line and measurements:
  * cap01_witnesses.py:135 `os.environ["BD_HOME"] = tempfile.mkdtemp()` (_w6)
  * cap01_witnesses.py:138 `lr.is_available = lambda: True` (_w6)
  * run01_witnesses.py:64  `sys.path.insert(0, WORK)` (_w2)
Do not read a green run here as "the witness suites are clean".

WHY THESE TWO.  `load_suite()` EXECUTES each shipped witness inside the calling
interpreter, and `tools/audit/witnesses/cap01_witnesses.py::_w7` forces the
body-retain path by rebinding `bodies_enabled` and seeding `BD_CAPTURE_BODIES`.
Both mutations outlived the witness, so any later test in the same worker read a
capture posture the witness chose, not the one the store holds.  That is the
order dependence row 659 reports: the full suite runs
tests/test_v3_66_1192_live_path_defaults_are_portable.py (which snapshots the
witnesses) before tests/test_v3_66_308_capture_parity.py, and
`test_bodies_enabled_store_over_env` then fails `assert True is False`.

The gate asserts what the READ PATH COMPUTES after a witness run, not that any
particular restore line exists.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

# repo-wide: the population this gate judges is EVERY witness suite shipped in
# the tree (tools/audit/witnesses/*_witnesses.py, enumerated by
# discover_suites), and the hazard it pins -- process-global state left in a
# pytest worker -- reaches any lane that executes one. Not a module property.
BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]

# The witness that mutates the capture read path.  Its presence in the snapshot
# results is the PRECONDITION that the hazard actually ran in this process.
_MUTATING_WITNESS_ID = "F-CAP01-manifest-gap"
_CAPTURE_ENV_SEED = "BD_CAPTURE_BODIES"

# Captured at import, BEFORE any witness in this process can rebind it.
from bulk_downloader import capture_bodies as _CB  # noqa: E402
_ORIGINAL_BODIES_ENABLED = _CB.bodies_enabled
_AMBIENT_ENV_SEED = os.environ.get(_CAPTURE_ENV_SEED)


def _load_run_witnesses():
    path = _REPO / "tools" / "run_witnesses.py"
    spec = importlib.util.spec_from_file_location("run_witnesses_row659", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_witness_drift():
    path = _REPO / "tools" / "witness_drift.py"
    spec = importlib.util.spec_from_file_location("witness_drift_row659", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_every_shipped_witness(tmp_path: Path) -> dict:
    """Run the real snapshot and prove the hazard-bearing witness executed."""
    witness_drift = _load_witness_drift()
    logdir = tmp_path / "witness-logs"
    rc = witness_drift.snapshot("row659", root=_REPO, logdir=logdir)
    assert rc == 0, f"precondition: snapshot refused (rc={rc})"
    payload = json.loads(
        (logdir / "WITNESS_LOG_vrow659.json").read_text(encoding="utf-8"))
    fired = payload["results"]
    assert len(fired) > 0, (
        "precondition: the witness denominator is empty, so nothing ran and a "
        "green verdict here would be manufactured")
    assert _MUTATING_WITNESS_ID in fired, (
        "precondition: the witness that rebinds the capture read path did not "
        f"run -- {_MUTATING_WITNESS_ID} absent from {len(fired)} results, so "
        "this gate is not judging the hazard row 659 names")
    # The restore must not neuter the probe: F-CAP01-manifest-gap is a
    # finding-repro, so ok=True means the documented gap still reproduces.
    # If a restore had run too early the witness would flip to False and
    # witness_drift.diff would report a phantom "fixed" finding.
    assert fired[_MUTATING_WITNESS_ID]["ok"] is True, (
        f"{_MUTATING_WITNESS_ID} reported "
        f"{fired[_MUTATING_WITNESS_ID]['ok']!r}: the witness no longer drives "
        "the retain path it exists to probe")
    return fired


@pytest.fixture()
def _restore_capture_state():
    """Never let THIS gate become the leak it is measuring."""
    from bulk_downloader import capture_bodies as CB
    saved_fn = CB.bodies_enabled
    saved_env = os.environ.get(_CAPTURE_ENV_SEED)
    try:
        yield
    finally:
        CB.bodies_enabled = saved_fn
        if saved_env is None:
            os.environ.pop(_CAPTURE_ENV_SEED, None)
        else:
            os.environ[_CAPTURE_ENV_SEED] = saved_env


def test_witness_run_leaves_the_store_authoritative_over_the_env_seed(
    tmp_path, _restore_capture_state, monkeypatch,
):
    """The exact symptom row 659 reports, reproduced without a 6-minute suite.

    After the witnesses run, a store that says OFF must still beat an env seed
    that says ON.  This asserts the value the read path COMPUTES from the store
    on disk -- no literal this patch writes.
    """
    from bulk_downloader import capture_bodies as CB
    from bulk_downloader import global_config as GC

    fired = _run_every_shipped_witness(tmp_path)

    workspace = tmp_path / "store"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    os.environ[_CAPTURE_ENV_SEED] = "1"

    for stored in (True, False):
        Path("app_config.json").write_text(
            json.dumps({"capture_bodies": stored}), encoding="utf-8")
        GC._cached = None
        GC._cached_mtime = 0.0
        assert CB.bodies_enabled() is stored, (
            f"after {len(fired)} witnesses ran in this process, "
            f"capture_bodies.bodies_enabled() returned {CB.bodies_enabled()!r} "
            f"for a store that says {stored!r} with {_CAPTURE_ENV_SEED}=1 -- "
            "a witness left the read path rebound")


def test_witness_run_restores_the_capture_read_path_it_rebinds(
    tmp_path, _restore_capture_state,
):
    """The seam itself: the module attribute must be the one we started with."""
    from bulk_downloader import capture_bodies as CB

    before = CB.bodies_enabled
    fired = _run_every_shipped_witness(tmp_path)
    after = CB.bodies_enabled

    assert after is before, (
        f"after {len(fired)} witnesses ran, "
        f"bulk_downloader.capture_bodies.bodies_enabled is {after!r}, not the "
        f"{before!r} this process imported")


def test_witness_run_restores_the_capture_env_seed_it_sets(
    tmp_path, _restore_capture_state, monkeypatch,
):
    """`os.environ.setdefault` is still a mutation when the key was absent."""
    monkeypatch.delenv(_CAPTURE_ENV_SEED, raising=False)

    fired = _run_every_shipped_witness(tmp_path)

    assert _CAPTURE_ENV_SEED not in os.environ, (
        f"after {len(fired)} witnesses ran, {_CAPTURE_ENV_SEED} is "
        f"{os.environ[_CAPTURE_ENV_SEED]!r} in a process that had it unset")


def test_the_operator_witness_runner_leaves_the_capture_read_path_intact(
    _restore_capture_state, capsys,
):
    """SEAM 2: tools/run_witnesses.py::main executes the same suites in-process.

    `witness_drift.snapshot` and `run_witnesses.main` both reach _w7 through
    the SAME `load_suite()`, so a restore that only satisfied the snapshot path
    would still leak here.  Drive the operator-facing runner directly.
    """
    from bulk_downloader import capture_bodies as CB

    runner = _load_run_witnesses()
    suites = runner.discover_suites(str(_REPO))
    assert len(suites) > 0, "precondition: no witness suite is shipped"
    entries = [e for s in suites for e in runner.load_suite(s)]
    assert len(entries) > 0, (
        "precondition: the runner produced an empty witness population, so a "
        "green verdict here would be manufactured")
    ids = {e.get("id") if isinstance(e, dict) else e[0] for e in entries}
    assert _MUTATING_WITNESS_ID in ids, (
        f"precondition: {_MUTATING_WITNESS_ID} did not run through the "
        f"operator runner -- {len(entries)} entries, so the hazard is absent")

    assert CB.bodies_enabled is _ORIGINAL_BODIES_ENABLED, (
        f"after {len(entries)} witnesses ran through tools/run_witnesses.py, "
        f"capture_bodies.bodies_enabled is {CB.bodies_enabled!r}")
    assert _CAPTURE_ENV_SEED not in os.environ or (
        os.environ[_CAPTURE_ENV_SEED] == _AMBIENT_ENV_SEED), (
        f"tools/run_witnesses.py left {_CAPTURE_ENV_SEED}="
        f"{os.environ.get(_CAPTURE_ENV_SEED)!r}, not the ambient "
        f"{_AMBIENT_ENV_SEED!r}")


def test_witness_run_gives_back_an_env_seed_the_operator_had_already_set(
    tmp_path, _restore_capture_state, monkeypatch,
):
    """The OTHER arm of the restore branch: a pre-existing value is handed back.

    `pop` is the arm a process with the seed unset takes.  A process that
    already had BD_CAPTURE_BODIES set takes the assignment arm instead, and a
    restore that only popped would DELETE an operator's value.  Zero-entropy
    fixture value, not a secret.
    """
    ambient = "0"  # zero-entropy fixture value, deliberately not "1"
    monkeypatch.setenv(_CAPTURE_ENV_SEED, ambient)

    fired = _run_every_shipped_witness(tmp_path)

    assert os.environ.get(_CAPTURE_ENV_SEED) == ambient, (
        f"after {len(fired)} witnesses ran, {_CAPTURE_ENV_SEED} is "
        f"{os.environ.get(_CAPTURE_ENV_SEED)!r}, not the {ambient!r} this "
        "process had set before the run")


def test_transform_control_imports_the_witness_runner_without_driving_it():
    """TRANSFORM CONTROL -- this node MUST ESCAPE every row-659 mutant.

    It loads the witness runner and proves the suite is discoverable, but it
    never executes a witness, so no restoration behaviour is judged here.  If a
    mutant of the restore seam is CAUGHT by this node, the catch was a compile
    or import break, not an assertion, and the other CAUGHTs are worthless.
    """
    witness_drift = _load_witness_drift()
    assert callable(witness_drift.snapshot)
    suites = witness_drift.discover_suites(str(_REPO))
    assert len(suites) > 0, "precondition: no witness suite is shipped"
