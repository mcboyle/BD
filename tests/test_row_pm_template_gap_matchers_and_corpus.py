"""PM-handoff 2026-09-06 template gap report -- matchers, five verified
templates, three corrected speculative entries.

The report (``bd-persist/PM-handoff-20260906T131616Z/reports/SITE_RUNBOOK.md``,
section "TEMPLATE GAP & CORRECTION REPORT") surveyed ~26 onboarded sites against
the committed corpus and found three classes of defect, all of which are silent:

  * A MATCHER GAP returns ``[]``. The member host is ``members.nubiles-porn.com``
    but the pattern was ``nubilesporn\\.com`` -- the same hyphen that bit the live
    campaign -- and ``xempire.com`` was missing from the Gamma family entirely.
  * A MATCHER MIS-MATCH is worse than a gap, because it returns a confident wrong
    answer: ``tiny4k.com`` was claimed by BOTH ``wowgirls_network`` and
    ``vip4k_family``, so a PornPros/Fame Digital Vue SPA was handed WowGirls
    selectors that cannot resolve on it.
  * A SPECULATIVE ENTRY is a guess wearing a verified entry's clothes. ``nookies``
    guessed ``#email``/``#username`` against a form whose fields are ``uid``/``pwd``,
    and ``bang_originals`` used a bare ``button:has-text('LOGIN')`` -- which, because
    Playwright's ``has-text`` is a case-insensitive SUBSTRING match, also matches
    "LOGIN WITH GOOGLE" and sends the worker to Google OAuth.

Every assertion below targets what ``suggest_for_url`` COMPUTES or what the
committed corpus HOLDS, and ``test_the_gate_fails_when_each_hazard_is_put_back``
reintroduces each defect into a copy of the corpus and requires this file's own
checks to fail on it.

READ-ONLY PROVENANCE: no value here was taken from a live page by this test. The
selector strings are the report's, cross-checked by hand against the archived
per-site selectors JSONs; account identifiers, vault names and tokened URLs from
those JSONs are deliberately absent, and
``test_no_committed_template_string_carries_a_credential_or_token`` holds the
whole corpus to that boundary.
"""
from __future__ import annotations

import copy
import importlib
import re

import pytest


BD_GATE_SCOPE = "repo-wide"


def _templates_module():
    return importlib.import_module("bulk_downloader.site_templates")


def _accessors():
    return importlib.import_module("bulk_downloader.site_templates.accessors")


@pytest.fixture()
def suggest(monkeypatch):
    """``suggest_for_url`` with the user-template overlay proven empty.

    The accessor merges ``user_templates`` ahead of the built-ins, and that file
    is read from the CURRENT WORKING DIRECTORY. A stray ``user_templates.json``
    beside the runner would prepend ids and turn every exact-list assertion here
    into a different experiment, so the overlay is neutralised and the
    neutralisation is asserted rather than assumed.
    """
    overlay = importlib.import_module("bulk_downloader.user_templates")
    monkeypatch.setattr(overlay, "suggest_for_url", lambda url: [])
    assert overlay.suggest_for_url("https://example.invalid/x") == [], (
        "precondition: the user-template overlay was not neutralised")
    return _templates_module().suggest_for_url


def _by_id(templates=None):
    items = templates if templates is not None else _templates_module().TEMPLATES
    out = {}
    for item in items:
        assert item["id"] not in out, f"duplicate template id {item['id']!r}"
        out[item["id"]] = item
    return out


def _download(template):
    return template["learned"]["download"]


def _login(template):
    return template["learned"]["login"]


# ── the behavioural denominator ──────────────────────────────────────────────
#
# (url, exact suggest_for_url result). FIXES are the rows the report filed;
# CONTROLS are hosts the report did NOT ask to change, and their expected value
# is the value MEASURED on origin/main d7176ce8 before any edit -- not a value
# retyped from the report, which for tiny4k would have been wrong (the report
# named one claimant; the corpus had two).

