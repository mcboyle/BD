"""Phase 9.16 -- semantic dedup preview only (RED-first)."""
from bulk_downloader import dedup_preview as dp

def test_exact_hash_grouping_deterministic():
    items=[{"id":1,"hash":"AAA","title":"Show 1080p"},
           {"id":2,"hash":"AAA","title":"Show 720p"},
           {"id":3,"hash":"BBB","title":"Other"}]
    out=dp.plan(items)
    # the AAA pair is an exact group (deterministic authority)
    assert any(set(g)=={1,2} for g in out["exact_groups"])

def test_near_dup_preview_only():
    items=[{"id":1,"hash":"H1","title":"The Big Show 1080p x265","resolution":"1080p","size":900},
           {"id":2,"hash":"H2","title":"The Big Show 720p","resolution":"720p","size":400}]
    out=dp.plan(items)
    assert out["near_groups"]
    assert out["requires_confirmation"] is True
    # best-quality keep candidate is the 1080p one
    assert out["near_groups"][0]["keep_candidate"]==1

def test_no_delete_or_apply_path():
    for n in ("delete","archive","move","apply","execute","commit"):
        assert not hasattr(dp,n)

def test_low_confidence_group_review():
    items=[{"id":1,"hash":"H1","title":"Alpha Beta Gamma show"},
           {"id":2,"hash":"H2","title":"Alpha Beta different thing"}]
    out=dp.plan(items)
    if out["near_groups"]:
        assert "review" in out["near_groups"][0]

def test_unique_items_no_groups():
    items=[{"id":1,"hash":"H1","title":"Totally Unique One"},
           {"id":2,"hash":"H2","title":"Completely Other Two"}]
    out=dp.plan(items)
    assert out["exact_groups"]==[]
