"""v3.66.726 -- the second dead control, the shadow route, and a gate so neither recurs.

724 found a control that called the RIGHT endpoint with a body it REJECTS: "Delete ALL
jobs" posted {} to an endpoint demanding {urls: [...]}. It failed 100% of the time and
EVERY GATE WE OWNED SCORED IT AS WIRED, because every gate asked a question that could not
see it:

    endpoint_reachability : "does a control reach this endpoint?"    -> yes
    bd-fe-dead-control    : "does this control reach anything?"      -> yes
    test_gui_parity       : "is the route literal in the FE source?" -> yes

Nobody asked DOES THE BODY SATISFY THE CONTRACT.

Asking it turned up a SECOND one. `MoreActions.tsx` renders a "Start import" button armed
with nothing but a site id, and posts {} to /api/import/start/<sid>. That endpoint reads
its URLs from the body (`{text: "url1\\nurl2"}`) or a file upload; with {} it resolves to
urls=[] and answers 400 "no valid URLs". EVERY TIME. It has never worked.

This cut:
  1. FIXES it -- the Imports card now collects the URL list and sends {text}.
  2. REMOVES /api/sites/<sid>/bulk_reorder -- a SHADOW of jobs/reorder (which the
     SortableQueueGroup already uses). Called by nothing but its own route. A second path
     to the same job is not reachability, it is debt.
  3. GATES the class: tools/body_contract.py replays the body each control ACTUALLY SENDS
     against the real app, and DEAD must stay 0.

ON THE GATE'S HONESTY -- this matters more than its coverage:
    It took five attempts to build, and the first four each reported confident nonsense
    (7, then 99, then 36 false positives) for the SAME reason every time: the denominator
    did not contain the question. It now claims DEAD only for what it can prove -- a
    LITERAL {} sent to an endpoint that demands a body -- and reports everything else it
    cannot judge as UNKNOWN (~141 call sites, whose bodies are typed variables a regex
    cannot see into). UNKNOWN IS NOT A PASS. A gate that reported those as OK would be the
    exact bug it exists to catch.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _code(rel):
    """Source with COMMENTS STRIPPED.

    Three times this session a test that grepped for a route literal was satisfied -- or
    broken -- by a PROSE COMMENT mentioning that route rather than by a call to it. A
    comment naming an endpoint is not a control calling it, and a comment explaining a
    REMOVAL is not the thing still being there. Strip them, then assert.
    """
    src = _read(rel)
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)   # tsx {/* ... */}
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)          # /* ... */
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)           # // ...
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)            # python #
    return src


# ── 1. the second dead control ─────────────────────────────────────────────
def test_import_start_sends_the_urls_it_needs():
    """It posted {} and got 400 'no valid URLs' every time. The endpoint takes {text}."""
    src = _code("frontend/src/routes/MoreActions.tsx")
    ix = src.find("/api/import/start/")
    assert ix != -1, "import/start is not called at all"
    window = src[max(0, ix - 400):ix + 300]
    assert "text" in window, (
        "the Start import control still sends no urls -- /api/import/start/<sid> reads "
        "{text} (or a file) and answers 400 'no valid URLs' on an empty body")


def test_import_start_has_an_input_to_collect_urls():
    """A control cannot send urls it never gathered. The button armed on a site id alone;
    there was no field on the page that could have supplied the list."""
    src = _code("frontend/src/routes/MoreActions.tsx")
    assert "importText" in src, (
        "no input collects the URL list, so the control has nothing to send")


# ── 2. the shadow route ────────────────────────────────────────────────────
def test_bulk_reorder_route_is_gone():
    """A SHADOW of jobs/reorder. Two endpoints doing one job, and the frontend uses the
    other one. Removing the surface; the runner method stays (it is part of the runner
    contract and pinned by test_v3_49_phase2)."""
    src = _code("bulk_downloader/app_sites_queue.py")
    assert "/bulk_reorder" not in src, "the shadow bulk_reorder route is still registered"


def test_the_surviving_reorder_endpoint_still_exists():
    """Removing the shadow must not take the real one with it."""
    src = _code("bulk_downloader/app_sites_queue.py")
    assert "/api/sites/<sid>/jobs/reorder" in src


def test_bulk_reorder_is_not_routable():
    from bulk_downloader.app import app

    rules = {str(r) for r in app.url_map.iter_rules()}
    assert "/api/sites/<sid>/bulk_reorder" not in rules
    assert "/api/sites/<sid>/jobs/reorder" in rules, "the real reorder endpoint vanished"


# ── 3. the gate ────────────────────────────────────────────────────────────
def test_no_control_sends_a_body_its_endpoint_refuses():
    """THE GATE. Replays the body each control ACTUALLY SENDS against the real app.

    DEAD = a control that sends a literal {} to an endpoint that demands a body. There is
    no value it could have supplied; the call as written can never succeed. Two of these
    shipped (724's bulk_delete, 726's import/start) and every other gate called them wired.

    Runs the detector in-process rather than shelling out, so it works in the harness.
    """
    from tools import body_contract as bc

    calls = bc.fe_calls(ROOT)
    assert calls, "the frontend scan found no mutating call sites -- the gate is blind"
    res = bc.probe(ROOT, calls)
    dead = [r for r in res if r["verdict"] == "DEAD"]
    assert not dead, (
        "controls whose endpoint REFUSES the body they send:\n"
        + "\n".join(f"  {r['path']}  ({r['file']})  {r['why']}" for r in dead))


def test_the_gate_can_actually_fail():
    """A gate that can only say OK is not a gate. Inject a control that posts {} to an
    endpoint demanding a body, and confirm the detector calls it DEAD.

    (The first four versions of this detector reported 7, then 99, then 36 false DEADs.
    A negative control is the only thing that separates 'strict' from 'broken'.)
    """
    from tools import body_contract as bc

    forged = [{
        "file": "forged.tsx",
        "fn": "apiPost",
        "path": "/api/import/start/${sid}",
        "keys": [],
        "shape": "{}",
    }]
    res = bc.probe(ROOT, forged)
    assert res and res[0]["verdict"] == "DEAD", (
        "the detector did not flag a control that posts {} to an endpoint requiring a "
        "body -- it cannot see the bug it exists for")


def test_unknown_is_reported_not_laundered_into_ok():
    """The gate cannot see into a body passed as a typed variable. It must SAY SO. If it
    ever reports those as OK, it has become the failure it was built to detect."""
    from tools import body_contract as bc

    res = bc.probe(ROOT, bc.fe_calls(ROOT))
    verdicts = {r["verdict"] for r in res}
    assert "UNKNOWN" in verdicts, (
        "no call site is UNKNOWN -- the detector is claiming certainty it does not have")


def test_the_gate_does_not_dirty_the_tree_it_inspects():
    """The probe BOOTS the real app, and booting it writes runtime state
    (plugins/plugins.json, notify_apprise.json, cockpit_tasks/operator_state.json) to
    CWD-relative paths. Probing from inside the work tree therefore DIRTIED THE SOURCE
    TREE, and bd-cut packaged the droppings into the release zip. It did exactly that at
    726, before this was fixed.

    A gate that corrupts the tree it inspects is not a gate. It now probes from a scratch
    CWD. Pin it: run the detector, then assert the tree is untouched."""
    from tools import body_contract as bc

    droppings = ["plugins/plugins.json", "notify_apprise.json",
                 "cockpit_tasks/operator_state.json"]
    before = {p: os.path.exists(os.path.join(ROOT, p)) for p in droppings}
    bc.probe(ROOT, bc.fe_calls(ROOT))
    after = {p: os.path.exists(os.path.join(ROOT, p)) for p in droppings}
    created = [p for p in droppings if after[p] and not before[p]]
    assert not created, f"the probe wrote runtime artifacts into the source tree: {created}"


# ── v3.66.727: TYPE-AWARE body checking ────────────────────────────────────
def test_type_aware_gate_finds_no_keyless_control():
    """THE 727 GATE. The regex detector could only prove the sliver where a control passes
    a LITERAL {}. Most controls pass a typed VARIABLE (`apiPost(url, req)`), which a regex
    cannot see into -- 133 call sites were UNKNOWN.

    The TYPE CHECKER can see into them. `apiPost(path, payload: unknown)` declares nothing,
    but the ARGUMENT EXPRESSION at each call site has an inferred type. So we ask the
    compiler what the body IS, synthesize a type-directed sample, and replay it.

    DEAD = the body type has NO KEYS and the endpoint refuses {}. That is the 724/726 shape,
    now provable for typed bodies.

    WHAT THIS DELIBERATELY DOES NOT CLAIM: that a 400 on a type-correct key set means a dead
    control. It does not. Our synthetic values ("x") are not real site ids or filenames, and
    a probe CANNOT distinguish "missing key" from "invalid value" when the endpoint reports
    both identically (/api/queue/v2/cancel answers "unknown site_id" to BOTH). An earlier
    version of this rule claimed otherwise and flagged /api/tools/run -- which we watched
    work live at 719. Judging those needs REAL FIXTURES, i.e. an integration harness.
    """
    from tools import body_contract as bc

    tcalls = bc.ts_calls(ROOT)
    if not tcalls:
        import pytest
        pytest.skip("node/typescript unavailable -- the type-aware pass cannot run here")
    res = bc.probe_typed(ROOT, tcalls)
    dead = [r for r in res if r["verdict"] == "DEAD"]
    assert not dead, (
        "controls whose body type has NO KEYS, against an endpoint that demands a body:\n"
        + "\n".join(f"  {r['path']}  ({r['file']})  {r['why']}" for r in dead))


def test_type_aware_pass_actually_resolves_bodies():
    """A type-aware gate that resolves nothing is the regex gate wearing a hat. Pin that it
    genuinely reads key sets off the checker."""
    from tools import body_contract as bc

    tcalls = bc.ts_calls(ROOT)
    if not tcalls:
        import pytest
        pytest.skip("node/typescript unavailable")
    resolved = [t for t in tcalls if not t["unknownType"]]
    assert len(resolved) >= 60, (
        f"only {len(resolved)} of {len(tcalls)} bodies resolved -- the checker pass is not "
        f"doing its job")


def test_open_dicts_are_unknown_not_empty():
    """Record<string, unknown> is an OPEN DICT: its keys are whatever the caller passes and
    are NOT statically knowable. Treating it as {} made two sound controls
    (marketplace/import + preview) look like they send an empty body. 'No keys' and 'keys I
    cannot see' are different facts, and collapsing them is the exact bug this file exists
    for."""
    from tools import body_contract as bc

    tcalls = bc.ts_calls(ROOT)
    if not tcalls:
        import pytest
        pytest.skip("node/typescript unavailable")
    mp = [t for t in tcalls if t["path"] == "/api/marketplace/import"]
    assert mp, "marketplace/import call site vanished"
    assert mp[0]["unknownType"] is True, (
        "an open dict was resolved to a concrete empty body -- it will be reported DEAD")
