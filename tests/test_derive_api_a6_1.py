"""A6-1: concrete api.base + named-endpoint derivation (build_template_from_wacz).

Unit-level oracle for ``_derive_api`` — the guard-free enrichment that rebuilds a
concrete runtime ``api.base`` + named endpoints from the raw network_log, gated on
``extraction_core`` having recognised exactly one api host. extraction_core itself
stays byte-identical (it only emits the observed-host hint + a templated pattern).
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import build_template_from_wacz as B  # noqa: E402


def _nl(*urls):
    return [{"type": "xhr", "method": "GET", "url": u, "response_status": 200}
            for u in urls]


def test_single_endpoint_base_and_name():
    nl = _nl("https://api.demo.example/api/v1/movie/9/download-resolution/1080")
    api = B._derive_api(nl, {"observed_api_hosts": ["api.demo.example"]})
    assert api["base"] == "https://api.demo.example/api/v1"
    assert api["download_resolution"] == "/movie/{movie_id}/download-resolution/{resolution}"
    assert [k for k in api if k != "base"] == ["download_resolution"]


def test_three_endpoints_share_base():
    nl = _nl(
        "https://api2.reptyle.com/api/v1/movie/9/watch",
        "https://api2.reptyle.com/api/v1/movie/9/trailer",
        "https://api2.reptyle.com/api/v1/movie/9/download-resolution/1080",
    )
    api = B._derive_api(nl, {"observed_api_hosts": ["api2.reptyle.com"]})
    assert api["base"] == "https://api2.reptyle.com/api/v1"
    named = {k for k in api if k != "base"}
    assert named == {"watch", "trailer", "download_resolution"}
    assert api["watch"] == "/movie/{movie_id}/watch"
    assert api["download_resolution"] == "/movie/{movie_id}/download-resolution/{resolution}"


def test_never_guesses_when_host_not_recognised():
    # no observed api host -> the core didn't recognise an api; derive nothing
    nl = _nl("https://api.demo.example/api/v1/movie/9/watch")
    assert B._derive_api(nl, {"observed_api_hosts": []}) is None


def test_never_guesses_with_multiple_api_hosts():
    nl = _nl(
        "https://a.example/api/v1/movie/9/watch",
        "https://b.example/api/v1/movie/9/watch",
    )
    assert B._derive_api(nl, {"observed_api_hosts": ["a.example", "b.example"]}) is None


def test_only_counts_requests_to_the_recognised_host():
    # page + cdn requests on other hosts must not pollute the derivation
    nl = _nl(
        "https://demo.example/v/9",
        "https://cdn.demo.example/9/file_1080.mp4",
        "https://api.demo.example/api/v1/movie/9/download-resolution/1080",
    )
    api = B._derive_api(nl, {"observed_api_hosts": ["api.demo.example"]})
    assert api["base"] == "https://api.demo.example/api/v1"
    assert [k for k in api if k != "base"] == ["download_resolution"]


def test_secret_free_no_query_no_ids():
    nl = _nl("https://api.demo.example/api/v1/movie/9/download-resolution/1080?token=SECRET&sig=abc")
    api = B._derive_api(nl, {"observed_api_hosts": ["api.demo.example"]})
    blob = repr(api)
    assert "SECRET" not in blob and "token" not in blob and "sig" not in blob
    assert "/9/" not in blob and "1080" not in blob   # ids/resolutions templated away


def test_excludes_media_and_templates_opaque_segments():
    """Real-capture defect lock: the recognised api host also serves media and
    content-named paths. Media (.m3u8/.mp4/...) must NOT become endpoints, and
    opaque/long content segments must be templated — no content names leak into
    the derived patterns or named keys (F2 sharing policy)."""
    nl = _nl(
        "https://api.demo.example/api/v1/movie/9/download-resolution/1080",
        "https://api.demo.example/pz-m3u8/ts/some_long_content_title_julia/x.m3u8",
        "https://api.demo.example/api/v1/asset/9/file_1080.mp4",
        "https://api.demo.example/api/v1/user/a1b2c3d4e5f60718293a4b5c6d7e8f90/profile",
    )
    api = B._derive_api(nl, {"observed_api_hosts": ["api.demo.example"]})
    blob = repr(api).lower()
    # no media endpoints / extensions, no leaked content title, no raw long hex id
    assert ".m3u8" not in blob and ".mp4" not in blob
    assert "some_long_content_title_julia" not in blob
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" not in blob
    # the load-bearing API endpoint still derives
    assert api["download_resolution"] == "/movie/{movie_id}/download-resolution/{resolution}"


def test_surfaced_as_review_candidate_not_runtime_api():
    """Gate-preserving (v3.66.155/157): the derived api rides as a REVIEW-ONLY
    api_candidate the reviewer accepts at promotion — never the runtime ``api``
    block (which would let build_api_url work pre-review and flip patterns
    absolute)."""
    import tempfile
    from pathlib import Path
    from bulk_downloader import template_normalize as TN
    from bulk_downloader.wacz_export import write_wacz
    import builder_gap_report as GR
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "s.wacz"
        write_wacz(GR._synthetic_capture(), w)
        cand = TN.normalize_draft(B.build_template(w))
    assert "api" not in cand                    # no runtime block
    cap = cand.get("api_candidate") or {}
    assert cap.get("base") and [k for k in cap if k != "base"]   # derived candidate present
