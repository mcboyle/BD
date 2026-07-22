"""v3.66.729 -- the body-contract gate gets a WORLD, and the UNKNOWNs get counted.

726 built the gate and it was honest: it claimed DEAD only for what it could prove
(a literal {} sent to an endpoint that demands a body) and reported everything else
as UNKNOWN. But UNKNOWN was two-thirds of the surface -- 66 call sites -- and an
endpoint we could not ask about is not an endpoint that passed. The gate was sound
and mostly blind.

It was blind for a reason it could not fix from where it stood: it replayed against
an EMPTY WORLD. Site id "_probe". task_id "x". A filename on no disk. So the endpoint
answered "unknown site_id" or "no such task", and the tool honestly said UNKNOWN. Its
own docstring named the fix: *judging these needs REAL FIXTURES, i.e. an integration
harness.*

This cut is that harness. tools/body_contract_fixtures.py stands up a real world -- a
site, real `queue` rows, real library/tags/history rows, a real file on disk, ids of
the RIGHT TYPE -- and tools/body_contract.probe_fixtures() replays into it.

    OK              35 -> 53      decisively accepted by the real endpoint
    UNKNOWN         83 -> 64      and every one of them now has a NAME
    HARNESS-FAULT    4 -> 0
    DEAD                   0      nothing survives proof

ALSO: ts_calls()/probe_typed() -- the entire type-directed differential probe, written
and documented at 726 -- were never called by main(). Dead code. That is where the "66
UNKNOWN" everyone quoted came from: a number produced by a capability nobody was running.

ON THE FIXTURES BEING HONEST -- this matters more than the coverage.
Building this produced 38 FALSE DEAD verdicts across FIVE mechanisms, each plausible
enough to have shipped as a bug report:

  1. (16) Trusted the literal parser's NO-BODY-ARG shape. It does not mean "sends no
     body" -- apiPost's payload is a REQUIRED positional, so a body-less call would not
     typecheck. It means "the regex could not parse a {...} literal", and it fires on
     shorthand props like `{ site_id, url }`, which DO send a body.
  2. (13) The differential rule A==B => DEAD collapses when OUR value is invalid.
     /api/queue/v2/cancel answers "unknown site_id" to BOTH {} (key missing) and to our
     body (key present, site not in queue-v2's store). Same string, opposite meanings.
  3.  --  Replayed 126 MUTATING calls against ONE shared world. apiDelete("/api/sites/
     ${}") fires and every later /api/sites/<sid>/* call 404s. The verdicts were a
     function of REPLAY ORDER. Hence ensure() before EVERY probe.
  4.  --  A type-correct, MEANING-WRONG fixture. `text` for /api/import/start is the
     operator's pasted URL LIST. Filling it with "fixture text" yields "no valid URLs",
     identical to {}, and the rule pronounced DEAD a control that was FIXED at 726.
  5.  (4) A stub runner that invents attributes: handing the app a function where it
     expects a dict produced 500s the product never had.

The guard that stops all five: a RESOURCE/VALUE complaint -- or an empty error message
-- can NEVER return DEAD. It returns FIXTURE-GAP, a named admission that our world is
too thin to judge that endpoint. A to-do list, not a verdict. And a 5xx is HARNESS-FAULT,
never a product finding.

A gate that cannot fail is not a gate: test_the_fixture_gate_can_actually_fail is the
one that earns the green.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The UNKNOWN ratchet. It may only ever go DOWN.
#
# 64 = 37 (the type checker cannot resolve the body -- structurally beyond fixtures)
#    +  9 (404: needs a bespoke domain fixture -- pending captcha, analyzer capture,
#          user_template, vpn tunnel)
#    +  9 (FIXTURE-GAP: a real draft template, an installed plugin, site accounts,
#          a cockpit capture task)
#    +  9 (body keys the value map does not cover)
#
# This number is a DEBT, not a target. Raising it requires the same scrutiny as
# raising a guard SHA: it means we asked about fewer controls than we used to.
# v3.66.743 -- RE-BASELINED 64 -> 129, and the reason is the denominator, not
# the controls. body_types.mjs ran with a RELATIVE ROOT, so tsconfig handed
# createProgram relative root-file names and the walk's `includes("/src/")`
# filter silently dropped every root file: the committed artifact held 126
# call sites of a real 253. Fixing ROOT to an absolute path doubled the
# scanned surface; OK rose 53 -> 115, DEAD stayed 0 (after fixing the one
# genuine dead control it exposed, /api/jsonapi/probe), HARNESS-FAULT stayed
# 0, and every new UNKNOWN is a previously-INVISIBLE call site now named. A
# ratchet measured against a blind scan was not a floor; this one is.
#
# v3.66.750 -- TIGHTENED 130 -> 129. The +1 tolerance covered a fixture-
# isolation flap whose documented cause (site-id collision) turned out to be
# wrong: the real channel was GLOBAL CONFIG. probe_fixtures snapshot/restored
# s_cfg but not _app_cfg, and a replayed settings probe left
# path_allowlist = ["x"] in the module-level dict -- so the second probe run
# in a process failed allowlist validation on its scratch download_dir and
# /api/captures/setup_site flapped OK -> 400/UNKNOWN (129 first run, 130
# after). Fixed by making the world INCLUDE global config: build() pins
# path_allowlist to the scratch home and snapshots _app_cfg; ensure()
# restores that baseline and drops probe-created sites; probe_fixtures
# snapshot/restores _app_cfg exactly as it always did s_cfg. Proven by
# test_verdicts_are_order_independent_across_probe_runs (double full probe,
# one process) -- do NOT raise this number to absorb a flap again; a ratchet
# that tolerates a known isolation bug has stopped being a ratchet.
#
# v3.66.755 (singleton-wiring): 129 -> 130. Wiring /api/queue/dead_letter/requeue
# added a mutating call site the probe now exercises. With a synthetic {site_id,url}
# it correctly 404s ("no dead-lettered job for that site_id/url") -- the fixture
# world contains a queued job (_queue_job) but no DEAD-LETTERED one, so the probe
# honestly returns UNKNOWN (a resource complaint can never be DEAD -- see the
# soundness guard). This is a real FIXTURE-GAP for a genuinely-wired control, not a
# broken control and not an isolation flap: the +1 is decisive and attributable to
# exactly this endpoint. Raising it by 1 with that attribution is the sanctioned
# response; seeding a dead-letter fixture whose url matches the probe's generated
# body would touch the probe's value-filling internals and is out of scope here.
UNKNOWN_BASELINE = 134  # v3.66.757: +1 for /cockpit/api/takeover/<sid>/input
# v3.66.789: +1 for /api/discovery/disco/run -- the A-DISCO operator run-now, a
# no-body action POST (apiPost(url, {})), the identical judgeability-neutral pattern
# already carried by the vpn empty-body actions (771) and cockpit input (757): an
# empty `{}` body has no keys to check, so the type checker correctly reports it
# UNKNOWN. Not a new unjudgeable SURFACE -- a known-benign shape.
# v3.66.771: +2 for /api/vpn/auto_blacklist and /api/vpn/tunnels/<id>/leak_test/run
# -- both are no-body action POSTs (apiPost(url, {})), the identical judgeability-
# neutral pattern already carried by the existing vpn empty-body actions
# (kill_switch/clear, providers/locations, providers/test_credentials, tunnels
# start/stop/cycle): an empty `{}` body has no keys to check, so the type checker
# correctly reports it UNKNOWN. Not a new unjudgeable SURFACE -- a known-benign shape.
# (MOD-1). The FE sends a dynamic CDP event dict (Record<string, unknown>), so the
# body type checker cannot resolve it and the replay probes UNKNOWN -- a real
# type/fixture gap (no `solving` captcha session + an unstructured body), NOT a
# broken control. Same shape as the 756 dead_letter/requeue entry (129 -> 130).


def _typed_calls():
    """Read the COMMITTED artifact. NEVER skip.

    The first version of this called ts_calls(), which shells out to node. The
    release zip has no frontend/node_modules, so in the band the extractor returned
    nothing and every test SKIPPED -- and a skip reads as green. This gate would have
    shipped blind, in the cut whose whole thesis is that a gate which cannot see is
    worse than no gate. The call sites are now a derived, committed artifact
    (tools/BODY_CONTRACT_CALLS.json), so the gate runs everywhere, always.
    """
    from tools import body_contract as bc

    calls = bc.load_calls(ROOT)
    assert calls, (
        "tools/BODY_CONTRACT_CALLS.json is empty or missing -- the gate cannot see "
        "the frontend. This is a FAILURE, not a skip: regenerate it with "
        "`python3 tools/body_contract.py --regen`.")
    return bc, calls


@pytest.fixture(scope="module")
def verdicts():
    bc, calls = _typed_calls()
    return bc.probe_fixtures(ROOT, calls)


def _by(verdicts, v):
    return [r for r in verdicts if r["verdict"] == v]


def test_no_control_sends_a_body_its_endpoint_refuses(verdicts):
    """THE GATE. A control that posts a body its endpoint rejects is a guaranteed
    4xx that every other ledger we own scores as WIRED. 724 and 726 each shipped one.

    With a real world underneath it, NOTHING survives proof -- which is the finding:
    the 724/726 remediation held, and no new one has crept in.
    """
    dead = _by(verdicts, "DEAD")
    assert not dead, (
        "controls whose endpoint REFUSES the body they send:\n"
        + "\n".join("  %s  (%s)  %s" % (r["path"], r["file"], r["why"])
                    for r in dead))


def test_the_app_never_5xxs_on_a_well_formed_request(verdicts):
    """A 500 is a robustness defect even when the fixture is odd. All four we saw
    were OURS (a stub runner inventing attributes); if one ever is not, it is real.
    """
    faults = _by(verdicts, "HARNESS-FAULT")
    assert not faults, (
        "the app 5xx'd on a well-formed request:\n"
        + "\n".join("  %s  %s" % (r["path"], r["why"]) for r in faults))


def test_unknown_only_ever_shrinks(verdicts):
    """THE RATCHET. UNKNOWN is not a pass -- it is the count of controls we could not
    ask about. It may go down. It may not go up: that would mean a new control landed
    on a surface we cannot judge, which is exactly how 724 got in.
    """
    unknown = _by(verdicts, "UNKNOWN")
    assert len(unknown) <= UNKNOWN_BASELINE, (
        "UNKNOWN rose to %d (baseline %d). New unjudgeable control(s):\n%s"
        % (len(unknown), UNKNOWN_BASELINE,
           "\n".join("  %s -- %s" % (r["path"], r["why"]) for r in unknown[:12])))


def test_the_fixture_world_is_actually_there(verdicts):
    """A harness that settles nothing is a harness that is not running.

    If the fixtures silently fail to build, EVERY call site degrades to UNKNOWN and
    the ratchet above still passes -- a green gate over a dead harness. So assert the
    world exists by its effect: a substantial number of controls must be decisively OK.
    """
    ok = _by(verdicts, "OK")
    assert len(ok) >= 45, (
        "only %d controls came back OK -- the fixture world is probably not building. "
        "A gate whose fixtures fail open reports clean and verifies nothing." % len(ok))


def test_the_fixture_gate_can_actually_fail():
    """A gate that can only say OK is not a gate.

    Inject a control that posts a keyless body to an endpoint demanding one, and
    confirm probe_fixtures calls it DEAD. Without this, every assertion above is
    satisfied by a detector that has quietly stopped detecting -- which is the exact
    class of bug this whole file exists to catch.
    """
    bc, _calls = _typed_calls()
    injected = [{
        "file": "tests/INJECTED.tsx",
        "fn": "apiPost",
        "path": "/api/sites/${}/jobs/bulk_delete",   # demands {urls: [...]}
        "sample": {},                                 # ...and we send nothing
        "keys": [],
        "unknownType": False,
    }]
    res = bc.probe_fixtures(ROOT, injected)
    assert res, "the injected control was not probed at all"
    assert res[0]["verdict"] == "DEAD", (
        "the gate did NOT flag a control posting {} to an endpoint that demands a "
        "body -- it has stopped detecting. verdict=%s why=%s"
        % (res[0]["verdict"], res[0]["why"]))


def test_template_onboard_probe_never_launches_a_capture(monkeypatch):
    """Contract probes are inert even when generated input asks to run."""
    from tools import body_contract as bc
    from tools import onboard_site_template

    def _unexpected_launch(*_args, **_kwargs):
        raise AssertionError("body-contract probe launched a real capture")

    monkeypatch.setattr(
        onboard_site_template, "run_capture_flow", _unexpected_launch
    )
    injected = [{
        "file": "tests/INJECTED.tsx",
        "fn": "apiPost",
        "path": "/api/sites/${}/template_onboard",
        "sample": {"run": True},
        "keys": ["run"],
        "unknownType": False,
    }]

    result = bc.probe_fixtures(ROOT, injected)

    assert result
    assert result[0]["verdict"] == "OK"


def test_a_resource_complaint_is_never_reported_as_dead():
    """THE SOUNDNESS GUARD, asserted directly.

    38 false DEADs came from judging a 400 whose message names a RESOURCE or VALUE.
    "unknown site_id" is the answer to a MISSING key AND to a present-but-unregistered
    one. If this guard ever weakens, the gate starts reporting our fixture poverty as
    product bugs -- confidently, and in bulk.
    """
    from tools import body_contract as bc

    for msg in ("unknown site_id", "no such task: 'abc'", "invalid draft filename",
                "account_index out of range", "no valid URLs", ""):
        assert (not msg.strip()
                or any(s in msg.lower() for s in bc._RESOURCEISH)), (
            "the resource/value guard no longer recognises %r -- a 400 saying this "
            "would now be reported as a DEAD control" % msg)


def test_the_committed_call_artifact_is_in_sync():
    """The artifact the gate reads must still describe the frontend that exists.

    Regenerating needs node; ENFORCING does not. When node is present we re-extract
    and compare, so a new mutating control cannot land without the artifact (and
    therefore the gate) noticing it. When node is absent this check -- and ONLY this
    check -- is skipped; the gate itself still runs against the committed artifact.
    """
    from tools import body_contract as bc

    fresh = bc.ts_calls(ROOT)
    if not fresh:
        pytest.skip("node/body_types.mjs unavailable -- cannot REGENERATE here "
                    "(the gate itself still ran against the committed artifact)")
    committed = bc.load_calls(ROOT)
    key = lambda c: (c["file"], c["fn"], c["path"])          # noqa: E731
    assert sorted(map(key, fresh)) == sorted(map(key, committed)), (
        "the frontend's mutating call sites changed but "
        "tools/BODY_CONTRACT_CALLS.json was not regenerated -- the gate is now "
        "judging a frontend that no longer exists. Run: "
        "python3 tools/body_contract.py --regen")


def test_verdicts_are_order_independent_across_probe_runs(verdicts):
    """v3.66.750 -- run the WHOLE probe a second time in the same process and
    demand the identical UNKNOWN set.

    The +1 flap this file's baseline used to tolerate was documented as a
    site-id collision. The real channel was GLOBAL CONFIG: probe_fixtures
    snapshot/restored s_cfg but not _app_cfg, and a config-mutating probe
    left path_allowlist poisoned (literally ["x"]) in the module dict.
    Run 2's scratch download_dir then failed allowlist validation, so
    /api/captures/setup_site flapped OK -> 400/UNKNOWN: 129 on the first
    run, 130 after. A verdict that depends on which run you are in is the
    same disease as a verdict that depends on replay order -- this test is
    the mechanical proof neither remains.
    """
    bc, calls = _typed_calls()
    second = bc.probe_fixtures(ROOT, calls)
    u1 = {r["probe"] for r in verdicts if r["verdict"] == "UNKNOWN"}
    u2 = {r["probe"] for r in second if r["verdict"] == "UNKNOWN"}
    assert u1 == u2, (
        "the probe's verdicts depend on which run of the process asked:\n"
        "  run2-only UNKNOWN: %s\n  run1-only UNKNOWN: %s\n"
        "state is leaking across probe runs (fixture isolation regression)"
        % (sorted(u2 - u1), sorted(u1 - u2)))


def test_ensure_resets_global_config_and_drops_probe_created_sites():
    """The two isolation contracts ensure() must honor, asserted directly:
    (1) a poisoned _app_cfg key (the real flap channel) is restored to the
    fixture baseline; (2) a site a probe created (setup_site's 8-hex id)
    is dropped, so no later differential sees a world another probe built."""
    import tempfile
    from tools import body_contract_fixtures as bcf
    import bulk_downloader.app as A

    saved_cfg = dict(A._app_cfg)
    saved = (dict(A.s_cfg), dict(A.s_meta), dict(A.runners))
    home = tempfile.mkdtemp(prefix="bd_fx_iso_")
    try:
        A.s_cfg.clear(); A.s_meta.clear(); A.runners.clear()
        fx = bcf.Fixtures(A, A.app.test_client(), home).build()
        # (1) poison global config the way a replayed settings probe does
        A._app_cfg["path_allowlist"] = ["x"]
        # (2) leave a probe-created site behind
        A.s_cfg["deadbeef"] = {"name": "leftover", "download_dir": home}
        A.s_meta["deadbeef"] = {"status": "idle"}
        fx.ensure()
        assert A._app_cfg.get("path_allowlist") != ["x"], (
            "ensure() left a probe-poisoned path_allowlist in place -- the "
            "exact channel that flapped setup_site OK->UNKNOWN across runs")
        assert "deadbeef" not in A.s_cfg and "deadbeef" not in A.s_meta, (
            "ensure() left a probe-created site behind; later verdicts "
            "would see a world an earlier probe built")
    finally:
        A._app_cfg.clear(); A._app_cfg.update(saved_cfg)
        A.s_cfg.clear(); A.s_cfg.update(saved[0])
        A.s_meta.clear(); A.s_meta.update(saved[1])
        A.runners.clear(); A.runners.update(saved[2])
