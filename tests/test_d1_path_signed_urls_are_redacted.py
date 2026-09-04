"""D1 -- a CDN that signs in the URL PATH left the signature, the expiry and
the OPERATOR'S PUBLIC IP intact in operator-visible evidence.

MEASURED DEFECT (campaign smoke, 2026-09-03). A media CDN signs by packing
comma-separated ``name=value`` assignments into one PATH segment::

    https://<host>/key=<sig>,end=<epoch>,ip=<client ip>/.../<file>.mp4

``capture_redact.redact_media_url`` redacts the QUERY, the userinfo, and any
path segment that is a sensitive LABEL (``token``/``auth``/...) or that looks
OPAQUE.  The signing segment is none of those -- its own name is not a label,
and the mixed ``=``/``,`` punctuation stops it looking opaque -- so on the base
tree ``redact_media_url`` returned the URL BYTE-IDENTICAL.
``capture_artifact_redact.redact_value`` removed only ``key=`` (its kv floor
knows that name) and left ``end=`` and ``ip=`` standing.

Both functions are display/derivation boundaries for evidence an operator
reads, so the surviving material is a live credential plus PII.

FIXTURES ARE ZERO-ENTROPY SYNTHETICS OF THE MEASURED SHAPE (CLAUDE.md A4).
The signature is a run of ``0``; ``192.0.2.10`` is RFC 5737 TEST-NET-1 and
``2001:db8::10`` is RFC 3849 -- both documentation ranges.  No real
signature, host, expiry or address appears in this file.

DENOMINATOR.  The expected name set is NOT derived from the redactor's own
output (that would repeat the defect's shape, CLAUDE.md A7).  It is taken from
``capture_workbench_impl._common._PATH_SIGN_TYPE`` -- the tree's independent,
pre-existing vocabulary for "what a path-signing assignment name means"
(credential / expiry / ip-binding).  Every key in it must be redacted when it
appears as a path assignment; the map is asserted non-empty first.

DELIBERATELY NOT ASSERTED HERE: ``end=`` / ``ip=`` inside a QUERY string.
Measured on the base tree, a query carries the same leak, but closing it means
widening ``SENSITIVE_QS_KEY`` -- the single source of truth for the ALWAYS-ON
kv floor and for the ``keep_full`` surface that deliberately retains signing
metadata.  That is a different blast radius and a different cut.  The query
case below is therefore a NON-INTERFERENCE control: it proves this cut left
the query surface byte-identical, and it does not pin the remaining gap as
correct.

ALSO NOT ASSERTED: a ``%2C``-encoded separator. It is accidentally safe -- the
first assignment's value run swallows the whole encoded list, so nothing
survives -- but ``redact_value``'s pre-existing kv floor then eats the rest of
the string with it, measured IDENTICAL on the base tree, so this cut neither
caused nor changed it. Pinning that over-redaction here would freeze a
behaviour this cut does not own.
"""
from __future__ import annotations

from urllib.parse import unquote

from bulk_downloader.capture_artifact_redact import redact_value
from bulk_downloader.capture_redact import (PLACEHOLDER, redact_media_url,
                                            redact_query)
from bulk_downloader.capture_workbench_impl._common import _PATH_SIGN_TYPE

BD_GATE_SCOPE = "module"

# ── zero-entropy synthetics of the measured shape ────────────────────────────
SIG = "0" * 22                 # a signature's SHAPE; all-zero == no entropy
END = "1700000000"             # an epoch expiry, fixed and meaningless
IPV4 = "192.0.2.10"            # RFC 5737 TEST-NET-1 (documentation range)
IPV6 = "2001:db8::10"          # RFC 3849 (documentation range)
HOST = "cdn.example.net"
FILE = "scene_2160.mp4"
RUNG = "2160p"

PATH_COMMA = (f"https://{HOST}/key={SIG},end={END},ip={IPV4}"
              f"/speed=0/buffer=1.0/download2={FILE}/tsm/{RUNG}/{FILE}")
PATH_SPACES = (f"https://{HOST}/key={SIG}, end={END}, ip={IPV4}"
               f"/download2={FILE}/{RUNG}/{FILE}")
PATH_IPV6 = (f"https://{HOST}/key={SIG},end={END},ip={IPV6}"
             f"/download2={FILE}/{RUNG}/{FILE}")
PATH_AMP = (f"https://{HOST}/key={SIG}&end={END}&ip={IPV4}"
            f"/download2={FILE}/{RUNG}/{FILE}")
# The production entry is ``redact_media_url(str(response.url))``
# (runner_telemetry): Chromium hands back a PERCENT-ENCODED URL, so the ", "
# separator arrives as ",%20" and ``%20end=`` parses as a name "20end" that no
# vocabulary matches -- the second and later assignments were invisible to a
# raw-text pass. This shape is the one a live capture actually produces.
PATH_PCT_SPACE = (f"https://{HOST}/key={SIG},%20end={END},%20ip={IPV4}"
                  f"/download2={FILE}/{RUNG}/{FILE}")
QUERY_CTL = f"https://{HOST}/{RUNG}/{FILE}?key={SIG}&end={END}&ip={IPV4}"

