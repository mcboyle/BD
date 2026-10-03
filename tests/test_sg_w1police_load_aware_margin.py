"""sg-w1police-load-aware-margin: the unfloored police margin follows the host.

`_w1_police` (tests/test_v3_66_1132_the_hunt_reaps_what_it_abandons.py)
compares every completed wait over _POLICE_ABSOLUTE_S against its recorded
baseline x _WARN_FACTOR. The nightly full suite (28k tests, xdist) stretched
"partial_coproc_setup_settles_every_acquired_owner/run" (baseline 2.1835s) to
8.50s and 24.87s while 41 idle and band-loaded samples stayed <= 1.46x
(sg-hunt-coproc-timing, D5): load, not code. PM 13(b): scale that margin by a
MEASURED contention factor, floored at 1.0 and capped at _CONTENTION_FACTOR.

The host is faked here -- os.getloadavg and os.cpu_count are patched -- so
every arm is deterministic on an idle or a loaded runner. The elapsed time is
handed to `_w1_police` directly: the change is inside it, and the autouse
boundary that feeds it real waits is unchanged.
"""

from __future__ import annotations

import os
import pathlib

import pytest

import test_v3_66_1132_the_hunt_reaps_what_it_abandons as hunt

BD_GATE_SCOPE = "module"

# The nightly's own site: 8.50s against 2.1835s is the 3.9x that went RED.
NIGHTLY_SITE = "partial_coproc_setup_settles_every_acquired_owner/run"
# The v3.66.1226 restated-baseline mutant's site: 6.8895s restated as 0.5s.
MUTANT_SITE = "a_slow_settlement_never_downgrades_a_decided_cancellation/wait"
# 20x of 0.7916s is 15.8s: over _POLICE_ABSOLUTE_S and under _WARN_FLOOR_S, so
# ONLY the unfloored check can judge it and only the cap can fail it.
CAP_SITE = "_w1_wait_for_gate/default"


def _host(monkeypatch, factor, cpus=8):
    monkeypatch.setattr(os, "cpu_count", lambda: cpus)
    monkeypatch.setattr(os, "getloadavg",
                        lambda: (factor * cpus, 0.0, 0.0))


def _police(site, elapsed):
    hunt._w1_police(hunt._Budget(30.0, site), elapsed)


def _measured(site):
    return hunt._MEASURED_S[site][0]


def _only_the_unfloored_check_judges(elapsed):
    assert hunt._POLICE_ABSOLUTE_S < elapsed < hunt._WARN_FLOOR_S, (
        "%.2fs is outside (%.0fs, %.0fs): the floored check would judge it "
        "too, so this arm would not isolate the margin"
        % (elapsed, hunt._POLICE_ABSOLUTE_S, hunt._WARN_FLOOR_S))


def test_a_contended_host_widens_the_margin_by_its_measured_factor(
        monkeypatch):
    """(a) RED on BASE: factor 4 lets the nightly's 3.9x wait through."""
    elapsed = _measured(NIGHTLY_SITE) * 3.9
    _only_the_unfloored_check_judges(elapsed)
    _host(monkeypatch, 4.0)
    _police(NIGHTLY_SITE, elapsed)

    # And factor 4 is a bound, not a pass: 12.1x still fails.
    with pytest.raises(AssertionError) as caught:
        _police(NIGHTLY_SITE, _measured(NIGHTLY_SITE) * 12.1)
    assert "host contention 4.00" in str(caught.value), caught.value


def test_an_idle_host_keeps_todays_exact_margin(monkeypatch):
    """The 1.0 floor: a load below the CPU count is not headroom."""
    measured = _measured(NIGHTLY_SITE)
    _only_the_unfloored_check_judges(measured * 3.1)
    _host(monkeypatch, 0.0)
    _police(NIGHTLY_SITE, measured * 2.9)
    with pytest.raises(AssertionError) as caught:
        _police(NIGHTLY_SITE, measured * 3.1)
    assert "past the 3.0x margin" in str(caught.value), caught.value
    assert "host contention 1.00" in str(caught.value), caught.value


def test_an_unreadable_load_average_is_never_permission(monkeypatch):
    """COULD NOT LOOK gives today's margin, not a wider one."""
    def unreadable():
        raise OSError("no load average")

    monkeypatch.setattr(os, "getloadavg", unreadable)
    measured = _measured(NIGHTLY_SITE)
    _police(NIGHTLY_SITE, measured * 2.9)
    with pytest.raises(AssertionError) as caught:
        _police(NIGHTLY_SITE, measured * 3.1)
    assert "load average unreadable" in str(caught.value), caught.value
    assert "past the 3.0x margin" in str(caught.value), caught.value


def test_the_restated_baseline_mutant_still_fails_on_an_idle_host(
        monkeypatch):
    """(b) NEGATIVE CONTROL, GREEN on BASE too: the 1226 mutant cannot escape.

    Restating 6.8895s as 0.5s makes a real 6.89s wait 13.8x its baseline. At
    idle the margin is 3x, so the policing fires by its own message; the true
    baseline passes the same wait, so the failure comes from nothing else.
    """
    real = hunt._MEASURED_S[MUTANT_SITE]
    elapsed = real[0]
    _only_the_unfloored_check_judges(elapsed)
    _host(monkeypatch, 1.0)
    _police(MUTANT_SITE, elapsed)

    monkeypatch.setitem(hunt._MEASURED_S, MUTANT_SITE, (0.5, real[1]))
    with pytest.raises(AssertionError) as caught:
        _police(MUTANT_SITE, elapsed)
    message = str(caught.value)
    assert "against a recorded baseline of 0.5000s" in message, message
    assert MUTANT_SITE in message, message


def test_the_cap_holds_however_loaded_the_host(monkeypatch):
    """(c) factor 50 is capped at _CONTENTION_FACTOR: a 20x wait still fails."""
    measured = _measured(CAP_SITE)
    _only_the_unfloored_check_judges(measured * 20)
    _host(monkeypatch, 50.0)
    with pytest.raises(AssertionError) as caught:
        _police(CAP_SITE, measured * 20)
    message = str(caught.value)
    assert "host contention %.2f" % hunt._CONTENTION_FACTOR in message, message
    assert "past the 18.0x margin" in message, message
    # The cap is the constant, not something tighter: 17.9x is inside 18x.
    _police(CAP_SITE, measured * 17.9)


def test_the_contention_probe_is_live(monkeypatch):
    """(d) POSITIVE CONTROL: the probe can report > 1, and reads the host."""
    # Unpatched, it reports THIS host's load1 / cpus, bracketed by two
    # independent reads of /proc/loadavg (the kernel updates it every 5s).
    loadavg = pathlib.Path("/proc/loadavg")

    def proc_load1():
        return float(loadavg.read_text().split()[0])

    before = proc_load1()
    raw, evidence = hunt._w1_host_contention()
    after = proc_load1()
    cpus = os.cpu_count()
    # /proc/loadavg prints two decimals; getloadavg() carries the unrounded
    # value, so the bracket is widened by one rounding step.
    low, high = min(before, after) - 0.01, max(before, after) + 0.01
    assert low / cpus <= raw <= high / cpus, (raw, before, after, cpus)
    assert "/ %d cpus" % cpus in evidence, evidence

    _host(monkeypatch, 3.0)
    raw, evidence = hunt._w1_host_contention()
    assert raw == 3.0, raw
    assert evidence == "load1 24.00 / 8 cpus", evidence
