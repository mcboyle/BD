"""DEEP LANE adversary probes for row761b (O809).

One RED test per lens finding in bd-review-wt/row761b-bd-cx-worker13-local/.review/
(current REFUTE VERDICT-bd-cx-rs-med1.md, plus superseded/) PLUS independent probing
of the refinementList input class (percent-encoding, key-vs-value, path-vs-query,
case, empty fields, whitespace). Author's current tree still admits the
query-KEY over-refusal the current lens REFUTE names; other findings were
already fixed by prior rounds and are pinned here as regression guards.

Do not weaken/skip: a real product fix must flip only the tests marked LIVE BUG.
"""

BD_GATE_SCOPE = "repo-wide"

from bulk_downloader.detect import _candidate_admission, _has_non_video_url_shape

PAGE = "https://members.gammaentertainment.example/en/scenes/a.html"


class Facet:
    def __init__(self, href):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None


def admitted(href, label="2160p"):
    """True if the candidate SURVIVES admission (is not refused as non_video)."""
    return _candidate_admission(Facet(href), f"{label} {href}", PAGE, label=label) is None


# ---------------------------------------------------------------------------
# 1. CURRENT lens finding (VERDICT-bd-cx-rs-med1.md, TREE 64b7db21...): a real
#    MP4 download whose query KEY is refinementList (encoded bracket) is still
#    incorrectly refused. LIVE BUG -- expected RED on current tree.
# ---------------------------------------------------------------------------
def test_lens_current_query_key_encoded_bracket_on_real_mp4_is_admitted():
    href = "https://cdn.example/scene_2160p.mp4?refinementList%5Bformat%5D=2160p"
    assert admitted(href), "real MP4 download refused: query-key false positive"


# Same finding, literal (undecoded) bracket spelling -- distinct code path
# through parse_qsl vs. the raw string; both are the identical product bug.
def test_lens_current_query_key_literal_bracket_on_real_mp4_is_admitted():
    href = "https://cdn.example/scene_2160p.mp4?refinementList[format]=2160p"
    assert admitted(href), "real MP4 download refused: query-key false positive"


# ---------------------------------------------------------------------------
# 2. SUPERSEDED findings (already fixed by prior rounds) -- pinned here as
#    regression guards so a re-fix of (1) cannot re-break them.
# ---------------------------------------------------------------------------
def test_superseded_query_value_with_slash_stays_admitted():
    # VERDICT-bd-cx-rs-med1.5b15453501f29779.md
    href = "https://cdn.example/scene_2160p.mp4?source=/refinementList%5Babc%5D"
    assert admitted(href), "regression: slash-bearing query VALUE re-broken"


def test_superseded_unbounded_token_in_value_stays_admitted():
    # VERDICT-bd-cx-rs-med1.cbcbbfae67deda00.md
    href = "https://cdn.example/scene_2160p.mp4?token=refinementList%5Babc%5D"
    assert admitted(href), "regression: refinementList token-in-value re-broken"


def test_superseded_encoded_listing_facet_path_still_refused():
    # VERDICT-correctness BOARD design: [?&/]refinementlist(...) on a PATH.
    href = "/en/videos/?refinementList%5Bvideo_formats.format%5D%5B0%5D=2160p"
    assert not admitted(href), "listing facet path must still be refused"


# ---------------------------------------------------------------------------
# 3. Own probes of the input CLASS (O809): percent-encoding, key vs value,
#    path vs query, case, empty/absent fields, whitespace, order-of-decode.
# ---------------------------------------------------------------------------
def test_query_key_case_variant_uppercase_r_on_real_mp4_is_admitted():
    href = "https://cdn.example/scene_2160p.mp4?RefinementList%5Bformat%5D=2160p"
    assert admitted(href), "case-insensitive key match still over-refuses media"


def test_query_key_with_empty_value_on_real_mp4_is_admitted():
    href = "https://cdn.example/scene_2160p.mp4?refinementList%5Bformat%5D="
    assert admitted(href), "empty query VALUE must not change the key false positive"


def test_query_key_amid_other_real_params_on_real_mp4_is_admitted():
    href = "https://cdn.example/scene_2160p.mp4?quality=2160p&refinementList%5Bformat%5D=2160p&t=1"
    assert admitted(href), "sibling real params must not rescue the false positive"


def test_query_key_without_brackets_is_admitted():
    # Not a listing key at all -- ?refinementlist=plain has no `[`.
    href = "https://cdn.example/scene_2160p.mp4?refinementlist=plain"
    assert admitted(href)


def test_non_prefix_key_containing_refinementlist_is_admitted():
    # `notarefinementList[...]` does not START with refinementlist[.
    href = "https://cdn.example/scene_2160p.mp4?notarefinementList%5Bformat%5D=2160p"
    assert admitted(href)


def test_double_percent_encoded_bracket_is_admitted():
    # %255B decodes once to the literal string "%5B", never becomes `[`.
    href = "https://cdn.example/scene_2160p.mp4?refinementList%255Bformat%255D=2160p"
    assert admitted(href)


def test_unicode_prefix_before_key_does_not_suppress_the_bug_or_fix():
    # A stray NBSP before the key changes the key string entirely; must not
    # itself become a listing key.
    href = "https://cdn.example/scene_2160p.mp4?%C2%A0refinementList%5Bformat%5D=2160p"
    assert admitted(href)


def test_absent_query_string_is_admitted():
    href = "https://cdn.example/scene_2160p.mp4"
    assert admitted(href)


def test_newline_in_value_with_real_key_matches_the_same_key_bug():
    # Whitespace/layout inside the VALUE is irrelevant; the KEY still drives
    # the (over-)refusal, so this must track test 1's outcome exactly.
    href = "https://cdn.example/scene_2160p.mp4?refinementList%5Bformat%5D=216%0A0p"
    assert admitted(href), "real MP4 download refused: query-key false positive"


def test_path_facet_with_media_quality_query_still_refused():
    # Path-shape facet with an otherwise MP4-flavoured query: path rule wins,
    # correctly refused (distinguishes path vs. query boundary).
    href = "/en/videos/refinementList%5Bx%5D/scene.mp4?quality=2160p"
    assert not admitted(href)


def test_real_world_variant_gamma_mp4_cdn_key_is_admitted():
    href = (
        "https://cdn.gammaentertainment.example/videos/scene_1080p_h264.mp4"
        "?refinementList%5Bvideo_formats.format%5D%5B0%5D=1080p&sig=abc123"
    )
    assert admitted(href), "real Gamma CDN download refused by facet-key false positive"


def test_real_world_variant_query_key_before_extension_hint_is_admitted():
    href = "https://cdn.example/dl?refinementList%5Bformat%5D=2160p&file=scene_2160p.mp4"
    assert admitted(href), "download-by-query-string URL refused by facet-key false positive"


def test_real_world_variant_multiple_bracket_segments_key_is_admitted():
    href = (
        "https://cdn.example/scene_2160p.mp4"
        "?refinementList%5Bvideo_formats.format%5D%5B0%5D=2160p"
    )
    assert admitted(href), "nested-bracket facet-shaped key still refuses real media"


def test_positive_control_has_non_video_url_shape_can_say_true():
    # Proves the probe itself can detect a refusal before any RED is trusted.
    assert _has_non_video_url_shape(Facet("/en/videos/?refinementList[x]=1"))
