"""VR-P11 (redirected) — integer-valued backstop for INT-typed NUMERIC_RANGES fields.

Closes the live ValueError-on-download hazard: the PUT /api/sites/<sid> numeric
backstop validates with ``float(v)`` and therefore ACCEPTS a fractional value
(e.g. ``min_resolution=1080.5`` or the string ``"1080.5"``) for a field whose
runtime consumer reads it with bare ``int(...)`` (runner.py:2962,
runner_extractors.py:116). ``int("1080.5")`` then raises mid-download.

Fix (cut 527): ``site_editor.validate_numeric_updates`` rejects a non-integer value
for the 8 integer-typed fields, while leaving the 6 float-typed fields and every
existing accept/reject verdict unchanged.

RED-first signature on PRISTINE source:
  * the two "...fractional...rejected" tests FAIL (current backstop accepts them),
  * the PUT e2e "...rejected_and_not_persisted" + "...float...rejected" FAIL
    (current PUT returns 200 + persists),
  * the consumer-guard source test FAILS (bare-int() reads not yet int(float())),
  * every other regression guard (floats untouched / clean+string ints accepted /
    out-of-range + non-numeric still rejected / the pinned "1080" contract /
    blank skipped / valid PUTs / the truncation contract) PASSES.
After the fix: all GREEN.

Fixture is embedded inline so the gate owns its fixtures (durable lesson: a
release-gate test must never depend on accumulated runtime state). It mirrors
/home/claude/fixture_numeric_sites.json (the 8-int x 6-float case matrix).

Runner notes: the pure-helper tests (part a) only import site_editor — no app boot.
The e2e test (part b) boots the app via the test_put_numeric_range_backstop pattern
(BD_HOME-isolated, scheduler stubbed). Zero-arg test fns; no pytest builtins required.
"""
import json
import os

import pytest


# ── Field typing (must match bulk_downloader.site_editor) ──────────────────────
INT_TYPED = {
    "max_concurrent", "max_retries", "no_button_threshold", "min_resolution",
    "chunk_size_mb", "prelogin_minutes", "parallel_chunks", "warmup_every",
}
FLOAT_TYPED = {
    "wait", "delay", "disk_threshold_gb", "parallel_min_size_mb",
    "auto_relogin_interval_hours", "min_size_pct",
}

# ── Embedded fixture matrix (mirrors fixture_numeric_sites.json) ───────────────
# Each entry: (field, value). Grouped by intended verdict.
FRACTIONAL_INT_FLOATS = [        # int field given a fractional float -> Opt1 REJECT
    ("max_concurrent", 4.5), ("min_resolution", 1080.5),
    ("chunk_size_mb", 8.25), ("parallel_chunks", 2.5),
]
FRACTIONAL_INT_STRINGS = [       # int field given a fractional string -> Opt1 REJECT
    ("max_concurrent", "4.5"), ("min_resolution", "1080.5"), ("warmup_every", "1800.9"),
]
CLEAN_INTS = [                   # in-range whole numbers -> accept (both)
    ("max_concurrent", 4), ("max_retries", 3), ("no_button_threshold", 5),
    ("min_resolution", 1080), ("chunk_size_mb", 8), ("prelogin_minutes", 15),
    ("parallel_chunks", 2), ("warmup_every", 1800),
]
GUI_STRING_INTS = [              # gui-safe integer strings -> accept (both); pinned contract
    ("max_concurrent", "4"), ("min_resolution", "1080"),
    ("chunk_size_mb", "8"), ("parallel_chunks", "2"),
]
INT_BOUNDARIES = [              # exact range bounds -> accept (both)
    ("max_concurrent", 1), ("min_resolution", 0),
    ("chunk_size_mb", 256), ("min_resolution", 8640),
]
FLOAT_FRACTIONAL = [            # float field, fractional value -> accept (both); NO int-check
    ("wait", 2.5), ("delay", 3.5), ("parallel_min_size_mb", 100.5),
    ("auto_relogin_interval_hours", 12.5), ("disk_threshold_gb", 2.0),
    ("min_size_pct", 5.0),
]
FLOAT_STRINGS = [              # float field as string -> accept (both)
    ("wait", "2.5"), ("delay", "3.5"), ("min_size_pct", "5.0"),
]
OUT_OF_RANGE = [              # pre-existing range reject -> reject (both)
    ("max_concurrent", 9999), ("min_resolution", 99999),
    ("parallel_chunks", 999), ("wait", 999), ("min_size_pct", 250),
]
NONNUMERIC = [               # pre-existing non-numeric reject -> reject (both)
    ("max_concurrent", "abc"), ("min_resolution", "lots"), ("wait", "fast"),
]


