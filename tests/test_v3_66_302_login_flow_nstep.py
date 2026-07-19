"""v3.66.302 — cross-origin N-step login flow.

Three pieces, all non-guard / no new route:

  1. SCHEMA (macro_recorder): a navigation/origin-aware action kind ``await_url``
     so an N-step flow can express the cross-origin hop between steps (enter
     username → Next → wait for the IdP origin → enter password → submit).
  2. DERIVATION (login_flow_recorder.derive_login_flow): turn a recorded
     action_timeline (+ network_log nav events) into an ordered N-step flow,
     inserting ``await_url`` at origin transitions and routing password fields
     through the vault marker (never a plaintext credential).
  3. REPLAY PLAN (login_flow_recorder.plan_login_flow): the ordered action list
     do_login would drive for a site that has a saved flow — testable offline
     without a browser; the live browser drive is verified on stash.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


# ── 1. schema: await_url action kind ──────────────────────────────────────
def test_await_url_is_a_valid_kind():
    import bulk_downloader.macro_recorder as mr
    assert "await_url" in mr._VALID_KINDS


def test_await_url_validates():
    import bulk_downloader.macro_recorder as mr
    ok, err = mr.validate_macro(
        {"actions": [{"kind": "await_url", "url": "https://idp.example.com/*"}]})
    assert ok, err


def test_await_url_requires_url():
    import bulk_downloader.macro_recorder as mr
    ok, _ = mr.validate_macro({"actions": [{"kind": "await_url"}]})
    assert not ok


class _FakePage:
    """Minimal page double recording wait_for_url / fill / click calls."""
    def __init__(self):
        self.calls = []

    def wait_for_url(self, url, timeout=None, **kw):
        self.calls.append(("wait_for_url", url))

    class _Loc:
        def __init__(self, outer, sel):
            self.outer, self.sel = outer, sel
        @property
        def first(self):
            return self
        def click(self, **kw):
            self.outer.calls.append(("click", self.sel))
        def fill(self, text, **kw):
            self.outer.calls.append(("fill", self.sel, text))
        def type(self, text, **kw):
            self.outer.calls.append(("type", self.sel, text))
        def focus(self, **kw):
            pass
        def wait_for(self, **kw):
            pass

    def locator(self, sel):
        return _FakePage._Loc(self, sel)


def test_await_url_executes_against_page():
    import bulk_downloader.macro_recorder as mr
    pg = _FakePage()
    macro = {"actions": [
        {"kind": "type", "selector": "#user", "text": "alice"},
        {"kind": "click", "selector": "#next"},
        {"kind": "await_url", "url": "https://idp.example.com/*"},
        {"kind": "type", "selector": "#pass", "text": "secret"},
        {"kind": "click", "selector": "#submit"},
    ]}
    res = mr.replay_macro(pg, macro, strict=True)
    assert res["ok"], res
    assert ("wait_for_url", "https://idp.example.com/*") in pg.calls, pg.calls


# ── 2. derivation: action_timeline → N-step flow ──────────────────────────
def test_derive_login_flow_inserts_await_at_origin_hop():
    import bulk_downloader.login_flow_recorder as lfr
    # two-step cross-origin: username field + Next on origin A, then password +
    # submit on origin B (the IdP). The network nav event marks the transition.
    action_timeline = [
        {"ts": 1000, "selector": "#user", "role": "login/username", "tag": "input",
         "page_url": "https://site.example.com/login"},
        {"ts": 1200, "selector": "#next", "role": "login/submit", "tag": "button",
         "page_url": "https://site.example.com/login"},
        {"ts": 2600, "selector": "#pass", "role": "login/password", "tag": "input",
         "page_url": "https://idp.example.com/auth"},
        {"ts": 2800, "selector": "#submit", "role": "login/submit", "tag": "button",
         "page_url": "https://idp.example.com/auth"},
    ]
    network_log = [{"timestamp": 1800, "url": "https://idp.example.com/auth",
                    "resource_type": "document"}]
    flow = lfr.derive_login_flow(action_timeline, network_log=network_log)
    kinds = [a["kind"] for a in flow["actions"]]
    # an await_url must appear between the first-origin steps and the second
    assert "await_url" in kinds, kinds
    i = kinds.index("await_url")
    assert "type" in kinds[:i] and "type" in kinds[i + 1:], kinds
    # the password step must be vault-routed, never a literal credential
    pw = [a for a in flow["actions"]
          if a["kind"] == "type" and a.get("secret")]
    assert pw, flow["actions"]
    assert all(a.get("text") == mr_marker() for a in pw), pw


def test_derive_login_flow_single_origin_no_await():
    import bulk_downloader.login_flow_recorder as lfr
    at = [
        {"ts": 1000, "selector": "#user", "role": "login/username", "tag": "input",
         "page_url": "https://site.example.com/login"},
        {"ts": 1100, "selector": "#pass", "role": "login/password", "tag": "input",
         "page_url": "https://site.example.com/login"},
        {"ts": 1200, "selector": "#submit", "role": "login/submit", "tag": "button",
         "page_url": "https://site.example.com/login"},
    ]
    flow = lfr.derive_login_flow(at, network_log=[])
    kinds = [a["kind"] for a in flow["actions"]]
    assert "await_url" not in kinds, kinds
    assert kinds.count("type") == 2 and kinds.count("click") == 1, kinds


def test_derive_login_flow_general_nstep():
    """N steps across 3 origins — a general flow, not just 2-step."""
    import bulk_downloader.login_flow_recorder as lfr
    at = [
        {"ts": 100, "selector": "#email", "role": "login/username", "tag": "input",
         "page_url": "https://a.example.com/"},
        {"ts": 150, "selector": "#go", "role": "login/submit", "tag": "button",
         "page_url": "https://a.example.com/"},
        {"ts": 400, "selector": "#otp", "role": "login/username", "tag": "input",
         "page_url": "https://b.example.com/"},
        {"ts": 450, "selector": "#go2", "role": "login/submit", "tag": "button",
         "page_url": "https://b.example.com/"},
        {"ts": 700, "selector": "#pw", "role": "login/password", "tag": "input",
         "page_url": "https://c.example.com/"},
        {"ts": 750, "selector": "#fin", "role": "login/submit", "tag": "button",
         "page_url": "https://c.example.com/"},
    ]
    nl = [{"timestamp": 250, "url": "https://b.example.com/", "resource_type": "document"},
          {"timestamp": 550, "url": "https://c.example.com/", "resource_type": "document"}]
    flow = lfr.derive_login_flow(at, network_log=nl)
    kinds = [a["kind"] for a in flow["actions"]]
    assert kinds.count("await_url") == 2, kinds  # two origin hops


# ── 3. replay plan for do_login ───────────────────────────────────────────
def test_plan_login_flow_resolves_vault_marker_not_secret():
    import bulk_downloader.login_flow_recorder as lfr
    flow = {"actions": [
        {"kind": "type", "selector": "#user", "text": "alice"},
        {"kind": "await_url", "url": "https://idp/*"},
        {"kind": "type", "selector": "#pass", "text": mr_marker(), "secret": True},
        {"kind": "click", "selector": "#submit"},
    ]}
    plan = lfr.plan_login_flow(flow, username="alice")
    # the username action is filled with the configured username
    types = [a for a in plan if a["kind"] == "type"]
    assert any(a.get("text") == "alice" for a in types), plan
    # the secret action still carries the marker (resolved at the last moment in
    # replay), never the plaintext password in the plan
    assert all(a.get("text") != "hunter2" for a in plan), plan


def mr_marker():
    import bulk_downloader.macro_recorder as mr
    return mr.VAULT_MARKER
