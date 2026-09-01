"""The site-integrations "test webhook" button reports a WORKING hook as broken.

`app_sites_integrations.api_hooks_test` handles six hook kinds. Five of them
call a sink that returns a 2-tuple. The sixth does::

    app_sites_integrations.py:204   ok, msg = send_webhook(...)

and `hooks.send_webhook` has returned THREE values on every one of its return
paths since v3.40.0 (Phase 80), so the operator's reply body could be handed
back for bidirectional webhooks. The unpack raises ``ValueError``, the route's
blanket ``except Exception`` at the bottom of the handler catches it, and the
operator is told::

    HTTP 500  {"ok": false,
               "message": "ValueError: too many values to unpack (expected 2)"}

MEASURED AT BASE f993f654 ON test5: the webhook is ACTUALLY DELIVERED first.
The receiving server got the POST and answered 202 before the unpack ran. So
the failure is not "the hook did not fire" -- it is a correctly configured,
fully working integration reported to its operator as a 500 with a diagnostic
that names a BD-internal bug rather than anything the operator can act on.
The other five kinds on the same route are unaffected.

WHY THE CALLER MOVES AND NOT THE CALLEE. `send_webhook` has two other callers,
both already correct: `hooks.py:86` (`ok, msg, resp`) CONSUMES the third value
to implement the pre_url_added bidirectional protocol -- skip / rewrite /
priority all read `resp` -- and `hooks.py:659` unpacks three and discards. A
2-tuple callee would break the bidirectional feature outright. One call site is
wrong; the contract is not.

AND THE THIRD VALUE IS NOT DISCARDED HERE. This route exists so the operator
can SEE what their hook endpoint said. Widening to ``ok, msg, _resp`` would
satisfy arity and still hide the reply, so the parsed body is returned as
``response``. The nonce control below cannot be satisfied by a constant.
"""
from __future__ import annotations

import ast
import json
import threading
import http.server
import socketserver
import uuid
from pathlib import Path

import pytest
from flask import Flask

from bulk_downloader import app_sites, app_sites_integrations, hooks
from bulk_downloader import app_state

# SCOPE, stated because nothing verifies that a "module" answer is honest --
# test_v3_66_939 says so itself, and an undefended declaration in a file whose
# last two gates read every .py in the package is exactly the silent decision
# that policy exists to prevent. The subject here is ONE function's call
# contract: `send_webhook` and the route that calls it. The package-wide walk
# below is a denominator choice, not a repo-wide subject -- callers can live
# anywhere, so the survey must look everywhere to be complete. This gate makes
# no claim about the tree, asserts nothing about repository infrastructure, and
# would be wrongly promoted into a safety shard by calling it repo-wide.
BD_GATE_SCOPE = "module"

_INTEGRATIONS_PY = Path(app_sites_integrations.__file__)
_HOOKS_PY = Path(hooks.__file__)

_SITE_ID = "hookreply-site"


class _RecordingHook:
    """A real HTTP endpoint on loopback. Records every request body it was
    given so a test can prove the webhook was delivered, and answers with a
    caller-chosen status and JSON body so the reply is a measurement rather
    than a constant."""

    def __init__(self, status=202, reply=None):
        self.received = []
        self.status = status
        self.reply = reply
        outer = self

        class _H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.received.append(self.rfile.read(n) if n else b"")
                payload = (json.dumps(outer.reply).encode("utf-8")
                           if outer.reply is not None else b"")
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            def log_message(self, *a):  # keep pytest output clean
                pass

        self._srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
        self.port = self._srv.server_address[1]
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/hook"

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()
        self._t.join(timeout=5)


@pytest.fixture()
def client(monkeypatch):
    """A Flask app carrying the real sites blueprint.

    `runners` and `s_cfg` are the live shared dicts app_state hands every
    handler; monkeypatch.setitem restores them so no other test in the session
    inherits this site.
    """
    app = Flask(__name__)
    n_rules = app_sites.register_routes(app)
    assert n_rules > 0, "sites blueprint registered zero rules"
    monkeypatch.setitem(app_state.runners, _SITE_ID, object())
    monkeypatch.setitem(app_state.s_cfg, _SITE_ID, {"name": "Hook Reply Site"})
    return app.test_client()


def _configure(hook_url):
    app_state.s_cfg[_SITE_ID] = {"name": "Hook Reply Site",
                                 "webhook_urls": hook_url}


@pytest.fixture()
def endpoint():
    made = []

    def _make(**kw):
        srv = _RecordingHook(**kw)
        made.append(srv)
        return srv

    yield _make
    for srv in made:
        srv.close()


# ── Preconditions ────────────────────────────────────────────────────

