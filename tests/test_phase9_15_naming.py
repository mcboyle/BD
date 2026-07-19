"""Phase 9.15 -- naming-template suggestion (RED-first)."""
from bulk_downloader import naming_template as nt

def test_sample_names_to_template():
    out=nt.suggest(["Show.S01E02.1080p.x265.mkv"], ["title","season","episode","resolution","codec"])
    t=out["proposed_template"]
    assert "{season}" in t and "{episode}" in t and "{resolution}" in t
    assert out["confidence"]>0
    assert out["requires_review"] is True

def test_unknown_fields_marked():
    out=nt.suggest(["Show.S01E02.mkv"], ["title","season","episode","director"])
    assert "director" in out["unknown_fields"]

def test_invalid_template_rejected():
    ok,unknown=nt.validate_template("{title}.{bogus}")
    assert ok is False and "bogus" in unknown

def test_valid_template_accepted():
    ok,unknown=nt.validate_template("{title}.S{season}E{episode}.{resolution}")
    assert ok is True and unknown==[]

def test_no_filesystem_mutation():
    for n in ("rename","move","apply","commit"):
        assert not hasattr(nt,n)

def test_examples_present():
    out=nt.suggest(["Show.S01E02.1080p.mkv"], ["title","season","episode","resolution"])
    assert out["examples"] and "before" in out["examples"][0] and "after" in out["examples"][0]
