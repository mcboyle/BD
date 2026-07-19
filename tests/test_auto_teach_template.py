"""Tests for v3.62.2 auto-teach behaviour: a site with an applied
template skips the first-run teach prompt; teach still triggers when
there is neither a template nor learned download selectors.

These test the preflight predicate logic directly -- the same boolean
the runner's Phase 41.5 block uses -- without spawning browsers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _should_defer_teach(config):
    """Mirror of the runner's Phase 41.5 decision (v3.62.2).

    Returns True when start() should defer worker spawn and flag the
    first URL needs_review for teaching. False = run normally.
    """
    if not config.get("auto_teach_first_run", True):
        return False
    learned_dl = (config.get("learned") or {}).get("download") or {}
    has_dl = bool(learned_dl.get("trigger_selectors")
                  or learned_dl.get("row_selectors"))
    has_template = bool(config.get("applied_template"))
    return (not has_dl) and (not has_template)


def test_blank_site_defers_to_teach():
    # No template, no learned selectors -> teach prompt (unchanged).
    assert _should_defer_teach({}) is True


def test_templated_site_skips_teach():
    # v3.62.2: template applied -> run normally, no teach window.
    assert _should_defer_teach({"applied_template": "video_js"}) is False


def test_templated_site_with_empty_learned_block_still_skips():
    # Many templates carry only URL patterns, no learned.download. The
    # old code keyed solely on learned selectors and would still teach;
    # the template stamp is what fixes that.
    cfg = {"applied_template": "kvs_engine",
           "learned": {"download": {}}}
    assert _should_defer_teach(cfg) is False


def test_learned_selectors_skip_teach_without_template():
    # A hand-taught site (learned selectors, no template stamp) runs
    # normally -- pre-existing behaviour, must not regress.
    cfg = {"learned": {"download": {"trigger_selectors": [".dl"]}}}
    assert _should_defer_teach(cfg) is False


def test_row_selectors_alone_skip_teach():
    cfg = {"learned": {"download": {"row_selectors": [".row a"]}}}
    assert _should_defer_teach(cfg) is False


def test_auto_teach_disabled_never_defers():
    # auto_teach_first_run off -> always run, regardless of template.
    assert _should_defer_teach({"auto_teach_first_run": False}) is False


def test_empty_template_string_does_not_count():
    # A blank stamp must not be mistaken for an applied template.
    assert _should_defer_teach({"applied_template": ""}) is True


def test_apply_template_stamps_config():
    """The /templates/apply path must record applied_template so the
    runner can see it. Exercised against the real app helper."""
    import os
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app as a
    from bulk_downloader.db import db_init
    Path("screenshots").mkdir(exist_ok=True)
    db_init()
    a.runners.clear(); a.s_cfg.clear(); a.s_meta.clear()
    sid, err = a._create_site({"name": "Tmpl Test",
                               "login_url": "https://t.example.com"})
    assert err is None and sid
    ok, msg = a._apply_template_by_id(sid, "video_js")
    assert ok, msg
    assert a.s_cfg[sid].get("applied_template") == "video_js"