def test_preconditions_the_route_and_the_sink_are_the_real_ones(client, endpoint):
    """Assert the shape every verdict below depends on, BEFORE any verdict.

    Without this a green could come from a route that 404s, a site that is
    not registered, a webhook URL the SSRF validator rejects before any
    network call, or a sink that was stubbed out.
    """
    srv = endpoint(reply={"action": "allow"})
    _configure(srv.url)

    # The route exists, is bound to the handler under test, and the site is
    # registered in BOTH shared dicts the handler consults.
    rules = {str(r): r.endpoint for r in client.application.url_map.iter_rules()}
    assert rules.get("/api/sites/<sid>/hooks/test") == "sites.api_hooks_test"
    assert _SITE_ID in app_state.runners and _SITE_ID in app_state.s_cfg

    # A loopback URL is a legitimate hook target -- the validator must not be
    # the thing that decides these tests.
    ok, why = hooks._validate_webhook_url(srv.url)
    assert ok is True, why

    # Nothing is stubbed: the handler resolves the production send_webhook.
    assert app_sites_integrations.hooks is hooks if hasattr(
        app_sites_integrations, "hooks") else True
    assert hooks.send_webhook.__module__ == "bulk_downloader.hooks"

    # And the sink really does hand back three values.
    ok, msg, resp = hooks.send_webhook(srv.url, {"probe": 1})
    assert ok is True and "202" in msg
    assert resp == {"action": "allow"}
    assert len(srv.received) == 1


# ── RED: the operator-visible consequence ────────────────────────────

def test_a_working_webhook_is_reported_as_working(client, endpoint):
    """RED at base f993f654 with:

        HTTP 500
        {"ok": false,
         "message": "ValueError: too many values to unpack (expected 2)"}

    while the endpoint below has already received the POST and answered 202.
    That is the whole defect: a delivered webhook reported as a server error,
    with a message the operator cannot act on.
    """
    nonce = f"reply-{uuid.uuid4().hex}"
    srv = endpoint(status=202, reply={"action": "allow", "nonce": nonce})
    _configure(srv.url)

    r = client.post(f"/api/sites/{_SITE_ID}/hooks/test", json={"kind": "webhook"})

    # The hook fired, exactly once, with a real payload.
    assert len(srv.received) == 1, srv.received
    sent = json.loads(srv.received[0])
    assert sent["event"] == "completed"
    assert sent["job"]["filename"] == "test.mp4"

    # And the operator is told so.
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True, body
    assert "202" in body["message"], body


# ── Negative control 1: the third value is carried, not discarded ─────

def test_the_endpoints_own_reply_reaches_the_operator(client, endpoint):
    """Direction 1. `ok, msg, _resp = ...` satisfies the arity and is just as
    useless on THIS route, whose purpose is showing the operator what their
    endpoint said. The reply carries a per-run nonce, so no constant, no
    hardcoded None and no discard can produce it.
    """
    nonce = f"nonce-{uuid.uuid4().hex}"
    srv = endpoint(status=200, reply={"action": "rewrite",
                                      "url": "https://example.invalid/x",
                                      "nonce": nonce})
    _configure(srv.url)

    r = client.post(f"/api/sites/{_SITE_ID}/hooks/test", json={"kind": "webhook"})

    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["response"] == {"action": "rewrite",
                                "url": "https://example.invalid/x",
                                "nonce": nonce}, body
    # The nonce is genuinely per-run, not baked into the assertion's source.
    assert nonce not in _INTEGRATIONS_PY.read_text(encoding="utf-8")


def test_an_endpoint_with_no_body_reports_a_null_reply(client, endpoint):
    """The discriminator for direction 1. Padding the response key with a
    constant -- or with `msg` -- would pass the test above and be just as
    wrong: an endpoint that answers 204-with-no-body genuinely said nothing,
    and `response` must be null while `ok` stays true.
    """
    srv = endpoint(status=200, reply=None)
    _configure(srv.url)

    r = client.post(f"/api/sites/{_SITE_ID}/hooks/test", json={"kind": "webhook"})

    body = r.get_json()
    assert r.status_code == 200, body
    assert body["ok"] is True
    assert body["response"] is None, body
    assert len(srv.received) == 1


# ── Negative control 2: failures are still failures ──────────────────

def test_a_failing_endpoint_is_still_reported_as_failing(client, endpoint):
    """Direction 2. Widening the unpack must not launder a genuine hook
    failure into a success. A 500 from the endpoint stays ok:false and the
    operator gets the real status, not a ValueError.
    """
    srv = endpoint(status=500, reply={"error": "boom"})
    _configure(srv.url)

    r = client.post(f"/api/sites/{_SITE_ID}/hooks/test", json={"kind": "webhook"})

    assert len(srv.received) == 1
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["ok"] is False, body
    assert "500" in body["message"], body
    assert "ValueError" not in body["message"], body


def test_an_unreachable_endpoint_names_the_transport_error(client, endpoint):
    """A hook URL nothing is listening on: still ok:false, and the message
    names the connection failure rather than an unpack bug. Proves the route's
    blanket `except Exception` is no longer the thing answering.
    """
    srv = endpoint()
    dead = srv.url
    srv.close()
    _configure(dead)

    r = client.post(f"/api/sites/{_SITE_ID}/hooks/test", json={"kind": "webhook"})

    body = r.get_json()
    assert r.status_code == 200, body
    assert body["ok"] is False, body
    assert "ValueError" not in body["message"], body
    assert body["response"] is None, body


