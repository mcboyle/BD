"""v3.66.680 (B1/P3): close the 4 COVERAGE_GAPS.json spans.

Tests only, no source edit. Exercises capture_bodies._redact_text._maybe
and provenance.record / verify_chain (which the suite did not reach).
"""
import bulk_downloader.capture_bodies as cb
import bulk_downloader.provenance as prov


_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.AbCdEf1234567890xyz"


def test_redact_text_masks_dangerous_token():
    out = cb._redact_text(f"prefix {_JWT} suffix")
    assert cb.PLACEHOLDER in out
    assert _JWT not in out
    assert "prefix" in out and "suffix" in out


def test_redact_text_leaves_benign_text():
    assert cb._redact_text("just some ordinary words here") == "just some ordinary words here"


def test_provenance_record_then_verify_chain_ok():
    rid = prov.record(site_id="s1", source_url="https://example.com/a",
                      final_filename="a.mp4", file_size=10, sha256="deadbeef")
    assert isinstance(rid, int) and rid > 0
    res = prov.verify_chain()
    assert res["ok"] is True
    assert res["checked"] >= 1


def test_provenance_verify_chain_detects_tamper():
    prov.record(site_id="s2", source_url="https://example.com/b", final_filename="b.mp4")
    prov.record(site_id="s3", source_url="https://example.com/c", final_filename="c.mp4")
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("UPDATE provenance SET source_url='https://example.com/TAMPERED' "
                   "WHERE id=(SELECT MAX(id) FROM provenance)")
    res = prov.verify_chain()
    assert res["ok"] is False
    assert res["first_bad_id"] is not None
