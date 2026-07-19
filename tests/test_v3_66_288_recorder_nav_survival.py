"""v3.66.288 — recorder survives a same-origin full-page navigation.

Defect (latent companion to v3.66.287): the in-page recorder keeps its
harvest in page-global state (``window.__pwrec_clicks`` /
``__pwrec_inputs``). A full document navigation destroys ``window``, so a
TWO-STEP login (enter username -> click Next / navigate -> enter password)
lost the page-1 username harvest entirely; ``harvest_recordings`` then read
only the page-2 password. Symptom: same "password fills but not the
username" — but caused by navigation, not by the type whitelist (#287).

Fix: the recorder rehydrates its arrays from ``sessionStorage`` on
(re)install and persists each event back, so same-origin same-tab
navigations accumulate. SECRET POSTURE: only STRUCTURE is written to
storage — the raw value channels (``_input_value`` and ``text``) are
stripped from the persisted copy, so no typed credential is ever written
to sessionStorage. The store is cleared when ``harvest_recordings`` reads
(capture done -> forget), so it can't leak into a later capture in the
same tab.

NOTE: cross-ORIGIN two-step flows (login.site.com -> site.com) don't share
sessionStorage and are NOT covered by this fix. End-to-end browser
navigation survival is not runtime-testable in this sandbox; these are
source-contract tests (same precedent as the existing
test_recorder_js_* checks) plus a pure classify_login guarantee.
"""

from pathlib import Path

from bulk_downloader.learn import classify_login

_LEARN_PY = Path(__file__).resolve().parent.parent / "bulk_downloader" / "learn.py"


def _learn_impl_src():
    """Concatenated source of the decomposed learn_impl/ package. learn.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 5); RECORDER_JS/TEACH_OVERLAY_JS and the
    function bodies live in learn_impl/*.py, so source/structure tests read the package."""
    from pathlib import Path as _P
    import bulk_downloader.learn_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))
LOGIN = "https://example.com/login"


# ── user-facing guarantee: a structure-only username record (value stripped
#    by the cross-nav persistence) still yields a selector, so replay
#    autofills the username on a two-step login. ────────────────────────────

def test_structure_only_username_still_yields_selector():
    """After a navigation, the rehydrated page-1 username record carries
    structure but no value. It must still classify into user_field so the
    learned login template can autofill the username on replay."""
    harvest = {"inputs": [
        # page-1 username, rehydrated structure-only (no _input_value, blank text)
        {"tag": "input", "type": "text", "name": "username", "id": "user",
         "text": "", "_input_value": "", "url": LOGIN},
        # page-2 password, fresh in-memory (full value)
        {"tag": "input", "type": "password", "name": "password",
         "_input_value": "secret", "url": "https://example.com/login/step2"},
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["user_field"], "structure-only username must still yield selectors"
    assert r["pass_field"]
    assert r["password_value"] == "secret"


# ── RECORDER_JS source contracts ─────────────────────────────────────────

def test_recorder_rehydrates_from_session_storage():
    """On (re)install the recorder must initialise its arrays FROM
    sessionStorage (so a fresh post-navigation window recovers prior
    events) rather than always resetting to []."""
    src = _learn_impl_src()
    pos = src.find("RECORDER_JS")
    body = src[pos:pos + 4000]
    assert "sessionStorage" in body, "recorder must use sessionStorage"
    # The arrays are seeded from a loader, not a bare [] literal.
    assert "__pwrec_clicks = _pwrecLoad(" in body
    assert "__pwrec_inputs = _pwrecLoad(" in body


def test_recorder_persists_events_to_storage():
    """Each recorded click/input must be persisted back to storage so it
    survives the next navigation."""
    src = _learn_impl_src()
    start = src.find("RECORDER_JS = r")
    end = src.find("def install_recorder", start)
    body = src[start:end]
    # persist helper exists and is called from both listeners
    assert "_pwrecPersist" in body
    assert body.count("_pwrecPersist('") >= 2  # called from click + input listeners


def test_recorder_storage_copy_strips_value_channels():
    """SECRET POSTURE: the copy written to sessionStorage must blank the
    raw value channels (_input_value AND text) — no typed credential may
    be persisted to browser storage."""
    src = _learn_impl_src()
    pos = src.find("_pwrecRedact")
    assert pos > 0, "a redaction helper must exist for the stored copy"
    body = src[pos:pos + 400]
    assert "_input_value" in body and "''" in body
    assert "c.text" in body or "text" in body


def test_recorder_storage_ops_are_guarded():
    """All sessionStorage access must be wrapped so a blocked/absent
    storage degrades to the prior in-memory-only behaviour."""
    src = _learn_impl_src()
    pos = src.find("_pwrecLoad")
    body = src[pos:pos + 600]
    assert "try" in body and "catch" in body


def test_harvest_recordings_clears_store_after_read():
    """harvest_recordings must clear the persisted store after reading
    (capture done -> forget) so stale records can't leak into a later
    capture reusing the same tab."""
    src = _learn_impl_src()
    pos = src.find("def harvest_recordings")
    body = src[pos:pos + 1200]
    assert "removeItem" in body or "clear" in body, \
        "harvest_recordings must clear the sessionStorage store after reading"
