"""Row 455 (successor to row 126): the reviewed app.reptyle.com template is
resolved against a LIVE DOM, and the outcome is pinned here so it can be
re-checked forever without the live site.

Row 126's gate (``tests/test_row126_reptyle_selectors_resolve_on_recorded_dom.py``)
measures the same twelve selectors against DOM RECORDED on 2026-06-15 and
replayed from ``Reptyle-2.redacted.scrubbed.wacz``. Row 455 asks the different
question: does the reviewed template still describe the site as it is served
TODAY? A recorded capture cannot answer that, and A6 forbids a repository test
from contacting the live site, so the two halves are split exactly as row 126
split its own:

* An operator instrument (``bd-live-template-resolve.py``, outside the
  repository) drove BD's canonical browser against the live site on
  2026-09-03 from host test6, with **no session of any kind** -- no cookie, no
  credential, no storage state -- and saved the rendered document.
* THIS gate replays those saved bytes through the SAME production resolver the
  live measurement used, ``template_selector_verifier.verify_template_source``,
  which serves a saved subject fully offline. It never contacts anything.

WHAT THE LIVE DOM COULD AND COULD NOT DECIDE. The live navigation to
``https://app.reptyle.com/movies/32088`` was redirected by the site to
``https://auth.reptyle.com/oauth/login?referer=spa`` and the shipped challenge
detector armed and PAUSED there (a Cloudflare ``challenges.cloudflare.com``
frame is present; the widget was never touched, and no attempt was made to
clear, solve or resume it). So the live page this gate pins is the site's own
logged-out authentication page, and it contains exactly one of the five
reviewed selector kinds:

  login  -> DECIDED live: email 1, password 1, submit 2, all HIT and visible.
  player, quality, download.trigger, download.row_selectors
         -> **UNKNOWN**, not "reviewed" and not "MISS-so-broken". Their subject
            does not exist on a logged-out page, so the live DOM cannot decide
            them (CLAUDE.md A7). The verifier says so in its own grammar: the
            row selectors come back ``UNKNOWN`` because the download trigger
            never uniquely resolved, so the modal state was unavailable, and
            the report's overall verdict is ``UNKNOWN`` and never ``ok``.

The pinned live counts are IDENTICAL to the counts the same probe produces on
row 126's recorded 2026-06-15 login fixture: 0 of 19 selectors differ. That is
the finding, and it is why this gate carries both subjects -- the reviewed
login selectors have not drifted in the two and a half months between the
recorded capture and the live one, and a future drift breaks this file.
"""
from __future__ import annotations

import functools
import hashlib
import json
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_FIXTURES = _REPO / "tests" / "fixtures" / "row455"
_ROW126 = _REPO / "tests" / "fixtures" / "row126"
_TEMPLATE = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"

_LIVE = "reptyle_live_login_wall"
_RECORDED_LOGIN = "reptyle_login_page"
_RECORDED_MODAL = "reptyle_download_modal"

# Exact counts measured on the LIVE 2026-09-03 document, reconciled against the
# in-browser counts taken at capture time (19 selectors, 0 mismatches).  A count
# is pinned per selector path so a drifted selector cannot pass by resolving
# "something".
_LIVE_COUNTS: dict[str, int] = {
    "learned.login.user_field": 1,
    "learned.login.pass_field": 1,
    "learned.login.submit_btn": 2,
    "learned.player.player_selectors.[0]": 0,
    "learned.player.player_selectors.[1]": 0,
    "learned.download.trigger_selectors.[0]": 0,
    "learned.download.trigger_selectors.[1]": 0,
    "learned.download.trigger_selectors.[2]": 0,
    "learned.download.trigger_selectors.[3]": 0,
    "learned.download.trigger_selectors.[4]": 0,
    "learned.download.trigger_selectors.[5]": 0,
    "learned.download.trigger_selectors.[6]": 0,
    "learned.download.trigger_selectors.[7]": 0,
    "learned.download.trigger_selectors.[8]": 0,
    "learned.download.trigger_selectors.[9]": 0,
    "learned.download.row_selectors.[0]": 0,
    "learned.download.row_selectors.[1]": 0,
    "learned.download.row_selectors.[2]": 0,
    "learned.download.row_selectors.[3]": 0,
}