_MATCHER_FIXES = [
    # A1: the members host is hyphenated.
    ("https://members.nubiles-porn.com/video/x", ["nubiles_network"]),
    # A2: xempire is Gamma.
    ("https://members.xempire.com/en/video/hardx/slug/123", ["gamma_kosmos"]),
    ("https://www.xempire.com/en/login", ["gamma_kosmos"]),
    # A3 + B4: tiny4k belongs to PornPros, and to NOTHING else.
    ("https://tiny4k.com/members/video/slug", ["pornpros_tiny4k"]),
    ("https://exotic4k.com/members/video/slug", ["pornpros_tiny4k"]),
    # B1, B2, B3, B5: hosts that matched nothing at all.
    ("https://members.africancasting.com/video/a-slug-2646.html", ["africancasting"]),
    ("https://www.pegasproductions.com/video/slug", ["pegasproductions"]),
    ("https://members.vixenplus.com/videos/slug", ["vixen_network"]),
    ("https://www.wifey.com/videos/slug", ["vixen_network"]),
    ("https://app.reptyle.com/movies/31551", ["reptyle_teamskeet"]),
]

_MATCHER_CONTROLS = [
    # The un-hyphenated Nubiles host keeps its answer.
    ("https://nubilesporn.com/video/x", ["nubiles_network"]),
    ("https://nubilefilms.com/video/x", ["nubiles_network"]),
    # Removing tiny4k removed nothing else from either claimant.
    ("https://vip4k.com/v", ["wowgirls_network", "vip4k_family"]),
    ("https://black4k.com/v", ["wowgirls_network", "vip4k_family"]),
    ("https://tushy4k.com/v", ["wowgirls_network", "vip4k_family"]),
    ("https://www.wowgirls.com/v", ["wowgirls_network"]),
    # Adding xempire added nothing else to Gamma.
    ("https://www.adulttime.com/v", ["gamma_kosmos", "adulttime_network"]),
    ("https://www.evilangel.com/v", ["gamma_kosmos", "evilangel"]),
    # The pre-existing id set for the hosts the new entries sit next to.
    ("https://www.teamskeet.com/movies/1", ["teamskeet_network"]),
    ("https://www.blacked.com/videos/x", ["vixen_network"]),
    ("https://nookies.com/membersarea/video/3480", ["nookies"]),
    ("https://www.bang.com/video/abc/slug", ["bang_originals"]),
    # A host in none of these families is still unmatched.
    ("https://example.invalid/video/1", []),
]


def test_the_matcher_answers_every_reported_gap_and_mismatch(suggest):
    assert len(_MATCHER_FIXES) == 10, "the fix denominator changed"
    wrong = [(url, suggest(url), expected)
             for url, expected in _MATCHER_FIXES if suggest(url) != expected]
    assert not wrong, f"matcher fixes not satisfied: {wrong}"
    # Exact fired count: every fix row resolves to at least one id, and the
    # tiny4k rows resolve to exactly one -- an empty list would satisfy a
    # "no wrong ids" check while satisfying nothing the report asked for.
    assert sum(len(suggest(url)) for url, _ in _MATCHER_FIXES) == 10


def test_no_host_the_report_left_alone_changed_its_answer(suggest):
    assert len(_MATCHER_CONTROLS) == 13, "the control denominator changed"
    wrong = [(url, suggest(url), expected)
             for url, expected in _MATCHER_CONTROLS if suggest(url) != expected]
    assert not wrong, f"negative controls regressed: {wrong}"


def test_tiny4k_is_claimed_once_and_by_the_right_operator():
    templates = _by_id()
    for other in ("wowgirls_network", "vip4k_family"):
        assert not any("tiny4k" in pattern for pattern in templates[other]["patterns"]), (
            f"{other} still claims tiny4k; it is PornPros / Fame Digital")
    claimants = [t["id"] for t in _templates_module().TEMPLATES
                 if any("tiny4k" in p for p in t.get("patterns") or [])]
    assert claimants == ["pornpros_tiny4k"], claimants


# ── the corpus denominator ───────────────────────────────────────────────────

_NEW_IDS = ["africancasting", "pegasproductions", "pornpros_tiny4k", "reptyle_teamskeet"]
_CORRECTED_IDS = ["nookies", "bang_originals", "nubiles_network", "vixen_network"]

