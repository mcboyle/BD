"""Row 722: one repo template per site that completed the login->download cycle.

Source of truth: .live-proof/row722-site-configs.json (the verified :5555 site
configs, credentials stripped) plus the LIVE-PROOF / LIVE-RUNNER journals. Each
site below finished a COMPLETE cycle on 2026-09-15 (login automated, one scene
file downloaded through the app, VERDICT PASS). Sites that did not (blacked,
vixen, adulttime) deliberately have NO row here and NO verified template.

Every assertion targets a property the wizard or runner actually consumes:
the id resolves exactly once, the host resolves to the template, the config
keys are all CFG_FIELDS (anything else is silently dropped on reload), and
the selectors pass the same validator the site editor applies to a PUT.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_PROOF = _REPO / ".live-proof" / "row722-site-configs.json"
_STAMP = "VERIFIED 2026-09-15 (row 722)"

# The verified :5555 configs, credentials stripped, copied from the proof file
# so this test judges the committed corpus on its own (the proof file is a
# campaign artifact, not a package file). Keys: the ones the live run needed.
_PROOF_VALUES = {
    "kink": {
        "login_url": "https://www.kink.com/login",
        "trigger_selector": "button.buy-shoot",
        "dl_selector": "a.dropdown-item[href*=\"/download?filename=\"]",
    },
    "ultrafilms": {"login_url": "https://ultrafilms.com/login"},
    "teenmegaworld": {
        "login_url": "https://members.teenmegaworld.net/authentication.php"},
    "tiny4k": {"login_url": "https://tiny4k.com/login"},
    "nookies": {
        "login_url": "https://nookies.com/login",
        "success_url": "https://nookies.com/membersarea",
        "dismiss_selectors": "button[aria-label=\"Close\"]",
        "trigger_selector": "button:has-text(\"Download\")",
        "dl_selector": "a[href*=\"/membersarea/video/stream/\"]",
    },
    "newsensations": {
        "login_url": "https://www.newsensations.com/members/",
        "dismiss_selectors": "a:has-text(\"TAKE ME TO MY MEMBERSHIP\")",
        "trigger_selector": "button.ex-iconbtn--download",
        "dl_selector": "#exDownloadMenu .exp-menu-item:has-text('2160p')",
    },
    "nubiles": {"login_url": "https://members.nubiles.net/login"},
    "nubilefilms": {"login_url": "https://members.nubilefilms.com/login"},
    "filthykings": {
        "login_url": "https://www.filthykings.com/en/login",
        "success_url": "https://members.filthykings.com/en",
        "trigger_selector":
            "button.ScenePlayerHeaderPlus-IconItem-Button:has-text('Download')",
        "dl_selector": "a.VideoJSPlayer-DownloadOption-Link:has-text('2160p')",
    },
    "dfxtra": {
        "login_url": "https://www.dfxtra.com/en/login",
        "success_url": "https://members.dfxtra.com/en",
        "dismiss_selectors": (
            "button:has-text('I Agree'), button[aria-label*='close' i], "
            "button:text-is('No Thanks'), a:text-is('No Thanks')"),
        "dismiss_selectors_login": (
            "a.SkipPageButton-ButtonLink, a:has-text('No Thanks. Continue'), "
            "a:has-text('Continue to Members Area')"),
        "trigger_selector": "div.download button",
        "dl_selector": "a.VideoJSPlayer-DownloadOption-Link[href*=\"2160p\"]",
    },
    "evilangel": {
        "login_url": "https://www.evilangel.com/en/login",
        "success_url": "https://members.evilangel.com/en",
        "dismiss_selectors_login": (
            "a.SkipPageButton-ButtonLink, a:has-text('No Thanks. Continue'), "
            "a:has-text('Continue to Members Area')"),
        "trigger_selector": "div.download button",
        "dl_selector": "a.VideoJSPlayer-DownloadOption-Link[href*=\"2160p\"]",
    },
    "wowgirls": {
        "login_url": "https://auth.wowgirls.com/login",
        "success_url": "https://venus.wowgirls.com/",
    },
    "site-ma-brazzers": {
        "login_url": "https://site-ma.brazzers.com/login",
        "success_url": "https://site-ma.brazzers.com/store",
        "trigger_selector": "button:has-text(\"Download\")",
        "dl_selector": "button.sc-o8a1bb-1",
    },
    "vip4k": {
        "login_url": "https://vip4k.com/en/login",
        "success_url": "https://members.vip4k.com/en/",
        "crawler_listing_url": "https://members.vip4k.com/en/videos",
        "trigger_selector": "button.player-actions__item--download",
        "dl_selector": "a.download__item",
    },
    "naughtyamerica": {"login_url": "https://members.naughtyamerica.com/login"},
    "site-ma-bangbros": {
        "login_url": "https://site-ma.bangbros.com/login",
        "success_url": "https://site-ma.bangbros.com/",
        "dismiss_selectors_login": (
            "div[data-test-id=\"secrev-submit\"] "
            "div:text-is(\"Continue to Members Area\")"),
        "trigger_selector": "button:text-is(\"Download\")",
        "dl_selector": "button:has-text(\"h264 - 1080p\")",
    },
    "stepsiblingscaught": {
        "login_url": "https://stepsiblingscaught.com/login",
        "success_url": "https://members.nubiles-porn.com/",
    },
}

# (verified site name, template id, host the login_url is on)
_VERIFIED = [
    ("kink", "kink_network", "kink.com"),
    ("ultrafilms", "ultrafilms", "ultrafilms.com"),
    ("teenmegaworld", "teen_mega_world", "teenmegaworld.net"),
    ("tiny4k", "pornpros_tiny4k", "tiny4k.com"),
    ("nookies", "nookies", "nookies.com"),
    ("newsensations", "new_sensations", "newsensations.com"),
    ("nubiles", "nubiles_network", "nubiles.net"),
    ("nubilefilms", "nubiles_network", "nubilefilms.com"),
    ("filthykings", "filthykings", "filthykings.com"),
    ("dfxtra", "dfxtra", "dfxtra.com"),
    ("evilangel", "evilangel", "evilangel.com"),
    ("wowgirls", "wowgirls_network", "wowgirls.com"),
    ("site-ma-brazzers", "brazzers", "brazzers.com"),
    ("vip4k", "vip4k_family", "vip4k.com"),
    ("naughtyamerica", "naughtyamerica", "naughtyamerica.com"),
    ("site-ma-bangbros", "bangbros_network", "bangbros.com"),
    ("stepsiblingscaught", "stepsiblingscaught", "stepsiblingscaught.com"),
]
_TEMPLATE_IDS = sorted({tid for _, tid, _ in _VERIFIED})
_NOT_COMPLETED = ("blacked", "vixen", "adulttime")

# Multi-brand templates carry no login_url on purpose (nubiles_network serves
# nubiles AND nubilefilms, whose login hosts differ); pornpros_tiny4k must
# keep real Chrome (Nuxt SPA).
_SHARED_TEMPLATES = {"nubiles_network"}
_EXTRA_REQUIRED = {"pornpros_tiny4k": ("use_real_chrome",)}


def _templates():
    return importlib.import_module("bulk_downloader.site_templates").TEMPLATES


def _by_id() -> dict:
    return {t["id"]: t for t in _templates()}


def _proof() -> dict:
    """The embedded values; cross-checked against the campaign file when present."""
    assert set(_PROOF_VALUES) == {name for name, _, _ in _VERIFIED}
    if _PROOF.is_file():
        data = json.loads(_PROOF.read_text(encoding="utf-8"))
        by_name = {v["name"]: v for v in data.values()}
        drift = [(name, key, by_name[name].get(key), value)
                 for name, values in _PROOF_VALUES.items() if name in by_name
                 for key, value in values.items()
                 if by_name[name].get(key) != value]
        assert not drift, f"embedded proof drifted from {_PROOF}: {drift}"
    return _PROOF_VALUES


def _cfg_fields() -> set:
    kernel = importlib.import_module("bulk_downloader.app_kernel")
    fields = set(kernel.CFG_FIELDS)
    assert "login_attempt_cap_per_day" in fields, (
        "precondition: row 722 made login_attempt_cap_per_day a CFG_FIELD")
    return fields


@pytest.fixture
def suggest(monkeypatch):
    """suggest_for_url with the user-template overlay proven empty."""
    overlay = importlib.import_module("bulk_downloader.user_templates")
    monkeypatch.setattr(overlay, "suggest_for_url", lambda url: [])
    assert overlay.suggest_for_url("https://example.invalid/x") == []
    return importlib.import_module("bulk_downloader.site_templates.accessors").suggest_for_url


def test_every_verified_template_id_is_present_exactly_once():
    ids = [t["id"] for t in _templates()]
    assert len(ids) == len(set(ids)), "duplicate template id in the corpus"
    wrong = {tid: ids.count(tid) for tid in _TEMPLATE_IDS if ids.count(tid) != 1}
    assert not wrong, f"verified template id count != 1: {wrong}"
    assert len(_TEMPLATE_IDS) == 16, _TEMPLATE_IDS


def test_the_verified_stamp_leads_every_description():
    by_id = _by_id()
    bad = {tid: by_id[tid]["description"][:60] for tid in _TEMPLATE_IDS
           if not by_id[tid]["description"].startswith(_STAMP)}
    assert not bad, f"description does not start with {_STAMP!r}: {bad}"
    lying = [tid for tid in _TEMPLATE_IDS
             if "speculative" in by_id[tid]["description"].lower()]
    assert not lying, f"verified template still calls itself speculative: {lying}"


def test_patterns_match_the_verified_login_host():
    import re
    by_id = _by_id()
    for _name, tid, host in _VERIFIED:
        pats = by_id[tid]["patterns"]
        assert pats and all(isinstance(p, str) for p in pats), (tid, pats)
        assert any(re.search(p, host) for p in pats), (
            f"{tid}: no pattern in {pats!r} matches {host!r}")


def test_the_login_url_resolves_to_the_template(suggest):
    proof = _proof()
    wrong = []
    for name, tid, _host in _VERIFIED:
        login_url = proof[name]["login_url"]
        assert login_url.startswith("https://"), (name, login_url)
        got = suggest(login_url)
        if tid not in got:
            wrong.append((name, login_url, got))
    assert not wrong, f"login_url does not resolve to its template: {wrong}"


def test_config_defaults_keys_are_all_cfg_fields():
    fields = _cfg_fields()
    by_id = _by_id()
    stray = {tid: sorted(set(by_id[tid].get("config_defaults") or {}) - fields)
             for tid in _TEMPLATE_IDS}
    stray = {k: v for k, v in stray.items() if v}
    assert not stray, (
        "config_defaults keys outside CFG_FIELDS are dropped on reload: "
        f"{stray}")


def test_config_defaults_carry_what_the_verified_run_needed():
    proof = _proof()
    by_id = _by_id()
    missing = {}
    drift = []
    for name, tid, _host in _VERIFIED:
        cd = by_id[tid].get("config_defaults") or {}
        for key in _EXTRA_REQUIRED.get(tid, ()):
            if cd.get(key) is not True:
                missing.setdefault(tid, []).append(key)
        if tid in _SHARED_TEMPLATES:
            assert "login_url" not in cd, (
                f"{tid} is shared by several login hosts; a login_url default "
                f"would misdirect every brand but one")
            continue
        for key, value in proof[name].items():
            if key not in cd or not cd[key]:
                missing.setdefault(tid, []).append(key)
            elif cd[key] != value:
                drift.append((tid, key, cd[key], value))
    assert not missing, f"template lacks a config the live run needed: {missing}"
    assert not drift, f"template value differs from the verified config: {drift}"


def test_every_selector_passes_the_site_editor_validator():
    se = importlib.import_module("bulk_downloader.site_editor")
    by_id = _by_id()
    errors = {}
    for tid in _TEMPLATE_IDS:
        cd = by_id[tid].get("config_defaults") or {}
        err = se.validate_selector_updates(cd)
        login_wall = cd.get("dismiss_selectors_login")
        if isinstance(login_wall, str) and login_wall.strip():
            reason = se.selector_syntax_error(login_wall)
            if reason:
                err["dismiss_selectors_login"] = reason
        if err:
            errors[tid] = err
    assert not errors, f"G25a: selector rejected by validate_selector_updates: {errors}"


def test_learned_selectors_parse_and_parallel_attributes_line_up():
    api = importlib.import_module("bulk_downloader.template_selector_verifier")
    by_id = _by_id()
    selectors = []
    for tid in _TEMPLATE_IDS:
        t = by_id[tid]
        download = t["learned"]["download"]
        assert download["row_selectors"], f"{tid}: row_selectors is empty"
        attr = download.get("url_attribute")
        if isinstance(attr, list):
            assert len(attr) == len(download["row_selectors"]), (
                f"{tid}: url_attribute list length {len(attr)} != "
                f"row_selectors {len(download['row_selectors'])}")
        selectors += [row["selector"] for row in api.enumerate_template_selectors(t)]
    assert len(selectors) >= 60, f"selector denominator too small: {len(selectors)}"
    bad = [r for r in api.parse_selectors(selectors) if r["status"] != "VALID"]
    assert not bad, bad


def test_no_template_string_carries_a_credential_cookie_or_token():
    import re
    shapes = re.compile(
        r"(?:[?&](?:token|uh|h|st|e|expires|validto|validfrom|ip)=[^&'\"\s]+)"
        r"|(?:[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
        r"|(?:cookie_file|username|password)\s*[:=]\s*\S",
    )
    by_id = _by_id()
    strings = []

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                assert k not in ("username", "password", "cookie_file"), (
                    f"credential-shaped key {k!r} in a template")
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            strings.append(value)

    for tid in _TEMPLATE_IDS:
        walk(by_id[tid])
    assert len(strings) > 200, len(strings)
    hits = sorted({s for s in strings if shapes.search(s)})
    assert hits == [], f"credential-shaped value(s): {hits}"


def test_sites_that_did_not_complete_the_cycle_have_no_verified_template():
    """Negative control: the stamp is a claim of a completed cycle, and the
    three sites that never completed one must not carry it anywhere."""
    by_id = _by_id()
    stamped = [tid for tid, t in by_id.items()
               if t["description"].startswith(_STAMP)]
    assert sorted(stamped) == _TEMPLATE_IDS, (
        f"stamped ids drifted from the verified list: {sorted(stamped)}")
    for tid in ("vixen_network", "adulttime_network", "gamma_kosmos"):
        assert tid in by_id, f"precondition: {tid} absent"
        assert not by_id[tid]["description"].startswith(_STAMP), tid
    for name in _NOT_COMPLETED:
        assert name not in by_id, f"{name}: no dedicated template was to be added"
