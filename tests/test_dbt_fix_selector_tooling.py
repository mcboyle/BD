"""dbt-fix: selector tooling disagreed with the browser or lost data.

Each test names its row (R3..R10 of the deep-adapters-browser-teach findings)
in the assertion message, and each defect has a control beside it that passes
before and after the fix.
"""
import errno
import json
import os
import time

import pytest
from playwright.sync_api import Error as PWError

from bulk_downloader import selector_chains as sc
from bulk_downloader import selector_check
from bulk_downloader import selector_playground as sp
from bulk_downloader import selector_versions as sv
from bulk_downloader import template_selector_verifier as tsv
from bulk_downloader.login_impl import replay as login_replay

BD_GATE_SCOPE = "module"


# ── R3 / R4: try_step on a page that changes after load ─────────────

class _LateLocator:
    """Matches nothing until ``delay`` seconds after the page was made."""

    def __init__(self, page, sel=""):
        self._page = page
        self._sel = sel

    @property
    def first(self):
        return self

    def count(self):
        self._page.count_calls += 1
        if self._page.delays is not None:
            if self._sel not in self._page.delays:
                return 0
            delay = self._page.delays[self._sel]
        else:
            delay = self._page.delay
        return 1 if time.monotonic() - self._page.t0 >= delay else 0

    def fill(self, value, timeout=None):
        self._page.fills.append((self._sel, value))

    def click(self, timeout=None):
        self._page.clicks += 1
        self._page.clicked.append(self._sel)


class _Page:
    url = "http://t/login"

    def __init__(self, *, delay=0.0, delays=None, source="", visible="",
                 no_body=False):
        self.t0 = time.monotonic()
        self.delay = delay
        self.delays = delays
        self.count_calls = 0
        self.clicks = 0
        self.clicked = []
        self.fills = []
        self._source = source
        self._visible = visible
        self._no_body = no_body

    def locator(self, sel):
        return _LateLocator(self, sel)

    def content(self):
        return self._source

    def inner_text(self, selector, *, timeout=None, **_kwargs):
        assert selector == "body"
        if self._no_body:
            wait_ms = 30000.0 if timeout is None else float(timeout)
            time.sleep(min(wait_ms, 2000.0) / 1000.0)
            raise PWError(f"Timeout {wait_ms}ms exceeded waiting for body")
        return self._visible


@pytest.mark.parametrize("raw", [
    "#late",
    {"selector": "#late", "timeout_ms": 3000},
    {"selector": "#late", "timeout_ms": 2000},
])
def test_r3_step_waits_its_timeout_for_a_late_element(raw):
    page = _Page(delay=0.6)
    step = sc.parse_step(raw)
    t0 = time.monotonic()
    outcome, detail = sc.try_step(step, page, "click")
    took = time.monotonic() - t0
    assert outcome == "ok", f"R3-NO-WAIT: {raw!r} -> {outcome!r} {detail!r} after {took:.3f}s"
    assert page.clicks == 1, "R3-NO-WAIT: the late element was never clicked"
    assert 0.55 <= took < 2.0, f"R3-WAIT-LENGTH: took {took:.3f}s"


def test_r3_wired_replay_waits_for_late_button_600ms():
    page = _Page(delays={"#user": 0.0, "#pass": 0.0, "#late": 0.6})
    cfg = {"username": "u", "password": "p",
           "user_field": "#user", "pass_field": "#pass", "submit_btn": "#late"}
    t0 = time.monotonic()
    ok, msg = login_replay._attempt_headless_fill_submit(page, cfg, timeout=0.1)
    took = time.monotonic() - t0
    assert ok and page.clicked == ["#late"], (
        f"R3-NO-WAIT: wired replay {ok!r} {msg!r} clicked={page.clicked!r} after {took:.3f}s")
    assert page.fills == [("#user", "u"), ("#pass", "p")]
    assert 0.55 <= took < 2.0, f"R3-WAIT-LENGTH: took {took:.3f}s"


def test_r3_abort_step_does_not_abort_on_an_element_still_rendering():
    page = _Page(delay=0.3)
    step = sc.parse_step({"selector": "#late", "timeout_ms": 2000,
                          "advance_on": ["threw"],
                          "on_unlisted_failure": "abort"})
    outcome, detail = sc.try_step(step, page, "click")
    assert outcome == "ok", f"R3-ABORT-EARLY: {outcome!r} {detail!r}"


