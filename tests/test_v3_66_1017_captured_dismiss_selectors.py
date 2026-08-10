"""@1017. A CAPTURED template can finally express its interstitial.

The second half of item E. @1016 built the runtime: two declared scopes
(`dismiss_selectors` per URL, `dismiss_selectors_login` once in do_login) and
one shared loop. What still could not happen is the thing 15.83 lists FIRST --
"captured templates learn to emit dismiss_selectors". `_html_selectors` emitted
login / quality / download groups and no dismissal vocabulary at all, so the
Gamma wall could only ever be hand-written into
`site_templates/_data_players.py`. 15.79 measured exactly that: "zero dismiss
vocabulary in the WACZ pipeline".

THE CLASSIFICATION IS ADVISORY AND THE CODE SAYS SO. 15.79 also recorded the
reason E was refused once: "the corpus's 'No thanks' marks both true
interstitials and ordinary upsell modals." That is still true and no amount of
regex fixes it -- the same words appear on a post-login wall and on a
mid-session upsell. So the builder proposes a bucket and the draft stays
`draft_requires_review`; it does not decide. A recognizer that claimed
certainty here would be asserting something the evidence does not carry, and
the operator would find out on a live site.

WHAT MAKES THE SPLIT DEFENSIBLE AT ALL is that the two vocabularies barely
overlap in practice: a consent gate says Accept / I Agree / age, and a wall
says Continue to / No Thanks / Skip. Where a phrase is genuinely ambiguous it
goes to `per_page`, because that is the SAFE default in both directions --
a per-page selector that should have been a wall selector still fires (it just
costs its timeout per URL), whereas a wall selector that should have been
per-page STOPS FIRING on the pages that needed it. Asymmetric cost, so the
default follows the cheaper mistake. `test_an_ambiguous_phrase_defaults_to_the
_SAFE_bucket` pins that direction.

DENOMINATOR. Every recognizer test below runs against HTML this file builds, so
a change to the corpus cannot quietly empty the subject. The two
"can-see-a-positive" tests exist because a recognizer that matches nothing
reports "no interstitial on this page" truthfully and uselessly.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
for p in (str(REPO), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def _btw():
    import build_template_from_wacz as B
    return B


# ── the recognizer exists and can see each shape ──────────────────

_WALL_HTML = """
<html><body>
  <a class="SkipPageButton-ButtonLink" href="/members">No Thanks. Continue</a>
</body></html>
"""

_CONSENT_HTML = """
<html><body>
  <button class="cookie-accept">I Agree</button>
</body></html>
"""

_AGE_HTML = """
<html><body>
  <button id="age-gate-yes">I am 18 or older</button>
</body></html>
"""


def _dismiss(html):
    return _btw()._dismiss_selectors(html)


def test_the_recognizer_can_SEE_a_login_wall():
    d = _dismiss(_WALL_HTML)
    assert d.get("login_wall"), (
        "the recognizer found no login wall in a page that is nothing but one: %r" % d)


def test_the_recognizer_can_SEE_a_consent_gate():
    d = _dismiss(_CONSENT_HTML)
    assert d.get("per_page"), (
        "the recognizer found no consent gate in a page that is nothing but one: %r" % d)


def test_an_age_gate_is_per_page_not_a_login_wall():
    """An age gate can appear on any content page, so it is per-URL. Putting it
    in the wall bucket would stop it firing exactly where it is needed."""
    d = _dismiss(_AGE_HTML)
    assert d.get("per_page"), d
    assert not d.get("login_wall"), (
        "an age gate was classified as a login wall: %r" % d)


def test_a_page_with_no_interstitial_emits_NOTHING():
    """The over-sensitive direction, and the one that costs real seconds: every
    emitted selector line is a 3s timeout per URL for a site that has no
    interstitial at all. A recognizer that fires on ordinary markup would tax
    every captured template."""
    plain = ("<html><body><h1>Scene</h1><a href='/dl'>Download</a>"
             "<button>Play</button><a href='/next'>Continue</a></body></html>")
    d = _dismiss(plain)
    assert d == {} or not any(d.values()), (
        "emitted interstitial selectors for a page that has none: %r" % d)


def test_an_ambiguous_phrase_defaults_to_the_SAFE_bucket():
    """15.79: "the corpus's 'No thanks' marks both true interstitials and
    ordinary upsell modals." A bare "No thanks" with no wall context is
    therefore NOT evidence of a login wall.

    per_page is the safe default because the two mistakes cost differently: a
    wall selector misfiled as per-page still fires (it costs a timeout per
    URL), while a per-page selector misfiled as a wall STOPS FIRING on the
    pages that needed it. The default follows the cheaper mistake."""
    ambiguous = "<html><body><button class='upsell-x'>No thanks</button></body></html>"
    d = _dismiss(ambiguous)
    assert not d.get("login_wall"), (
        "a bare 'No thanks' with no members-area context was called a login "
        "wall; 15.79 measured that this phrase marks upsells too: %r" % d)


def test_the_emitted_selectors_are_usable_by_the_runtime_loop():
    """A selector the runtime cannot parse is worse than none -- it costs its
    timeout and can never match. Every emitted line must survive
    interstitial.selector_lines and be a non-empty single line."""
    from bulk_downloader import interstitial
    for html in (_WALL_HTML, _CONSENT_HTML, _AGE_HTML):
        d = _dismiss(html)
        for bucket, sels in d.items():
            for s in sels:
                assert isinstance(s, str) and s.strip() == s and s, (bucket, s)
                assert "\n" not in s, (
                    "a selector containing a newline would be split into two "
                    "unusable halves by the runtime loop: %r" % s)
                assert interstitial.selector_lines(s) == [s], (bucket, s)


# A ZERO-ENTROPY REPEAT, NOT A REALISTIC-LOOKING STRING, and the obvious
# "improvement" is to make it look real again. CLAUDE.md section 7: gitleaks
# scans the PR's whole commit range, so a plausible 16-hex value in a test
# ABOUT credentials is itself a finding. Measured: the first version of this
# fixture used one, gitleaks scored it generic-api-key at entropy 4.0, and the
# `gates` job failed -- on the test written to prove tokens never leak. It
# could not be fixed forward either; the range scan still sees the earlier
# commit, so the commit had to be amended rather than followed up.
_FAKE_TOKEN = "a" * 16


def test_no_secret_or_signed_url_can_reach_a_dismiss_selector():
    """The builder's standing guardrail: capture-derived values never persist.
    A dismissal selector is structural, so nothing resembling a token belongs
    in one."""
    html = ("<html><body><a class='SkipPageButton-ButtonLink' "
            "href='/members?token=" + _FAKE_TOKEN + "'>No Thanks. Continue</a>"
            "</body></html>")
    d = _dismiss(html)
    blob = repr(d)
    assert _FAKE_TOKEN not in blob, blob
    assert "token=" not in blob, blob


# ── it reaches the built draft ────────────────────────────────────

def _draft_selectors_for(html):
    """Build a real draft through build_template's own selector assembly."""
    B = _btw()
    sel = B._html_selectors(html)
    d = B._dismiss_selectors(html)
    if d:
        sel["dismiss"] = d
    return sel


