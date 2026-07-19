"""v3.66.529 -- THD: scrub_globals must scan bytes/set/tuple, not pass them through.

scrub_globals recursively redacts token-like string leaves but used to handle only
dict/list/str -> a bytes blob (which can carry tokens) or a set/frozenset/tuple of
strings (which can carry URL tokens) fell through to the passthrough ``return obj``
and was never scanned -> a fail-OPEN gap in the redaction floor. The fix scans
set/frozenset/tuple members and drops bytes to a shape marker (fail closed). It
STRENGTHENS the floor and must not weaken the existing dict/list/str redaction.

Zero-arg fns; stdlib only; runs under the custom runner and pytest.
"""
from bulk_downloader.capture_redact import scrub_globals, PLACEHOLDER

_TOKEN = "SECRETTOKEN1234567890"
_URL = f"https://x.com/v?sig={_TOKEN}"


def test_bytes_leaf_is_dropped_to_marker_not_passed_through():
    out = scrub_globals({"blob": b"raw-bytes-with-" + _TOKEN.encode()})
    assert isinstance(out["blob"], str) and out["blob"].startswith(PLACEHOLDER), out
    assert _TOKEN not in out["blob"], ("bytes content (a possible token) leaked", out)


def test_bytearray_leaf_is_dropped_to_marker():
    out = scrub_globals({"blob": bytearray(b"x" + _TOKEN.encode())})
    assert isinstance(out["blob"], str) and out["blob"].startswith(PLACEHOLDER), out
    assert _TOKEN not in out["blob"], out


def test_set_members_are_scanned():
    out = scrub_globals({"tags": {_URL, "plain-tag"}})
    assert isinstance(out["tags"], set), out
    flat = " ".join(sorted(out["tags"]))
    assert _TOKEN not in flat, ("a URL token inside a set leaked unscanned", out)
    assert "plain-tag" in out["tags"], "non-token content must be preserved"


def test_frozenset_type_preserved_and_scanned():
    out = scrub_globals({"tags": frozenset({_URL})})
    assert isinstance(out["tags"], frozenset), out
    assert _TOKEN not in " ".join(out["tags"]), out


def test_tuple_members_are_scanned():
    out = scrub_globals({"pair": (_URL, "keep")})
    assert isinstance(out["pair"], tuple), out
    assert _TOKEN not in " ".join(out["pair"]), out
    assert out["pair"][1] == "keep"


def test_dict_list_str_behavior_unchanged():
    """The fix must not weaken the existing nested-URL redaction or strip page text."""
    out = scrub_globals({"a": [{"u": _URL}], "title": "a nice description"})
    assert _TOKEN not in str(out), out
    assert out["title"] == "a nice description"   # page content kept intact
