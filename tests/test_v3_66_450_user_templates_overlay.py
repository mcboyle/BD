"""RED-first regression for the v3.66.448 site_templates shim (DECOMP-LEAF cut 1).

cut-448 moved the accessors into the site_templates/ subpackage but kept
`from . import user_templates`, which there resolves to the non-existent
bulk_downloader.site_templates.user_templates instead of the sibling
bulk_downloader.user_templates. The ImportError was swallowed by a broad
`except Exception`, silently dropping the user-template overlay.

These assert the overlay is live through the public `templates` shim. RED on the
unfixed `from .` tree (user template absent); GREEN after the `from ..` depth fix.
"""
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def _temp_user_templates():
    from bulk_downloader import user_templates as ut
    with tempfile.TemporaryDirectory() as td:
        orig = ut.USER_TEMPLATES_FILE
        ut.USER_TEMPLATES_FILE = Path(td) / "user_templates.json"
        try:
            yield ut
        finally:
            ut.USER_TEMPLATES_FILE = orig


def _good_learned():
    return {"download": {"row_selectors": ["a.btn[href]"], "url_attribute": "href"}}


def test_site_templates_get_resolves_user_overlay():
    from bulk_downloader import templates as tpls
    with _temp_user_templates() as ut:
        ok, t = ut.save_user_template("Findme450", "d", [], _good_learned())
        assert ok
        got = tpls.get(t["id"])
        assert got is not None, "user template not found via shim (overlay import broken)"
        assert got["name"] == "Findme450"


def test_site_templates_list_includes_user_overlay():
    from bulk_downloader import templates as tpls
    with _temp_user_templates() as ut:
        ut.save_user_template("Mine450", "d", [], _good_learned())
        items = tpls.list_templates()
        user_items = [i for i in items if i.get("source") == "user"]
        assert len(user_items) == 1, "user overlay dropped from list_templates"
        assert user_items[0]["name"] == "Mine450"


def test_site_templates_suggest_includes_user_overlay():
    from bulk_downloader import templates as tpls
    with _temp_user_templates() as ut:
        ok, t = ut.save_user_template("Match450", "d", [r"example450\.com"], _good_learned())
        matches = tpls.suggest_for_url("https://example450.com/x")
        assert t["id"] in matches, "user overlay dropped from suggest_for_url"
