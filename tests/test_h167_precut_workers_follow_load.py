"""H167: precut's underived xdist worker count is derived from the box's load.

MEASURED 2026-09-07 (bd-f-f2, test5, 48 cores): the marker population passes
standalone at -n 16 in 881 s, and three precut runs of it on the shared box
(load 30-85) each timed out 1-3 subprocess gates. -n 16 was min(16, cpu_count)
-- a figure that never looks at the load. Acceptance (a): the worker count is
derived from os.getloadavg, so the per-gate subprocess budgets hold.

The box is faked: cpu_count and getloadavg are patched, the argv is read back.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
PRECUT = Path(__file__).resolve().parents[1] / "toolchain" / "bin" / "bd-precut"


@pytest.fixture(scope="module")
def mod():
    loader = SourceFileLoader("bd_precut_h167", str(PRECUT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, PRECUT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workers(mod, monkeypatch, cpus, load1):
    monkeypatch.setattr(mod.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(mod.os, "cpu_count", lambda: cpus)
    if isinstance(load1, BaseException):
        def _raise():
            raise load1
        monkeypatch.setattr(mod.os, "getloadavg", _raise)
    else:
        monkeypatch.setattr(mod.os, "getloadavg", lambda: (load1, load1, load1))
    argv = mod._underived_parallelism()
    assert argv[argv.index("--dist") + 1] == "loadfile", argv
    return int(argv[argv.index("-n") + 1])


def test_a_quiet_box_keeps_the_cap(mod, monkeypatch):
    # positive control: nothing else running -> the pre-H167 figure, unchanged
    assert _workers(mod, monkeypatch, 48, 0.0) == mod._UNDERIVED_WORKERS_CAP


def test_the_measured_loads_shrink_the_worker_count(mod, monkeypatch):
    # the measured 48-core box: at load 40 only 8 cores are idle; at 85 none are
    assert _workers(mod, monkeypatch, 48, 40.0) == 8
    assert _workers(mod, monkeypatch, 48, 85.0) == 1


def test_load_plus_workers_never_oversubscribes_while_cores_are_idle(mod, monkeypatch):
    for load in (0.0, 12.5, 30.0, 32.0, 47.0):
        workers = _workers(mod, monkeypatch, 48, load)
        assert 1 <= workers <= mod._UNDERIVED_WORKERS_CAP
        assert load + workers <= 48, (load, workers)


def test_a_small_box_is_still_capped_by_its_cores(mod, monkeypatch):
    assert _workers(mod, monkeypatch, 4, 0.0) == 4


def test_the_derivation_is_printed_naming_the_load(mod, monkeypatch, capsys):
    _workers(mod, monkeypatch, 48, 40.0)
    err = capsys.readouterr().err
    assert "workers=8" in err and "load1=40.00" in err and "H167" in err, err


def test_an_unsampleable_load_is_named_not_silent(mod, monkeypatch, capsys):
    workers = _workers(mod, monkeypatch, 48, OSError("no /proc/loadavg"))
    assert workers == mod._UNDERIVED_WORKERS_CAP // 2  # unknown is not an idle box
    assert "load1=UNSAMPLED" in capsys.readouterr().err


def test_the_live_box_yields_a_valid_count(mod):
    # no patching: the real sample on whatever host runs this
    if importlib.util.find_spec("xdist") is None:
        pytest.skip("xdist absent: precut runs the underived gates serially")
    argv = mod._underived_parallelism()
    workers = int(argv[argv.index("-n") + 1])
    assert 1 <= workers <= min(mod._UNDERIVED_WORKERS_CAP, os.cpu_count() or 1)
