"""Regression tests for the v3.66.11 minor-bug batch from the
v3.66.10 deep_detect audit.

Bug R — HLS #EXT-X-KEY encryption check used a substring test for
        "METHOD=NONE" anywhere in the playlist text, which false-
        negatived on URIs containing the literal "METHOD=NONE" and
        on comments/attributes elsewhere. Fix: hls_has_encryption()
        parses each #EXT-X-KEY line and inspects its actual METHOD=
        attribute.

Bug T — HLS #EXT-X-STREAM-INF RESOLUTION attribute regex was start-
        anchored only via re.match(r"(\\d+)x(\\d+)", ...). Trailing
        non-digit garbage like "1920x1080p" or "1920x1080extra" was
        silently truncated to (1920, 1080) — accepting malformed
        input as if it were spec-compliant. Fix: re.fullmatch after
        .strip() (to tolerate trailing whitespace some encoders ship).

Bug U — BANDWIDTH=8000000.0 (float-shaped) raised ValueError under
        int(s), silently zeroing the variant's bandwidth. Fix: coerce
        via float() then int().

Bug V — FRAME-RATE=0 and missing FRAME-RATE collapsed to the same
        None via `float(x or 0) or None`. Fix: distinguish missing
        (None) from explicit zero (0.0).

Bug JJ — Manifest-as-input dispatcher said
         `is_hls_master(html) or is_hls_manifest(html)`. Redundant:
         every master is also a manifest. Fix: simplified to
         is_hls_manifest() alone; the parser handles master vs media
         internally.

Bug KK — When prefer_resolution was set, candidates with no known
         resolution (provider embeds, anchor heuristics, extensionless
         URLs awaiting HEAD) were `continue`'d in the bias loop —
         meaning the user's preference had ZERO effect on them. If
         every candidate was resolution-less, the request was silently
         ignored. Fix: apply the soft undershoot penalty (-100) to
         resolution-less candidates so they cede ranking to anything
         that does match the requested tier.

Bug S — The full schema of alt-media (audio/subtitles) entries
        emitted by parse_hls_master was documented as
        `[{url, name, lang, default, group_id, ...}]` — the `...`
        hid `channels`, `forced`. Fix: docstring expanded to
        enumerate the complete schema. Tests lock the schema in.

Bug X / Bug Y — _dedup_candidates was flagged for mutating the
        winner dict in place (its reasons, warnings, merged_from,
        alternate_urls). The handoff notes the in-place mutation is
        the existing contract — changing it would break ~15
        downstream callers. Fix: documented as INV-DEDUP-MUTATES;
        test locks the contract in.

Bug Z — _dedup_candidates uses strict `>` for the score comparison,
        so same-score duplicates resolve to first-occurrence. This is
        deliberate (the flatten step emits structured sources first
        so first-occurrence reflects "more structured" wins).
        Fix: documented as INV-DEDUP-TIEBREAK; test locks it in.

Bug 17 — canonicalize_url dropped ALL non-functional fragments. SPA
         fragment routing (`#/path/123`, `#!/page`) was being
         stripped, meaning two distinct SPA-routed URLs like
         `app#/video/123` and `app#/video/456` canonicalized to the
         SAME key and would silently dedupe. Fix: preserve fragments
         that start with `/` or `!/` in addition to media-fragment
         prefixes.

Bug 19 — IDN hosts (`éxmple.com` vs `xn--xmple-9ra.com`) didn't
         normalize. The two spellings of the same host produced
         different canonical keys, breaking dedupe. Fix: encode the
         host to ASCII via the idna codec when any non-ASCII char is
         present. Failures fall back to the lowercased original so
         the function never raises on bad input.

Bug EE — Multiple <meta http-equiv="refresh"> tags on one page:
         documented as "first-match-wins" (matches browser behavior).
         No code change; test locks in the contract.

Bug FF — follow_meta_refresh is a single-call helper that does NOT
         recurse, so meta-refresh redirect chains can't infinite-
         loop at the helper level. The live-mode caller now flags
         self-referential targets (target == base_url) in
         report["probes"]["meta_refresh"]["self_loop"] so downstream
         tooling that does walk the chain can detect loops without
         needing to re-parse.

Bug GG — Decoy <meta http-equiv="refresh"> tags inside HTML
         comments were matching the regex, shadowing real
         meta-refresh tags elsewhere on the page. Fix: strip HTML
         comments before scanning. Real-world scrape concern + a
         potential decoy-injection vector.

Bug 25 — DASH subtitle precedence: a Representation with
         mimeType="application/mp4" inside an AdaptationSet
         declaring contentType="text" (the legitimate way to mark a
         muxed subtitle track) was being missed. The parser only
         honoured the Representation's mime prefix. Fix: treat
         AdaptationSet contentType in
         {"text", "subtitle(s)", "caption(s)"} as an explicit
         subtitle signal regardless of Representation mimeType.

Bug 26 — DASH parser used root.iter() to find Period elements,
         which descends into the full tree and counts any element
         named "Period" anywhere (including spurious nested
         instances inside SupplementalProperty etc.). period_idx
         advanced past every match, so a non-spec MPD with a
         nested Period-named tag produced wrong period numbers.
         Fix: iterate direct children only at each level.

Bug D/E/F/H — state-blob walker edge cases. The audit grouped these
         as "mostly mitigated by the bug G fix" (the v3.66.10
         template-literal + ${...} interp-stack tracking). On
         investigation, the balancer correctly handles strings,
         template literals, and regex-shaped values inside string
         contexts; the residual failure mode is when the assignment
         is a JavaScript object literal (not JSON) — function
         values, naked regex literals, trailing commas, unquoted
         keys. The balancer extracts a syntactically-balanced blob
         in those cases, but `json.loads()` then cleanly rejects
         it and the call returns no candidates. This is a
         documented limitation, not a fixable bug at this scope
         (fixing would require a full JS-syntax parser). Tagged as
         INV-STATE-BLOB-JSON-ONLY; tests lock in the no-crash
         behaviour and the regression-guard around the bug-G fix.

These tests are written to pass against the FIXED code and fail
against the pre-patch behaviour.

The fixture mirrors test_v3_66_9_deep_detect_audit_fixes.py
(isolated BD_HOME, sys.modules wipe — Pattern A) so the package is
reimported fresh.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Bug R: HLS encryption detection ───────────────────────────────────

def test_bug_R_method_none_in_uri_does_not_suppress_encryption():
    """A real encrypted #EXT-X-KEY tag must be detected even when a
    sibling URI string happens to contain the substring 'METHOD=NONE'.
    This is the false-negative case the pre-fix code had."""
    from bulk_downloader.deep_detect import hls_has_encryption
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://cdn.example.com/'
        'key.bin?session=METHOD=NONE&v=1"\n'
        "#EXTINF:6.0,\n"
        "seg1.ts\n"
    )
    assert hls_has_encryption(playlist) is True


def test_bug_R_method_none_is_detected_as_unencrypted():
    """A real METHOD=NONE key declaration (the legitimate way HLS
    says 'no encryption') must NOT trigger the encrypted flag."""
    from bulk_downloader.deep_detect import hls_has_encryption
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-KEY:METHOD=NONE\n"
        "#EXTINF:6.0,\n"
        "seg1.ts\n"
    )
    assert hls_has_encryption(playlist) is False


def test_bug_R_no_key_tag_at_all_is_unencrypted():
    """Sanity: a playlist with no #EXT-X-KEY tag isn't encrypted."""
    from bulk_downloader.deep_detect import hls_has_encryption
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXTINF:6.0,\n"
        "seg1.ts\n"
    )
    assert hls_has_encryption(playlist) is False


