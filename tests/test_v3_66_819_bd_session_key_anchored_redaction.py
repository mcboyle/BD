"""capture_scrub's share-redactor missed KEY-ANCHORED session cookies, and its
negative-control corpus could not represent the leak.

THE DEFECT. `RE_KV_SECRET` matched the key with `\\b(...|session|sid|...)\\b`.
`\\b` is a word-boundary assertion and `_` is a word character, so it never
falls inside `bd_session` -- the boundary sits at the start of `bd`, not before
`session`. Every underscore- or hyphen-affixed variant therefore survived the
pre-share scrubber untouched:

    bd_session=<value>      session_id=<value>
    sessionid=<value>       csrf_token=<value>      x_session=<value>

`bd_session` is BD's own auth cookie name, so the one shape most likely to
appear in a capture bundle was the one shape the redactor could not see.

THE SECOND HALF, AND THE WORSE HALF. `bd-secret-canary` is the negative control
that proves the scrubber works -- it plants known secrets and asserts they come
back redacted. It scored a clean sheet, because `bd-secret-fixture` only ever
emitted the COVERED `session=` shape. The gate's corpus structurally excluded
the leaking shape, so it reported clean: truthfully, and uselessly (CLAUDE.md
section 0). Fixing the regex without fixing the corpus would leave the gate
just as blind to the next variant, so both land together.

WHY THE FIXTURE VALUE IS DELIBERATELY LOW-ENTROPY. capture_scrub has an
opaque-token backstop that redacts any sufficiently long high-entropy run
regardless of its key. A canary with a 40-hex body would be caught by THAT arm
even with the key-anchoring bug still present -- the gate would pass for the
wrong reason and prove nothing about key matching. `bd_cookie` emits a short,
low-entropy body (below `token_min`), which the backstop cannot rescue, so only
a key-anchored rule can redact it. `test_fixture_bd_cookie_body_is_low_entropy`
pins that property so a future fixture edit cannot quietly re-mask the hole.

`_VAL` below is chosen on the same principle: >= 8 chars so the value-length
floor `{8,}` COULD fire if the key matched, but 12 chars and all-alpha so
`_is_opaque_run` is False. Any redaction observed is therefore attributable to
the key arm alone.

Every assertion here runs the real scrubber and reads its output. None of them
scans source for the presence of a pattern -- presence is not behaviour.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "tools"))
import capture_scrub as cs  # noqa: E402

_FIXTURE = os.path.join(_ROOT, "toolchain", "bin", "bd-secret-fixture")

_VAL = "abcabcabcabc"


def _load_fixture():
    """Import the extensionless bd-secret-fixture as a module."""
    ld = importlib.machinery.SourceFileLoader("bd_secret_fixture", _FIXTURE)
    m = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("bd_secret_fixture", ld))
    ld.exec_module(m)
    return m


# ── RED: these fail on pristine capture_scrub ──────────────────────────────

def test_bd_session_underscore_prefixed_value_redacted():
    """RED. BD's own auth cookie. `\\b` cannot fall inside `bd_session`, so the
    value went into the share bundle in the clear."""
    out = cs.scrub_string(f"bd_session={_VAL}; Path=/; HttpOnly", "safe", 24)
    assert _VAL not in out, out


def test_session_id_and_prefixed_variants_redacted():
    """RED. The same boundary failure for every affix shape, not just BD's."""
    for s in (f"session_id={_VAL}", f"sessionid={_VAL}", f"x_session={_VAL}"):
        assert _VAL not in cs.scrub_string(s, "safe", 24), s


def test_csrf_underscore_token_redacted():
    """RED. `csrf`/`xsrf` were in RE_SENS_KEY_SUBSTR but absent from
    RE_KV_SECRET entirely -- a key-value CSRF token matched neither."""
    out = cs.scrub_string(f"csrf_token={_VAL}", "safe", 24)
    assert _VAL not in out, out


def test_scan_residual_independently_flags_bd_session():
    """RED. scan_residual is the self-verify pass that runs AFTER scrubbing to
    catch anything the scrubber missed. It shares RE_KV_SECRET, so it was blind
    in exactly the same way -- the backstop had the same hole as the primary."""
    hits = cs.scan_residual({"note": f"bd_session={_VAL}"})
    assert any("kv-secret" in kind for _, kind in hits), hits


# ── canary-denominator guards: keep the gate able to see its subject ───────

def test_fixture_exposes_bd_cookie_kind():
    """The corpus must contain the leaking shape, or the canary certifies clean
    over a set that excludes what it is asked about."""
    assert "bd_cookie" in _load_fixture().ALL_KINDS


def test_fixture_bd_cookie_body_is_low_entropy():
    """The canary must be UNRESCUABLE by the opaque-token backstop, or it would
    pass even with the key-anchoring bug present -- a gate that cannot fail."""
    val = _load_fixture().gen("bd_cookie", 0)
    body = val.split("=", 1)[1].split(";", 1)[0]
    assert not cs._is_opaque_run(body, 24), (len(body), body)


# ── regression guards: green on pristine AND patched, NOT counted as RED ───

def test_regressionguard_plain_session_still_redacted():
    """The covered shape must stay covered: the fix widens, never narrows."""
    assert _VAL not in cs.scrub_string(f"session={_VAL}", "safe", 24)


def test_regressionguard_benign_key_preserved():
    """Anti-cry-wolf. A key with no sensitive substring must survive intact.
    Over-redaction is a soundness bug, not a safe default."""
    assert "1280x720xy" in cs.scrub_string("width=1280x720xy", "safe", 24)


def test_regressionguard_tokenizer_not_overmasked():
    """`tokenizer` contains `token`, but `token` stays on the WHOLE-WORD arm, so
    it must not be masked. This is what makes the two-arm split necessary rather
    than just turning every key into a substring match."""
    assert "abcdef1234" in cs.scrub_string("tokenizer=abcdef1234", "safe", 24)


def test_regressionguard_short_value_below_floor_preserved():
    """The `{8,}` value floor must survive: a 3-char value is not a secret, and
    masking it would be noise in every capture bundle."""
    assert cs.scrub_string("sid=abc", "safe", 24) == "sid=abc"
