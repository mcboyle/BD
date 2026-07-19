"""v3.66.705 -- GUARD CUT: close the capture-time floor's ATTRIBUTE-PREFIX leak.

THE BUG (found by the 704 coverage cut, pinned there by a characterization test):

    _TOKENISH is r"\\S+", so the whitespace-bounded token is the WHOLE of
        data-token="eyJhbGci..."
    and _maybe's `.strip("\\"'<>(),;")` only trims the ENDS. The `data-token="` prefix
    survives, so `_value_is_dangerous`'s anchored patterns (^eyJ... / ^[A-Za-z0-9_-]{32,})
    never match, and the token is stored RAW.

Any secret behind a `key="` prefix slipped through: HTML data-attrs, `href=`, an
unparseable-JSON body's `"token":"..."`. This contradicted the function's OWN comment,
which names "an HTML data-attr" as the motivating case.

NOT an egress leak (the SHARE-time scrubber tools/capture_scrub.scrub_string masks this
shape, so the .redacted.wacz twin was always clean) -- but a real hole in the capture-time
FLOOR: the raw local capture stored a live credential.

THE FIX: before the danger test, also consider the token's value AFTER an `attr=` /
`key:` prefix -- i.e. split on the last `=` or `:` that precedes a quote, and test the
inner value too. A token is masked if EITHER the whole stripped token OR its inner value
is dangerous.

CRITICAL NEG CONTROL: a scrub that masks everything is useless -- it would destroy the
captured DOM/playlist structure the capture exists to preserve. The fix must NOT start
masking ordinary `key=value` text (`width=1280`, `href="/about"`, `charset=utf-8`).

RED-first: the first two tests FAIL on pristine v3.66.704 (that is the bug); the
over-redaction controls PASS before AND after (they pin what must NOT change).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import capture_bodies as CB
from bulk_downloader.capture_redact import PLACEHOLDER

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
_LONG = "B" * 40
_SIGNED = "https://cdn.example.com/v.m3u8?Expires=1799999999&Signature=abc&Key-Pair-Id=K1"


# ── THE BUG: tokens behind an attribute prefix must be masked ────────────
def test_masks_a_jwt_in_an_html_data_attribute():
    """The exact shape the function's own comment says it exists to catch."""
    out = CB._redact_text(f'<a data-token="{_JWT}">x</a>')
    assert _JWT not in out, "a JWT in an HTML data-attr must not be stored raw"
    assert PLACEHOLDER in out


def test_masks_a_long_token_behind_an_attribute_prefix():
    out = CB._redact_text(f'session={_LONG}')
    assert _LONG not in out
    assert PLACEHOLDER in out


def test_masks_a_signed_url_behind_an_href_attribute():
    out = CB._redact_text(f'<video src="{_SIGNED}"></video>')
    assert "Signature=abc" not in out
    assert PLACEHOLDER in out


def test_masks_a_token_in_an_unparseable_json_body():
    """Unparseable JSON falls through to this text scrub -- so `"token":"eyJ..."` must
    still be masked even though it never reached the JSON path."""
    out = CB._redact_text('{"token":"%s", ' % _JWT)   # deliberately truncated -> unparseable
    assert _JWT not in out
    assert PLACEHOLDER in out


# ── the shapes that ALREADY worked must keep working ─────────────────────
def test_still_masks_a_bare_jwt():
    out = CB._redact_text(f"authorization {_JWT} end")
    assert _JWT not in out and PLACEHOLDER in out


def test_still_masks_a_signed_url_in_a_playlist():
    playlist = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\n" + _SIGNED + "\n"
    out = CB._redact_text(playlist)
    assert "Signature=abc" not in out
    assert "#EXTM3U" in out                       # structure survives


# ── NEG CONTROLS: the fix must NOT become an over-redactor ───────────────
def test_does_not_mask_ordinary_key_value_text():
    """A scrub that masks everything destroys the captured DOM/playlist the capture
    exists to preserve. Ordinary attributes must survive byte-identical."""
    benign = 'width=1280 height=720 charset=utf-8 lang="en"'
    assert CB._redact_text(benign) == benign


def test_does_not_mask_a_normal_href():
    benign = '<a href="/about/contact">Contact</a>'
    assert CB._redact_text(benign) == benign


def test_does_not_mask_playlist_directives():
    """HLS directives are `KEY=VALUE` and must never be redacted -- masking them would
    corrupt the very playlist the capture is storing."""
    line = "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1280x720,CODECS=avc1.4d401f"
    assert CB._redact_text(line) == line


def test_benign_prose_untouched():
    benign = "the quick brown fox jumps over 13 lazy dogs"
    assert CB._redact_text(benign) == benign


def test_empty_and_whitespace_unchanged():
    assert CB._redact_text("") == ""
    assert CB._redact_text("   \n\t ") == "   \n\t "