def test_r3_never_appearing_element_is_not_found_after_the_timeout():
    page = _Page(delay=60)
    step = sc.parse_step({"selector": "#never", "timeout_ms": 300})
    t0 = time.monotonic()
    outcome, detail = sc.try_step(step, page, "click")
    took = time.monotonic() - t0
    assert outcome == "advance" and detail.startswith("not_found"), (
        f"R3-NEVER: {outcome!r} {detail!r}")
    assert 0.3 <= took < 1.5, f"R3-TIMEOUT-NOT-APPLIED: not_found after {took:.3f}s"
    assert page.clicks == 0


def test_r3_bare_string_never_appearing_is_not_found_after_default_timeout():
    page = _Page(delay=60)
    t0 = time.monotonic()
    outcome, detail = sc.try_step(sc.parse_step("#never"), page, "click")
    took = time.monotonic() - t0
    assert outcome == "advance" and detail.startswith("not_found"), (
        f"R3-NEVER: {outcome!r} {detail!r}")
    assert 2.8 <= took < 4.5, f"R3-TIMEOUT-NOT-APPLIED: not_found after {took:.3f}s"
    assert page.clicks == 0


def test_r3_control_present_element_succeeds_immediately():
    page = _Page(delay=0.0)
    t0 = time.monotonic()
    outcome, detail = sc.try_step(sc.parse_step("#now"), page, "click")
    took = time.monotonic() - t0
    assert outcome == "ok" and page.clicks == 1, f"{outcome!r} {detail!r}"
    assert page.count_calls == 1 and took < 0.2, (
        f"{page.count_calls} count() calls in {took:.3f}s")


def _text_step(pattern):
    return sc.parse_step({"selector": "#go", "timeout_ms": 200,
                          "post_condition": "text_appeared:" + pattern})


@pytest.mark.parametrize("pattern", ["Welcome back", "Logout"])
def test_r4_text_only_in_source_has_not_appeared(pattern):
    page = _Page(
        source="<html><script>var m='Welcome back'</script>"
               "<div style='display:none'>Logout</div><p>Sign in</p></html>",
        visible="Sign in")
    outcome, detail = sc.try_step(_text_step(pattern), page, "click")
    assert outcome == "advance" and detail.startswith("no_text"), (
        f"R4-SOURCE-TEXT: {pattern!r} counted as appeared: {outcome!r} {detail!r}")


def test_r4_control_no_body_page_respects_step_timeout():
    page = _Page(no_body=True, source="<html><frameset></frameset></html>")
    t0 = time.monotonic()
    outcome, detail = sc.try_step(_text_step("Welcome"), page, "exists")
    took = time.monotonic() - t0
    assert outcome == "advance" and detail.startswith("no_text"), (
        f"R4-NO-BODY: {outcome!r} {detail!r}")
    assert 0.15 <= took < 1.0, (
        f"R4-NO-BODY-TIMEOUT: no-body page waited {took:.3f}s for 200ms step")


def test_r4_control_visible_text_has_appeared():
    page = _Page(source="<p>Welcome back</p>", visible="Welcome back")
    outcome, detail = sc.try_step(_text_step("Welcome ba.k"), page, "click")
    assert outcome == "ok", f"R4-VISIBLE: {outcome!r} {detail!r}"


# ── R5: parse_step on malformed persisted entries ───────────────────

@pytest.mark.parametrize("bad", [7, 7.5, True, ["#a"], {"css": "#a"}, b"#a"])
def test_r5_non_string_selector_is_an_unusable_entry(bad):
    try:
        step = sc.parse_step({"selector": bad})
    except Exception as e:  # the defect: AttributeError from int.strip
        pytest.fail(f"R5-SELECTOR-TYPE: parse_step raised {type(e).__name__}: {e}")
    assert step is None, f"R5-SELECTOR-TYPE: {bad!r} parsed to {step!r}"


