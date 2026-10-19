"""stbai F4a + F7 residual -- teach dry_run must use the site config; malformed input is a 400.

F4a: POST /api/sites/<sid>/template/dry_run called template_dry_run(url, html=html) without
site_config=cfg, so on a site with quality_preference "720p" the candidate classification said
rung_selection "UNKNOWN" ("no site config supplied") and picked the 1080p URL. The sibling
/api/sites/<sid>/candidates/inspect passes site_config=cfg and picks 720p.

F7 residual (each measured 500 or silently coerced on the pristine tree):
  /api/tools/run: "flags": [] / "" / 0 / false were coerced to {} and the tool RAN, while [1] -> 400.
  /api/daily_budget/history?days=abc -> 500 (int() inside the route's catch-all).
  /api/analyzer/tree max_depth / max_children non-int -> 500 (same).
  /api/captures/setup_site body [1] / "x" -> 500 (dict([1]) TypeError, dict("x") ValueError;
  neither reaches app.py's AttributeError -> 400 handler).
List bodies on teach dry_run / analyzer load,tree,test / captures build_draft,scrub already answer
400 via that handler; those are pinned here, not changed.
"""
import os
import sys
import tempfile

import pytest

BD_GATE_SCOPE = "module"  # O906.1 gate marker (precollect guard)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HTML = ('<html><body><a href="https://alpha.example.com/dl/movie_1080p.mp4">Download 1080p</a>'
        '<a href="https://alpha.example.com/dl/movie_720p.mp4">Download 720p</a></body></html>')
URL = "https://alpha.example.com/videos"


@pytest.fixture(scope="module")
def env():
    os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="stbai-f4a-f7-home-"))
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    import bulk_downloader.app as A
    from bulk_downloader.app import app
    app.config["TESTING"] = True
    app.config["PROPAGATE_EXCEPTIONS"] = False  # an unhandled error must surface as its 500
    c = app.test_client()
    tok = (c.get("/api/csrf").get_json(silent=True) or {}).get("csrf_token", "")
    return A, c, {"X-CSRFToken": tok, "X-CSRF-Token": tok}


@pytest.fixture(autouse=True)
def site(env):
    """Per test: the shared conftest resets the app's module-level s_cfg between tests."""
    env[0].s_cfg["stbai_site"] = {"name": "Site A", "url": URL,
                                  "quality_preference": "720p", "min_resolution": 720}


def _post(env, path, body):
    _, c, hdr = env
    return c.post(path, headers=hdr, json=body)


def test_f4a_template_dry_run_uses_site_config_like_inspect(env):
    body = {"html": HTML, "url": URL}
    rc = _post(env, "/api/sites/stbai_site/candidates/inspect", body)
    ctl = rc.get_json()
    assert rc.status_code == 200 and "rung_selection" in ctl, (rc.status_code, ctl)
    assert ctl["rung_selection"] != "UNKNOWN", ctl
    assert ctl["winner"]["url"].endswith("movie_720p.mp4"), ctl  # control: inspect honours 720p
    r = _post(env, "/api/sites/stbai_site/template/dry_run", body)
    assert r.status_code == 200, r.get_json()
    cc = r.get_json()["candidate_classification"]
    got = (cc.get("rung_selection"), (cc.get("winner") or {}).get("url"))
    assert got == (ctl["rung_selection"], ctl["winner"]["url"]), (
        "F4A-SITE-CONFIG-DROPPED: template/dry_run classified without the site config "
        "(got %r, inspect control %r)" % (got, (ctl["rung_selection"], ctl["winner"]["url"])))


@pytest.fixture
def tool_stub(monkeypatch):
    import bulk_downloader.tool_bridge as tb
    calls = []
    monkeypatch.setattr(tb, "run", lambda tool, flags: calls.append((tool, flags)) or {
        "returncode": 0, "stdout": "", "stderr": "", "argv": [], "timed_out": False})
    return sorted(tb.ALLOWLIST)[0], calls


@pytest.mark.parametrize("flags", [[], "", 0, False], ids=["list", "str", "zero", "false"])
def test_f7_tools_run_non_object_flags_are_400_and_never_run(env, tool_stub, flags):
    tool, calls = tool_stub
    r = _post(env, "/api/tools/run", {"tool": tool, "flags": flags})
    assert (r.status_code, calls) == (400, []), (
        "F7-FLAGS-COERCED: flags=%r answered %s and ran %r (flags=[1] is a 400)"
        % (flags, r.status_code, calls))


@pytest.mark.parametrize("body", [{"flags": {}}, {}], ids=["dict", "absent"])
def test_f7_tools_run_object_or_absent_flags_still_run(env, tool_stub, body):
    tool, calls = tool_stub
    r = _post(env, "/api/tools/run", dict(body, tool=tool))
    assert r.status_code == 200 and calls == [(tool, {})], (r.status_code, calls)


def test_f7_daily_budget_days_non_int_is_400(env):
    c = env[1]
    r = c.get("/api/daily_budget/history?site=stbai_site&days=abc")
    assert r.status_code == 400, (
        "F7-DAYS-500: ?days=abc answered %s %r" % (r.status_code, r.get_json()))
    ok = c.get("/api/daily_budget/history?site=stbai_site&days=7")
    assert ok.status_code == 200 and ok.get_json()["site_id"] == "stbai_site", ok.get_json()


@pytest.mark.parametrize("field", ["max_depth", "max_children"])
def test_f7_analyzer_tree_non_int_limit_is_400(env, field):
    r = _post(env, "/api/analyzer/tree", {"capture": "stbai-nope", field: "not-an-int"})
    assert r.status_code == 400, (
        "F7-MAXDEPTH-500: %s='not-an-int' answered %s %r" % (field, r.status_code, r.get_json()))
    ok = _post(env, "/api/analyzer/tree", {"capture": "stbai-nope", field: 3})
    assert ok.status_code == 404, ok.get_json()  # parse passed; the capture is unknown


@pytest.mark.parametrize("raw", ["1e999", "-1e999"], ids=["inf", "neg_inf"])
@pytest.mark.parametrize("field", ["max_depth", "max_children"])
def test_f7_analyzer_tree_infinite_limit_is_400(env, field, raw):
    """Lens: a JSON number past float range parses as inf; int(inf) is OverflowError."""
    _, c, hdr = env
    r = c.post("/api/analyzer/tree", headers=hdr, content_type="application/json",
               data='{"capture": "stbai-nope", "%s": %s}' % (field, raw))
    assert r.status_code == 400, (
        "F7-MAXDEPTH-INF-500: %s=%s answered %s %r" % (field, raw, r.status_code, r.get_json()))


@pytest.mark.parametrize("body", [[1], "x"], ids=["list", "str"])
def test_f7_setup_site_non_object_body_is_400(env, body):
    r = _post(env, "/api/captures/setup_site", body)
    assert r.status_code == 400, (
        "F7-SETUP-SITE-NONDICT-500: body %r answered %s %r" % (body, r.status_code, r.get_json()))


def test_f7_setup_site_object_body_still_reaches_validation(env):
    r = _post(env, "/api/captures/setup_site", {"name": "", "login_url": ""})
    assert r.status_code == 400 and "name and login_url" in r.get_json()["error"], r.get_json()


@pytest.mark.parametrize("path", [
    "/api/sites/stbai_site/template/dry_run", "/api/analyzer/load", "/api/analyzer/tree",
    "/api/analyzer/test", "/api/captures/build_draft", "/api/captures/scrub"])
def test_f7_list_body_pins_already_400(env, path):
    assert _post(env, path, [1]).status_code == 400