def test_bug_R_aes_sample_method():
    """SAMPLE-AES is encryption too, not just AES-128."""
    from bulk_downloader.deep_detect import hls_has_encryption
    playlist = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key.bin"\n'
        "seg1.ts\n"
    )
    assert hls_has_encryption(playlist) is True


def test_bug_R_mixed_methods_one_encrypted_means_encrypted():
    """A playlist with BOTH a METHOD=NONE clearing tag and a later
    METHOD=AES-128 tag is encrypted (the later key applies)."""
    from bulk_downloader.deep_detect import hls_has_encryption
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-KEY:METHOD=NONE\n"
        "#EXTINF:6.0,\n"
        "seg1.ts\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n'
        "#EXTINF:6.0,\n"
        "seg2.ts\n"
    )
    assert hls_has_encryption(playlist) is True


def test_bug_R_parse_hls_master_uses_helper_for_drm_flag():
    """End-to-end: parse_hls_master honours hls_has_encryption when
    setting drm_or_encryption_detected. A master with METHOD=AES-128
    must flag DRM; a clean master must not."""
    from bulk_downloader.deep_detect import parse_hls_master
    encrypted_master = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://cdn/key.bin"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(encrypted_master, base_url="https://cdn/")
    assert out["drm_or_encryption_detected"] is True

    clean_master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out2 = parse_hls_master(clean_master, base_url="https://cdn/")
    assert out2["drm_or_encryption_detected"] is False


# ── Bug T: RESOLUTION fullmatch ───────────────────────────────────────

def test_bug_T_resolution_with_trailing_garbage_is_rejected():
    """RESOLUTION=1920x1080extra (non-spec garbage tail) must NOT be
    accepted. The previous re.match-only check was start-anchored,
    so any trailing text was silently truncated and the variant
    surfaced as 1920x1080. Now: fullmatch rejects."""
    from bulk_downloader.deep_detect import parse_hls_master
    # The HLS parser comma-splits the attribute list, so the
    # realistic way to get garbage into the attribute *value* is via
    # a non-comma trailing character. Construct that case via a
    # quoted-attribute-style attack vector (some real-world encoders
    # have shipped RESOLUTION values like "1920x1080p" mistakenly).
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080p\n"
        "weird.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert len(out["variants"]) == 1
    v = out["variants"][0]
    # 1920x1080p is malformed; the parser must NOT silently accept
    # the (1920, 1080) prefix and pretend it parsed cleanly.
    assert v.get("width") in (None, 0), (
        f"RESOLUTION=1920x1080p must not be truncate-parsed; "
        f"got width={v.get('width')!r}")
    assert v.get("height") in (None, 0)


