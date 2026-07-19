"""U41 — L24 (service-starts-on-boot) + L25 (restart-on-crash) + L27
(survives-logout), the systemd lifecycle live tests.

These are grouped disruptive=True and meant to run as one flagged
batch. Critically, they do NOT reboot the box, kill the process, or
log anyone out — a true end-to-end check of each is an explicit
operator action. They verify the systemd CONFIGURATION and OBSERVABLE
STATE that make those behaviours work, and degrade to WARN where
there is no systemd bus (this sandbox) — never a crash, never a
spurious FAIL.

T56 (v3.63.3): the assertions used to branch on a two-way
`_env.HAS_BULKDOWNLOADER_UNIT` flag, which conflated the
"sandbox / no bus" case with the "bus exists but no unit installed
yet" case. On a freshly-rsync'd deployment host where the test
suite runs BEFORE install_service.sh has registered the unit,
that second case is real — and L25/L27 used to mis-FAIL on it
because `systemctl show <missing> -p Restart` returns the systemd
default `'no'` and `systemctl is-active <missing>` returns
`'inactive'`. T56 fixed the checks to gate on `_unit_exists`
first; this file's assertions are rewritten to a three-branch
truth table covering all three environments.
"""
import os
import sys

import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h

# tests/ is not a package; add this file's dir to sys.path so the
# sibling _env helper imports cleanly under both pytest and the
# custom run_tests.py runner.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402


_LEVELS = (h.PASS, h.WARN, h.FAIL)


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── registration ───────────────────────────────────────────────────

def test_l24_l25_l27_registered():
    ids = {t.id for t in h.registry()}
    assert {"L24", "L25", "L27"} <= ids


def test_systemd_lifecycle_tests_are_disruptive():
    # grouped disruptive so a default run never includes them
    for tid in ("L24", "L25", "L27"):
        assert _get_test(tid).disruptive is True


# ── graceful degradation — environment truth table ─────────────────
#
# Three environments matter, in order of strictness:
#
#   (a) NO SYSTEMD BUS — the sandbox or any container without
#       --privileged. _env.HAS_SYSTEMD_BUS is False. Every helper
#       degrades, every check returns WARN.
#
#   (b) BUS EXISTS BUT NO UNIT — a fresh deployment host before
#       install_service.sh has run, or any normal Linux box that
#       isn't this app's deployment. _env.HAS_SYSTEMD_BUS is True,
#       _env.HAS_BULKDOWNLOADER_UNIT is False. T56 made this case
#       behave the same as (a) from the live-check perspective
#       (everything WARNs) — but the underlying helpers differ:
#       _systemctl returns ok=True (it ran), while _svc_property
#       returns None (T56 gates it on _unit_exists).
#
#   (c) FULL DEPLOYMENT — bus + unit. PASS / FAIL outcomes for the
#       checks (FAIL is a real config problem worth surfacing); the
#       helpers return real values.
#
# The old assertions collapsed (a) and (b) into one branch and got
# stash wrong during step [2] of the fresh-machine capture.

def _ctx():
    return h.Context("http://localhost:1", "/tmp", disruptive=True)


def test_l24_environment_branched_outcome():
    level, detail = _get_test("L24").fn(_ctx())
    if _env.HAS_BULKDOWNLOADER_UNIT:
        # PASS if enabled (the normal stash state), FAIL if installed
        # but disabled (a real deploy issue worth surfacing).
        assert level in (h.PASS, h.FAIL)
    else:
        # Both (a) no-bus and (b) bus-no-unit -> WARN per the live
        # test contract: "we can't tell, not a crash".
        assert level == h.WARN


def test_l25_environment_branched_outcome():
    level, detail = _get_test("L25").fn(_ctx())
    if _env.HAS_BULKDOWNLOADER_UNIT:
        # the unit may legitimately have Restart=on-failure (PASS) or
        # Restart=no (FAIL — a real deploy issue worth surfacing)
        assert level in (h.PASS, h.FAIL)
    else:
        # (a) and (b) -> WARN. T56 fix: _svc_property returns None
        # for a missing unit, so L25 short-circuits to WARN here
        # instead of mis-FAILing on the default Restart=no.
        assert level == h.WARN, (
            f"L25 returned {level} ({detail}) — T56 contract "
            "violation. _env.HAS_BULKDOWNLOADER_UNIT is False so "
            "L25 must WARN, not FAIL. Likely cause: _svc_property "
            "is no longer gating on _unit_exists.")


def test_l27_environment_branched_outcome():
    level, detail = _get_test("L27").fn(_ctx())
    if _env.HAS_BULKDOWNLOADER_UNIT:
        # PASS if active+system-managed; FAIL if inactive (real
        # deploy issue); WARN if it is a user-unit
        assert level in (h.PASS, h.WARN, h.FAIL)
    else:
        # (a) and (b) -> WARN. T56 fix: L27 now gates on
        # _unit_exists BEFORE reading is-active, so a missing unit
        # short-circuits to WARN instead of mis-FAILing on the
        # 'inactive' systemd returns for missing units.
        assert level == h.WARN, (
            f"L27 returned {level} ({detail}) — T56 contract "
            "violation. _env.HAS_BULKDOWNLOADER_UNIT is False so "
            "L27 must WARN, not FAIL. Likely cause: L27's "
            "_unit_exists gate is missing or after the is-active "
            "read instead of before.")