def test_a_rejected_scheme_is_refused_before_any_request(client, endpoint):
    """send_webhook's validator arm returns its 3-tuple too, and it is the
    arm an operator hits by typo. It must reach the same shaped answer."""
    _configure("file:///etc/passwd")

    r = client.post(f"/api/sites/{_SITE_ID}/hooks/test", json={"kind": "webhook"})

    body = r.get_json()
    assert r.status_code == 200, body
    assert body["ok"] is False, body
    assert "rejected" in body["message"], body


# ── The other five kinds are 2-tuple sinks and must stay that way ────

def test_a_two_tuple_sink_on_the_same_route_is_unaffected(client, endpoint):
    """Scope control. Five of the six kinds call 2-tuple sinks. If the fix had
    been applied to the wrong side -- or applied route-wide -- this breaks."""
    _configure("")
    r = client.post(f"/api/sites/{_SITE_ID}/hooks/test", json={"kind": "post_cmd"})
    body = r.get_json()
    assert r.status_code == 200, body
    assert body["ok"] is False and "No command configured" in body["message"]


# ── Structural gate over the paths no runtime test reaches ───────────

def _send_webhook_returns():
    tree = ast.parse(_HOOKS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "send_webhook":
            return [r for r in ast.walk(node) if isinstance(r, ast.Return)]
    raise AssertionError("send_webhook not found at module level of hooks.py")


def test_every_send_webhook_return_path_carries_three_values():
    """Two of these arms need a downed network or a malformed URL to reach.
    Every caller's unpack is unconditional, so arity is a static property and
    is proved statically for all of them.

    The floor is the return count measured at base f993f654. A refactor that
    deletes return paths must fail here rather than silently shrink the
    denominator this gate surveys.
    """
    returns = _send_webhook_returns()
    assert len(returns) >= 5, (
        f"send_webhook has {len(returns)} returns, fewer than the 5 measured "
        "at base -- the gate's denominator shrank")
    short = [(r.lineno, len(r.value.elts) if isinstance(r.value, ast.Tuple) else None)
             for r in returns
             if not (isinstance(r.value, ast.Tuple) and len(r.value.elts) == 3)]
    assert short == [], (
        f"send_webhook returns a short/non-tuple result at {short}; "
        "every call site unpacks three values from all of them")


def test_every_send_webhook_call_site_unpacks_three():
    """The tree-wide denominator: EVERY unpacking call of send_webhook in the
    application package, not only the one this cut repairs. Measured at base
    f993f654: three call sites, one of them short. A fourth caller added with
    the wrong arity fails here by file and line.
    """
    pkg = Path(hooks.__file__).parent
    sources = sorted(pkg.rglob("*.py"))
    assert len(sources) > 0, "zero-file denominator"

    def _callee(call):
        fn = call.func
        return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)

    sites = []          # tuple-unpacking assignments -- the arity population
    every_call = []     # EVERY invocation, so the survey can be reconciled
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _callee(node) == "send_webhook":
                every_call.append((path.name, node.lineno))
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if not isinstance(call, ast.Call) or _callee(call) != "send_webhook":
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Tuple):
                    sites.append((path.name, node.lineno, len(tgt.elts)))

    assert len(sites) >= 3, f"expected at least the 3 call sites measured at base, got {sites}"
    wrong = [s for s in sites if s[2] != 3]
    assert wrong == [], f"send_webhook unpacked with the wrong arity at {wrong}"
    assert "app_sites_integrations.py" in {s[0] for s in sites}, sites

    # RECONCILE COLLECTION TO EXECUTION (A7). This gate judges tuple-unpacking
    # assignments. A call reached some OTHER way -- `x = send_webhook(...)`
    # then `x[0]`, a comprehension target, a starred unpack -- would slip past
    # it silently, so every invocation must be one this survey actually saw.
    # A new call in an unsurveyed form fails here by file and line rather than
    # shrinking the denominator without saying so.
    unsurveyed = sorted(set(every_call) - {(s[0], s[1]) for s in sites})
    assert unsurveyed == [], (
        f"send_webhook is invoked at {unsurveyed} in a form this arity gate "
        "does not judge; widen the survey rather than leaving it unjudged")


def test_the_documented_contract_does_not_invite_a_short_unpack():
    """The callee's docstring said old callers "can ignore the third element".
    Tuple unpacking does not ignore -- that sentence is how this defect got
    written, and leaving it is how the next one does."""
    doc = ast.get_docstring(
        next(n for n in ast.parse(_HOOKS_PY.read_text(encoding="utf-8")).body
             if isinstance(n, ast.FunctionDef) and n.name == "send_webhook")) or ""
    assert "(ok, status_or_error, response_body)" in doc, doc[:400]
    assert "can ignore the third element" not in doc, doc[-400:]
