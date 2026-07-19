"""v3.66.704 -- P3: close the four COVERAGE_GAPS. Tests only; no source edit.

These four spans were WHOLLY uncovered by a green suite -- and they are not trivia:

  capture_bodies._redact_text (+ ._maybe)  -- the capture-time SECRETS SCRUB for
      non-JSON text bodies. Free text can embed a signed media URL (an HLS master
      playlist, an HTML data-attr), so it cannot be stored raw. Nothing proved the
      scrub actually masks a JWT / signed URL / long opaque token in free text.
      (capture_bodies.py is a RELEASE GUARD -- these are TESTS ONLY; the guard file
      stays byte-identical.)

  provenance.verify_chain  -- the TAMPER DETECTOR. It exists to catch a deleted row,
      modified content, and a broken hash chain. It had ZERO coverage: nothing proved
      it detects any of them. A tamper detector nobody has ever seen detect tampering
      is a claim, not a control. Each fault is injected here and asserted caught.

  provenance.record  -- the append path the chain is built from.

Coverage is the reason these were found; it is NOT the reason they are written this
way. Each test injects a real fault and asserts the real behaviour.
"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ══ capture_bodies._redact_text -- the free-text secrets scrub ═══════════
from bulk_downloader import capture_bodies as CB
from bulk_downloader.capture_redact import PLACEHOLDER

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
_LONG_TOKEN = "A" * 40                     # >= 32 chars opaque -> dangerous
_SIGNED = "https://cdn.example.com/v.m3u8?Expires=1799999999&Signature=abc&Key-Pair-Id=K123"


def test_redact_text_masks_a_jwt_in_free_text():
    out = CB._redact_text(f"line one {_JWT} line two")
    assert _JWT not in out
    assert PLACEHOLDER in out
    assert "line one" in out and "line two" in out   # only the token is destroyed


def test_redact_text_masks_a_long_opaque_token():
    out = CB._redact_text(f"token={_LONG_TOKEN}" if False else f"prefix {_LONG_TOKEN} suffix")
    assert _LONG_TOKEN not in out
    assert PLACEHOLDER in out


def test_redact_text_masks_a_signed_url_inside_a_playlist():
    """The stated reason this function exists: an HLS master playlist is free text
    that can embed a SIGNED media URL. Storing it raw would persist a live
    credential."""
    playlist = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\n" + _SIGNED + "\n"
    out = CB._redact_text(playlist)
    assert "Signature=abc" not in out
    assert PLACEHOLDER in out
    assert "#EXTM3U" in out                          # structure survives


def test_redact_text_masks_a_token_in_an_html_attribute():
    """v3.66.705 (GUARD CUT): INVERTED. This test was written at 704 as a
    CHARACTERIZATION of a real leak -- the capture-time floor did NOT mask a token behind
    an attribute prefix (`data-token="eyJ..."`), because _TOKENISH is whitespace-bounded
    and the anchored danger patterns never matched the prefixed/suffixed token.

    It was pinned asserting the LEAKY behaviour on purpose, so that the day the guard was
    fixed it would FAIL as "unexpectedly fixed" and force itself to be inverted. That is
    exactly what happened. The finding could not be silently forgotten.

    The full proof of the fix lives in tests/test_v3_66_705_capture_floor_attr_leak.py
    (including the over-redaction NEG controls: the scrub must not start masking ordinary
    `key=value` text or it would destroy the DOM/playlist the capture exists to store)."""
    out = CB._redact_text(f'<a data-token="{_JWT}">x</a>')
    assert _JWT not in out
    assert PLACEHOLDER in out
    assert 'data-token=' in out, "structure must survive -- only the VALUE is masked"


def test_redact_text_leaves_benign_text_untouched():
    """The NEG control: a scrub that masks everything is useless. Ordinary words must
    survive byte-identical."""
    benign = "the quick brown fox jumps over 13 lazy dogs"
    assert CB._redact_text(benign) == benign


def test_redact_text_handles_empty_and_whitespace():
    assert CB._redact_text("") == ""
    assert CB._redact_text("   \n\t ") == "   \n\t "


# ══ provenance.record + verify_chain -- the tamper detector ══════════════
@pytest.fixture
def prov(tmp_path, monkeypatch):
    """A provenance module bound to a throwaway DB."""
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    from bulk_downloader import db as _db
    importlib.reload(_db)
    from bulk_downloader import provenance as _p
    importlib.reload(_p)
    return _p


def _add(p, url="https://x.test/a.mp4", name="a.mp4", size=10):
    return p.record(site_id="s1", source_url=url, final_filename=name, file_size=size)


def test_record_appends_and_returns_an_id(prov):
    rid = _add(prov)
    assert isinstance(rid, int) and rid > 0


def test_record_is_not_deduped(prov):
    """Documented contract: the operator may legitimately re-download the same URL,
    so identical rows are appended, not merged."""
    a = _add(prov)
    b = _add(prov)
    assert a != b


def test_verify_chain_ok_on_an_untampered_chain(prov):
    for i in range(3):
        _add(prov, url=f"https://x.test/{i}.mp4", name=f"{i}.mp4")
    res = prov.verify_chain()
    assert res["ok"] is True
    assert res["checked"] == 3
    assert res["first_bad_id"] is None


def test_verify_chain_DETECTS_modified_content(prov):
    """THE control. Tamper with a row's content and the detector must name it. If this
    ever passes silently, the audit chain is decorative."""
    _add(prov, name="a.mp4")
    _add(prov, name="b.mp4")
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("UPDATE provenance SET final_filename='TAMPERED' WHERE id=1")
    res = prov.verify_chain()
    assert res["ok"] is False
    assert res["first_bad_id"] == 1
    assert "content_hash" in res["message"]


def test_verify_chain_DETECTS_a_broken_chain_hash(prov):
    """Content intact but the CHAIN link rewritten -- e.g. someone spliced a row out
    and re-stitched. The content hash still matches; only the chain catches it."""
    _add(prov, name="a.mp4")
    _add(prov, name="b.mp4")
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("UPDATE provenance SET chain_hash='deadbeef' WHERE id=2")
    res = prov.verify_chain()
    assert res["ok"] is False
    assert res["first_bad_id"] == 2
    assert "chain_hash" in res["message"]


def test_verify_chain_DETECTS_a_deleted_row(prov):
    """A deleted row breaks the chain for every row after it -- that is the whole
    point of chaining rather than hashing rows independently."""
    for i in range(3):
        _add(prov, url=f"https://x.test/{i}.mp4", name=f"{i}.mp4")
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("DELETE FROM provenance WHERE id=2")
    res = prov.verify_chain()
    assert res["ok"] is False, "a deleted row must break the chain"
    assert res["first_bad_id"] == 3


def test_verify_chain_on_an_empty_table_is_ok(prov):
    res = prov.verify_chain()
    assert res["ok"] is True
    assert res["checked"] == 0


def test_verify_chain_streams_in_batches(prov):
    """batch_size is the memory bound -- the walk must be correct across batch
    boundaries, not just within one batch."""
    for i in range(5):
        _add(prov, url=f"https://x.test/{i}.mp4", name=f"{i}.mp4")
    res = prov.verify_chain(batch_size=2)
    assert res["ok"] is True
    assert res["checked"] == 5
