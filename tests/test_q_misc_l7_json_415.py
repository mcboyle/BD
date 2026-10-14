"""q-misc L7 (O1789 bughunt queue-templates-io): a non-JSON Content-Type on a JSON API route must
answer a JSON 415 error, like every other /api/ 4xx (400/404/405/413), not Flask's HTML 415 page.
application/json keeps working; non-API paths keep Flask's default error page."""

from pathlib import Path
import sys

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest


def _client_and_token():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    tok = c.get("/api/csrf").get_json()["csrf_token"]
    return c, {"X-CSRF-Token": tok}


def _assert_json_415(r, what):
    body = r.get_json(silent=True)
    assert r.status_code == 415 and r.mimetype == "application/json" and isinstance(body, dict), (
        f"BD_Q_MISC_L7_HTML_415: {what} answered {r.status_code} {r.mimetype}: {r.get_data()[:120]!r}")
    assert body.get("ok") is False and isinstance(body.get("error"), str) and body["error"], body


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", None])
def test_non_json_content_type_on_queue_templates_is_a_json_415(ctype):
    c, h = _client_and_token()
    if ctype:
        h["Content-Type"] = ctype
    r = c.post("/api/queue_templates", data='{"name":"l7 ct","urls":[]}', headers=h)
    _assert_json_415(r, f"POST /api/queue_templates Content-Type={ctype}")


def test_non_json_content_type_on_a_second_api_route_is_a_json_415():
    c, h = _client_and_token()
    r = c.post("/api/sites", data='{"name":"l7-site"}', headers={**h, "Content-Type": "text/plain"})
    _assert_json_415(r, "POST /api/sites")


def test_application_json_is_still_accepted():
    """Positive control: the same body as application/json creates the template (200 JSON)."""
    c, h = _client_and_token()
    r = c.post("/api/queue_templates", json={"name": "l7 ok", "urls": ["https://l7.invalid/a"]}, headers=h)
    assert r.status_code == 200 and r.mimetype == "application/json", (r.status_code, r.get_data()[:200])
    assert r.get_json().get("ok") is not False, r.get_json()


def test_non_api_path_keeps_the_default_error_page():
    """Negative control: the 415 handler only rewrites API paths; elsewhere Flask's error stands."""
    from werkzeug.exceptions import UnsupportedMediaType
    from bulk_downloader import app as A
    handler = A.app.error_handler_spec[None][415][UnsupportedMediaType]
    e = UnsupportedMediaType()
    with A.app.test_request_context("/not-api/x", method="POST"):
        assert handler(e) is e
    for path in ("/api/x", "/cockpit/api/x"):
        with A.app.test_request_context(path, method="POST"):
            out = handler(e)
            assert isinstance(out, tuple), f"BD_Q_MISC_L7_HTML_415: handler left {path} on the HTML page"
            resp, status = out
            assert status == 415 and resp.get_json()["ok"] is False, path