def test_the_draft_carries_the_dismiss_group():
    sel = _draft_selectors_for(_WALL_HTML)
    assert "dismiss" in sel, (
        "the built draft has no dismiss group, so a reviewer cannot see or "
        "promote what was recognised: %r" % sorted(sel))


def test_build_template_ITSELF_emits_the_group_not_just_this_test():
    """The half a helper-only test cannot see. `_dismiss_selectors` existing and
    `build_template` CALLING it are different facts, and a test that assembles
    the draft by hand (as _draft_selectors_for does) proves only the first."""
    import ast
    src = (REPO / "tools" / "build_template_from_wacz.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_template")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_dismiss_selectors" in called, (
        "build_template never calls _dismiss_selectors -- the recognizer would "
        "exist and no captured template would ever carry its output")


# ── the bridge onto the live config keys ──────────────────────────

def _wire():
    from bulk_downloader import capture_login_wire as W
    return W


def test_the_bridge_maps_each_bucket_to_its_OWN_config_key():
    W = _wire()
    cfg = {}
    filled = W.apply_draft_dismiss_selectors(
        cfg, {"login_wall": ["a.wall"], "per_page": ["button.consent"]})
    assert cfg.get("dismiss_selectors_login") == "a.wall", cfg
    assert cfg.get("dismiss_selectors") == "button.consent", cfg
    assert set(filled) == {"dismiss_selectors_login", "dismiss_selectors"}, filled


def test_the_bridge_writes_ONE_SELECTOR_PER_LINE():
    """The runtime splits on newlines. Joining with ", " would collapse N
    selectors into one locator -- which still works, but silently changes the
    cost model @1016 measured (1 locator = 3.00s; N lines = 3.00s each) and
    makes a single unparseable selector poison the whole group."""
    W = _wire()
    cfg = {}
    W.apply_draft_dismiss_selectors(cfg, {"per_page": ["a.one", "b.two", "c.three"]})
    assert cfg["dismiss_selectors"] == "a.one\nb.two\nc.three", repr(cfg["dismiss_selectors"])


def test_the_bridge_PRESERVES_an_operator_value():
    """Same contract as apply_draft_login_selectors: a picked selector is a
    fallback seed, never an override."""
    W = _wire()
    cfg = {"dismiss_selectors": "button.mine"}
    filled = W.apply_draft_dismiss_selectors(cfg, {"per_page": ["a.theirs"]})
    assert cfg["dismiss_selectors"] == "button.mine", cfg
    assert "dismiss_selectors" not in filled, filled


def test_the_bridge_tolerates_garbage():
    W = _wire()
    for bad in (None, [], "nope", {"login_wall": None}, {"per_page": [""]}):
        cfg = {}
        assert W.apply_draft_dismiss_selectors(cfg, bad) == []
        assert cfg == {}, (bad, cfg)


def test_an_empty_bucket_does_not_write_an_empty_key():
    """Writing "" would make the key present-and-blank, which reads as
    "configured with nothing" rather than "not configured" everywhere that
    treats blank as unset."""
    W = _wire()
    cfg = {}
    W.apply_draft_dismiss_selectors(cfg, {"login_wall": [], "per_page": ["a.x"]})
    assert "dismiss_selectors_login" not in cfg, cfg
