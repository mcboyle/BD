"""bcf-main-latent -- the take_over trio, judged in a fixture world that answers truthfully.

FF-D's artifact regen (283 call sites) put the TeachSessionCard controls in the
body-contract probe's scanned set, and two reds surfaced:

  1. /api/sites/${}/take_over_url -> HARNESS-FAULT "app 5xx'd on OUR fixture".
     The traceback is the stub, not the product: app_sites_auth.api_take_over_url does
         ok,msg=runners[sid].start_manual_download(target_url)
     and _StubRunner.__getattr__ fabricated start_manual_download as a no-op returning
     0. Unpacking an int is "TypeError: cannot unpack non-iterable int object" -- the
     same mechanism #5 that load_urls already fixed. The REAL ManualMixin methods
     (runner_manual.py) always return a (bool, str) pair.
  2. take_over_done / take_over_cancel -> UNKNOWN "type checker could not resolve the
     body". The SPA posted a literal {} to endpoints that read no body at all; the
     extractor cannot type a propertyless object, so the ratchet rose 139 -> 140.
     Fix: apiPost's payload is optional and these two calls send none.

The tests below hold the stub to the real runner's shape and hold the three
endpoints to "never 5xx" in-process, including the body shapes that once reached
the fabricated method.
"""
import os
import sys
import tempfile

BD_GATE_SCOPE = "module"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MANUAL_METHODS = (
    ("start_manual_download", ("https://example.invalid/scene/1",)),
    ("finish_manual_download", ()),
    ("cancel_manual_download", ()),
)


def test_stub_manual_methods_are_defined_not_fabricated():
    """The handlers unpack ok,msg from all three; a fabricated no-op returns 0."""
    from tools import body_contract_fixtures as bcf

    missing = [n for n, _ in MANUAL_METHODS if n not in vars(bcf._StubRunner)]
    assert not missing, (
        "_StubRunner still leaves these to __getattr__, which returns 0 and turns "
        "the handler's `ok,msg=` unpack into a phantom 500: %s" % missing)


def test_stub_manual_methods_return_the_real_two_tuple():
    from tools import body_contract_fixtures as bcf

    stub = bcf._StubRunner("fx")
    for name, args in MANUAL_METHODS:
        got = getattr(stub, name)(*args)
        assert isinstance(got, tuple) and len(got) == 2, (
            "%s returned %r; the real ManualMixin returns (ok, message)" % (name, got))
        ok, msg = got
        assert isinstance(ok, bool) and isinstance(msg, str) and msg, (name, got)


def test_stub_start_refuses_a_non_http_url_like_the_real_runner():
    """runner_manual.start_manual_download refuses an empty or non-http URL with
    (False, "target_url must be an http(s) URL"); the stub keeps that answer."""
    from tools import body_contract_fixtures as bcf

    stub = bcf._StubRunner("fx")
    for bad in ("", "ftp://x", "not a url"):
        assert stub.start_manual_download(bad) == (
            False, "target_url must be an http(s) URL"), bad


def test_take_over_endpoints_never_5xx_in_the_fixture_world():
    """In-process, against the same world the body-contract probe uses. Each case
    names a body shape; none may come back 5xx or raise."""
    from tools import body_contract_fixtures as bcf
    import bulk_downloader.app as A
    import bulk_downloader.template_manager as tm

    saved_cfg = dict(A._app_cfg)
    saved = (dict(A.s_cfg), dict(A.s_meta), dict(A.runners))
    saved_drafts_dir = tm.DRAFTS_DIR
    saved_testing = A.app.config.get("TESTING")
    home = tempfile.mkdtemp(prefix="bd_fx_takeover_")
    cases = (
        ("take_over_url", "well-formed", {"json": {"url": "https://example.invalid/s/1"}}),
        ("take_over_url", "empty-url", {"json": {"url": ""}}),
        ("take_over_url", "body-list", {"json": []}),
        ("take_over_url", "url-int", {"json": {"url": 123}}),
        ("take_over_done", "no-body", {}),
        ("take_over_cancel", "no-body", {}),
    )
    try:
        A.app.config["TESTING"] = True
        A.s_cfg.clear(); A.s_meta.clear(); A.runners.clear()
        client = A.app.test_client()
        fx = bcf.Fixtures(A, client, home).build()
        tok = fx.csrf()
        headers = {"X-CSRFToken": tok, "X-CSRF-Token": tok}
        bad = []
        for endpoint, label, kw in cases:
            fx.ensure()
            path = fx.probe_path("/api/sites/${}/" + endpoint)
            try:
                r = client.post(path, headers=headers, **kw)
            except Exception as e:  # noqa: BLE001 -- TESTING propagates; name it
                bad.append("%s %s RAISED %s: %s" % (endpoint, label, type(e).__name__, e))
                continue
            if r.status_code >= 500:
                bad.append("%s %s -> %d %s" % (endpoint, label, r.status_code,
                                               r.get_data(as_text=True)[:120]))
        assert not bad, "take_over endpoints 5xx'd in the fixture world:\n  " + "\n  ".join(bad)
    finally:
        tm.DRAFTS_DIR = saved_drafts_dir
        A.app.config["TESTING"] = saved_testing
        A._app_cfg.clear(); A._app_cfg.update(saved_cfg)
        A.s_cfg.clear(); A.s_cfg.update(saved[0])
        A.s_meta.clear(); A.s_meta.update(saved[1])
        A.runners.clear(); A.runners.update(saved[2])
