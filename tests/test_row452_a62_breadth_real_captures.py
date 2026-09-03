"""Row 452 (successor of parked row 124, FR-A6.2) -- the A6.2 breadth claim is
re-derived against REAL captures instead of synthetic fixtures.

Row 124 said the recognizers were "implemented and synthetically tested" and
that what was missing was real-capture corpus evidence.  Five real captures
arrived 2026-08-29.  This module is the evidence population and the pin, so the
claim cannot rot and the corpus cannot drift silently.

WHAT THIS MODULE ASSERTS, and just as importantly what it refuses to assert:

* Each fixture is a GENUINE real-site capture and not a synthetic fixture, and
  that is proven by a predicate measured from the fixture bytes -- not by its
  filename.  The tracked synthetic ``tests/corpus/recognizer/dailymotion.cap.json``
  is the negative control: it must FAIL the same predicate.
* "GUIDED" IS NOT ASSERTED AND READS UNKNOWN.  All five carry zero cookies and
  are public landing/item pages taken by the operator's ``bd-capture-url.py``
  (``redaction_profile.schema == v3.66.171``).  They are real, unauthenticated
  browser captures.  Row 452's phrase "genuine guided capture" is therefore
  satisfied only in its "genuine, not synthetic" half; the authenticated-drive
  half is UNKNOWN and is recorded as such per CLAUDE.md A7 rather than assumed.
* ALL FIVE CARRY ZERO DOM (``dom_log_len == 0`` in every source capture's own
  ``capture_health``).  Every DOM-keyed recognizer criterion is therefore
  UNDECIDABLE by this corpus and reads UNKNOWN, never "satisfied".
* Exact nonzero counts per criterion, with the captures that FAIL a criterion
  explicitly excluded from that criterion's evidence set.

FOUND AND DELIBERATELY NOT FIXED HERE (each would change product behaviour and
move already-pinned verdicts in ``tests/corpus/recognizer/expected_verdicts.json``,
which row 452 does not authorize -- an existing assertion is a contract):

1. ``twitch_hlsjs`` is pinned ``downloadable=True`` / ``direct_progressive`` /
   2 renditions, and BOTH renditions are advertisement creatives served from
   ``m.media-amazon.com`` -- no Twitch content at all.  Only a real capture can
   surface that; a synthetic fixture never carries an ad stack.
2. ``recognize_protection``'s ``anti_bot`` list does not distinguish first-party
   delivery from third-party beacons.  ``ted_talks`` is tagged ``cloudflare``
   solely from ``api.idsequoia.ai`` (an analytics host) while TED's own media is
   Bunny CDN; ``nbcnews_video_akamai_jw``'s cloudflare tell is
   ``experience.tinypass.com``, a paywall vendor.  Criterion C2 below is
   therefore stated in the recognizer's own terms and its limit is named.
3. CHANGELOG v3.66.1354 claims "New tests/corpus/a62_breadth: archiveorg_item
   (jwplayer, progressive, 4 ren[ditions])".  Commit ``03b0b46e`` shipped only
   row 122's files; ``tests/corpus/a62_breadth`` is absent from all git history.
   The numbers in that entry match this module's re-derivation exactly, so the
   work was measured and its artifacts never landed.
"""
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest

BD_GATE_SCOPE = "repo-wide"

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import build_recognizer_corpus as brc  # noqa: E402

sys.path.insert(0, _REPO)
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets  # noqa: E402

_CORPUS = Path(_REPO) / "tests" / "corpus" / "a62_breadth"

# The independent denominator: the five 2026-08-29 captures row 452 names, with
# the sha256 of the RAW source .wacz each fixture was distilled from.  These are
# literals here on purpose -- deriving the expected set from the artifact under
# test would let a deleted fixture pass as a complete corpus.
_EXPECTED = {
    "archiveorg_item":
        "630c39378f5831a11d57ddc1bef73da7b651a5935edf84fdc5717136b0c39ef6",
    "dailymotion":
        "b8d952eb1407e754e0570a0eb99819a73638487860fc6a2019c5eaee6217d5b2",
    "nbcnews_video_akamai_jw":
        "d3bb8e7ca9a58fb4758b997d81438d71f3e39c777c16fcf5b1754515ed598d94",
    "ted_talks":
        "694dbc6dfac516169f3cd583798744dc9ea0cf08666ed49a785e6ba5e4ced3c5",
    "twitch_hlsjs":
        "7278981c1dde945ca83ba79ab1578de74a13b4833986995651e584ddaea701e5",
}

