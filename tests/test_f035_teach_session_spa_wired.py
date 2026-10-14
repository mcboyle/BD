"""F035 -- the takeover teach-session endpoints are operator-reachable from the SPA.

The runner parks a site's first URL in needs_review saying "take over to teach
download selectors", and before this cut no SPA control could do it: the three
endpoints below were dark, classified "CLI-ONLY" in the reachability ledger.

RED at the base: build() reports all three `dark`, and the pinned ledger lists them.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "reports", "endpoint_reachability.json")
CARD = os.path.join(ROOT, "frontend", "src", "components", "TeachSessionCard.tsx")

# Three named endpoints and the one component that calls them: not a repo-wide gate.
BD_GATE_SCOPE = "module"

TEACH_RULES = (
    "/api/sites/<sid>/take_over_url",
    "/api/sites/<sid>/take_over_done",
    "/api/sites/<sid>/take_over_cancel",
)


def _reach():
    from tools import endpoint_reachability as er

    return {e["rule"]: e["reach"] for e in er.build(ROOT)["endpoints"]}


def _shipped_spa_source():
    """frontend/src WITHOUT its *.test.* files. The ledger's own blob includes them,
    so a spec that merely names a route would certify it wired; a control has to
    live in source the bundle ships."""
    out = []
    for dp, dn, fns in os.walk(os.path.join(ROOT, "frontend", "src")):
        dn[:] = [d for d in dn if d != "node_modules"]
        for fn in sorted(fns):
            if fn.endswith((".ts", ".tsx")) and ".test." not in fn:
                out.append(open(os.path.join(dp, fn), encoding="utf-8").read())
    return "".join(out)


def test_teach_session_endpoints_reach_the_spa():
    from tools import endpoint_reachability as er

    src = _shipped_spa_source()
    assert er._reaches("/api/sites/<sid>/settings", src), "control: probe finds nothing"
    unwired = [rule for rule in TEACH_RULES if not er._reaches(rule, src)]
    assert not unwired, "F035 teach-session endpoints have no shipped SPA caller: %r" % unwired
    reach = _reach()
    got = {rule: reach.get(rule, "absent") for rule in TEACH_RULES}
    assert got == {rule: "spa" for rule in TEACH_RULES}, got


def test_reachability_probe_can_still_say_dark():
    """Positive control for the test above: a sibling teach endpoint this cut does
    NOT wire must still read dark, or `spa` above would prove nothing."""
    from tools import endpoint_reachability as er

    assert not er._reaches("/api/sites/<sid>/teach_cancel", _shipped_spa_source())
    assert _reach().get("/api/sites/<sid>/teach_cancel") == "dark"


def test_ledger_no_longer_pins_them_dark():
    led = json.load(open(LEDGER, encoding="utf-8"))
    still_dark = sorted(set(TEACH_RULES) & set(led["dark"]))
    assert not still_dark, "ledger still pins F035 endpoints dark: %r" % still_dark
    stale = sorted(set(TEACH_RULES) & set(led["classified"]))
    assert not stale, "ledger still classifies wired endpoints as dark: %r" % stale
    assert led["dark_count"] == len(led["dark"])


def test_card_posts_each_endpoint_as_a_full_literal():
    """A call the scanners can resolve: apiPost with a /api/sites/${...}/<segment>
    template literal per endpoint, and the start call carries the {url} body the
    route reads."""
    src = open(CARD, encoding="utf-8").read()
    for seg in ("take_over_url", "take_over_done", "take_over_cancel"):
        calls = re.findall(
            r"apiPost<[^>]+>\(`/api/sites/\$\{[^}]+\}/%s`,\s*(\{[^)]*\})\)" % seg, src)
        assert len(calls) == 1, "expected one apiPost literal for %s, got %r" % (seg, calls)
        body = re.sub(r"\s+", "", calls[0])
        assert body == ("{url:target}" if seg == "take_over_url" else "{}"), (seg, body)
