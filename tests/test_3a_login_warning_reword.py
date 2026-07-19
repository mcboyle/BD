"""3a (v3.66.513): reword the false "login may fail" wizard warning.

RED-first. `site_editor.validate_config` (the /api/sites/validate inline
validator) emits a per-field "login may fail" warning for EACH blank login
selector when credentials are set. For a host with a CURATED login template the
selectors are auto-filled on save (`_auto_pick_templates`), so the warning is a
false alarm. Asserts the reworded behavior:

  - curated host (auth.reptyle.com) -> ONE informational auto-fill line that
    names the template, and NO "login may fail".
  - non-curated host -> ONE collapsed actionable warning (pristine emits three),
    no auto-fill claim, no "login may fail".

Pure function (no Flask) -> straightforward in the custom runner.
"""
from bulk_downloader.site_editor import validate_config


def test_curated_host_suppresses_login_may_fail():
    cfg = {
        "name": "reptyle",
        "login_url": "https://auth.reptyle.com/login",
        "username": "u@example.com",
        "password": "secret",
        # all three login selectors intentionally blank
    }
    res = validate_config(cfg)
    warns = res.get("warnings", [])
    joined = " ".join(warns)
    assert "login may fail" not in joined, warns
    autofill = [w for w in warns if "auto-filled" in w]
    assert len(autofill) == 1, warns
    assert "login template" in autofill[0], autofill
    # the curated template name should be surfaced
    assert "Reptyle" in autofill[0], autofill


def test_noncurated_host_collapses_to_one_actionable_warning():
    cfg = {
        "name": "nowhere",
        "login_url": "https://no-such-host.example.invalid/login",
        "username": "u@example.com",
        "password": "secret",
    }
    res = validate_config(cfg)
    warns = res.get("warnings", [])
    sel_warns = [w for w in warns
                 if "selector" in w.lower() and "login" in w.lower()]
    # collapsed: exactly ONE selector warning (pristine emits three)
    assert len(sel_warns) == 1, warns
    assert "auto-filled" not in sel_warns[0], sel_warns
    assert "login may fail" not in " ".join(warns), warns