def test_bug_T_resolution_with_trailing_whitespace_still_parses():
    """Regression-guard: some encoders emit RESOLUTION=1920x1080 with
    trailing whitespace before the next attribute (non-spec but seen
    in the wild). The stripped-before-fullmatch fix must preserve
    this behaviour rather than over-rejecting."""
    from bulk_downloader.deep_detect import parse_hls_master
    # Note: attribute list parsing handles surrounding whitespace at
    # the attribute level, but value-internal whitespace could still
    # leak in. Pre-strip means this works either way.
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000, RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert len(out["variants"]) == 1
    assert out["variants"][0].get("width") == 1920
    assert out["variants"][0].get("height") == 1080


def test_bug_T_valid_resolution_still_parses():
    """Regression-guard: well-formed RESOLUTION values still work."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=18000000,RESOLUTION=3840x2160\n"
        "2160.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert len(out["variants"]) == 2
    widths = sorted(v["width"] for v in out["variants"])
    assert widths == [1920, 3840]


# ── Bug U: BANDWIDTH float-then-int ───────────────────────────────────

def test_bug_U_bandwidth_float_string_parses():
    """BANDWIDTH=8000000.0 is non-spec but seen in the wild. Must
    coerce to 8000000 rather than silently fall back to 0."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000.0,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert len(out["variants"]) == 1
    assert out["variants"][0]["bandwidth"] == 8000000


def test_bug_U_bandwidth_integer_string_still_parses():
    """Regression-guard: BANDWIDTH=8000000 (the spec-correct form)
    still works."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert out["variants"][0]["bandwidth"] == 8000000


def test_bug_U_bandwidth_garbage_falls_back_to_zero():
    """Non-numeric BANDWIDTH must NOT crash; falls back to 0."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=auto,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert out["variants"][0]["bandwidth"] == 0


# ── Bug V: FRAME-RATE 0.0 vs missing ──────────────────────────────────

def test_bug_V_missing_frame_rate_is_none():
    """A variant that doesn't declare FRAME-RATE must report None."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert out["variants"][0]["frame_rate"] is None


def test_bug_V_zero_frame_rate_is_distinguishable_from_missing():
    """An explicit FRAME-RATE=0 is non-meaningful but distinguishable
    from missing. Preserved as 0.0 for diagnostic visibility (callers
    that want 'usable frame rate' can treat <= 0 as missing)."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,"
        "FRAME-RATE=0\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    fr = out["variants"][0]["frame_rate"]
    # Must be EXPLICIT 0.0, not None
    assert fr == 0.0
    assert fr is not None


def test_bug_V_nonzero_frame_rate_parses():
    """Regression-guard: a real frame rate parses through."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,"
        "FRAME-RATE=29.97\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert out["variants"][0]["frame_rate"] == pytest.approx(29.97)


def test_bug_V_garbage_frame_rate_is_none():
    """A FRAME-RATE attribute with non-numeric content must not crash;
    becomes None (failed parse)."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,"
        "FRAME-RATE=auto\n"
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert out["variants"][0]["frame_rate"] is None


# ── Bug JJ: HLS/DASH manifest dispatch ordering ───────────────────────

def test_bug_JJ_hls_master_input_routes_to_hls_branch():
    """Master playlist as input still routes to the HLS branch and
    surfaces variants. (The dispatcher simplification must not break
    the master-as-input path.)"""
    from bulk_downloader.deep_detect import deep_detect
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080\n"
        "1080.m3u8\n"
    )
    out = deep_detect(master, base_url="https://cdn/feed/")
    assert out.get("buckets", {}).get("accepted"), \
        "master HLS input should produce candidates"
    assert out["manifests"].get("hls") is not None
    assert out["manifests"]["hls"]["kind"] == "hls_master"


def test_bug_JJ_hls_media_input_still_surfaces_manifest_candidate():
    """Media playlist (not a master) as input must still surface the
    manifest itself as a candidate when base_url is provided. This is
    the v3.66.10 fix that bug JJ's dispatcher change must preserve."""
    from bulk_downloader.deep_detect import deep_detect
    media = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXTINF:6.0,\n"
        "seg1.ts\n"
        "#EXTINF:6.0,\n"
        "seg2.ts\n"
        "#EXT-X-ENDLIST\n"
    )
    out = deep_detect(media, base_url="https://cdn/feed/master.m3u8")
    assert out.get("buckets", {}).get("accepted"), \
        "media HLS input with base_url should surface one candidate"
    types = {c.get("source_type") for c in out["buckets"]["accepted"]}
    assert "hls_manifest" in types