def test_r5_one_bad_entry_does_not_lose_the_chain():
    try:
        steps = sc.parse_chain([{"selector": 7}, "#a", {"selector": " #b "}])
    except Exception as e:
        pytest.fail(f"R5-CHAIN-DIES: parse_chain raised {type(e).__name__}: {e}")
    assert sc.selectors_of(steps) == ["#a", "#b"], f"R5-CHAIN-DIES: {steps!r}"


@pytest.mark.parametrize("bad", [-1, -5000, "-1", float("-inf"), float("inf")])
def test_r5_negative_timeout_falls_back_to_the_default(bad):
    try:
        step = sc.parse_step({"selector": "#a", "timeout_ms": bad})
    except Exception as e:
        pytest.fail(f"R5-TIMEOUT: parse_step raised {type(e).__name__}: {e}")
    assert step.timeout_ms == 3000, f"R5-TIMEOUT: {bad!r} kept as {step.timeout_ms!r}"


@pytest.mark.parametrize("good,want", [(250, 250), ("1500", 1500), (0, 0)])
def test_r5_control_usable_timeout_is_kept(good, want):
    assert sc.parse_step({"selector": "#a", "timeout_ms": good}).timeout_ms == want


# ── R6: selector_check agrees with a CSS engine ─────────────────────

_FORM = ('<form id="f"><input id="user" name="user" type="text">'
         '<button type="button" class="go">Log   in</button>'
         '<a class="x" href="/a b">help</a></form>')


@pytest.mark.parametrize("selector", ["input[name='nope']",
                                      "button[type='submit']",
                                      "a[href='/elsewhere']",
                                      "button:has-text('Sign up')",
                                      "button:has-text('Log in'):has-text('Sign up')"])
def test_r6_zero_match_attribute_selector_is_rejected(selector):
    r = selector_check.check_candidate(selector, _FORM)
    assert (r["status"], r["count"]) == ("reject", 0), (
        f"R6-APPROVES-ZERO-MATCH: {selector!r} -> {r!r}")


@pytest.mark.parametrize("selector", ["button:has-text('Log in')",
                                      'button:has-text("log IN")',
                                      "form #user", "input#user",
                                      "input[name='user']",
                                      "a[href='/a b']", "form > button.go"])
def test_r6_matching_selector_is_not_rejected(selector):
    r = selector_check.check_candidate(selector, _FORM)
    assert (r["status"], r["count"]) == ("approve", 1), (
        f"R6-REJECTS-MATCH: {selector!r} -> {r!r}")


@pytest.mark.parametrize("selector", ["div[", "text=Log in", "(//a)[1]",
                                      "a:has-text('help') span",
                                      "a:has-text('x') i:has-text('z')", ""])
def test_r6_selector_it_cannot_evaluate_goes_to_review(selector):
    r = selector_check.check_candidate(selector, _FORM)
    assert r["status"] == "review" and r["count"] is None, (
        f"R6-GUESSED: {selector!r} -> {r!r}")
    assert selector_check.merge_model(r["status"], True) == "review"


def test_r6_control_plain_selectors_keep_their_verdicts():
    assert selector_check.check_candidate("#user", _FORM)["status"] == "approve"
    assert selector_check.check_candidate("#nope", _FORM)["status"] == "reject"
    assert selector_check.check_candidate("#user:nth-child(9)", _FORM)["status"] == "warn"
    assert selector_check.resolve("button.go", _FORM)["matches"][0]["tag"] == "button"


# ── R7: selector_versions never overwrites the only copy ────────────

def _tpl(tid, sel):
    return {"id": tid, "selectors": {"row": sel}}


def _seed(tmp_path):
    for tid in ("t1", "t2", "t3"):
        assert sv.record_template_version(_tpl(tid, "a." + tid), base_dir=tmp_path)
    return tmp_path / sv.HISTORY_FILE


def _aside(tmp_path):
    return sorted(p for p in tmp_path.iterdir() if ".corrupt-" in p.name)


