"""v3.43.32 regression tests — cookie file round-trip validation.

Every successful save_cookies_to_file() now reads the file back and
verifies the round-trip. Catches partial writes, encoding corruption,
and format drift.

Coverage:
  - Happy path: write + read back + matching count
  - Round-trip failure detected when file is corrupted post-write
    (simulated via mock)
  - validate=False bypasses for unit tests / quick paths
  - Dict-of-lists shape (legacy Phase 9 format) handled correctly
  - CookieRoundTripError carries kind for routing
  - session_keeper._persist_cookies now delegates to save_cookies_to_file
  - app.py keep-alive login cookie writer delegates too
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


# ── Happy path ───────────────────────────────────────────────────────

def test_save_cookies_validates_round_trip_by_default():
    """validate=True (the default) is what we want in production —
    every write must round-trip cleanly."""
    from bulk_downloader.cookies import save_cookies_to_file
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.json"
        cookies = [
            {"name": "session", "value": "abc123", "domain": ".example.com"},
            {"name": "user", "value": "xyz", "domain": ".example.com"},
        ]
        result = save_cookies_to_file(path, cookies)
        assert result == path
        # File parsed and count matches
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 2


def test_save_cookies_with_validate_false():
    """validate=False is for unit tests that mock the filesystem.
    Must not crash even when round-trip would fail."""
    from bulk_downloader.cookies import save_cookies_to_file
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.json"
        save_cookies_to_file(path, [], validate=False)
        # No error raised; file exists
        assert path.exists()


# ── Failure detection ────────────────────────────────────────────────

def test_round_trip_detects_count_mismatch():
    """If the readback shows fewer cookies than we wrote, raise
    CookieRoundTripError. Simulate by manually mutating the file
    between write and validate using a hook."""
    from bulk_downloader.cookies import (
        save_cookies_to_file, CookieRoundTripError,
        _verify_cookie_roundtrip,
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.json"
        # Write 5 cookies
        cookies = [{"name": f"c{i}", "value": "v"} for i in range(5)]
        save_cookies_to_file(path, cookies)
        # Now corrupt the file: overwrite with fewer cookies
        path.write_text(json.dumps([{"name": "alone", "value": "v"}]))
        # Manually invoke the verifier with expected_count=5
        try:
            _verify_cookie_roundtrip(path, expected_count=5)
        except CookieRoundTripError as e:
            assert e.kind == "count_mismatch"
            assert "5" in str(e)
            assert "1" in str(e)
        else:
            raise AssertionError("expected CookieRoundTripError")


def test_round_trip_detects_unparseable_file():
    """If the file isn't valid JSON post-write, raise."""
    from bulk_downloader.cookies import (
        _verify_cookie_roundtrip, CookieRoundTripError,
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.json"
        path.write_text("not valid {{{ json")
        try:
            _verify_cookie_roundtrip(path, expected_count=2)
        except CookieRoundTripError as e:
            assert e.kind == "unreadable"
        else:
            raise AssertionError("expected CookieRoundTripError")


def test_round_trip_detects_wrong_root_type():
    """If the root JSON value is a string or number instead of a
    list/dict, that's a shape error."""
    from bulk_downloader.cookies import (
        _verify_cookie_roundtrip, CookieRoundTripError,
    )
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.json"
        path.write_text('"just a string"')
        try:
            _verify_cookie_roundtrip(path, expected_count=0)
        except CookieRoundTripError as e:
            assert e.kind == "shape"
        else:
            raise AssertionError("expected CookieRoundTripError")


# ── Format compatibility ─────────────────────────────────────────────

def test_round_trip_accepts_dict_of_lists_shape():
    """The dict-of-domain shape (legacy Phase 9 / extension-vault
    format) is valid on disk. Verifier must count cookies across
    nested lists, not reject the file."""
    from bulk_downloader.cookies import _verify_cookie_roundtrip
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.json"
        data = {
            "example.com": [{"name": "a", "value": "1"},
                              {"name": "b", "value": "2"}],
            "other.com": [{"name": "c", "value": "3"}],
        }
        path.write_text(json.dumps(data))
        # 3 cookies total across the two domains
        _verify_cookie_roundtrip(path, expected_count=3)


def test_round_trip_handles_empty_cookie_list():
    """An empty list is legal (rare — a brand-new context with no
    cookies set yet). Don't false-flag this."""
    from bulk_downloader.cookies import save_cookies_to_file
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "empty.json"
        save_cookies_to_file(path, [])
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == []


def test_round_trip_handles_utf8_cookies():
    """International cookie values must round-trip through the UTF-8
    write/read path. v3.36.8 was about the WRITE side; this confirms
    READ also handles them."""
    from bulk_downloader.cookies import save_cookies_to_file
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "utf8.json"
        cookies = [
            {"name": "user", "value": "Müller", "domain": ".de"},
            {"name": "session", "value": "日本語", "domain": ".jp"},
            {"name": "id", "value": "café", "domain": ".fr"},
        ]
        save_cookies_to_file(path, cookies)
        # Validation passed; verify content via load_cookies_from_file
        from bulk_downloader.cookies import load_cookies_from_file
        loaded = load_cookies_from_file(path)
        assert len(loaded) == 3
        loaded_values = {c["value"] for c in loaded}
        assert "Müller" in loaded_values
        assert "日本語" in loaded_values
        assert "café" in loaded_values


# ── CookieRoundTripError carries .kind ───────────────────────────────

def test_error_class_has_kind_attribute():
    """Callers (session_keeper, app.py) route on .kind. Lock in the
    attribute."""
    from bulk_downloader.cookies import CookieRoundTripError
    e = CookieRoundTripError("count_mismatch", "wrote 5 got 3")
    assert e.kind == "count_mismatch"
    assert str(e) == "wrote 5 got 3"


# ── Integration: session_keeper uses save_cookies_to_file ────────────

def test_session_keeper_delegates_to_save_cookies_to_file():
    """_persist_cookies must go through save_cookies_to_file so the
    round-trip validation runs on every heartbeat write."""
    src = Path(__file__).resolve().parent.parent / "bulk_downloader" / "session_keeper.py"
    body = src.read_text(encoding="utf-8")
    import re
    m = re.search(
        r"def _persist_cookies\(self\).*?(?=\n    def |\nclass |\Z)",
        body, re.DOTALL)
    assert m, "_persist_cookies not found"
    method_body = m.group(0)
    assert "save_cookies_to_file" in method_body, (
        "_persist_cookies must delegate to cookies.save_cookies_to_file "
        "for round-trip validation"
    )
    # And it must catch the round-trip error class
    assert "CookieRoundTripError" in method_body, (
        "_persist_cookies must handle CookieRoundTripError so a failed "
        "round-trip is logged loudly"
    )


def test_app_keep_alive_login_writer_delegates_too():
    """The keep-alive auto-relogin path also writes cookies. Without
    routing through save_cookies_to_file, it'd silently bypass the
    round-trip check."""
    src = Path(__file__).resolve().parent.parent / "bulk_downloader" / "app.py"
    body = src.read_text(encoding="utf-8")
    # The function is _do_login_for_keeper
    pos = body.find("login ok; cookie")
    assert pos > 0, "Couldn't locate keep-alive login cookie writer"
    nearby = body[max(0, pos-2000):pos+500]
    assert "save_cookies_to_file" in nearby, (
        "Keep-alive login cookie writer must delegate to "
        "save_cookies_to_file for round-trip validation"
    )


# ── Backward compatibility ───────────────────────────────────────────

def test_default_kwargs_for_save_cookies_to_file():
    """validate=True must be the default — existing callers that
    don't pass the kwarg should automatically get validation."""
    import inspect
    from bulk_downloader.cookies import save_cookies_to_file
    sig = inspect.signature(save_cookies_to_file)
    assert "validate" in sig.parameters
    assert sig.parameters["validate"].default is True, (
        "validate must default to True so existing callers get the "
        "benefit without code changes"
    )