# Reserved / non-routable TLDs a synthetic fixture uses (RFC 2606 / 6761).
_FAKE_TLDS = {"test", "example", "invalid", "localhost", "local"}


def _fixtures():
    return sorted(_CORPUS.glob("*.cap.json")) if _CORPUS.is_dir() else []


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def genuineness(cap: dict) -> dict:
    """Measured from the fixture bytes: is this a real-site capture?

    Returns each independent check by name so a failure says WHICH property of a
    real capture is absent.  A synthetic fixture fails several of these; the
    negative control below proves the predicate is not vacuously true.

    THIRD-PARTY TRAFFIC IS DELIBERATELY NOT REQUIRED.  It was, and it rejected
    ``archiveorg_item``: all 51 of its requests are ``*.archive.org``.  A clean
    ad-free page is not a synthetic one, so requiring an ad stack would have
    thrown away the only capture that satisfies this row's headline criterion.
    """
    host = (cap.get("host") or "").lower()
    tld = host.rsplit(".", 1)[-1] if "." in host else host
    net = cap.get("network_log") or []
    netlocs = {urlparse(e.get("url") or "").netloc for e in net}
    netlocs.discard("")
    ts = cap.get("captured_at") or ""
    try:
        parsed = datetime.fromisoformat(ts)
        subsecond = parsed.microsecond != 0
    except ValueError:
        parsed, subsecond = None, False
    # A real HTTP response carries origin-server headers; a hand-written
    # synthetic entry carries only the content-type its author needed.
    served = sum(1 for e in net
                 if any(str(h.get("name", "")).lower() in ("date", "server")
                        for h in (e.get("response_headers") or [])))
    return {
        "host_is_routable_tld": bool(host) and tld not in _FAKE_TLDS,
        "captured_at_parses": parsed is not None,
        "captured_at_has_subsecond_precision": subsecond,
        "network_log_at_least_50": len(net) >= 50,
        "at_least_5_distinct_netlocs": len(netlocs) >= 5,
        "no_reserved_tld_netlocs": bool(netlocs) and not any(
            n.split(":")[0].rsplit(".", 1)[-1].lower() in _FAKE_TLDS for n in netlocs),
        "majority_of_responses_carry_origin_headers": bool(net) and served * 2 >= len(net),
    }


def is_genuine(cap: dict) -> bool:
    return all(genuineness(cap).values())


# ── the evidence population ───────────────────────────────────────────────---

def test_the_a62_real_capture_evidence_population_is_exactly_the_five_captures():
    """Row 452's denominator.  A missing directory globs to [] rather than
    raising, so this fails on the population size and never on an import."""
    got = {p.name[: -len(".cap.json")] for p in _fixtures()}
    assert got == set(_EXPECTED), (
        "A6.2 real-capture evidence population is "
        f"{len(got)}, expected 5. missing={sorted(set(_EXPECTED) - got)} "
        f"unexpected={sorted(got - set(_EXPECTED))}"
    )


def test_provenance_names_every_fixtures_source_capture_by_sha256():
    prov_path = _CORPUS / "provenance.json"
    assert prov_path.is_file(), f"missing {prov_path}"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    assert set(prov["captures"]) == set(_EXPECTED), (
        "provenance population != the five row-452 captures: "
        f"{sorted(prov['captures'])}"
    )
    for name, want_sha in _EXPECTED.items():
        row = prov["captures"][name]
        assert row["sha256"] == want_sha, f"{name}: sha256 drifted"
        assert row["bytes"] > 0, f"{name}: zero-byte source"
        # distill() drops these, so the provenance file is the only carrier.
        assert row["dom_log_len"] == 0, f"{name}: dom_log_len is not 0"
        assert row["cookie_count"] == 0, f"{name}: cookies present"
        assert row["distill_reduced_fields"] == [], (
            f"{name}: distillation changed the verdict -- the fixture no longer "
            f"reproduces its source capture: {row['distill_reduced_fields']}"
        )
        cap = _load(_CORPUS / f"{name}.cap.json")
        for field in ("url", "host", "captured_at"):
            assert cap[field] == row[field], f"{name}: {field} != provenance"
        assert len(cap["network_log"]) == row["network_log_count"], (
            f"{name}: network_log_count != provenance")


