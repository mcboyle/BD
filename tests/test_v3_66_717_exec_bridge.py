"""v3.66.717 (Cut 7) -- the exec bridge. The seam that did not exist.

tools_exec_bridged = 0: no code path took a GUI value and passed it as an argument to
a tool. That is why 739 CLI flags could not be exposed incrementally -- there was
nothing to hang a control on. Operator decision: build ONE validated, allowlisted
bridge. Build it once, build it paranoid, and all 739 become addressable through it.

This bridge is a SECURITY SURFACE -- an HTTP endpoint that runs local binaries with
operator-supplied arguments. The whole value is in what it REFUSES. These tests are
the contract, and the negative controls are the point:

  * only an ALLOWLISTED tool may run -- an arbitrary path is rejected;
  * only an ALLOWLISTED flag may be passed to that tool -- an unknown flag is rejected;
  * each argument is TYPE/RANGE validated against the allowlist spec;
  * NO SHELL, ever -- argv is a list; a shell-metacharacter payload is data, not code,
    and is rejected on validation rather than "escaped";
  * no path traversal into the value of a path-typed flag;
  * a hard TIMEOUT and an output SIZE CAP -- a tool cannot hang or flood the caller;
  * CSRF-gated like every other mutating endpoint;
  * the allowlist is DATA (reviewable), not scattered branches.

Bias: the safe default is DENY. Anything the allowlist does not explicitly permit is
refused.
"""
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _client():
    from bulk_downloader.app import app

    return app.test_client()


def _csrf(c):
    j = c.get("/api/csrf").get_json() or {}
    t = j.get("csrf_token") or j.get("token")
    return {"X-CSRFToken": t, "X-CSRF-Token": t, "Content-Type": "application/json"}


def _run(c, body):
    return c.post("/api/tools/run", data=json.dumps(body), headers=_csrf(c))


# ---- the allowlist is data ----------------------------------------------------

def test_allowlist_is_data_not_code():
    from bulk_downloader import tool_bridge

    spec = tool_bridge.ALLOWLIST
    assert isinstance(spec, dict) and spec, "the allowlist must be a reviewable data structure"
    # every entry declares its interpreter, its tool path, and its permitted flags
    for name, entry in spec.items():
        assert "argv0" in entry, "%s: no resolved tool path" % name
        assert "flags" in entry and isinstance(entry["flags"], dict), name


def test_listing_endpoint_reflects_the_allowlist():
    c = _client()
    r = c.get("/api/tools/available")
    assert r.status_code == 200
    names = {t["name"] for t in (r.get_json() or {}).get("tools", [])}
    from bulk_downloader import tool_bridge

    assert names == set(tool_bridge.ALLOWLIST), "listing must mirror the allowlist exactly"


# ---- the refusals (the whole point) ------------------------------------------

def test_unallowlisted_tool_is_refused():
    c = _client()
    r = _run(c, {"tool": "rm", "flags": {}})
    assert r.status_code in (400, 403), "an unlisted tool must be refused, not run"
    assert "not allowed" in json.dumps(r.get_json() or {}).lower()


def test_arbitrary_path_as_tool_is_refused():
    c = _client()
    for evil in ("/bin/sh", "../../etc/passwd", "bash", "python3 -c 'x'"):
        r = _run(c, {"tool": evil, "flags": {}})
        assert r.status_code in (400, 403), "%s must be refused" % evil


def test_unallowlisted_flag_is_refused():
    """A permitted tool with a flag the spec does not list must be rejected."""
    c = _client()
    from bulk_downloader import tool_bridge

    tool = next(iter(tool_bridge.ALLOWLIST))
    r = _run(c, {"tool": tool, "flags": {"--definitely-not-a-real-flag": "x"}})
    assert r.status_code == 400
    assert "flag" in json.dumps(r.get_json() or {}).lower()


def test_shell_metacharacters_are_data_not_code():
    """The classic. A ; rm -rf / payload in a flag value must be REFUSED by validation
    (or passed as an inert literal argv element), NEVER interpreted by a shell."""
    c = _client()
    from bulk_downloader import tool_bridge

    tool = next(iter(tool_bridge.ALLOWLIST))
    flag = next(iter(tool_bridge.ALLOWLIST[tool]["flags"]))
    for payload in ("; rm -rf /", "$(whoami)", "`id`", "x && curl evil", "|nc attacker 1"):
        r = _run(c, {"tool": tool, "flags": {flag: payload}})
        # either the value fails validation, or it ran with the payload as an inert
        # literal -- but the process must exist, so a 500/traceback is a failure
        assert r.status_code in (200, 400), (payload, r.status_code)
        assert not tool_bridge._USES_SHELL, "the bridge must never invoke a shell"


def test_path_typed_flag_rejects_traversal():
    from bulk_downloader import tool_bridge

    # find a path-typed flag if the allowlist has one; else this is vacuously fine
    for tool, entry in tool_bridge.ALLOWLIST.items():
        for flag, spec in entry["flags"].items():
            if spec.get("type") == "path":
                c = _client()
                r = _run(c, {"tool": tool, "flags": {flag: "../../../../etc/passwd"}})
                assert r.status_code == 400, "path traversal must be refused"
                return


# ---- the guarantees ----------------------------------------------------------

def test_bridge_never_uses_a_shell():
    from bulk_downloader import tool_bridge

    assert tool_bridge._USES_SHELL is False


def test_a_real_allowlisted_call_succeeds_and_is_bounded():
    """A benign, read-only allowlisted tool must actually run and return bounded output."""
    c = _client()
    from bulk_downloader import tool_bridge

    # pick a tool marked safe-to-smoke in the allowlist
    smoke = [(n, e) for n, e in tool_bridge.ALLOWLIST.items() if e.get("smoke")]
    if not smoke:
        pytest.skip("no smoke-safe tool in the allowlist")
    name, entry = smoke[0]
    r = _run(c, {"tool": name, "flags": entry.get("smoke_flags", {})})
    assert r.status_code == 200, r.get_json()
    body = r.get_json() or {}
    assert "stdout" in body and "returncode" in body
    assert len(body["stdout"]) <= tool_bridge.MAX_OUTPUT_BYTES


def test_timeout_and_output_caps_are_declared():
    from bulk_downloader import tool_bridge

    assert 0 < tool_bridge.TIMEOUT_S <= 120
    assert 0 < tool_bridge.MAX_OUTPUT_BYTES <= 5 * 1024 * 1024


def test_endpoint_is_csrf_gated():
    """CSRF is enforced once a SESSION exists (the app skips it for cookieless requests,
    which the token layer already covers). Establish a session, then a POST WITHOUT the
    CSRF header must be refused -- the exec bridge is a mutating endpoint like any other."""
    c = _client()
    from bulk_downloader import tool_bridge

    tool = next(iter(tool_bridge.ALLOWLIST))
    # establish a browser session (pair -> redeem), the way test_security does
    pr = c.get("/api/pair")
    if pr.status_code != 200:
        pytest.skip("pairing endpoint unavailable in this configuration")
    token = (pr.get_json() or {}).get("token")
    c.post("/api/pair/redeem", json={"token": token})
    # now cookie-based CSRF is live: a POST with no CSRF header must be refused
    r = c.post("/api/tools/run", json={"tool": tool, "flags": {}})
    assert r.status_code in (400, 403), (
        "with a session established, a POST with no CSRF header must be refused")