# The per-kind adjudication row 455 asks for.  UNKNOWN is a VERDICT here, not an
# absence of one: it records that the live DOM could not decide the kind.
_KIND_VERDICT: dict[str, str] = {
    "login": "DECIDED",
    "player": "UNKNOWN",
    "quality": "UNKNOWN",
    "download.trigger": "UNKNOWN",
    "download.row_selectors": "UNKNOWN",
}

_TRIGGER_UNAVAILABLE = "no uniquely resolved download trigger"


def _template() -> dict:
    assert _TEMPLATE.is_file(), f"precondition: reviewed template absent: {_TEMPLATE}"
    data = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    assert data.get("host") == "app.reptyle.com", data.get("host")
    return data


def _provenance() -> dict:
    path = _FIXTURES / f"{_LIVE}.provenance.json"
    assert path.is_file(), f"precondition: provenance sidecar absent: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _live_bytes() -> bytes:
    path = _FIXTURES / f"{_LIVE}.html"
    assert path.is_file(), f"precondition: live fixture absent: {path}"
    raw = path.read_bytes()
    assert raw, f"precondition: live fixture is empty: {path}"
    prov = _provenance()
    # The fixture is EVIDENCE, so it must still be the bytes the live capture
    # produced.  An edited fixture is authored markup wearing a capture's name.
    assert hashlib.sha256(raw).hexdigest() == prov["output_sha256"], (
        f"live fixture {_LIVE} no longer matches its captured digest"
    )
    assert len(raw) == prov["output_bytes"], (len(raw), prov["output_bytes"])
    return raw


def _probe() -> dict:
    """The reviewed template in the shape the PRODUCTION adapter emits.

    Identical construction to row 126's ``_probe_template``, deliberately: the
    live numbers and the recorded numbers must be about the same selectors, or
    the comparison between them means nothing. Row 673 made that identity
    STRUCTURAL rather than a promise -- both gates now call the one shipped
    adapter instead of each keeping its own copy of the composition.
    """
    from bulk_downloader.template_assist import template_to_selector_probe

    return template_to_selector_probe(_template(), "row455_reptyle_live_dom")


@functools.lru_cache(maxsize=None)
def _report_for(path_str: str) -> dict:
    from bulk_downloader.template_selector_verifier import verify_template_source

    return verify_template_source(_probe(), Path(path_str), timeout=25)


def _live_report() -> dict:
    _live_bytes()  # digest precondition before the subject is rendered
    return _report_for(str(_FIXTURES / f"{_LIVE}.html"))


def _counts(report: dict) -> dict[str, int | None]:
    return {
        row["path"].split(".", 1)[1]: row["count"]
        for row in report["selectors"]
    }


def _row(report: dict, path_suffix: str) -> dict:
    matches = [
        item for item in report["selectors"] if item["path"].endswith(path_suffix)
    ]
    assert len(matches) == 1, (
        f"precondition: {path_suffix!r} occurs {len(matches)} times in the report"
    )
    return matches[0]


def _committed_selector_strings(template: dict) -> list[str]:
    """Independent walk of the reviewed-JSON selector block.

    Deliberately NOT the production enumerator: the denominator this gate
    reconciles against must not come from the code under measurement.
    """
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            found.append(value)

    walk(template.get("selectors") or {})
    return found


# --- the fixture is LIVE evidence, captured without a session ---------------