@pytest.mark.parametrize("damage", ["truncated", "not-an-object", "empty"])
def test_r7_damaged_store_is_set_aside_before_the_next_save(tmp_path, damage):
    store = _seed(tmp_path)
    whole = store.read_bytes()
    damaged = {"truncated": whole[:-5], "not-an-object": b'["t1"]', "empty": b""}[damage]
    store.write_bytes(damaged)
    vid = sv.record_template_version(_tpl("new", "a.new"), base_dir=tmp_path)
    kept = _aside(tmp_path)
    assert len(kept) == 1 and kept[0].read_bytes() == damaged, (
        f"R7-HISTORY-OVERWRITTEN: {damage}: no copy of the damaged store was kept "
        f"({[p.name for p in tmp_path.iterdir()]})")
    assert vid and list(json.loads(store.read_text())) == ["new"]


def test_r7_store_that_cannot_be_set_aside_is_not_overwritten(tmp_path, monkeypatch):
    store = _seed(tmp_path)
    damaged = store.read_bytes()[:-5]
    store.write_bytes(damaged)
    real_replace = os.replace

    def _replace(src, dst, *a, **k):
        if ".corrupt-" in str(dst):
            raise OSError(errno.EACCES, "denied")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(sv.os, "replace", _replace)
    vid = sv.record_template_version(_tpl("new", "a.new"), base_dir=tmp_path)
    assert vid is None and store.read_bytes() == damaged, (
        f"R7-HISTORY-OVERWRITTEN: vid={vid!r}, store now {store.read_bytes()[:60]!r}")


def test_r7_failed_save_leaves_no_temp_file(tmp_path, monkeypatch):
    store = _seed(tmp_path)
    whole = store.read_bytes()

    def _dump(doc, fh, **_k):
        fh.write('{"t1": [')
        raise OSError(errno.EFBIG, "File too large")

    monkeypatch.setattr(sv.json, "dump", _dump)
    vid = sv.record_template_version(_tpl("new", "a.new"), base_dir=tmp_path)
    assert vid is None and store.read_bytes() == whole
    left = sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp"))
    assert left == [], f"R7-TEMP-LEFT: {left}"


def test_r7_control_sound_store_appends_and_sets_nothing_aside(tmp_path):
    store = _seed(tmp_path)
    assert sv.record_template_version(_tpl("new", "a.new"), base_dir=tmp_path)
    assert sorted(json.loads(store.read_text())) == ["new", "t1", "t2", "t3"]
    assert _aside(tmp_path) == []
    assert not (tmp_path / (sv.HISTORY_FILE + ".tmp")).exists()


def test_r7_control_first_save_into_an_empty_dir(tmp_path):
    assert sv.record_template_version(_tpl("t1", "a.t1"), base_dir=tmp_path)
    assert _aside(tmp_path) == []


# ── R9: selector_playground without cssselect ───────────────────────

_PLAY = ('<html><body><div><p>1</p><p>2</p><span><p>3</p></span></div>'
         '<a href="/a b">x</a><a href="/c" title="it\'s">y</a>'
         '<a class="k" href="/a > b"><i>z</i></a></body></html>')


def _fallback_count(monkeypatch, selector):
    pytest.importorskip("lxml")
    monkeypatch.setattr(sp, "_HAS_CSSSELECT", False)
    return sp.evaluate_selectors(_PLAY, [selector])[0]


@pytest.mark.parametrize("selector,want", [
    ("div > p", 2), ("div>p", 2), ("div  >  p", 2),
    ("a[href='/a b']", 1), ('a[title="it\'s"]', 1), ("a[href='/a > b'] > i", 1),
])
def test_r9_fallback_counts_what_the_selector_says(monkeypatch, selector, want):
    r = _fallback_count(monkeypatch, selector)
    assert (r["count"], r["error"]) == (want, ""), (
        f"R9-FALLBACK-WRONG: {selector!r} -> xpath "
        f"{sp._css_to_xpath_simple(selector)!r}, {r['count']} matches, want {want}")


@pytest.mark.parametrize("selector", ["a:hover", "a[href^='/a']", "p:first-child",
                                      "div >", "> p", "a[href='/c", "div + a"])
def test_r9_fallback_refuses_what_it_cannot_translate(monkeypatch, selector):
    r = _fallback_count(monkeypatch, selector)
    assert r["count"] == 0 and "too complex" in r["error"], (
        f"R9-FALLBACK-GUESSED: {selector!r} -> xpath "
        f"{sp._css_to_xpath_simple(selector)!r}, {r!r}")