def _rejected(field, value):
    """True iff validate_numeric_updates flags `field` for `value`."""
    from bulk_downloader import site_editor as se
    return field in se.validate_numeric_updates({field: value})


# ── Part (a): validation-layer, fixture-driven ────────────────────────────────

def test_fixture_typing_matches_site_editor():
    """The test's INT/FLOAT split must cover NUMERIC_RANGES exactly (no drift)."""
    from bulk_downloader import site_editor as se
    assert INT_TYPED | FLOAT_TYPED == set(se.NUMERIC_RANGES), (
        "test field-typing has drifted from site_editor.NUMERIC_RANGES")
    assert not (INT_TYPED & FLOAT_TYPED), "a field is typed both int and float"


def test_fixture_fractional_int_floats_rejected():
    """RED on pristine: a fractional FLOAT for an int field must be rejected."""
    bad = [(f, v) for f, v in FRACTIONAL_INT_FLOATS if not _rejected(f, v)]
    assert not bad, f"int fields accepted a fractional float (should reject): {bad}"


def test_fixture_fractional_int_strings_rejected():
    """RED on pristine: a fractional STRING for an int field must be rejected.

    These are the values that ValueError at the bare-int() consumer today.
    """
    bad = [(f, v) for f, v in FRACTIONAL_INT_STRINGS if not _rejected(f, v)]
    assert not bad, f"int fields accepted a fractional string (should reject): {bad}"


def test_fixture_clean_and_string_ints_accepted():
    """Regression: whole-number ints + gui-safe integer strings stay accepted."""
    bad = [(f, v) for f, v in (CLEAN_INTS + GUI_STRING_INTS + INT_BOUNDARIES)
           if _rejected(f, v)]
    assert not bad, f"a valid integer value was wrongly rejected: {bad}"


def test_fixture_all_float_fields_accept_fractional():
    """Regression: the 6 float-typed fields must NOT be integer-checked."""
    bad = [(f, v) for f, v in (FLOAT_FRACTIONAL + FLOAT_STRINGS) if _rejected(f, v)]
    assert not bad, f"a float field wrongly rejected a fractional value: {bad}"


def test_fixture_out_of_range_and_nonnumeric_still_rejected():
    """Regression: pre-existing range / non-numeric rejections are preserved."""
    missed = [(f, v) for f, v in (OUT_OF_RANGE + NONNUMERIC) if not _rejected(f, v)]
    assert not missed, f"a value that should reject was accepted: {missed}"


def test_pinned_numeric_string_contract_preserved():
    """The pinned VR-P11 contract (test_helper_numeric_string_is_range_checked)
    must survive: "1080" accepted, "99999" rejected — verbatim."""
    from bulk_downloader import site_editor as se
    assert se.validate_numeric_updates({"min_resolution": "99999"})    # out of (0,8640)
    assert se.validate_numeric_updates({"min_resolution": "1080"}) == {}


def test_blank_and_none_skipped_for_int_fields():
    """Regression: preserve-on-blank — '' / None never error (both behaviors)."""
    from bulk_downloader import site_editor as se
    assert se.validate_numeric_updates({"min_resolution": ""}) == {}
    assert se.validate_numeric_updates({"min_resolution": None}) == {}
    assert se.validate_numeric_updates({"max_concurrent": ""}) == {}


# ── Part (b): end-to-end PUT + consumer-crash characterization ─────────────────

pytestmark = pytest.mark.bd_module_wipe  # app-booting tests below re-read BD_HOME