def test_bug_JJ_dash_input_routes_to_dash_branch():
    """DASH MPD as input routes to the DASH branch (and never gets
    mis-routed to HLS)."""
    from bulk_downloader.deep_detect import deep_detect
    mpd = (
        '<?xml version="1.0"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'type="static" mediaPresentationDuration="PT1M">\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <Representation id="1" bandwidth="8000000" '
        'width="1920" height="1080">\n'
        '        <BaseURL>video.mp4</BaseURL>\n'
        '      </Representation>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    out = deep_detect(mpd, base_url="https://cdn/")
    assert out["manifests"].get("dash") is not None
    assert out["manifests"].get("hls") is None, \
        "DASH input must not be misclassified as HLS"


# ── Bug S: full alt-media schema documented ───────────────────────────

def test_bug_S_alt_media_audio_entry_carries_full_schema():
    """parse_hls_master must emit the full documented schema for
    audio entries: url, name, lang, default, group_id, channels,
    forced. Pre-fix the docstring listed only {url, name, lang,
    default, group_id, ...} without enumerating the rest, leaving
    callers guessing at the shape."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English",'
        'LANGUAGE="en",DEFAULT=YES,CHANNELS="2",'
        'URI="https://cdn/audio.m3u8"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,"
        'AUDIO="aac"\n'
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert len(out["audio"]) == 1
    e = out["audio"][0]
    # All documented keys must be present
    for key in ("url", "name", "lang", "default", "group_id",
                "channels", "forced"):
        assert key in e, f"audio entry missing documented key {key!r}"
    assert e["group_id"] == "aac"
    assert e["channels"] == "2"
    assert e["default"] is True
    assert e["forced"] is False  # FORCED defaults to NO


def test_bug_S_alt_media_subtitle_entry_carries_full_schema():
    """Same schema requirement for subtitle entries."""
    from bulk_downloader.deep_detect import parse_hls_master
    master = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",'
        'LANGUAGE="en",FORCED=YES,URI="https://cdn/subs.m3u8"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,"
        'SUBTITLES="subs"\n'
        "1080.m3u8\n"
    )
    out = parse_hls_master(master, base_url="https://cdn/")
    assert len(out["subtitles"]) == 1
    e = out["subtitles"][0]
    for key in ("url", "name", "lang", "default", "group_id",
                "channels", "forced"):
        assert key in e, f"subtitle entry missing documented key {key!r}"
    assert e["forced"] is True
    assert e["group_id"] == "subs"


# ── Bug X/Y/Z: dedup contracts (load-bearing invariants) ──────────────

def test_bug_X_dedup_mutates_winner_in_place():
    """INV-DEDUP-MUTATES: _dedup_candidates is documented to mutate
    the winner dict in place (its reasons, warnings, merged_from,
    alternate_urls). This test locks in that contract so a future
    well-intentioned refactor to "make it pure" doesn't silently
    break the ~15 downstream callers that read merged_from off the
    same dict they passed in."""
    from bulk_downloader.deep_detect import _dedup_candidates
    a = {"url": "https://x.com/v.mp4", "score": 100,
         "found_in": "hls_master:variant",
         "reasons": ["a-reason"], "warnings": []}
    b = {"url": "https://x.com/v.mp4?utm_source=foo", "score": 50,
         "found_in": "anchor",
         "reasons": ["b-reason"], "warnings": []}
    pre_a_reasons_id = id(a.get("reasons"))
    out = _dedup_candidates([a, b])
    assert len(out) == 1
    winner = out[0]
    # The mutation contract: winner is the SAME object as the input
    # high-scoring dict, with its lists extended.
    assert winner is a
    # merged_from must capture b's origin
    assert "anchor" in (winner.get("merged_from") or [])
    # b's reason got folded into winner's reasons
    assert "b-reason" in winner["reasons"]
    # The reasons list may have been reassigned to a new list (the
    # implementation builds a list copy before merging), but the
    # winner dict identity is preserved.


def test_bug_Z_dedup_tiebreak_goes_to_first_occurrence():
    """INV-DEDUP-TIEBREAK: with strict-`>` comparison, same-score
    candidates resolve to the FIRST-OCCURRENCE one. Order is
    deliberate: flatten emits surfaces in a stable
    HLS-then-DASH-then-state-blob-then-anchor order, so first-
    occurrence reflects 'the more structured source got there
    first'."""
    from bulk_downloader.deep_detect import _dedup_candidates
    first = {"url": "https://x.com/v.mp4", "score": 100,
             "found_in": "hls_master:variant",
             "reasons": [], "warnings": []}
    second = {"url": "https://x.com/v.mp4", "score": 100,
              "found_in": "anchor",
              "reasons": [], "warnings": []}
    out = _dedup_candidates([first, second])
    assert len(out) == 1
    assert out[0] is first, "tie must resolve to first-occurrence"
    assert out[0]["found_in"] == "hls_master:variant"


def test_bug_Z_dedup_strict_inequality_lets_later_higher_win():
    """Companion to the tiebreak invariant: when the later candidate
    has a STRICTLY higher score, it wins. Only ties go to first."""
    from bulk_downloader.deep_detect import _dedup_candidates
    first = {"url": "https://x.com/v.mp4", "score": 50,
             "found_in": "anchor",
             "reasons": [], "warnings": []}
    second = {"url": "https://x.com/v.mp4", "score": 100,
              "found_in": "hls_master:variant",
              "reasons": [], "warnings": []}
    out = _dedup_candidates([first, second])
    assert len(out) == 1
    assert out[0]["found_in"] == "hls_master:variant"
    assert out[0]["score"] == 100


# ── Bug 17: SPA fragment routing preserved ────────────────────────────

def test_bug_17_spa_fragment_routing_preserved():
    """Two SPA URLs that differ only in fragment-routing path must
    NOT dedupe to the same canonical form."""
    from bulk_downloader.deep_detect import canonicalize_url
    a = "https://x.com/app#/video/123"
    b = "https://x.com/app#/video/456"
    assert canonicalize_url(a) != canonicalize_url(b), (
        "SPA-routed URLs differing only in fragment-route must not "
        "canonicalize to the same key")


def test_bug_17_hashbang_routing_preserved():
    """The older hashbang convention (`#!/page`) is also preserved."""
    from bulk_downloader.deep_detect import canonicalize_url
    a = "https://x.com/app#!/page/1"
    b = "https://x.com/app#!/page/2"
    assert canonicalize_url(a) != canonicalize_url(b)


def test_bug_17_in_page_anchors_still_dropped():
    """Regression-guard: bare in-page anchor fragments (`#section-1`)
    must STILL be dropped — they're navigation, not route. Two URLs
    that differ only in in-page anchor must dedupe."""
    from bulk_downloader.deep_detect import canonicalize_url
    a = "https://x.com/page#section-1"
    b = "https://x.com/page#section-2"
    assert canonicalize_url(a) == canonicalize_url(b)


def test_bug_17_media_timecode_fragment_still_preserved():
    """Regression-guard: the original functional-fragment prefixes
    (`#t=...`, `#xywh=...`, etc.) must still be preserved."""
    from bulk_downloader.deep_detect import canonicalize_url
    a = "https://x.com/v.mp4#t=10,20"
    b = "https://x.com/v.mp4#t=30,40"
    assert canonicalize_url(a) != canonicalize_url(b)


# ── Bug 19: IDN host normalized to punycode ───────────────────────────

def test_bug_19_idn_host_normalizes_to_punycode():
    """Two URLs differing only in IDN spelling (unicode vs punycode)
    must canonicalize to the same key."""
    from bulk_downloader.deep_detect import canonicalize_url
    # éxmple.com → xn--xmple-9ra.com
    unicode_form = "https://\u00e9xmple.com/file.mp4"
    puny_form = "https://xn--xmple-9ra.com/file.mp4"
    assert canonicalize_url(unicode_form) == canonicalize_url(puny_form)


def test_bug_19_ascii_hosts_unchanged():
    """Regression-guard: pure-ASCII hosts are unaffected by the IDN
    branch (the `any(ord(ch) > 127)` predicate gates it)."""
    from bulk_downloader.deep_detect import canonicalize_url
    assert canonicalize_url("https://example.com/x") == \
        "https://example.com/x"


def test_bug_19_invalid_idn_falls_back_gracefully():
    """Regression-guard: a unicode host that's not valid IDN (e.g.
    invalid combining marks) must NOT crash; the fix's try/except
    falls back to the lowercased original."""
    from bulk_downloader.deep_detect import canonicalize_url
    # Unicode hostname with content that idna codec rejects.
    # IDN codec rejects empty labels and various edge cases; this
    # input is at least exotic enough to exercise the except path.
    weird = "https://\u200b\u200b.com/x"  # zero-width spaces
    # Either it canonicalizes silently or it falls back; either way
    # MUST not raise.
    result = canonicalize_url(weird)
    assert isinstance(result, str)


# ── Bug EE/FF/GG: meta-refresh edge cases ─────────────────────────────

def test_bug_GG_commented_out_meta_refresh_does_not_match():
    """A decoy <meta http-equiv='refresh'> inside an HTML comment
    must NOT shadow a real meta-refresh elsewhere on the page.
    Pre-fix, the regex scanned the entire HTML including comment
    bodies, so the decoy URL was returned."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = (
        '<!-- <meta http-equiv="refresh" content="0;url=https://decoy.example/"> -->'
        '<meta http-equiv="refresh" content="0;url=https://real.example/">'
    )
    result = follow_meta_refresh(html, base_url="https://src.example/")
    assert result == "https://real.example/"


def test_bug_GG_only_commented_out_meta_returns_none():
    """A page that has ONLY a commented-out meta-refresh must
    return None, not the commented-out URL."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = (
        '<!-- <meta http-equiv="refresh" content="0;url=https://decoy/"> -->'
    )
    result = follow_meta_refresh(html, base_url="https://src.example/")
    assert result is None