def test_r9_parenthesised_xpath_is_xpath():
    pytest.importorskip("lxml")
    r = sp.evaluate_selectors(_PLAY, ["(//a)[1]"])[0]
    assert (r["syntax"], r["count"], r["error"]) == ("xpath", 1, ""), (
        f"R9-XPATH-AS-CSS: {r!r}")


@pytest.mark.parametrize("use_cssselect", [True, False])
@pytest.mark.parametrize("selector,want", [
    ("button:has-text('Log in')", 1),
    ('button:has-text("log IN")', 1),
    ("button:has-text('Sign up')", 0),
    ("form > button.go:has-text('Log in')", 1),
])
def test_r9_playground_evaluates_playwright_has_text(
        monkeypatch, use_cssselect, selector, want):
    pytest.importorskip("lxml")
    monkeypatch.setattr(sp, "_HAS_CSSSELECT", use_cssselect)
    r = sp.evaluate_selectors(_FORM, [selector])[0]
    assert (r["count"], r["error"]) == (want, ""), (
        f"R9-HAS-TEXT: cssselect={use_cssselect} {selector!r} -> {r!r}")


@pytest.mark.parametrize("selector", [
    "a:has-text('help') span",
    "a:has-text('x') i:has-text('z')",
    "text=Log in",
])
def test_r9_playground_unevaluable_has_text_stays_review_not_error(selector):
    pytest.importorskip("lxml")
    r = sp.evaluate_selectors(_FORM, [selector])[0]
    assert r.get("status") == "review" and r["count"] is None and not r["error"], (
        f"R9-HAS-TEXT-REVIEW: {selector!r} -> {r!r}")


@pytest.mark.parametrize("selector,want", [("div p", 3), ("a", 3), ("a.k", 1),
                                           ("a[title]", 1), ("//a", 3), ("*", 11)])
def test_r9_control_supported_shapes_are_unchanged(monkeypatch, selector, want):
    r = _fallback_count(monkeypatch, selector)
    assert (r["count"], r["error"]) == (want, ""), f"{selector!r} -> {r!r}"


# ── R10: stored cookie -> storage_state cookie ──────────────────────

def _cookie(**extra):
    return dict({"name": "sid", "value": "v", "domain": "example.test"}, **extra)


@pytest.mark.parametrize("stored,want", [
    ("no_restriction", "None"), ("lax", "Lax"), ("strict", "Strict"),
    ("NONE", "None"), (" Lax ", "Lax"),
])
def test_r10_exporter_same_site_spelling_is_kept(stored, want):
    got = tsv._normalise_session_cookie(_cookie(sameSite=stored), 0)
    assert got.get("sameSite") == want, (
        f"R10-SAMESITE-DROPPED: {stored!r} -> {got.get('sameSite')!r}")


@pytest.mark.parametrize("key", ["expires", "expirationDate"])
def test_r10_timestamp_stored_as_text_is_an_expiry(key):
    got = tsv._normalise_session_cookie(_cookie(**{key: "4102444800.5"}), 0)
    assert got.get("expires") == 4102444800.5, (
        f"R10-EXPIRES-DROPPED: {key}='4102444800.5' -> {got.get('expires')!r}")


@pytest.mark.parametrize("extra", [
    {"sameSite": "bogus"}, {"sameSite": "unspecified"}, {"sameSite": None},
    {"expires": "-1"}, {"expires": "soon"}, {"expires": "inf"}, {"expires": "nan"},
    {"expires": -1}, {"expires": True},
])
def test_r10_control_unset_or_unusable_values_stay_out(extra):
    got = tsv._normalise_session_cookie(_cookie(**extra), 0)
    assert got == {"name": "sid", "value": "v", "domain": "example.test",
                   "path": "/"}, f"{extra!r} -> {got!r}"


def test_r10_infinite_expiry_is_not_a_timestamp():
    got = tsv._normalise_session_cookie(_cookie(expires=float("inf")), 0)
    assert "expires" not in got, f"R10-EXPIRES-INF: {got!r}"


def test_r10_control_playwright_spelling_and_number_pass_through():
    got = tsv._normalise_session_cookie(
        _cookie(sameSite="Strict", expires=4102444800), 0)
    assert (got["sameSite"], got["expires"]) == ("Strict", 4102444800.0)