# name -> (url, client-address literal that must not survive)
PATH_SHAPES = {
    "comma": (PATH_COMMA, IPV4),
    "comma_spaces": (PATH_SPACES, IPV4),
    "ipv6": (PATH_IPV6, IPV6),
    "ampersand": (PATH_AMP, IPV4),
    "pct_space": (PATH_PCT_SPACE, IPV4),
}

REDACTORS = {"redact_media_url": redact_media_url, "redact_value": redact_value}


def _assert_fixture_is_the_measured_shape(url: str, addr: str) -> None:
    """Precondition: the fixture really carries all three sensitive values in
    the PATH (before any '?'), so a green verdict cannot come from an empty or
    mis-built input."""
    path = unquote(url.split("?", 1)[0])
    assert f"key={SIG}" in path, url
    assert f"end={END}" in path, url
    assert f"ip={addr}" in path, url


def test_the_signing_vocabulary_denominator_is_nonempty():
    """The independent expected set exists and is nonzero, and it really does
    name the three classes this row is about."""
    assert len(_PATH_SIGN_TYPE) >= 20, _PATH_SIGN_TYPE
    kinds = set(_PATH_SIGN_TYPE.values())
    assert {"token", "expiry", "ip-binding"} <= kinds, sorted(kinds)


class TestPathSignedUrlLosesItsCredentialExpiryAndClientIp:

    def test_media_url_redacts_all_three_assignments(self):
        for name, (url, addr) in PATH_SHAPES.items():
            _assert_fixture_is_the_measured_shape(url, addr)
            out = redact_media_url(url)
            assert SIG not in out, f"{name}: signature survived redact_media_url: {out}"
            assert END not in out, f"{name}: expiry survived redact_media_url: {out}"
            assert addr not in out, f"{name}: client IP survived redact_media_url: {out}"
            assert out.count(PLACEHOLDER) >= 3, (
                f"{name}: expected >=3 redactions, got {out.count(PLACEHOLDER)}: {out}")

    def test_artifact_value_redacts_all_three_assignments(self):
        for name, (url, addr) in PATH_SHAPES.items():
            _assert_fixture_is_the_measured_shape(url, addr)
            out = redact_value(url)
            assert SIG not in out, f"{name}: signature survived redact_value: {out}"
            assert END not in out, f"{name}: expiry survived redact_value: {out}"
            assert addr not in out, f"{name}: client IP survived redact_value: {out}"
            assert out.count(PLACEHOLDER) >= 3, (
                f"{name}: expected >=3 redactions, got {out.count(PLACEHOLDER)}: {out}")

    def test_every_name_in_the_shared_signing_vocabulary_is_redacted(self):
        """Independent denominator: every path-signing name the tree already
        classifies must lose its value, in BOTH redactors."""
        value = "0" * 16
        checked = 0
        for key in sorted(_PATH_SIGN_TYPE):
            url = f"https://{HOST}/{key}={value}/{RUNG}/{FILE}"
            assert f"{key}={value}" in url
            for label, fn in REDACTORS.items():
                out = fn(url)
                assert value not in out, f"{label}: {key}= value survived: {out}"
            checked += 1
        assert checked == len(_PATH_SIGN_TYPE) and checked > 0, checked


class TestRedactionPreservesTheUrlsIdentity:
    """Redaction must not destroy the evidence the operator needs -- the host,
    the file name, the resolution rung and the non-signing assignments."""

    def test_host_filename_and_rung_survive(self):
        for name, (url, _addr) in PATH_SHAPES.items():
            for label, fn in REDACTORS.items():
                out = fn(url)
                assert HOST in out, f"{name}/{label}: host destroyed: {out}"
                assert FILE in out, f"{name}/{label}: file name destroyed: {out}"
                assert RUNG in out, f"{name}/{label}: rung destroyed: {out}"
                assert f"download2={FILE}" in out, (
                    f"{name}/{label}: download2 assignment destroyed: {out}")

    def test_benign_path_assignments_survive(self):
        """Negative control: the rule is NAME-driven, not shape-driven. A path
        segment of the same comma-assignment SHAPE whose names are benign must
        pass through byte-identical."""
        benign = (f"https://{HOST}/speed=0,buffer=1.0,media=hls"
                  f"/download2={FILE}/{RUNG}/{FILE}")
        for label, fn in REDACTORS.items():
            out = fn(benign)
            assert out == benign, f"{label}: benign assignments were redacted: {out}"
            assert PLACEHOLDER not in out, f"{label}: {out}"


class TestIdempotenceAndQueryNonInterference:

    def test_redacting_twice_equals_redacting_once(self):
        for name, (url, _addr) in list(PATH_SHAPES.items()) + [("query", (QUERY_CTL, IPV4))]:
            for label, fn in REDACTORS.items():
                once = fn(url)
                assert fn(once) == once, f"{name}/{label}: not idempotent: {once!r}"

    def test_query_surface_is_untouched_by_the_path_rule(self):
        """Control that must hold on the base tree AND after the fix: the path
        rule owns the path only, so a query-signed URL still gets exactly the
        pre-existing query treatment."""
        assert f"key={SIG}" in QUERY_CTL.split("?", 1)[1]
        assert redact_media_url(QUERY_CTL) == redact_query(QUERY_CTL)
        assert SIG not in redact_media_url(QUERY_CTL)
        assert SIG not in redact_value(QUERY_CTL)
        assert HOST in redact_media_url(QUERY_CTL)
        assert FILE in redact_media_url(QUERY_CTL)