def test_bug_GG_multiline_comment_stripped():
    """The comment regex uses re.S so multi-line comments are also
    handled. A real meta-refresh after a multi-line comment must
    still match."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = (
        '<!--\n'
        '  legacy redirect, kept for reference:\n'
        '  <meta http-equiv="refresh" content="0;url=https://old.example/">\n'
        '-->\n'
        '<meta http-equiv="refresh" content="0;url=https://new.example/">\n'
    )
    result = follow_meta_refresh(html, base_url="https://src.example/")
    assert result == "https://new.example/"


def test_bug_EE_first_match_wins_when_multiple_meta_refresh():
    """When a page has multiple <meta refresh> tags, follow_meta_refresh
    returns the first in document order. This matches browser behavior
    and the v3.66.11 documented contract."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = (
        '<meta http-equiv="refresh" content="0;url=https://first.example/">'
        '<meta http-equiv="refresh" content="5;url=https://second.example/">'
    )
    result = follow_meta_refresh(html, base_url="https://src.example/")
    assert result == "https://first.example/"


def test_bug_FF_self_loop_does_not_crash():
    """follow_meta_refresh itself does not recurse, so a self-loop
    (target equals base_url) is just returned as a string. The
    caller is responsible for not following it."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    html = (
        '<meta http-equiv="refresh" '
        'content="0;url=https://src.example/page">'
    )
    result = follow_meta_refresh(html, base_url="https://src.example/page")
    # The helper returns the target verbatim — no recursion, no crash.
    assert result == "https://src.example/page"


def test_bug_FF_live_mode_flags_self_loop_in_probes():
    """deep_detect_live records meta-refresh in report['probes']
    ['meta_refresh']. The dict now carries a `self_loop` flag so
    downstream tooling that walks the report can warn the operator
    instead of blindly following the redirect."""
    # We can verify by inspection: a real deep_detect_live call needs
    # an http client and is heavier than a regex test. Instead, just
    # confirm the source has the self_loop annotation logic and the
    # flag is in the docstring contract.
    import bulk_downloader.deep_detect as dd
    import inspect
    from pathlib import Path
    # deep_detect is a package after the structural split; getsource(pkg) returns only the
    # __init__ shim, so read the aggregate of all submodule sources (still works for a
    # single module).
    if hasattr(dd, "__path__"):
        src = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(Path(dd.__file__).parent.glob("*.py")))
    else:
        src = inspect.getsource(dd)
    # The annotation site exists
    assert '"self_loop": is_self_loop' in src, (
        "expected self_loop annotation in meta_refresh probe record")


def test_bug_GG_realistic_quoted_url_forms_still_match():
    """Regression-guard for the existing v3.66.10 fixes: a URL
    surrounded by single quotes inside double-quoted content, and
    vice versa, still parse correctly."""
    from bulk_downloader.deep_detect import follow_meta_refresh
    # Single-quoted URL inside double-quoted content attribute
    html_a = (
        '<meta http-equiv="refresh" '
        '''content="0;url='https://x.example/dest'">'''
    )
    assert follow_meta_refresh(html_a, base_url="https://src/") == \
        "https://x.example/dest"
    # Double-quoted URL inside single-quoted content attribute
    html_b = (
        '<meta http-equiv="refresh" '
        '''content='0;url="https://x.example/dest"'>'''
    )
    assert follow_meta_refresh(html_b, base_url="https://src/") == \
        "https://x.example/dest"


# ── Bug 25/26: DASH parser edge cases ─────────────────────────────────

def test_bug_25_subtitle_via_adaptation_set_content_type():
    """A subtitle Representation declared with mimeType="application/
    mp4" (muxed subs) inside an AdaptationSet whose contentType is
    "text" must surface in the subtitles list. Pre-fix the parser
    only checked the Representation's mimeType prefix and a single
    contentType-substring check — missed the legitimate
    `contentType="text"` case entirely."""
    from bulk_downloader.deep_detect import parse_dash_mpd
    mpd = (
        '<?xml version="1.0"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">\n'
        '  <Period>\n'
        '    <AdaptationSet contentType="text" lang="en">\n'
        '      <Representation id="sub-en" mimeType="application/mp4"\n'
        '                      bandwidth="1000" codecs="stpp"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    out = parse_dash_mpd(mpd, base_url="https://x/")
    assert out["kind"] == "dash_mpd"
    assert len(out["subtitles"]) == 1
    assert out["subtitles"][0]["lang"] == "en"


def test_bug_25_subtitle_via_content_type_subtitles():
    """contentType="subtitles" (plural) is also valid."""
    from bulk_downloader.deep_detect import parse_dash_mpd
    mpd = (
        '<?xml version="1.0"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">\n'
        '  <Period>\n'
        '    <AdaptationSet contentType="subtitles" lang="es">\n'
        '      <Representation id="sub-es" mimeType="application/mp4"\n'
        '                      bandwidth="1500"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    out = parse_dash_mpd(mpd, base_url="https://x/")
    assert len(out["subtitles"]) == 1


def test_bug_25_subtitle_via_content_type_caption():
    """contentType="caption" / "captions" is also valid (CEA-608)."""
    from bulk_downloader.deep_detect import parse_dash_mpd
    mpd = (
        '<?xml version="1.0"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">\n'
        '  <Period>\n'
        '    <AdaptationSet contentType="captions" lang="en">\n'
        '      <Representation id="cap-en" mimeType="application/mp4"\n'
        '                      bandwidth="500"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    out = parse_dash_mpd(mpd, base_url="https://x/")
    assert len(out["subtitles"]) == 1


def test_bug_25_video_still_takes_precedence_over_subtitle():
    """Regression-guard: when a Representation HAS width/height (so
    it's clearly video), it must go to the video bucket regardless
    of what contentType says about the AdaptationSet."""
    from bulk_downloader.deep_detect import parse_dash_mpd
    mpd = (
        '<?xml version="1.0"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <Representation id="r1" width="1920" height="1080"\n'
        '                      bandwidth="8000000"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    out = parse_dash_mpd(mpd, base_url="https://x/")
    assert len(out["video"]) == 1
    assert len(out["subtitles"]) == 0


def test_bug_26_nested_period_tag_not_counted():
    """A non-spec MPD with an element named "Period" nested inside a
    non-Period parent (e.g., SupplementalProperty) must NOT count
    toward period_idx. Pre-fix, root.iter() flattened the tree and
    advanced period_idx for any tag named Period anywhere."""
    from bulk_downloader.deep_detect import parse_dash_mpd
    mpd = (
        '<?xml version="1.0"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <Representation id="r1" width="1920" height="1080"\n'
        '                      bandwidth="8000000"/>\n'
        '    </AdaptationSet>\n'
        '    <SupplementalProperty>\n'
        '      <Period>not really a Period</Period>\n'
        '    </SupplementalProperty>\n'
        '  </Period>\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <Representation id="r2" width="3840" height="2160"\n'
        '                      bandwidth="20000000"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    out = parse_dash_mpd(mpd, base_url="https://x/")
    # Two true Periods → period_idx values should be 1 and 2, NOT 1
    # and 3 (the pre-fix bug had the inner fake-Period advance the
    # counter).
    periods = sorted({v["period"] for v in out["video"]})
    assert periods == [1, 2], (
        f"expected period indices [1, 2]; got {periods}. The inner "
        f"fake-Period tag must not advance the counter.")


def test_bug_26_two_period_mpd_unaffected():
    """Regression-guard: a clean two-Period MPD still reports
    period=1 and period=2 (no double-counting)."""
    from bulk_downloader.deep_detect import parse_dash_mpd
    mpd = (
        '<?xml version="1.0"?>\n'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <Representation id="r1" width="1920" height="1080"\n'
        '                      bandwidth="8000000"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '  <Period>\n'
        '    <AdaptationSet mimeType="video/mp4">\n'
        '      <Representation id="r2" width="3840" height="2160"\n'
        '                      bandwidth="20000000"/>\n'
        '    </AdaptationSet>\n'
        '  </Period>\n'
        '</MPD>\n'
    )
    out = parse_dash_mpd(mpd, base_url="https://x/")
    periods = sorted({v["period"] for v in out["video"]})
    assert periods == [1, 2]


# ── Bugs D/E/F/H: state-blob balancer / JSON-vs-JS impedance ──────────

def test_bug_D_valid_json_with_regex_shaped_string_still_extracts():
    """A JSON state-blob whose string values happen to LOOK like
    regex literals (`/foo{1,2}/`) must still extract cleanly. The
    balancer must not confuse the slashes with regex delimiters."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = (
        '<html><body><script>\n'
        'window.__INITIAL_STATE__ = {\n'
        '  "regex_pattern": "/foo{1,2}/",\n'
        '  "videos": [{"url": "https://example.com/v.mp4"}]\n'
        '};\n'
        '</script></body></html>'
    )
    urls = extract_state_blob_urls(html, base_url="https://x/")
    assert any(u.get("url") == "https://example.com/v.mp4" for u in urls)


