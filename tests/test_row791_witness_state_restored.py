"""Row 791: audit witnesses restore every process-global probe mutation."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]

# (tracked witness suite, import name for this test, the probe whose body mutates
# ambient state -- the seam this row is about).
_WITNESS_SUITES = (
    ("tools/audit/witnesses/cap01_witnesses.py", "row791_cap01_witnesses", "F-CAP01-01"),
    ("tools/audit/witnesses/run01_witnesses.py", "row791_run01_witnesses", "F-RUN01-02"),
)

_LEAKY_PROBE = '''\
import os, sys
from bulk_downloader import live_recorder as lr

RESULTS = [{"id": "ROW791-CONTROL", "ok": True, "detail": "deliberate leak"}]
os.environ["BD_HOME"] = "row791-control-home"
lr.is_available = lambda: True
sys.path.insert(0, "/row791-control-path")
'''


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _leaks_left_by(load):
    """Run ``load`` and NAME every ambient mutation it failed to restore.

    The helper always puts the three globals back, so a control that leaks on
    purpose cannot escape into the rest of the session.
    """
    from bulk_downloader import live_recorder as lr

    before_home = os.environ.get("BD_HOME")
    before_available = lr.is_available
    before_path = list(sys.path)
    try:
        loaded = load()
        leaks = []
        if os.environ.get("BD_HOME") != before_home:
            leaks.append("BD_HOME")
        if lr.is_available is not before_available:
            leaks.append("live_recorder.is_available")
        if sys.path != before_path:
            leaks.append("sys.path")
        return loaded, leaks
    finally:
        if before_home is None:
            os.environ.pop("BD_HOME", None)
        else:
            os.environ["BD_HOME"] = before_home
        lr.is_available = before_available
        sys.path[:] = before_path


def test_the_witness_suites_restore_the_ambient_process_state(monkeypatch):
    """The real witness decorators execute their probes while the module loads."""
    monkeypatch.setenv("BD_HOME", "row791-ambient-home")

    modules, leaks = _leaks_left_by(
        lambda: [
            _load_module(_REPO / relative, name)
            for relative, name, _ in _WITNESS_SUITES
        ]
    )

    for module, (relative, _, claim_id) in zip(modules, _WITNESS_SUITES):
        assert len(module.RESULTS) > 0, (
            f"precondition: {relative} recorded no witness results"
        )
        fired = [r for r in module.RESULTS if r["id"] == claim_id]
        assert len(fired) == 1, (
            f"precondition: {claim_id} ran {len(fired)} times in {relative}, "
            "expected exactly 1 -- the mutating probe is the seam under test"
        )
        # REGISTRATION IS NOT EXECUTION, and the count above cannot tell them
        # apart. w() calls the probe inside try/except and appends EXACTLY ONE
        # entry either way -- on failure `ok, detail = False, f"witness raised:
        # ..."` (cap01_witnesses.py, the w() decorator). So len(fired) == 1 holds
        # even for a probe emptied at its first line, and a mutant that guts the
        # body escapes a pure arity check. Assert the entry the probe ITSELF
        # produced.
        #
        # NOT `ok is True`: measured on the tracked suites, F-CAP01-01 and
        # F-RUN01-02 both record ok=False today, and legitimately so -- these are
        # findings whose live re-derivation currently comes back flipped, which is
        # the witness working. Binding this gate to the verdict would make it fail
        # the day a finding flips back, which is not what this row is about. What
        # the row is about is that the scoping did not cost the witness its RUN.
        detail = fired[0]["detail"]
        assert isinstance(detail, str) and detail and not detail.startswith(
            "witness raised:"
        ), (
            f"{claim_id} in {relative} REGISTERED WITHOUT RUNNING: its recorded "
            f"detail is {detail!r}. The witness must still re-derive its claim "
            "under the new scoping; w() records one entry whether the probe "
            "reports a finding or dies, so the entry's own detail is the only "
            "evidence that the probe body executed."
        )

    assert leaks == [], f"witness suites left ambient state mutated: {leaks}"


def test_the_leak_detector_names_every_mutation_a_leaking_probe_leaves(
    tmp_path, monkeypatch
):
    """Negative control: the same detector must SEE all three leaks, or it proves nothing."""
    monkeypatch.setenv("BD_HOME", "row791-ambient-home")
    probe = tmp_path / "row791_leaky_witness.py"
    probe.write_text(_LEAKY_PROBE, encoding="utf-8")

    module, leaks = _leaks_left_by(
        lambda: _load_module(probe, "row791_leaky_witness")
    )

    assert len(module.RESULTS) == 1, "precondition: the control probe did not run"
    assert leaks == ["BD_HOME", "live_recorder.is_available", "sys.path"], (
        f"the detector is blind to a deliberate leak; it saw only {leaks}"
    )
    assert os.environ["BD_HOME"] == "row791-ambient-home", (
        "the helper failed to restore BD_HOME after the control leaked"
    )