def test_the_fixture_is_a_live_capture_taken_without_any_session():
    prov = _provenance()
    assert prov["source"] == "LIVE capture, not a replay", prov["source"]
    assert prov["capture_url"].startswith("https://auth.reptyle.com/oauth/login"), prov
    assert prov["session"].startswith("NONE"), prov["session"]
    assert "PAUSED" in prov["challenge"], prov["challenge"]
    # The subject must be the template this gate measures, byte for byte.
    assert prov["template_sha256"] == hashlib.sha256(
        _TEMPLATE.read_bytes()
    ).hexdigest(), "the reviewed template moved after the live capture"
    # A capture whose masking or scrubbing was not verified is not evidence.
    assert prov["mask"]["residual_visible"] == 0, prov["mask"]
    assert prov["scrub"]["needle_hits_after"] == 0, prov["scrub"]
    # A live oauth page carries a per-render CSRF nonce; it is zero-entropy here.
    assert prov["scrub"]["csrf_nonce_values_zeroed"] == 1, prov["scrub"]
    assert prov["scrub"]["bd_scan_artifact_secrets_after"] == [], prov["scrub"]
    assert prov["scrub_preserves_measurement"].startswith("all 19"), prov
    raw = _live_bytes()
    assert len(raw) > 10_000, len(raw)