def test_all_three_return_valid_tuples():
    for tid in ("L24", "L25", "L27"):
        res = _get_test(tid).fn(_ctx())
        assert isinstance(res, tuple) and len(res) == 2
        assert res[0] in _LEVELS
        assert isinstance(res[1], str) and res[1]


# ── _systemctl + _svc_property + _unit_exists helpers ──────────────

def test_systemctl_helper_three_environment_outcomes():
    """The _systemctl helper says "did systemctl run and reach the
    bus" — NOT "does the unit exist". The three environments:

      (a) no bus       -> ok=False (helper degraded)
      (b) bus, no unit -> ok=True  (helper ran; out reflects the
                          unit's nonexistence, e.g. "inactive" for
                          is-active or rc=4 with no stdout)
      (c) bus + unit   -> ok=True, out has the real value
    """
    ok, out, err = checks._systemctl(["is-active", "bulkdownloader"])
    assert isinstance(err, str)
    if _env.HAS_BULKDOWNLOADER_UNIT:
        assert ok is True
        assert out  # "active" / "inactive" / etc.
    elif _env.HAS_SYSTEMD_BUS:
        # (b) — bus exists but no unit. The helper says ok=True
        # because systemctl ran successfully; the test in this
        # branch must not assume "no unit" implies "ok=False".
        assert ok is True
        # `is-active` against a missing unit may print "inactive"
        # (with rc=3) or nothing at all depending on systemd
        # version; both are valid for the helper's contract.
        assert isinstance(out, str)
    else:
        # (a) — no bus on this host.
        assert ok is False


def test_svc_property_three_environment_outcomes():
    """_svc_property returns:
      (a) no bus       -> None (helper can't run)
      (b) bus, no unit -> None (T56 gates on _unit_exists)
      (c) bus + unit   -> the real value (e.g. 'on-failure')
    """
    val = checks._svc_property("bulkdownloader", "Restart")
    if _env.HAS_BULKDOWNLOADER_UNIT:
        assert val is not None
        assert isinstance(val, str)
    else:
        # Both (a) and (b) — T56 added the gate so the answer is
        # the same in both cases.
        assert val is None, (
            f"_svc_property returned {val!r} — T56 contract "
            "violation. With no unit installed, the helper must "
            "return None; previously it returned the systemd "
            "default (e.g. 'no' for Restart) which the L25 check "
            "then mis-FAIL'd on.")


def test_unit_exists_helper_exposed():
    """T56 added _unit_exists. The other tests rely on it as the
    primary 'does the unit exist' probe; check it's there."""
    assert callable(getattr(checks, "_unit_exists", None)), (
        "_unit_exists helper missing — T56 unit not wired")
    result = checks._unit_exists("bulkdownloader")
    assert isinstance(result, bool)
    # And the answer must match the cached env fact.
    assert result == _env.HAS_BULKDOWNLOADER_UNIT


def test_unit_exists_returns_false_for_obvious_nonexistent():
    """Regardless of bus / no-bus, a unit name that obviously
    can't exist must return False."""
    assert checks._unit_exists(
        "definitely-not-a-real-unit-T56-test") is False


# ── the tests do NOT perform destructive actions ───────────────────

def test_lifecycle_tests_do_not_reboot_or_kill(monkeypatch):
    # guard: none of L24/L25/L27 should invoke reboot/kill/poweroff.
    # We stub subprocess.run to record every command and assert none
    # is destructive.
    import subprocess as _sp
    seen = []
    real_run = _sp.run

    def _spy(cmd, *a, **k):
        seen.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(_sp, "run", _spy)
    for tid in ("L24", "L25", "L27"):
        _get_test(tid).fn(_ctx())
    flat = " ".join(
        " ".join(c) if isinstance(c, (list, tuple)) else str(c)
        for c in seen).lower()
    for danger in ("reboot", "poweroff", "shutdown", "kill", "stop"):
        assert danger not in flat, f"a lifecycle test ran {danger!r}"


# ── harness integration ────────────────────────────────────────────

def test_lifecycle_tests_skipped_without_disruptive_flag(tmp_path):
    rdir = tmp_path / "r"
    h.run_all("http://localhost:1", str(tmp_path), results_dir=rdir)
    for tid in ("L24", "L25", "L27"):
        assert not (rdir / f"{tid}.log").exists()


def test_lifecycle_tests_environment_branched_run(tmp_path):
    """T56: when no unit is installed, L24/L25/L27 must all WARN,
    giving exit 0. When the unit IS installed, the outcome depends
    on the unit's configuration and may include a FAIL (which is
    the right answer — a misconfigured real unit IS a deploy
    issue worth surfacing). Either way, the artifacts must exist.
    """
    rdir = tmp_path / "r"
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L24", "L25", "L27"], results_dir=rdir)
    for tid in ("L24", "L25", "L27"):
        assert (rdir / f"{tid}.log").is_file()
    if _env.HAS_BULKDOWNLOADER_UNIT:
        # Real unit installed; could be PASS-only (exit 0) or
        # include a FAIL on a misconfig (exit 1). Both are valid
        # outcomes — the test exists to confirm the artifacts
        # land, not to legislate the unit's config.
        assert code in (0, 1)
    else:
        # No unit -> every check WARNs -> exit 0.
        assert code == 0, (
            f"with no unit installed, L24/L25/L27 must all WARN "
            f"and run_all() must exit 0; got {code}. Likely cause: "
            "a check mis-FAILed on a missing unit (T56 regression).")
