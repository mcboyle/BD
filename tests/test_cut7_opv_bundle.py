"""Cut 7 (7.3) — OPV evidence automation: auto-bundle + auto-triage, human
confirms.

`tools/opv_bundle.py` stages the evidence for an OPV item and attaches a
*triage signal* (green/amber/red) derived from DETERMINISTIC signals only
(non-200, drift flag, missing redaction marker, baseline diff). Tiered by
verifiability:
  * Tier A (CLI / read-only) -> fully auto-bundled + triaged
  * Tier B (noVNC / GUI)     -> stage evidence + instruct the human
  * Tier C (phone / device)  -> instruct only

THE INVARIANT (structural, non-negotiable): the tool NEVER emits a verdict. No
code path writes an OPV PASS/FAIL sign-off — that field is human-only, because a
verdict needs real-world ground truth a model cannot observe. A guard test
asserts no verdict-shaped key ever appears in the output.

RED on pristine 376: `tools/opv_bundle.py` does not exist.
"""

_VERDICT_KEYS = {"verdict", "passed", "pass", "fail", "failed", "signoff",
                 "sign_off", "approved", "certified"}


class FakeOpvClient:
    """Returns scripted report payloads for the curl/report pulls."""

    def __init__(self, *, healthy=True):
        self.calls = []
        self._healthy = healthy

    def probe(self, name):
        self.calls.append(("probe", name))
        return {"status": 200 if self._healthy else 503}

    def report(self, name):
        self.calls.append(("report", name))
        if self._healthy:
            return {"status": 200, "drift": False, "redaction_marker": True,
                    "baseline_diff": False}
        return {"status": 200, "drift": True, "redaction_marker": False,
                "baseline_diff": True}


def _assert_no_verdict(obj):
    """Recursively assert no verdict-shaped key appears anywhere."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k.lower() not in _VERDICT_KEYS, f"verdict key leaked: {k}"
            _assert_no_verdict(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _assert_no_verdict(v)


def test_tier_a_is_fully_bundled_and_triaged_green_on_clean_evidence():
    from tools import opv_bundle as ob
    c = FakeOpvClient(healthy=True)
    out = ob.assemble("OPV-BASE", client=c)
    assert out["tier"] == "A"
    assert out["triage"] == "green"
    assert out["evidence"]  # something was staged
    _assert_no_verdict(out)


def test_tier_a_triages_red_on_bad_signals():
    from tools import opv_bundle as ob
    c = FakeOpvClient(healthy=False)
    out = ob.assemble("OPV-F4.3", client=c)
    assert out["tier"] == "A"
    assert out["triage"] in ("amber", "red")
    _assert_no_verdict(out)


def test_invariant_never_emits_a_verdict_across_all_items():
    from tools import opv_bundle as ob
    for healthy in (True, False):
        c = FakeOpvClient(healthy=healthy)
        for item in ob.OPV_TIERS:
            out = ob.assemble(item, client=c)
            # triage is a color, never a pass/fail sign-off
            assert out["triage"] in ("green", "amber", "red", "n/a")
            _assert_no_verdict(out)


def test_tier_b_stages_and_instructs():
    from tools import opv_bundle as ob
    c = FakeOpvClient(healthy=True)
    out = ob.assemble("OPV-PICK", client=c)
    assert out["tier"] == "B"
    assert out["instructions"]            # human is told what to click
    _assert_no_verdict(out)


def test_tier_c_is_instruct_only():
    from tools import opv_bundle as ob
    c = FakeOpvClient(healthy=True)
    out = ob.assemble("OPV-F4.1", client=c)
    assert out["tier"] == "C"
    assert out["instructions"]
    assert out["triage"] == "n/a"         # nothing to triage from a device flow
    _assert_no_verdict(out)


def test_unknown_item_raises():
    from tools import opv_bundle as ob
    c = FakeOpvClient()
    try:
        ob.assemble("OPV-NOPE", client=c)
        assert False, "expected an error for an unknown OPV item"
    except (KeyError, ValueError):
        pass