def _boot_with_site():
    """Boot the app against the BD_HOME-isolated tmp config with one demo site.
    Mirrors tests/test_put_numeric_range_backstop.py::_boot_with_site."""
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    a.SITES_FILE.write_text(json.dumps({"demo": {
        "name": "Demo", "max_concurrent": 4, "min_resolution": 1080,
        "wait": 5, "password": "ORIG_PW"}}), encoding="utf-8")
    a._load_sites_config()
    a.runners["demo"].update_config = lambda *_a, **_k: None  # stub live scheduler bounce
    return a, a.app.test_client()


def _disk(a):
    return json.loads(a.SITES_FILE.read_text(encoding="utf-8"))["demo"]


def test_put_fractional_string_int_field_rejected_and_not_persisted():
    """RED on pristine: PUT min_resolution='1080.5' must be rejected (400) and the
    bad value must NOT reach persisted config. Today the backstop accepts it
    (200 + persists the string) -> it would ValueError at the runner consumer."""
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"min_resolution": "1080.5"})
    assert r.status_code == 400, (
        f"fractional-string int field was accepted (status {r.status_code}); "
        "it persists and crashes the bare-int() consumer")
    # the persisted value must be untouched (the original 1080), never "1080.5"
    assert _disk(a).get("min_resolution") != "1080.5", \
        "rejected value leaked into persisted config"


def test_put_fractional_float_int_field_rejected():
    """RED on pristine: PUT min_resolution=1080.5 (float) must be rejected (400)."""
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"min_resolution": 1080.5})
    assert r.status_code == 400, (
        f"fractional-float int field was accepted (status {r.status_code})")


def test_put_valid_int_and_float_still_accepted():
    """Regression e2e: a clean int PUT and a fractional FLOAT-field PUT both pass."""
    a, c = _boot_with_site()
    assert c.put("/api/sites/demo", json={"min_resolution": 720}).status_code == 200
    assert c.put("/api/sites/demo", json={"wait": 2.5}).status_code == 200


def test_consumer_guard_present_in_source():
    """RED on pristine: defense-in-depth. The two min_resolution consumers
    (runner.py min-res gate, runner_extractors yt-dlp hint) must read via
    int(float(...)) so a fractional value arriving from a NON-API source
    (hand-edited / overlaid config) truncates instead of ValueErroring
    mid-download. Source-text gate (mirrors test_v3_66_322's structural style)."""
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent / "bulk_downloader"
    runner = (repo / "runner.py").read_text(encoding="utf-8")
    extr = (repo / "runner_extractors.py").read_text(encoding="utf-8")
    needle = 'int(float(self.config.get("min_resolution"'
    assert needle in runner, "runner.py min_resolution read is not int(float(...)) guarded"
    assert needle in extr, "runner_extractors.py min_resolution read is not int(float(...)) guarded"
    # and the bare-int form must be gone from both
    bare = 'int(self.config.get("min_resolution"'
    assert bare not in runner, "runner.py still has a bare-int() min_resolution read"
    assert bare not in extr, "runner_extractors.py still has a bare-int() min_resolution read"


def test_consumer_guard_truncates_not_crashes():
    """Behavioral contract of the hardened consumer expression
    int(float(cfg.get("min_resolution", DEFAULT) or 0)): a fractional value
    (string or float) truncates to a sane int; clean/absent/blank unchanged.
    This is the non-API safety net behind the boundary rejection."""
    from bulk_downloader.runner import DEFAULT_MIN_RESOLUTION as D

    def consume(cfg):                       # mirrors the hardened runner.py read
        return int(float(cfg.get("min_resolution", D) or 0))

    assert consume({"min_resolution": "1080"}) == 1080
    assert consume({"min_resolution": 1080}) == 1080
    assert consume({}) == D
    assert consume({"min_resolution": ""}) == 0
    assert consume({"min_resolution": None}) == 0
    # the hazard inputs now SURVIVE (truncate) instead of crashing
    assert consume({"min_resolution": "1080.5"}) == 1080
    assert consume({"min_resolution": 1080.5}) == 1080