def test_bug_E_valid_json_with_braces_in_strings_still_extracts():
    """A JSON state-blob whose string values contain literal `{` and
    `}` (e.g., code samples) must still extract cleanly. Strings
    are quoted; the balancer must ignore braces inside them."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = (
        '<html><body><script>\n'
        'window.__INITIAL_STATE__ = {\n'
        '  "code_sample": '
        '"for (var i = 0; i < 10; i++) { console.log(i); }",\n'
        '  "videos": [{"url": "https://example.com/v2.mp4"}]\n'
        '};\n'
        '</script></body></html>'
    )
    urls = extract_state_blob_urls(html, base_url="https://x/")
    assert any(u.get("url") == "https://example.com/v2.mp4" for u in urls)


def test_bug_F_js_object_literal_with_function_value_does_not_crash():
    """INV-STATE-BLOB-JSON-ONLY: a `window.X = {...}` assignment
    containing JavaScript function values (not valid JSON) must
    NOT crash — extraction silently returns no candidates. This
    is a documented limitation: JS object literals with function
    values require a full JS parser to handle, and that's out of
    scope. The balancer correctly extracts a syntactically-
    balanced blob; the JSON parser then cleanly rejects it."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = (
        '<html><body><script>\n'
        'window.__INITIAL_STATE__ = {\n'
        '  "validator": function(s) { return /{\\d+}/.test(s); },\n'
        '  "videos": [{"url": "https://example.com/v3.mp4"}]\n'
        '};\n'
        '</script></body></html>'
    )
    # Must not raise — must just return no candidates from this
    # script tag (extraction silently fails on the JS).
    urls = extract_state_blob_urls(html, base_url="https://x/")
    # The point is that this doesn't crash; we accept 0 results.
    assert isinstance(urls, list)


