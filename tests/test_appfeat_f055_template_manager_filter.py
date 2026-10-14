"""F055 CockpitTemplateManager (/cockpit/template-manager): list/filter.

AGY FAIL (F055-agy-feat-31-r2, live v3.66.1841): the page had 0 <input>/<select>
(no filter), and a draft whose host has no reviewed gold still got a
"drift vs gold" link that could only answer HTTP 400
{"error":"no reviewed gold for this host"}.

Now the page carries a client-side filter box (host/status, no form, no button:
the page stays read-only) and links drift only when the endpoint can answer.

SELF-SEEDING like test_p3_template_manager_ui: two drafts are written under
templates/drafts/ with names no real draft uses, and only those are removed.
"""
BD_GATE_SCOPE = "module"

import json
import re
import sys
from pathlib import Path

from flask import Flask

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from bulk_downloader.app_template_manager_ui import register_routes  # noqa: E402

_DRAFTS = _REPO / "templates" / "drafts"
_NOGOLD = "f055-nogold.template-draft.json"
_NOGOLD_HOST = "f055-nogold.invalid"
_WITHGOLD = "f055-withgold.template-draft.json"


def _client():
    app = Flask(__name__)
    register_routes(app)
    return app.test_client()


def _with_seeded_drafts(fn):
    gold = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
    cand = json.loads(gold.read_text("utf-8"))
    cand["status"] = "draft_review_required"
    seeds = {_NOGOLD: dict(cand, host=_NOGOLD_HOST),
             _WITHGOLD: dict(cand, host="app.reptyle.com")}
    _DRAFTS.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for name, body in seeds.items():
            p = _DRAFTS / name
            assert not p.exists(), f"refusing to overwrite a real draft: {p}"
            p.write_text(json.dumps(body), "utf-8")
            created.append(p)
        fn(_client())
    finally:
        for p in created:
            if p.is_file():
                p.unlink()


def _draft_row(body, host):
    m = re.search(r"<tr[^>]*>(?:(?!</tr>).)*?<td[^>]*>"
                  + re.escape(host) + r"</td>.*?</tr>", body, re.S)
    assert m, f"no list row for host {host}"
    return m.group(0)


def test_every_rendered_drift_link_answers_and_nogold_draft_is_unlinked():
    def body(c):
        page = c.get("/cockpit/template-manager").get_data(as_text=True)
        links = re.findall(r'href="(/api/template_manager/drift\?file=[^"]+)"', page)
        assert any(_WITHGOLD in u for u in links), "draft with a reviewed gold lost its drift link"
        for url in links:
            r = c.get(url)
            assert r.status_code == 200, (
                "rendered drift link %s returns HTTP %d %s"
                % (url, r.status_code, r.get_data(as_text=True)[:80]))
        row = _draft_row(page, _NOGOLD_HOST)
        assert "template_manager/drift" not in row, "no-gold draft still links drift"
        assert "no reviewed gold" in row
        inv = c.get("/api/template_manager/inventory").get_json()
        by_host = {r["host"]: r for r in inv["dirs"]["drafts"]}
        assert by_host[_NOGOLD_HOST]["drift_available"] is False
        assert by_host["app.reptyle.com"]["drift_available"] is True
    _with_seeded_drafts(body)


def test_page_has_a_filter_control_over_host_and_status():
    def body(c):
        page = c.get("/cockpit/template-manager").get_data(as_text=True)
        box = re.search(r"<input[^>]*id=\"tm-filter\"[^>]*>", page)
        assert box, "no filter control (<input id=tm-filter>) on the template manager page"
        assert 'type="search"' in box.group(0)
        assert "addEventListener('input'" in page
        row = _draft_row(page, _NOGOLD_HOST)
        assert "data-tm-filter='%s draft_review_required'" % _NOGOLD_HOST in row
        # still read-only: the filter is client-side, no form and no button
        assert "<form" not in page.lower() and "<button" not in page.lower()
    _with_seeded_drafts(body)