# Every selector this cut ADDS or CORRECTS, pinned by identity. The values are
# the report's; the archived selectors JSONs write attribute selectors without
# quotes (``input[name=uid]``) while the committed corpus quotes them, so the
# comparison below is made after normalising that one difference and nothing
# else -- see ``_normalise_attribute_quotes``.
_SELECTOR_PINS = {
    ("africancasting", "login", "user_field"): ["input[name='ahd_username']"],
    ("africancasting", "login", "pass_field"): ["input[name='ahd_password']"],
    ("africancasting", "login", "submit_btn"): ["button:has-text('Sign In')"],
    ("africancasting", "download", "row_selectors"): ["video source", "source[title]"],
    ("pegasproductions", "login", "user_field"): ["input[name='nom_util_vod']"],
    ("pegasproductions", "login", "pass_field"): ["input[name='pass_vod']"],
    ("pegasproductions", "login", "submit_btn"): ["input[type='submit'].bouton-connexion2"],
    ("pornpros_tiny4k", "download", "row_selectors"): ["a[href*='download_mp4_']"],
    ("reptyle_teamskeet", "login", "user_field"): ["input[name='email']"],
    ("reptyle_teamskeet", "login", "pass_field"): ["input[name='password']"],
    ("reptyle_teamskeet", "download", "trigger_selectors"): ["[aria-label*='download' i]"],
    ("vixen_network", "login", "user_field"): ["input[name='username']"],
    ("vixen_network", "login", "pass_field"): ["input[name='password']"],
    ("vixen_network", "login", "submit_btn"): ["button:has-text('LOGIN')"],
    ("nookies", "login", "user_field"): ["input[name='uid']"],
    ("nookies", "login", "pass_field"): ["input[name='pwd']"],
    ("nookies", "login", "submit_btn"): ["button:has-text('LOGIN')"],
    ("nookies", "download", "trigger_selectors"): ["#downloadTrigger"],
    ("nookies", "download", "row_selectors"): [
        "#downloadModal a:has-text('Full quality video')"],
    ("bang_originals", "login", "submit_btn"): [
        "form[action*=login_check] button[type=submit]"],
}

# Values the corrections must have REMOVED. A correction that only appends
# leaves the wrong guess in front of the right answer, where the runner reaches
# it first -- which is exactly how bang_originals would still find the Google
# OAuth button.
_SELECTOR_MUST_BE_ABSENT = {
    ("nookies", "login", "user_field"): ["#email", "#username", "input[name='username']"],
    ("nookies", "login", "pass_field"): ["#password", "input[type='password']"],
    ("nookies", "download", "row_selectors"): ["a[href*='/download/']"],
    ("bang_originals", "download", "row_selectors"): [
        "a[href*='/download/']", "a[href*='download.php']"],
}


def _normalise_attribute_quotes(selector: str) -> str:
    """``input[name=uid]`` and ``input[name='uid']`` are the same selector."""
    return re.sub(r"\[([A-Za-z_:-]+)=(['\"])([^'\"\]]*)\2", r"[\1=\3]", selector)


def test_every_new_template_is_present_exactly_once_with_a_nonzero_corpus():
    templates = _templates_module().TEMPLATES
    assert len(templates) >= 95, f"template population shrank to {len(templates)}"
    ids = [t["id"] for t in templates]
    assert len(ids) == len(set(ids)), "duplicate template id in the corpus"
    for new_id in _NEW_IDS:
        assert ids.count(new_id) == 1, f"{new_id} is not present exactly once"
    for corrected in _CORRECTED_IDS:
        assert ids.count(corrected) == 1


def test_every_added_and_corrected_selector_holds_its_verified_value():
    templates = _by_id()
    assert len(_SELECTOR_PINS) == 20, "the selector denominator changed"
    checked = 0
    for (template_id, block, field), expected in _SELECTOR_PINS.items():
        actual = templates[template_id]["learned"][block][field]
        assert [_normalise_attribute_quotes(s) for s in actual] == \
               [_normalise_attribute_quotes(s) for s in expected], \
               f"{template_id}.{block}.{field} = {actual!r}"
        checked += 1
    assert checked == 20, checked


def test_the_wrong_guesses_are_gone_and_not_merely_outranked():
    templates = _by_id()
    still_present = []
    for (template_id, block, field), forbidden in _SELECTOR_MUST_BE_ABSENT.items():
        actual = templates[template_id]["learned"][block][field]
        still_present += [(template_id, field, value)
                          for value in forbidden if value in actual]
    assert not still_present, still_present
    # bang_originals: no UNSCOPED has-text submit may survive, because
    # Playwright's has-text is a case-insensitive substring match and
    # "LOGIN WITH GOOGLE" contains "login".
    submit = _login(templates["bang_originals"])["submit_btn"]
    unscoped = [s for s in submit if "has-text" in s and "form[" not in s]
    assert unscoped == [], (
        f"an unscoped has-text submit can still match 'LOGIN WITH GOOGLE': {unscoped}")


