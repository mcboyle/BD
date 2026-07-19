"""v3.66.289 — wire /capture login picks into the live-login config.

Defect: the /capture SPA login pickers populate a DRAFT template login block
({email, password, submit}); the live login (do_login) reads ONLY
config.user_field / pass_field / submit_btn (+ learned.login). Nothing mapped
the two, so picked login selectors never drove the actual login — the OPV
verification login fell back to the 154-selector list instead of the operator's
picks.

Fix: a pure mapper applied where the draft becomes operative for login
(test_extract's draft_test_override set). PRESERVE-IF-PRESENT: only fills a
config key that is currently empty/blank, so a manually-set or teach-learned
selector is never clobbered.
"""

from bulk_downloader.capture_login_wire import apply_draft_login_selectors


def test_maps_empty_cfg():
    cfg = {}
    filled = apply_draft_login_selectors(cfg, {
        "email": "input#user",
        "password": "input#pass",
        "submit": "button[type=submit]",
    })
    assert cfg["user_field"] == "input#user"
    assert cfg["pass_field"] == "input#pass"
    assert cfg["submit_btn"] == "button[type=submit]"
    assert set(filled) == {"user_field", "pass_field", "submit_btn"}


def test_preserve_if_present():
    """An existing user_field (operator-set or teach-learned) is never clobbered."""
    cfg = {"user_field": "input#existing"}
    filled = apply_draft_login_selectors(cfg, {
        "email": "input#user", "password": "input#pass",
    })
    assert cfg["user_field"] == "input#existing"
    assert cfg["pass_field"] == "input#pass"
    assert "user_field" not in filled
    assert "pass_field" in filled


def test_whitespace_only_existing_counts_as_empty():
    cfg = {"user_field": "   "}
    filled = apply_draft_login_selectors(cfg, {"email": "input#user"})
    assert cfg["user_field"] == "input#user"
    assert "user_field" in filled


def test_tolerates_missing_and_nonstring_values():
    cfg = {}
    filled = apply_draft_login_selectors(cfg, {
        "email": "", "password": None, "submit": 123,
    })
    assert filled == []
    assert not cfg.get("user_field")
    assert not cfg.get("pass_field")
    assert not cfg.get("submit_btn")


def test_tolerates_non_dict_block():
    cfg = {}
    assert apply_draft_login_selectors(cfg, None) == []
    assert apply_draft_login_selectors(cfg, "nope") == []
    assert apply_draft_login_selectors(cfg, ["x"]) == []
    assert cfg == {}


def test_partial_block_only_fills_present_keys():
    cfg = {}
    filled = apply_draft_login_selectors(cfg, {"email": "input#user"})
    assert cfg["user_field"] == "input#user"
    assert "pass_field" not in cfg
    assert "submit_btn" not in cfg
    assert filled == ["user_field"]
