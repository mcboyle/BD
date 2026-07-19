"""A9 -- supervised-autonomy controller (v3.66.481) -- the capstone.

The operator sets policy ONCE (drift tolerance, resolution floor, content rules,
the trusted-auto host set); the controller runs the A1->A2/A3/A5/A7/A8 loop
unattended for trusted-auto hosts, surfacing ONLY trust-boundary exceptions.

Load-bearing, built + tested FIRST (per the roadmap): the MASTER OFF-SWITCH and
full reversibility. The off-switch is an instant revert-to-manual that DOMINATES
every other toggle -- when engaged the controller is inert no matter what else
is on. Every action is recorded to an audit trail with a revert handle.

Posture: ``controller`` toggle is DEFAULT OFF and NOT keystone-required -- it
ORCHESTRATES already-gated entries (auto_promote / self_heal stay individually
keystone-gated downstream; defense in depth). Boundary-crossing cases (new API
host, first-time host, below the resolution floor, a content-rule hit) are
SURFACED for manual confirm, never auto-acted.

Zero-arg + injected fakes; runs under run_tests.py AND pytest.
"""
import contextlib

from bulk_downloader import automation_controller as ctl
from bulk_downloader import lifecycle_automation as la


@contextlib.contextmanager
def _arm(controller_on=True, off_switch=False):
    """Arm/disarm the controller toggle AND set the master off-switch
    independently."""
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    orig_off = ctl._read_off_switch
    on_keys = {la.AUTOMATION_TOGGLES["controller"]} if controller_on else set()
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: False
    ctl._read_off_switch = lambda: off_switch
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone
        ctl._read_off_switch = orig_off


def _policy(**over):
    p = {"drift_tolerance": 0.3, "resolution_floor": 720,
         "trusted_auto": {"vixen"}, "content_rules": []}
    p.update(over)
    return p


# ── LOAD-BEARING: the master off-switch dominates everything ──────────────────

def test_off_switch_dominates_even_when_armed_and_trusted():
    """The single most important invariant: an engaged off-switch makes the
    controller inert even with the toggle ON, a trusted host, and clean work."""
    ran = []
    steps = {"self_heal": lambda host, ctx: ran.append("self_heal")}
    with _arm(controller_on=True, off_switch=True):
        out = ctl.run_host_cycle("vixen", {}, _policy(), steps=steps)
    assert out["inert"] is True, out
    assert out["reason"] == "master_off_switch_engaged", out
    assert ran == [], ran  # NOTHING ran


def test_controller_armed_requires_toggle_and_clear_off_switch():
    with _arm(controller_on=True, off_switch=False):
        assert ctl.controller_armed() is True
    with _arm(controller_on=True, off_switch=True):
        assert ctl.controller_armed() is False  # off-switch wins
    with _arm(controller_on=False, off_switch=False):
        assert ctl.controller_armed() is False  # toggle off


def test_disarmed_controller_is_noop():
    ran = []
    with _arm(controller_on=False, off_switch=False):
        out = ctl.run_host_cycle("vixen", {}, _policy(),
                                 steps={"x": lambda h, c: ran.append("x")})
    assert out.get("skipped") or out.get("inert"), out
    assert ran == [], ran


def test_off_switch_toggle_registered():
    assert "controller" in la.AUTOMATION_TOGGLES, la.AUTOMATION_TOGGLES
    assert la.AUTOMATION_TOGGLES["controller"] == "automation.controller_enabled"
    assert "controller" not in la.KEYSTONE_REQUIRED


# ── trusted-auto gating ──────────────────────────────────────────────────────

def test_is_trusted_auto():
    assert ctl.is_trusted_auto("vixen", _policy()) is True
    assert ctl.is_trusted_auto("unknown", _policy()) is False


def test_non_trusted_host_is_surfaced_never_acted():
    ran = []
    with _arm():
        out = ctl.run_host_cycle("stranger", {}, _policy(),
                                 steps={"self_heal": lambda h, c: ran.append("x")})
    assert out["surfaced"] is True, out
    assert "not_trusted_auto" in out["reasons"], out
    assert ran == [], ran


# ── trust-boundary classification ────────────────────────────────────────────

def test_classify_boundary_flags_new_api_host():
    r = ctl.classify_boundary("vixen", {"new_api_host": "api.new.example"}, _policy())
    assert any("new_api_host" in x for x in r), r


def test_classify_boundary_flags_first_time_host():
    r = ctl.classify_boundary("vixen", {"first_time": True}, _policy())
    assert any("first_time" in x for x in r), r


def test_classify_boundary_flags_below_resolution_floor():
    r = ctl.classify_boundary("vixen", {"resolution": 480}, _policy(resolution_floor=720))
    assert any("resolution_floor" in x for x in r), r


def test_classify_boundary_clean_candidate():
    r = ctl.classify_boundary("vixen", {"resolution": 1080}, _policy())
    assert r == [], r


# ── the loop: clean vs boundary ──────────────────────────────────────────────

def test_armed_trusted_clean_runs_loop_and_audits():
    ran = []
    steps = {
        "self_heal":   lambda host, ctx: ran.append(("self_heal", host)) or {"ok": True},
        "auto_promote": lambda host, ctx: ran.append(("auto_promote", host)) or {"ok": True},
    }
    with _arm():
        out = ctl.run_host_cycle("vixen", {"candidate": {"resolution": 1080}},
                                 _policy(), steps=steps)
    assert out["ran"] is True and out.get("surfaced") is not True, out
    assert ("self_heal", "vixen") in ran and ("auto_promote", "vixen") in ran, ran
    # Audit records every step WITH a revert handle (reversibility).
    assert len(out["audit"]) == 2, out
    assert all("revert" in e for e in out["audit"]), out["audit"]


def test_armed_trusted_boundary_surfaces_without_acting():
    ran = []
    steps = {"self_heal": lambda host, ctx: ran.append("self_heal")}
    with _arm():
        out = ctl.run_host_cycle(
            "vixen", {"candidate": {"new_api_host": "api.new.example"}},
            _policy(), steps=steps)
    assert out["surfaced"] is True, out
    assert any("new_api_host" in r for r in out["reasons"]), out
    assert ran == [], ran  # boundary -> NO mutating step ran


def test_step_exception_is_isolated_and_audited():
    steps = {
        "self_heal":    lambda host, ctx: (_ for _ in ()).throw(RuntimeError("boom")),
        "auto_promote": lambda host, ctx: {"ok": True},
    }
    with _arm():
        out = ctl.run_host_cycle("vixen", {"candidate": {"resolution": 1080}},
                                 _policy(), steps=steps)
    assert out["ran"] is True, out
    # The throwing step is recorded as an error, the next step still ran.
    errs = [e for e in out["audit"] if e.get("error")]
    assert errs and any(e["step"] == "self_heal" for e in errs), out["audit"]
    assert any(e["step"] == "auto_promote" and not e.get("error")
               for e in out["audit"]), out["audit"]


def test_off_switch_checked_even_mid_policy_eval():
    # Engaging the off-switch with a non-trusted host still yields inert (the
    # off-switch is checked BEFORE trusted/boundary classification).
    with _arm(controller_on=True, off_switch=True):
        out = ctl.run_host_cycle("stranger", {}, _policy(), steps={})
    assert out["inert"] is True, out