def test_nubiles_download_rows_lead_with_the_verified_direct_href():
    rows = _download(_by_id()["nubiles_network"])["row_selectors"]
    assert rows[:2] == ["span.dimensions", "a[href*='.mp4?st=']"], rows


def test_the_reptyle_entry_declares_that_it_has_no_url_attribute_to_read():
    """The download is a BROWSER EVENT, and that is a third state.

    ``url_attribute`` here is neither "href" nor a missing key: the tier click
    fires a Playwright download event to a generated CacheFly URL and there is
    no attribute in the DOM to read. An empty string says so explicitly; a
    missing key would read as an unfilled template, and "href" would send the
    runner to look for a link that does not exist.
    """
    download = _download(_by_id()["reptyle_teamskeet"])
    assert "url_attribute" in download, "the empty attribute must be DECLARED, not absent"
    assert download["url_attribute"] == ""
    assert download["row_selectors"], "the tier buttons are still the rows"
    description = _by_id()["reptyle_teamskeet"]["description"].lower()
    assert "download event" in description, (
        "the empty url_attribute must be explained where an operator reads it")


def test_reptyle_does_not_take_a_host_an_existing_template_already_serves():
    """Two claimants for one host is the tiny4k defect, filed forward."""
    templates = _by_id()
    assert not any("teamskeet" in p for p in templates["reptyle_teamskeet"]["patterns"]), (
        "teamskeet.com is already served by teamskeet_network")
    assert any("teamskeet" in p for p in templates["teamskeet_network"]["patterns"])


def test_a_corrected_entry_no_longer_calls_itself_speculative():
    templates = _by_id()
    lying = [tid for tid in _CORRECTED_IDS
             if "speculative" in templates[tid]["description"].lower()]
    assert not lying, f"verified selectors still described as speculative: {lying}"


def test_the_new_login_template_reaches_the_host_that_had_none():
    login_data = importlib.import_module("bulk_downloader.login_templates_data")
    hosts = [t.get("host") for t in login_data.LOGIN_TEMPLATES]
    assert hosts.count("members.africancasting.com") == 1, hosts
    entry = next(t for t in login_data.LOGIN_TEMPLATES
                 if t["id"] == "login_africancasting")
    assert entry["login"]["user_field"] == ["input[name='ahd_username']"]
    assert entry["login"]["pass_field"] == ["input[name='ahd_password']"]
    assert entry["login"]["submit_btn"] == ["button:has-text('Sign In')"]
    suggested = login_data.suggest_login_for_url(
        "https://members.africancasting.com/login")
    assert "login_africancasting" in suggested, suggested


def test_every_added_and_corrected_selector_parses(): 
    """Reuse the row-671 verifier rather than re-deriving selector validity."""
    api = importlib.import_module("bulk_downloader.template_selector_verifier")
    templates = _by_id()
    selectors = []
    for template_id in _NEW_IDS + _CORRECTED_IDS:
        selectors += [row["selector"]
                      for row in api.enumerate_template_selectors(templates[template_id])]
    assert len(selectors) >= 40, f"selector denominator too small: {len(selectors)}"
    parsed = api.parse_selectors(selectors)
    bad = [r for r in parsed if r["status"] != "VALID"]
    assert not bad, bad


# ── the A4 secret boundary ───────────────────────────────────────────────────

_CREDENTIAL_SHAPES = re.compile(
    r"(?:[?&](?:token|uh|h|st|e|expires|validto|validfrom|ip)=[^&'\"\s]+)"
    r"|(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
)


def test_no_committed_template_string_carries_a_credential_or_token():
    """The report's source JSONs hold usernames, vault names and tokened URLs.

    Only selector strings, hosts and URL SHAPES were copied out. This holds the
    WHOLE corpus to that, not just the entries this cut touched -- the boundary
    is a property of the committed data, and a denominator of "the files I
    edited" would stop seeing it the moment someone else edits one.
    """
    modules = [_templates_module().TEMPLATES,
               importlib.import_module("bulk_downloader.login_templates_data").LOGIN_TEMPLATES]
    strings = []

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            strings.append(value)

    for corpus in modules:
        walk(corpus)
    assert len(strings) > 1000, f"secret-boundary denominator collapsed: {len(strings)}"
    hits = sorted({s for s in strings if _CREDENTIAL_SHAPES.search(s)})
    # A selector may legitimately mention a query PARAMETER NAME with no value
    # (``a[href*='.mp4?st=']`` is the shape, not a token); those end at the '='.
    assert hits == [], f"credential-shaped value(s) in the committed corpus: {hits}"