def test_bug_H_nested_function_with_regex_does_not_crash():
    """INV-STATE-BLOB-JSON-ONLY: deeply-nested JS object literal
    (function returning a function returning a regex match) must
    not crash. Same limitation as F; documented behaviour is
    silent extraction failure."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = (
        '<html><body><script>\n'
        'window.__INITIAL_STATE__ = {\n'
        '  "check": function() {\n'
        '    return function(x) { return x.match(/foo{1,2}/); };\n'
        '  },\n'
        '  "videos": [{"url": "https://example.com/v4.mp4"}]\n'
        '};\n'
        '</script></body></html>'
    )
    urls = extract_state_blob_urls(html, base_url="https://x/")
    assert isinstance(urls, list)


def test_invariant_state_blob_balancer_handles_template_literals():
    """Regression-guard: the v3.66.10 bug-G fix (template-literal
    + ${...} interp stack) must keep working. A state-blob value
    that contains a backtick template literal with interpolation
    must still produce a balanced extraction (the JSON parse will
    still fail because it's JS, but the balancer must extract a
    balanced blob and not consume the rest of the document)."""
    from bulk_downloader.deep_detect import extract_state_blob_urls
    html = (
        '<html><body><script>\n'
        'window.__INITIAL_STATE__ = {\n'
        '  "template": `hello ${name} {nested}`,\n'
        '  "videos": [{"url": "https://example.com/tpl.mp4"}]\n'
        '};\n'
        '</script></body></html>'
    )
    # Must not raise, must not hang, must not consume past the };
    urls = extract_state_blob_urls(html, base_url="https://x/")
    assert isinstance(urls, list)


# ── Bug KK: prefer_resolution applies to resolution-less candidates ───

def _make_candidate(url, source_type, score, rank=None, height=None,
                    width=None):
    """Build a minimal candidate dict matching what flatten emits."""
    c = {
        "url": url,
        "source_type": source_type,
        "score": score,
        "resolution": None,
        "codec": None,
        "fps": None,
        "size_bytes": None,
        "found_in": "test",
        "reasons": [],
        "warnings": [],
        "requires_click": False,
    }
    if rank is not None:
        c["resolution"] = {
            "rank": rank,
            "label": f"rank_{rank}",
            "width": width,
            "height": height,
        }
    return c


def test_bug_KK_resolution_less_candidates_get_penalty_under_pref():
    """When prefer_resolution names a tier (e.g. '4k') and the
    candidate list contains BOTH a matching candidate and a
    resolution-less one, the matching candidate must beat the
    resolution-less one. Pre-fix, the resolution-less candidate kept
    its full score because the bias loop `continue`'d on it."""
    # We can't easily call the full deep_detect() to drive this, but
    # the bias is applied in deep_detect() right before the final
    # sort. We exercise the logic by calling deep_detect on synthetic
    # HTML that exposes both candidate shapes.
    #
    # The cleanest end-to-end angle: simulate a page with a known-4K
    # resolution card AND a provider embed (resolution-less). Without
    # prefer_resolution, both surface. With prefer_resolution="4k",
    # the 4K candidate must rank first.
    from bulk_downloader.deep_detect import deep_detect

    html = """
    <html><body>
      <div class="dl-card" data-quality="4k">
        <a href="/video_4k.mp4" class="download-4k">
          Download 4K (3840x2160)
        </a>
      </div>
      <iframe src="https://player.vimeo.com/video/12345"></iframe>
    </body></html>
    """
    out = deep_detect(html, base_url="https://example.com/",
                      prefer_resolution="4k")
    cands = out.get("buckets", {}).get("accepted") or []
    assert cands, "expected at least one candidate"
    # The top candidate should be the 4K one, NOT the resolution-less
    # provider embed. Identify by URL substring.
    top = cands[0]
    assert "video_4k" in (top.get("url") or "") or \
           (top.get("resolution") or {}).get("rank") is not None, (
        f"top candidate should be the resolution-bearing one with "
        f"prefer_resolution=4k; got url={top.get('url')!r} "
        f"resolution={top.get('resolution')}")