def test_an_edited_fixture_cannot_pass_as_the_live_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Negative control on the evidence itself, exercising THIS gate's guard.

    One flipped byte, same length, fed through ``_live_bytes`` -- the digest
    precondition every other case depends on must refuse it. Comparing two
    hashes would only test hashlib.
    """
    prov = _provenance()
    raw = _live_bytes()
    tampered = bytearray(raw)
    tampered[len(tampered) // 2] ^= 0x20
    assert len(tampered) == prov["output_bytes"], "control must differ only in content"

    staged = tmp_path / "row455"
    staged.mkdir()
    (staged / f"{_LIVE}.html").write_bytes(bytes(tampered))
    (staged / f"{_LIVE}.provenance.json").write_text(
        json.dumps(prov), encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "_FIXTURES", staged)
    assert _FIXTURES == staged, "precondition: the guard was not repointed"
    with pytest.raises(AssertionError, match="no longer matches its captured digest"):
        _live_bytes()


def test_the_committed_fixture_is_inert():
    """A fixture a browser renders offline must carry no executable markup.

    Same standard tools/build_recorded_dom_fixture.py applies to row 126's
    fixtures (``_INERT_TAGS = {"script", "noscript"}``), and the prune count is
    recorded rather than silently discarded.
    """
    text = _live_bytes().decode("utf-8")
    assert "<script" not in text.lower(), "fixture carries executable markup"
    assert "<noscript" not in text.lower(), "fixture carries noscript markup"
    assert _provenance()["scrub"]["inert_tags_pruned"] > 0


# --- the denominator is nonzero, complete, and independently derived -------


def test_no_committed_selector_escapes_the_live_measurement():
    template = _template()
    committed = _committed_selector_strings(template)
    assert len(committed) == 12, committed
    probe = _probe()["learned"]
    measured: set[str] = set()
    for group in probe.values():
        for value in group.values():
            if isinstance(value, str):
                measured.add(value)
            else:
                measured.update(value)
    from bulk_downloader.template_assist import preferred_resolutions

    for selector in committed:
        if "{resolution}" in selector:
            expansions = {
                selector.replace("{resolution}", str(r))
                for r in preferred_resolutions(template)
            }
            assert expansions <= measured, selector
            continue
        assert selector in measured, f"committed selector never measured: {selector}"

    report = _live_report()
    assert report["selector_count"] == len(_LIVE_COUNTS) == 19, report["selector_count"]
    assert set(_counts(report)) == set(_LIVE_COUNTS)


def test_every_selector_compiles_so_a_zero_is_a_real_zero():
    """A MALFORMED selector also counts 0; separate the two before reading any."""
    from bulk_downloader.template_selector_verifier import parse_selectors

    report = _live_report()
    statuses = {row["status"] for row in report["selectors"]}
    assert "MALFORMED" not in statuses, statuses
    parsed = parse_selectors([row["selector"] for row in report["selectors"]])
    assert len(parsed) == 19, len(parsed)
    assert {item["status"] for item in parsed} == {"VALID"}, parsed


# --- the live adjudication, pinned ----------------------------------------


@pytest.mark.parametrize("path_suffix,expected", sorted(_LIVE_COUNTS.items()))
def test_the_live_dom_resolves_each_selector_with_its_measured_count(
    path_suffix: str, expected: int
):
    row = _row(_live_report(), path_suffix)
    assert row["count"] == expected, (path_suffix, row)


def test_the_login_kind_is_decided_by_the_live_dom():
    report = _live_report()
    for suffix in ("learned.login.user_field", "learned.login.pass_field",
                   "learned.login.submit_btn"):
        row = _row(report, suffix)
        assert row["status"] == "HIT", (suffix, row)
    # submit is over-broad on the live page: it takes the real Login control AND
    # the magic-link button, so a consumer taking .first depends on DOM order.
    assert _row(report, "learned.login.submit_btn")["count"] == 2


def test_a_kind_the_live_dom_cannot_decide_reads_unknown_not_reviewed():
    """CLAUDE.md A7: unavailable measurement is UNKNOWN, never a clean verdict."""
    report = _live_report()
    assert report["verdict"] == "UNKNOWN", report["verdict"]
    assert report["ok"] is False, report["ok"]
    assert report["interaction"]["clicked"] is False, report["interaction"]
    assert report["interaction"]["error"] == _TRIGGER_UNAVAILABLE, report["interaction"]
    for index in range(4):
        row = _row(report, f"learned.download.row_selectors.[{index}]")
        assert row["status"] == "UNKNOWN", (index, row)
        assert row["error"] == _TRIGGER_UNAVAILABLE, (index, row)
    assert _KIND_VERDICT["download.row_selectors"] == "UNKNOWN"
    assert sorted(k for k, v in _KIND_VERDICT.items() if v == "UNKNOWN") == [
        "download.row_selectors", "download.trigger", "player", "quality",
    ]
    assert _KIND_VERDICT["login"] == "DECIDED"


# --- the finding: live has not drifted from the recorded capture ----------


def test_the_live_login_page_matches_the_recorded_one_selector_for_selector():
    """The comparison row 455 exists to make.

    Same probe, same resolver, two subjects 80 days apart: DOM recorded
    2026-06-15 and DOM served live 2026-09-03. Every one of the 19 counts is
    equal, so the reviewed login selectors are confirmed against the live site
    and not merely against an archive. A future drift fails here first.
    """
    live = _counts(_live_report())
    recorded = _counts(_report_for(str(_ROW126 / f"{_RECORDED_LOGIN}.html")))
    assert set(live) == set(recorded), sorted(set(live) ^ set(recorded))
    differing = {k: (recorded[k], live[k]) for k in live if live[k] != recorded[k]}
    assert differing == {}, differing
    assert live == _LIVE_COUNTS


# --- negative controls: the pass cannot be manufactured -------------------


def test_a_page_without_the_login_form_cannot_manufacture_the_login_pass():
    """Discriminating power: the same three selectors must go to 0 elsewhere."""
    modal = _report_for(str(_ROW126 / f"{_RECORDED_MODAL}.html"))
    for suffix in ("learned.login.user_field", "learned.login.pass_field",
                   "learned.login.submit_btn"):
        row = _row(modal, suffix)
        assert row["count"] == 0, (suffix, row)
    # ...and that subject really is a different page, not an empty one.
    assert _row(modal, "learned.download.trigger_selectors.[0]")["count"] == 1


def test_an_unavailable_subject_is_unknown_and_never_ok(tmp_path: Path):
    from bulk_downloader.template_selector_verifier import verify_template_source

    missing = verify_template_source(_probe(), tmp_path / "absent.html", timeout=5)
    assert missing["verdict"] == "UNKNOWN", missing["verdict"]
    assert missing["ok"] is False, missing
    assert missing["selector_count"] == 19, missing["selector_count"]


# --- the authenticated half of row 455 ------------------------------------
#
# Everything above measures the LOGGED-OUT live page, and it is honest about
# what that page cannot decide: four of the five reviewed kinds come back
# UNKNOWN because their subject does not exist behind a login wall.  Row 455's
# acceptance is not "say UNKNOWN nicely", it is ADJUDICATE EVERY KIND, and that
# needs a subject a logged-out navigation can never produce.
#
# The second subject is the operator harness's own AUTHENTICATED live capture:
# a real browser, logged in on host test2 with the campaign's configured
# reptyle credentials, navigated to a scene page and left with the "SELECT
# DOWNLOAD QUALITY" modal open, and the rendered document saved.  It was made
# inert with tools/build_recorded_dom_fixture.py, scrubbed of its two signed
# artifacts (a Cloudflare Stream playback JWT and a CacheFly signed path
# segment) by zero-entropy fill, and the scrub is proven measurement-preserving:
# all 19 counts are identical before and after.
#
# The two subjects are complementary and neither is sufficient alone -- the
# login kind exists only on the logged-out page and the other four exist only
# behind the login -- so the adjudication is stated across BOTH and every kind
# is decided exactly once.
_AUTH = "reptyle_live_authenticated_scene"

# In-browser counts for the authenticated subject (19 selectors).  A count, not
# a boolean: 0 is a measurement here, and so is 7.
_AUTH_COUNTS: dict[str, int] = {
    "learned.download.row_selectors.[0]": 1,
    "learned.download.row_selectors.[1]": 1,
    "learned.download.row_selectors.[2]": 1,
    "learned.download.row_selectors.[3]": 3,
    "learned.download.trigger_selectors.[0]": 1,
    "learned.download.trigger_selectors.[1]": 2,
    "learned.download.trigger_selectors.[2]": 0,
    "learned.download.trigger_selectors.[3]": 0,
    "learned.download.trigger_selectors.[4]": 0,
    "learned.download.trigger_selectors.[5]": 0,
    "learned.download.trigger_selectors.[6]": 0,
    "learned.download.trigger_selectors.[7]": 0,
    "learned.download.trigger_selectors.[8]": 0,
    "learned.download.trigger_selectors.[9]": 0,
    "learned.login.user_field": 0,
    "learned.login.pass_field": 0,
    "learned.login.submit_btn": 0,
    "learned.player.player_selectors.[0]": 7,
    "learned.player.player_selectors.[1]": 2,
}

# Which subject decides which kind.  "UNKNOWN" stays a verdict, not a gap: the
# login kind is UNKNOWN on an authenticated page for the same A7 reason the
# player kind is UNKNOWN on the login wall -- its subject is not there.
_AUTH_KIND_VERDICT: dict[str, str] = {
    "login": "UNKNOWN",
    "player": "DECIDED",
    "quality": "DECIDED",
    "download.trigger": "DECIDED",
    "download.row_selectors": "DECIDED",
}

# The reviewed quality selectors in probe shape: open_menu, then the eight
# resolution options the adapter expands from the {resolution} placeholder.
_QUALITY_OPEN_MENU = "learned.download.trigger_selectors.[1]"
_QUALITY_RESOLUTIONS = tuple(
    f"learned.download.trigger_selectors.[{i}]" for i in range(2, 10)
)


def _auth_provenance() -> dict:
    path = _FIXTURES / f"{_AUTH}.provenance.json"
    assert path.is_file(), f"precondition: authenticated provenance absent: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _auth_bytes() -> bytes:
    path = _FIXTURES / f"{_AUTH}.html"
    assert path.is_file(), f"precondition: authenticated fixture absent: {path}"
    raw = path.read_bytes()
    assert raw, f"precondition: authenticated fixture is empty: {path}"
    prov = _auth_provenance()
    assert hashlib.sha256(raw).hexdigest() == prov["output_sha256"], (
        f"authenticated fixture {_AUTH} no longer matches its captured digest"
    )
    assert len(raw) == prov["output_bytes"], (len(raw), prov["output_bytes"])
    return raw


def _auth_report() -> dict:
    _auth_bytes()  # digest precondition before the subject is rendered
    return _report_for(str(_FIXTURES / f"{_AUTH}.html"))


def test_the_authenticated_fixture_is_a_live_capture_taken_with_a_session():
    prov = _auth_provenance()
    assert prov["source"] == "LIVE capture, not a replay", prov["source"]
    assert prov["capture_url"].startswith("https://app.reptyle.com/movies/"), prov
    assert prov["session"].startswith("AUTHENTICATED"), prov["session"]
    assert prov["capture_host"].startswith("test2"), prov["capture_host"]
    assert prov["template_sha256"] == hashlib.sha256(
        _TEMPLATE.read_bytes()
    ).hexdigest(), "the reviewed template moved after the authenticated capture"
    # No signed artifact survives, and the scrub that removed them did not move
    # a single count -- otherwise the fixture measures the scrubber, not a site.
    assert prov["scrub"]["bd_scan_artifact_secrets_after"] == [], prov["scrub"]
    assert prov["scrub"]["cloudflare_stream_jwt_zeroed"] == 1, prov["scrub"]
    assert prov["scrub"]["cachefly_signed_segments_zeroed"] == 1, prov["scrub"]
    assert prov["scrub_preserves_measurement"].startswith("all 19"), prov
    assert len(_auth_bytes()) > 10_000, len(_auth_bytes())


def test_the_authenticated_fixture_is_inert():
    text = _auth_bytes().decode("utf-8")
    assert "<script" not in text.lower(), "fixture carries executable markup"
    assert "<noscript" not in text.lower(), "fixture carries noscript markup"
    assert _auth_provenance()["inert_tags_pruned"] > 0


def test_an_edited_authenticated_fixture_cannot_pass_as_the_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Same guard as the logged-out fixture, exercised on this one."""
    prov = _auth_provenance()
    raw = _auth_bytes()
    tampered = bytearray(raw)
    tampered[len(tampered) // 2] ^= 0x20
    assert len(tampered) == prov["output_bytes"], "control must differ only in content"
    staged = tmp_path / "row455auth"
    staged.mkdir()
    (staged / f"{_AUTH}.html").write_bytes(bytes(tampered))
    (staged / f"{_AUTH}.provenance.json").write_text(json.dumps(prov), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "_FIXTURES", staged)
    assert _FIXTURES == staged, "precondition: the guard was not repointed"
    with pytest.raises(AssertionError, match="no longer matches its captured digest"):
        _auth_bytes()


def test_the_authenticated_subject_really_carries_the_download_modal():
    """The shape precondition, asserted BEFORE any verdict is read off it.

    A capture that never opened the modal would make every DECIDED below
    vacuous: the four row selectors would be absent for a boring reason and the
    gate would still be green. The unqualified modal family resolving THREE
    buttons is the proof the modal is present and populated.
    """
    report = _auth_report()
    assert report["selector_count"] == 19, report["selector_count"]
    assert _row(report, "learned.download.row_selectors.[3]")["count"] == 3, report
    assert _row(report, "learned.download.trigger_selectors.[0]")["count"] == 1, report


@pytest.mark.parametrize("path_suffix,expected", sorted(_AUTH_COUNTS.items()))
def test_the_authenticated_dom_resolves_each_selector_with_its_measured_count(
    path_suffix: str, expected: int
):
    row = _row(_auth_report(), path_suffix)
    assert row["count"] == expected, (path_suffix, row)


def test_the_authenticated_dom_decides_the_four_kinds_the_login_wall_could_not():
    report = _auth_report()
    # Nothing is UNKNOWN here: the trigger resolved, so the modal-scoped rows
    # were measurable and the verifier never had to say "could not look".
    assert report["verdict"] == "HIT", report["verdict"]
    assert report["ok"] is True, report["ok"]
    assert {row["status"] for row in report["selectors"]} == {"HIT", "MISS"}, report
    for index in range(4):
        row = _row(report, f"learned.download.row_selectors.[{index}]")
        assert row["status"] == "HIT", (index, row)
        assert row["error"] == "", (index, row)
    assert _row(report, "learned.download.trigger_selectors.[0]")["status"] == "HIT"
    for suffix in ("learned.player.player_selectors.[0]",
                   "learned.player.player_selectors.[1]"):
        assert _row(report, suffix)["status"] == "HIT", suffix


def test_the_quality_kind_is_decided_and_its_misses_are_the_reviewed_reason():
    """quality is DECIDED, and a MISS here is a finding, not a failure.

    open_menu resolves the two quality controls. The eight resolution options
    the adapter expands from ``{resolution}`` resolve ZERO on a populated modal
    -- exactly what the reviewed template's own row-126 note predicts, because
    each button's text is Standard/High/Ultra and the 720p/1080p/2160p label
    lives in a SIBLING div. The modal-scoped replacements resolve 1 each, so the
    tier really is offered; only the placeholder-shaped selector cannot see it.
    """
    report = _auth_report()
    assert _row(report, _QUALITY_OPEN_MENU)["count"] == 2, report
    missed = [s for s in _QUALITY_RESOLUTIONS if _row(report, s)["count"] == 0]
    assert len(missed) == 8, missed
    for suffix in _QUALITY_RESOLUTIONS:
        assert _row(report, suffix)["status"] == "MISS", suffix
    for index, expected_tier in enumerate(("2160p", "1080p", "720p")):
        row = _row(report, f"learned.download.row_selectors.[{index}]")
        assert row["count"] == 1, (expected_tier, row)
        assert expected_tier in row["selector"], (expected_tier, row)


def test_every_reviewed_kind_is_decided_across_the_two_live_subjects():
    """Row 455's acceptance, stated as one exact count.

    Five reviewed kinds; each is decided by exactly one of the two live
    subjects and by the other not at all; nothing is left UNKNOWN anywhere.
    """
    assert sorted(_KIND_VERDICT) == sorted(_AUTH_KIND_VERDICT), (
        sorted(_KIND_VERDICT), sorted(_AUTH_KIND_VERDICT)
    )
    decided = {
        kind
        for kind in _KIND_VERDICT
        if "DECIDED" in (_KIND_VERDICT[kind], _AUTH_KIND_VERDICT[kind])
    }
    assert len(decided) == 5, sorted(decided)
    assert decided == set(_KIND_VERDICT), sorted(set(_KIND_VERDICT) - decided)
    # ...and exactly one subject decides each, so no kind is double-counted.
    both = [
        kind
        for kind in _KIND_VERDICT
        if _KIND_VERDICT[kind] == _AUTH_KIND_VERDICT[kind] == "DECIDED"
    ]
    assert both == [], both
    assert _AUTH_KIND_VERDICT["login"] == "UNKNOWN"
    assert _KIND_VERDICT["login"] == "DECIDED"


def test_the_login_wall_cannot_manufacture_the_authenticated_decision():
    """Negative control: the same four kinds must fail for the intended reason.

    Not "some other page gives other numbers" -- the SAME probe on the SAME
    resolver against the logged-out live capture must leave every one of the
    four authenticated kinds unmeasurable, and the reason must be the one the
    verifier names, ``no uniquely resolved download trigger``.
    """
    wall = _live_report()
    assert wall["verdict"] == "UNKNOWN", wall["verdict"]
    for index in range(4):
        row = _row(wall, f"learned.download.row_selectors.[{index}]")
        assert row["status"] == "UNKNOWN", (index, row)
        assert row["error"] == _TRIGGER_UNAVAILABLE, (index, row)
    assert _row(wall, "learned.download.trigger_selectors.[0]")["count"] == 0
    for suffix in ("learned.player.player_selectors.[0]",
                   "learned.player.player_selectors.[1]"):
        assert _row(wall, suffix)["count"] == 0, suffix
    assert _row(wall, _QUALITY_OPEN_MENU)["count"] == 0
    # ...and the control is a real page, not an empty one: it decides login.
    assert _row(wall, "learned.login.user_field")["count"] == 1