# ── the hazard, put back ─────────────────────────────────────────────────────

def _mutated_corpus(mutate):
    corpus = copy.deepcopy(_templates_module().TEMPLATES)
    mutate(_by_id(corpus))
    return corpus


def _restore_tiny4k_to_wowgirls(by_id):
    by_id["wowgirls_network"]["patterns"].append(r"tiny4k\.com")


def _restore_tiny4k_to_vip4k(by_id):
    by_id["vip4k_family"]["patterns"].append(r"tiny4k\.com")


def _drop_the_hyphenated_nubiles_pattern(by_id):
    by_id["nubiles_network"]["patterns"] = [
        p for p in by_id["nubiles_network"]["patterns"] if "nubiles-porn" not in p]


def _drop_the_xempire_pattern(by_id):
    by_id["gamma_kosmos"]["patterns"] = [
        p for p in by_id["gamma_kosmos"]["patterns"] if "xempire" not in p]


def _restore_the_nookies_guess(by_id):
    _login(by_id["nookies"])["user_field"] = ["#email", "#username"]


def _restore_the_bang_oauth_trap(by_id):
    _login(by_id["bang_originals"])["submit_btn"] = ["button:has-text('LOGIN')"]


def _give_reptyle_an_href(by_id):
    _download(by_id["reptyle_teamskeet"])["url_attribute"] = "href"


_HAZARDS = [
    ("tiny4k back on wowgirls_network", _restore_tiny4k_to_wowgirls),
    ("tiny4k back on vip4k_family", _restore_tiny4k_to_vip4k),
    ("the hyphenated nubiles-porn pattern deleted", _drop_the_hyphenated_nubiles_pattern),
    ("the xempire pattern deleted", _drop_the_xempire_pattern),
    ("the nookies #email guess restored", _restore_the_nookies_guess),
    ("the bang LOGIN-WITH-GOOGLE trap restored", _restore_the_bang_oauth_trap),
    ("reptyle given an href it does not have", _give_reptyle_an_href),
]


def _judge(corpus, suggest_over):
    """Re-run this file's own claims against an arbitrary corpus."""
    by_id = _by_id(corpus)
    for url, expected in _MATCHER_FIXES + _MATCHER_CONTROLS:
        assert suggest_over(url) == expected, (url, suggest_over(url), expected)
    claimants = [t["id"] for t in corpus if any("tiny4k" in p for p in t.get("patterns") or [])]
    assert claimants == ["pornpros_tiny4k"], claimants
    for (template_id, block, field), expected in _SELECTOR_PINS.items():
        actual = by_id[template_id]["learned"][block][field]
        assert [_normalise_attribute_quotes(s) for s in actual] == \
               [_normalise_attribute_quotes(s) for s in expected]
    submit = _login(by_id["bang_originals"])["submit_btn"]
    assert [s for s in submit if "has-text" in s and "form[" not in s] == []
    assert _download(by_id["reptyle_teamskeet"])["url_attribute"] == ""


def test_the_gate_fails_when_each_hazard_is_put_back(monkeypatch, suggest):
    """Seven deletions of the fix, seven required failures.

    Without this, every assertion above could be reading a value this same cut
    wrote and would pass whatever the matcher does.
    """
    accessors = _accessors()
    # Control: the unmutated corpus passes, so a failure below is the mutation.
    _judge(_templates_module().TEMPLATES, suggest)
    caught = []
    for name, mutate in _HAZARDS:
        corpus = _mutated_corpus(mutate)
        monkeypatch.setattr(accessors, "TEMPLATES", corpus)
        try:
            _judge(corpus, accessors.suggest_for_url)
        except AssertionError:
            caught.append(name)
        else:
            pytest.fail(f"HAZARD ESCAPED -- the gate stayed green with: {name}")
        monkeypatch.undo()
    assert len(caught) == len(_HAZARDS) == 7, caught


def test_transform_control_imports_the_corpus_without_judging_it():
    """Deliberately assertion-free about behaviour: this one must ESCAPE.

    It proves the CAUGHTs above are assertion failures rather than import or
    attribute errors.
    """
    assert callable(_templates_module().suggest_for_url)