def test_every_tracked_fixture_rehashes_to_its_provenance_fixture_sha256():
    """``provenance.json`` records a ``fixture_sha256`` per TRACKED fixture, and
    this is the only test in the repository that HASHES one.

    Without it the field was named and never read.  The sibling test above
    compares ``row["sha256"]`` -- the digest of the RAW ``.wacz`` -- against a
    literal authored in the same change, which pins NAMING, not content; and
    ``test_raw_source_captures_rehash_to_the_pinned_sha256`` skips wherever
    ``/home/mboyle/bd-persist`` is absent, which is exactly the CI shard this
    gate joins.  A one-byte flip inside a tracked ``.cap.json`` therefore left
    every test in this module green.  It no longer does.

    The population precondition runs BEFORE any hashing and does not derive its
    expected set from the artifact under test: the corpus glob, the literal
    ``_EXPECTED`` denominator and provenance's own keys must be the same five
    names, so neither a deleted fixture nor a dropped provenance row can shrink
    the loop below to a vacuous green over an empty iterable.
    """
    globbed = sorted(_CORPUS.glob("*.cap.json"))
    names = {p.name[: -len(".cap.json")] for p in globbed}
    assert len(globbed) == 5, (
        f"tracked fixture glob under {_CORPUS} is {len(globbed)}, expected 5")
    assert names == set(_EXPECTED), (
        "tracked fixtures != the row-452 denominator: "
        f"missing={sorted(set(_EXPECTED) - names)} "
        f"unexpected={sorted(names - set(_EXPECTED))}")
    assert {p.name for p in globbed} == {p.name for p in _fixtures()}, (
        "the module's _fixtures() helper and the corpus glob disagree")

    prov = json.loads((_CORPUS / "provenance.json").read_text(encoding="utf-8"))
    rows = prov["captures"]
    assert set(rows) == names, (
        "provenance rows != tracked fixtures: "
        f"unhashed={sorted(names - set(rows))} orphan={sorted(set(rows) - names)}")

    digests = []
    for path in globbed:
        name = path.name[: -len(".cap.json")]
        row = rows[name]
        want = row["fixture_sha256"]
        assert isinstance(want, str) and len(want) == 64 and all(
            c in "0123456789abcdef" for c in want), (
                f"{name}: fixture_sha256 is not a lowercase sha256 hex "
                f"digest: {want!r}")
        # Column guard: the RAW .wacz digest must never be pasted into this
        # field, which would pin the tracked fixture to bytes it never had.
        assert want != row["sha256"], (
            f"{name}: fixture_sha256 equals the source .wacz sha256")
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        assert got == want, (
            f"{name}: tracked fixture content drifted from provenance -- "
            f"sha256 of {path.name} is {got}, provenance pins {want}")
        digests.append(got)

    assert len(digests) == 5, f"hashed {len(digests)} fixtures, expected 5"
    assert len(set(digests)) == 5, (
        f"two tracked fixtures share one digest: {sorted(names)}")


# ── genuine, not synthetic (and the negative control that proves it) ───────---

def test_every_a62_fixture_is_a_genuine_real_site_capture():
    fixtures = _fixtures()
    assert len(fixtures) == 5, f"expected 5 fixtures, got {len(fixtures)}"
    for p in fixtures:
        checks = genuineness(_load(p))
        failed = [k for k, v in checks.items() if not v]
        assert not failed, f"{p.name}: not provably a real capture -- {failed}"


def test_the_genuineness_predicate_rejects_the_tracked_synthetic_fixture():
    """NEGATIVE CONTROL.  ``tests/corpus/recognizer/dailymotion.cap.json`` is a
    hand-built synthetic on the reserved host ``demo-dailymotion.test``.  If the
    predicate above accepted it, every 'genuine' verdict in this module would be
    vacuous.  Named failures are asserted, not merely a False."""
    synthetic = Path(_REPO) / "tests" / "corpus" / "recognizer" / "dailymotion.cap.json"
    assert synthetic.is_file(), f"negative control absent: {synthetic}"
    cap = _load(synthetic)
    checks = genuineness(cap)
    failed = {k for k, v in checks.items() if not v}
    assert failed >= {
        "host_is_routable_tld",
        "captured_at_has_subsecond_precision",
        "network_log_at_least_50",
        "at_least_5_distinct_netlocs",
        "no_reserved_tld_netlocs",
        "majority_of_responses_carry_origin_headers",
    }, f"synthetic control was not rejected for the intended reasons: {checks}"
    assert not is_genuine(cap), "the synthetic fixture passed as genuine"


