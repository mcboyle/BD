"""F7 phase 2 (v3.66.45) — buckets is canonical; flat keys retired.

Phase 1 emitted `buckets` additively alongside the flat keys. Phase 2
makes `buckets` the single source of truth and REMOVES the top-level
`download_candidates` / `best_download` / `rejected` / `warnings` keys.
"""
import pytest

from bulk_downloader.deep_detect import (
    deep_detect, _finalize_buckets, _rejected_view,
)


def test_buckets_present_and_canonical():
    out = deep_detect('<video><source src="https://x.com/v.mp4" '
                      'type="video/mp4"></video>', base_url="https://x.com/")
    assert "buckets" in out
    b = out["buckets"]
    for k in ("accepted", "rejected", "rejected_raw", "warnings",
              "counts", "best"):
        assert k in b, f"buckets missing {k}"


def test_flat_keys_retired():
    out = deep_detect('<video><source src="https://x.com/v.mp4"></video>',
                      base_url="https://x.com/")
    for k in ("download_candidates", "best_download", "rejected", "warnings"):
        assert k not in out, f"flat key {k} should be retired in phase 2"


def test_best_is_top_accepted():
    out = deep_detect('<video><source src="https://x.com/v.mp4"></video>',
                      base_url="https://x.com/")
    b = out["buckets"]
    assert b["best"] == (b["accepted"][0] if b["accepted"] else None)


def test_counts_consistent_with_lists():
    out = deep_detect('<video><source src="https://x.com/v.mp4"></video>',
                      base_url="https://x.com/")
    b = out["buckets"]
    assert b["counts"]["accepted"] == len(b["accepted"])
    assert b["counts"]["rejected"] == len(b["rejected"])
    assert b["counts"]["warnings"] == len(b["warnings"])


def test_source_breakdown_still_top_level():
    out = deep_detect('<video><source src="https://x.com/v.mp4"></video>',
                      base_url="https://x.com/")
    assert "source_breakdown" in out


def test_rejected_view_carries_reasons():
    html = ('<a href="/trap?token=abc" style="display:none">x</a>'
            '<video><source src="https://x.com/v.mp4"></video>')
    out = deep_detect(html, base_url="https://x.com/")
    for r in out["buckets"]["rejected"]:
        assert "reasons" in r and isinstance(r["reasons"], list) and r["reasons"]


def test_rejected_raw_preserved():
    html = ('<a href="/trap?token=abc" style="display:none">x</a>'
            '<video><source src="https://x.com/v.mp4"></video>')
    out = deep_detect(html, base_url="https://x.com/")
    b = out["buckets"]
    assert isinstance(b["rejected_raw"], list)
    assert len(b["rejected"]) == len(b["rejected_raw"])


def test_finalize_buckets_removes_flat_keys():
    out = {"download_candidates": [{"url": "u"}], "best_download": {"url": "u"},
           "rejected": [], "warnings": ["w"], "best_login": None}
    _finalize_buckets(out, accepted=[{"url": "u"}])
    assert "download_candidates" not in out
    assert "best_download" not in out
    assert out["buckets"]["counts"]["accepted"] == 1
    assert out["buckets"]["warnings"] == ["w"]


def test_rejected_view_dedups():
    rv = _rejected_view([{"url": "https://x/a", "reasons": ["t", "t"],
                          "warnings": ["t"]}])
    assert rv[0]["reasons"] == ["t"]