def test_bug_KK_highest_preference_does_not_penalize():
    """Regression-guard: with prefer_resolution='highest' (the default),
    NO bias is applied — so resolution-less candidates keep their
    original scores. Verify by checking that the bias branch is not
    entered (resolution-less candidate score unchanged)."""
    # Build two test candidates and run the bias loop in isolation by
    # invoking deep_detect with prefer_resolution="highest" — which is
    # the no-op preference. Scores should reflect only the base
    # flatten scores, not penalties.
    from bulk_downloader.deep_detect import deep_detect

    html = """
    <html><body>
      <iframe src="https://player.vimeo.com/video/12345"></iframe>
    </body></html>
    """
    out_default = deep_detect(html, base_url="https://example.com/")
    out_highest = deep_detect(html, base_url="https://example.com/",
                              prefer_resolution="highest")
    # Default and "highest" must produce identical candidate scoring
    # for resolution-less candidates (no penalty branch is reached).
    if out_default.get("buckets", {}).get("accepted") and \
       out_highest.get("buckets", {}).get("accepted"):
        s1 = out_default["buckets"]["accepted"][0].get("score")
        s2 = out_highest["buckets"]["accepted"][0].get("score")
        assert s1 == s2, \
            f"highest preference must not penalize; got {s1} vs {s2}"
