"""v3.66.157 — surface the OBSERVED API host as a review hint (not a guess).

A reviewed template needs an ``api{base}`` block for the runtime's
``build_api_url`` to work, but the v151 rule keeps the API host out of the
normalized candidate unless the builder states it outright — the page host
(app.reptyle.com) is not the API host (api2.reptyle.com), and signed URLs are
scrubbed. To make the manual review step easy without breaking that rule, the
builder now records the host that ACTUALLY served the download-resolution call
as ``network_discovery.observed_api_hosts`` (diagnostic, like ``top_hosts``),
and the normalizer names it in the existing "set api{base}" warning.

Crucially this is a HINT ONLY: it is stored under ``observed_api_hosts`` (NOT
``api_host``), so ``_api_host`` never consumes it, the API patterns stay
relative, and no ``api`` block is auto-created. A human still makes the call.

No browser, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import build_template_from_wacz as B  # noqa: E402
from bulk_downloader import template_normalize as N  # noqa: E402


def _draft(observed):
    nd = {
        "api_patterns": ["/api/v{version}/movie/{movie_id}/download-resolution/{resolution}"],
        "media_patterns": [".../{manifest}.m3u8"],
        "resolutions_seen": [1080],
    }
    if observed is not None:
        nd["observed_api_hosts"] = observed
    return {
        "schema_version": "bulk_downloader.template_draft.v1",
        "source": {"host": "app.reptyle.com"},
        "match": {"hosts": ["app.reptyle.com"]},
        "selectors": {"download": {"button_hint": '[aria-label*="Download" i]'}},
        "network_discovery": nd,
        "resolution_priority": [1080],
    }


# ── builder records the observed host (a fact, not a guess) ───────────────
def test_builder_records_observed_api_host() -> None:
    nl = [{"url": "https://api2.reptyle.com/api/v1/movie/9/download-resolution/1080?token=S&sig=A",
           "response_status": 200}]
    nd = B._network_patterns(nl)
    assert nd["observed_api_hosts"] == ["api2.reptyle.com"]
    # and it is NOT placed under the auto-consumed `api_host` key
    assert "api_host" not in nd


def test_builder_no_observed_host_without_api_call() -> None:
    nl = [{"url": "https://cdn.reptyle.com/hls/9/master.m3u8", "response_status": 200}]
    assert B._network_patterns(nl)["observed_api_hosts"] == []


# ── normalizer surfaces it as a hint ──────────────────────────────────────
def test_warning_names_observed_host() -> None:
    cand = N.normalize_draft(_draft(["api2.reptyle.com"]))
    api_warn = [w for w in cand["warnings"] if "API patterns kept relative" in w]
    assert api_warn, "expected the relative-API warning"
    assert "api2.reptyle.com" in api_warn[0]


def test_warning_plain_when_no_observed_host() -> None:
    cand = N.normalize_draft(_draft([]))
    api_warn = [w for w in cand["warnings"] if "API patterns kept relative" in w]
    assert api_warn
    assert "observed on" not in api_warn[0]


# ── the hint must NOT change behaviour (stays inside the v151 rule) ───────
def test_api_patterns_stay_relative_despite_observed_host() -> None:
    cand = N.normalize_draft(_draft(["api2.reptyle.com"]))
    # the relative pattern is kept as-is; the observed host is NOT prefixed
    assert "/api/v{version}/movie/{movie_id}/download-resolution/{resolution}" \
        in cand["network_patterns"]
    assert not any("api2.reptyle.com" in p for p in cand["network_patterns"])


def test_no_api_block_auto_created() -> None:
    cand = N.normalize_draft(_draft(["api2.reptyle.com"]))
    assert "api" not in cand


def test_api_host_helper_ignores_observed_hosts() -> None:
    # _api_host only honours an explicit api_host / api.base — observed_api_hosts
    # must not satisfy it
    assert N._api_host(_draft(["api2.reptyle.com"])) == ""