def test_the_synthetic_and_real_dailymotion_are_different_subjects():
    """The synthetic pins ``player_family='dailymotion'`` on a fabricated watch
    page; the real capture is the DOM-less dailymotion.com HOME page and yields
    ``unknown``.  That is NOT the recognizer being contradicted -- the real
    capture cannot decide the criterion.  Pinned here so nobody 'corrects' a
    synthetic fixture against a capture that never exercised it."""
    syn = json.loads(
        (Path(_REPO) / "tests" / "corpus" / "recognizer" / "expected_verdicts.json")
        .read_text(encoding="utf-8"))["dailymotion"]
    real = _pins()["dailymotion"]
    assert syn["player_family"] == "dailymotion"
    assert real["player_family"] == "unknown"
    assert _load(_CORPUS / "dailymotion.cap.json")["url"].rstrip("/") == \
        "https://www.dailymotion.com", "the real capture is not the home page"


# ── the pinned verdicts ───────────────────────────────────────────────────---

def _pins() -> dict:
    return json.loads(
        (_CORPUS / "expected_verdicts.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(_EXPECTED))
def test_real_capture_verdict_reproduces_its_pin(name):
    fixture = _CORPUS / f"{name}.cap.json"
    assert fixture.is_file(), f"missing fixture {fixture}"
    draft = brc.build_from_fixture(fixture)
    assert scan_artifact_secrets(draft) == [], f"{name}: secret leak in draft"
    assert brc.verdict_pin(draft) == _pins()[name], (
        f"{name}: recognizer verdict drifted from the real-capture pin")


def test_no_capture_carries_a_dom_so_dom_keyed_criteria_read_unknown():
    """The load-bearing limit of this corpus, asserted rather than described.

    The population precondition is asserted first: over an empty corpus this
    body iterates nothing and would report OK, which is the exact empty-iterable
    green CLAUDE.md A7 forbids.
    """
    fixtures = _fixtures()
    assert len(fixtures) == 5, f"expected 5 fixtures, got {len(fixtures)}"
    for p in fixtures:
        cap = _load(p)
        html = "".join(e.get("html", "") for e in (cap.get("dom_log") or []))
        assert html == "", f"{p.name}: unexpectedly carries DOM ({len(html)} chars)"


# ── the per-criterion table ───────────────────────────────────────────────---

def test_a62_criteria_are_decided_with_exact_counts_and_named_exclusions():
    """Row 452: state per criterion whether the real captures satisfy it, with
    exact nonzero counts, and let an undecidable criterion read UNKNOWN.

    The exclusion sets are the negative controls the row demands: a capture that
    does NOT exercise a criterion must not be counted as evidence for it.
    """
    pins = _pins()
    assert len(pins) == 5, f"criterion denominator is {len(pins)}, expected 5"

    claims_family = {n for n, v in pins.items() if v["player_family"] != "unknown"}
    non_cf = {n for n, v in pins.items() if "cloudflare" not in v["anti_bot"]}
    non_videojs = {n for n, v in pins.items() if v["player_family"] != "videojs"}

    # C1 -- a player family is claimed at all, on a real capture.
    assert claims_family == {"archiveorg_item", "nbcnews_video_akamai_jw",
                             "ted_talks"}, claims_family
    assert len(claims_family) == 3

    # C2 -- non-Cloudflare, in the recognizer's own flat anti_bot terms.
    assert non_cf == {"archiveorg_item", "dailymotion", "twitch_hlsjs"}, non_cf
    assert len(non_cf) == 3
    # ted_talks and nbcnews are EXCLUDED from C2's evidence set.
    assert {"ted_talks", "nbcnews_video_akamai_jw"} & non_cf == set()

    # C3 -- non-video.js.  No capture in this set is video.js, so the criterion
    # is satisfied trivially over the whole population and cannot discriminate.
    assert non_videojs == set(pins), non_videojs

    # C4 -- ROW 124's HEADLINE: a claimed player family on a real capture that
    # is both non-Cloudflare and non-video.js.  SATISFIED, exactly once.
    c4 = claims_family & non_cf & non_videojs
    assert c4 == {"archiveorg_item"}, c4
    assert len(c4) == 1, "C4 evidence count must be nonzero and is the row's claim"
    arc = pins["archiveorg_item"]
    assert arc["player_family"] == "jwplayer"
    assert arc["primary_protocol"] == "progressive"
    assert arc["rendition_count"] == 4
    assert arc["anti_bot"] == []

    # C5 -- a non-Cloudflare hls.js capture.  NOT SATISFIED: ted_talks is hls.js
    # but Cloudflare-tagged; twitch_hlsjs is non-Cloudflare but the recognizer
    # claims no family, so its FILENAME's hlsjs claim is not evidence.
    c5 = {n for n in non_cf if pins[n]["player_family"] == "hlsjs"}
    assert c5 == set(), f"C5 was expected empty (still missing), got {c5}"
    assert pins["ted_talks"]["player_family"] == "hlsjs"
    assert "cloudflare" in pins["ted_talks"]["anti_bot"]
    assert pins["twitch_hlsjs"]["player_family"] == "unknown"
    assert pins["twitch_hlsjs"]["anti_bot"] == ["kasada"]

    # C6 -- every DOM-keyed criterion: UNKNOWN, denominator 0 of 5.
    assert sum(1 for p in _fixtures()
               if "".join(e.get("html", "")
                          for e in (_load(p).get("dom_log") or []))) == 0


def test_the_twitch_downloadable_verdict_rests_only_on_advertisement_media():
    """Recorded, NOT fixed.  ``twitch_hlsjs`` is pinned downloadable with two
    progressive renditions and both are ad creatives from m.media-amazon.com.
    Pinning it here means a later recognizer change that alters this cannot pass
    silently, and the finding survives as a row rather than as prose."""
    draft = brc.build_from_fixture(_CORPUS / "twitch_hlsjs.cap.json")
    rends = (draft.get("recognition") or {}).get("renditions") or []
    assert len(rends) == 2, f"expected 2 renditions, got {len(rends)}"
    hosts = {urlparse(r.get("url_shape") or "").netloc for r in rends}
    assert hosts == {"m.media-amazon.com"}, hosts
    assert _pins()["twitch_hlsjs"]["downloadable"] is True


def test_the_cloudflare_tag_on_ted_talks_comes_from_a_third_party_host():
    """Recorded, NOT fixed.  C2 excludes ted_talks because the recognizer says
    cloudflare; the capture says the tell is an analytics beacon, not TED's own
    delivery.  Asserted from provenance so C2's limit is measured, not claimed."""
    prov = json.loads((_CORPUS / "provenance.json").read_text(encoding="utf-8"))
    vendors = prov["captures"]["ted_talks"]["anti_bot_vendors"]
    assert [v["vendor"] for v in vendors] == ["cloudflare"], vendors
    first_seen = vendors[0]["first_seen_host"]
    assert first_seen != "www.ted.com", (
        "the cloudflare tell is first-party after all; C2's caveat is wrong")
    assert first_seen == "api.idsequoia.ai", first_seen


# ── raw source re-derivation (operator host only) ─────────────────────────---

_RAW_DIR = Path("/home/mboyle/bd-persist/corpus-new")


def test_raw_source_captures_rehash_to_the_pinned_sha256():
    """Runs only where the read-only raw corpus is mounted.  CI has no
    /home/mboyle/bd-persist, so the tracked-fixture gates above must stand
    alone; this one names its skip reason rather than passing vacuously."""
    import hashlib

    missing = [n for n in _EXPECTED if not (_RAW_DIR / f"{n}.wacz").is_file()]
    if missing:
        pytest.skip(
            "raw row-452 capture corpus not present on this host "
            f"({_RAW_DIR} missing {len(missing)} of 5: {sorted(missing)}); "
            "the tracked .cap.json fixtures and provenance.json carry the pin")
    for name, want in _EXPECTED.items():
        digest = hashlib.sha256((_RAW_DIR / f"{name}.wacz").read_bytes()).hexdigest()
        assert digest == want, f"{name}: raw capture sha256 drifted"


def test_brand_pack_transform_control_only_imports_the_builder():
    """TRANSFORM CONTROL for the M1 mutant in
    ``tests/mutants/row452_a62_breadth.json``.

    M1 stops ``player_families.ensure_registered`` from registering the brand
    pack.  Every capture here has ``dom_log_len == 0``, so the pack driven by
    network-derived script srcs is the ONLY reason ``archiveorg_item`` reads
    ``jwplayer`` and ``ted_talks`` reads ``hlsjs``; unregistered, both read
    ``unknown``.  This test imports the builder and asserts nothing about a
    family, so it must ESCAPE M1 -- proving the CAUGHT verdicts elsewhere in
    this module are assertion failures rather than the mutant failing to import.
    """
    import build_template_from_wacz as _btw

    assert callable(_btw.build_template)
