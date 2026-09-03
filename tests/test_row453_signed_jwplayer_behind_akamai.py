"""Row 453 -- the recognizer corpus finally holds the signed x JWPlayer x Akamai case.

Row 120 was parked as CORPUS-BOUND, not code-bound: its signed-URL and player
recognizers ship and are synthetically tested, and what was missing was a real
capture in the intersection *signed* AND *JWPlayer* AND *Akamai/CloudFront*.
Row 453 measured that intersection empty on 2026-08-31 over the 46 shipped pins
and over 238 surveyed captures, and it was empty over that denominator: the
UltraFilms captures are signed JWPlayer with no CDN in the media path, and the
nbcnews capture is JWPlayer behind Akamai with no signing token.  The
2026-09-03 survey of 707 archived captures found the artifact that fills it --
a public, no-login ``www.yupptv.com`` session whose HLS master carries an Akamai
token-auth ``hdnts=`` query and whose player is JWPlayer 8.33.2 served from an
``akamaized.net`` host.

WHAT THIS GATE JUDGES, AND WHY IT IS NOT THE CORPUS TEST.
``tests/test_recognizer_corpus*.py`` asserts that each fixture reproduces its
own pin -- 47 independent per-fixture verdicts, none of which can see whether
the corpus as a POPULATION still covers this class.  Deleting the yupptv
fixture and its pin together leaves that battery green over 46 cases, exactly
the "wrong glob returns clean" shape that let row 120 sit parked for months
while the answer was already in the archive.  This gate holds the population
claim instead: exactly one pinned verdict is signed Akamai-token JWPlayer, and
every member of that intersection takes the runtime path.

WHAT THIS GATE ALSO CORRECTS.  Row 453's acceptance text asks to "build and
prove the AUTO-TEMPLATE from it".  ``build_template_from_wacz.classify`` cannot
return ``auto_template`` for this class by its own design -- signing schemes or
anti-bot send every site to ``pick_test_promote`` with the recorded reason
"signature expires / session-gated -> runtime capture", and ``auto_template`` is
reachable only on the clean branch.  No capture can satisfy that clause, so the
honest closure is the ``signed_akamai_token`` verdict pin plus a template test
that proves the runtime path is chosen and the auto path refused.  The
population control below measures that contract over all 47 pins rather than
asserting it from the docstring.

F2 / IP SAFETY.  The distilled fixture is derived from ``last6.redacted.wacz``
and never from its full twin, which embeds the operator's public IPv4 address in
the Akamai ``acl=`` parameter.  ``test_the_yupptv_fixture_carries_no_network_address``
enumerates every IPv4-shaped literal in the fixture against an audited set and
proves, with a positive control planting an address in that exact parameter,
that the scanner would see one.
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_TOOLS = _REPO / "tools"


def _load_tool(stem: str):
    """Load a ``tools/`` module by path, as the row-176 sibling gate does.

    A static ``import build_recognizer_corpus`` would add a tests -> tools edge
    to the frozen import graph. The sibling recognizer-identity gate resolves
    the same modules by path for the same reason, so this mirrors it rather
    than re-freezing a whole-tree baseline for one gate.
    """
    if str(_TOOLS) not in sys.path:
        sys.path.insert(0, str(_TOOLS))
    spec = importlib.util.spec_from_file_location(
        f"row453_{stem}", _TOOLS / f"{stem}.py"
    )
    assert spec is not None and spec.loader is not None, (
        f"cannot import {stem} from {_TOOLS}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CORPUS = _REPO / "tests" / "corpus" / "recognizer"
_FIXTURE = _CORPUS / "yupptv.cap.json"
_PINS = json.loads((_CORPUS / "expected_verdicts.json").read_text(encoding="utf-8"))

# The subject, declared independently of the artifacts under test.
_CASE = "yupptv"
_SOURCE_CAPTURE = "last6.redacted.wacz"
_HOST = "www.yupptv.com"

_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

# Every IPv4-SHAPED literal the distilled fixture contains, audited one by one
# on 2026-09-03 and declared here rather than derived from the fixture, so a
# newly introduced address fails instead of being absorbed.  None is a network
# address: the first two are Chrome's four-part version string inside a
# ``user-agent`` / ``uafvl=`` beacon value, and the rest are relative
# coordinate runs inside SVG ``path d="..."`` data in the page markup.
_AUDITED_IPV4_SHAPED = {
    "146.0.0.0": "Chrome/146.0.0.0 user-agent version",
    "24.0.0.0": "Not-A.Brand;24.0.0.0 client-hint brand version",
    "2.67.76.87": "SVG path d= coordinate run",
    "1.78.53.61": "SVG path d= coordinate run",
    "1.77.45.63": "SVG path d= coordinate run",
    "1.29.63.68": "SVG path d= coordinate run",
    "1.58.53.55": "SVG path d= coordinate run",
    "1.31.55.61": "SVG path d= coordinate run",
    "1.12.33.7": "SVG path d= coordinate run",
}


def _intersection(pins: dict) -> list:
    """Pins in the row-453 intersection: Akamai-token signed AND JWPlayer.

    ``site_type`` is ``"signed_" + first scheme`` (build_template_from_wacz
    classify), so ``signed_akamai_token`` is the classifier's own statement that
    an Akamai token-auth signature was recognised on a media URL.
    """
    return sorted(
        name for name, v in pins.items()
        if v.get("site_type") == "signed_akamai_token"
        and v.get("player_family") == "jwplayer"
        and v.get("signing_schemes") == ["akamai_token"]
    )


def _unaudited_ipv4(text: str) -> list:
    return sorted({m.group(0) for m in _IPV4.finditer(text)} - set(_AUDITED_IPV4_SHAPED))


# --------------------------------------------------------------------------
# The population claim.  RED on the defective parent: the intersection is empty.
# --------------------------------------------------------------------------

def test_exactly_one_pinned_verdict_is_signed_jwplayer_behind_akamai():
    fixtures = {p.name.removesuffix(".cap.json") for p in _CORPUS.glob("*.cap.json")}
    # Preconditions, asserted so an empty or mismatched corpus cannot make the
    # verdict below vacuous or falsely RED.
    assert fixtures, "no recognizer fixtures found; the population is unmeasurable"
    assert set(_PINS) == fixtures, (
        "pin/fixture population mismatch: "
        f"pin-only={sorted(set(_PINS) - fixtures)}, "
        f"fixture-only={sorted(fixtures - set(_PINS))}"
    )
    signed = sorted(n for n, v in _PINS.items() if v.get("signing_schemes"))
    jw = sorted(n for n, v in _PINS.items() if v.get("player_family") == "jwplayer")
    assert signed, "no pin carries any signing scheme; the signing axis is unmeasured"
    assert jw, "no pin carries player_family jwplayer; the player axis is unmeasured"

    assert _intersection(_PINS) == [_CASE], (
        "row 120/453: the recognizer corpus must pin exactly one capture in the "
        "signed x JWPlayer x Akamai intersection, and it must be the yupptv "
        f"capture; got {_intersection(_PINS)} over {len(_PINS)} pins "
        f"(signed={len(signed)}, jwplayer={len(jw)})"
    )


def test_the_intersection_takes_the_runtime_path_and_never_auto_template():
    """The honest form of row 453's 'auto-template' clause.

    The row asks for an auto-template.  classify() forbids it for this class,
    so the provable claim is that the intersection resolves to
    ``pick_test_promote`` through the real recognizer, and that the reason it
    is not ``auto_template`` is the recorded signing reason rather than an
    accident of the fixture.
    """
    members = _intersection(_PINS)
    assert members == [_CASE], (
        f"the signed x JWPlayer x Akamai intersection is {members}, not ['{_CASE}']"
    )
    pin = _PINS[_CASE]
    assert pin["recommended_path"] == "pick_test_promote", pin
    assert pin["recommended_path"] != "auto_template", pin
    assert pin["requires_runtime_capture"] is True, pin
    assert pin["downloadable"] is True, pin
    assert pin["primary_protocol"] == "hls", pin
    assert pin["drm"] is False, pin

    # Resolve the capture through the production recognizer, so the pin is not
    # merely self-consistent JSON.
    brc = _load_tool("build_recognizer_corpus")
    draft = brc.build_from_fixture(_FIXTURE)
    verdict = draft["verdict"]
    assert verdict["site_type"] == "signed_akamai_token", verdict
    assert verdict["recommended_path"] == "pick_test_promote", verdict
    assert verdict["recommended_path"] != "auto_template", verdict
    assert "signed-URL scheme(s): akamai_token" in verdict["reasons"], verdict
    assert "signature expires / session-gated -> runtime capture" in verdict["reasons"], verdict
    rec = draft["recognition"]
    assert rec["player_family"] == "jwplayer", rec["player_family"]
    assert (rec["protection"]["signing"] or {}).get("schemes") == ["akamai_token"], rec


def test_classify_refuses_auto_template_for_every_signed_or_anti_bot_pin():
    """Population control for the acceptance-wording correction.

    Over the WHOLE pin population: no signed or anti-bot site is ever handed
    ``auto_template``, and -- the negative control -- ``auto_template`` is still
    reachable, so the claim is a contract and not a dead branch.
    """
    protected = [n for n, v in _PINS.items() if v.get("signing_schemes") or v.get("anti_bot")]
    auto = [n for n, v in _PINS.items() if v.get("recommended_path") == "auto_template"]
    assert protected, "no pin is signed or anti-bot protected; the claim is vacuous"
    assert auto, "no pin reaches auto_template; the negative control is vacuous"
    leaked = sorted(n for n in protected if _PINS[n]["recommended_path"] == "auto_template")
    assert leaked == [], (
        "a signed or anti-bot site was handed the auto_template path: " + repr(leaked)
    )
    # ... and the same refusal read straight off classify(), not off the pins.
    btw = _load_tool("build_template_from_wacz")
    signed_call = btw.classify(
        framework="jwplayer",
        protocol={"primary": "hls"},
        protection={"signing": {"schemes": ["akamai_token"]}, "anti_bot": []},
        selectors={"player": "#player"},
    )
    assert signed_call["recommended_path"] == "pick_test_promote", signed_call
    assert signed_call["site_type"] == "signed_akamai_token", signed_call
    clean_call = btw.classify(
        framework="jwplayer",
        protocol={"primary": "hls"},
        protection={"signing": {"schemes": []}, "anti_bot": []},
        selectors={"player": "#player"},
    )
    assert clean_call["recommended_path"] == "auto_template", clean_call


# --------------------------------------------------------------------------
# Provenance and IP safety.
# --------------------------------------------------------------------------

def test_the_yupptv_producer_names_the_redacted_twin_only():
    brc = _load_tool("build_recognizer_corpus")
    assert brc.CORPUS_SRC.get(_CASE) == _SOURCE_CAPTURE, (
        "the yupptv fixture producer is not pinned to the redacted capture; "
        f"got {brc.CORPUS_SRC.get(_CASE)!r}"
    )
    assert _CASE in brc.DEFAULT_CORPUS, "yupptv is not in the default corpus"
    # The full twin embeds the operator's public IPv4 in the Akamai acl=
    # parameter and must never become a corpus source.
    assert "last6.wacz" not in set(brc.CORPUS_SRC.values()), brc.CORPUS_SRC


def test_the_yupptv_fixture_carries_no_network_address():
    text = _FIXTURE.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload.get("host") == _HOST, payload.get("host")

    found = _IPV4.findall(text)
    # Nonzero denominator: the scanner must be proven to be looking at
    # something.  Every hit is then classified against the audited set.
    assert found, "the IPv4 scanner matched nothing at all; it is not measuring"
    assert _unaudited_ipv4(text) == [], (
        "an unaudited IPv4-shaped literal entered the yupptv fixture; classify "
        "it explicitly or remove it"
    )

    # No network address in a host position anywhere in the capture.
    entries = payload.get("network_log") or []
    assert len(entries) > 0, "the fixture has no network entries to judge"
    ip_hosts = []
    for entry in entries:
        host = urlsplit(entry.get("url") or "").hostname or ""
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue
        ip_hosts.append(host)
    assert ip_hosts == [], f"a request URL names a bare IP host: {ip_hosts}"


def test_the_ipv4_control_sees_an_address_in_the_akamai_acl_parameter():
    """Positive control: the scanner catches an address in the exact position
    the operator's IPv4 occupies in the unredacted twin.

    ``203.0.113.7`` is RFC 5737 TEST-NET-3 documentation space -- a
    zero-entropy stand-in, never a real address.
    """
    planted = (
        'https://vglivessai.akamaized.net/sg/v1/master/x/y/playlist.m3u8'
        '?hdnts=st=1750301552~exp=1750387952~acl=/sg/*!203.0.113.7~hmac=deadbeef'
    )
    assert _unaudited_ipv4(planted) == ["203.0.113.7"], _unaudited_ipv4(planted)
    # ... and the audited literals are not themselves a blanket allow: an
    # address that merely resembles one still fails.
    assert _unaudited_ipv4("146.0.0.1") == ["146.0.0.1"]
    assert _unaudited_ipv4("146.0.0.0") == []


def test_transform_control_imports_the_builder_without_judging_the_corpus():
    """Mutation transform control: import success is not a corpus verdict."""
    assert callable(_load_tool("build_recognizer_corpus").build_from_fixture)
    assert callable(_load_tool("build_template_from_wacz").classify)
