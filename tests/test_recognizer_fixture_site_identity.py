"""Recognizer corpus fixture names must not lie about their captured site.

Backlog row 176 found that ``reptyle.cap.json`` was distilled from a capture
whose own payload says ``auth.wowgirls.com``.  A recognizer verdict can be
perfectly correct for those bytes while being useless evidence about Reptyle,
so verdict pins alone cannot catch this class of corpus corruption.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_CORPUS = _REPO / "tests" / "corpus" / "recognizer"
_BUILDER = _REPO / "tools" / "build_recognizer_corpus.py"


# Independent declaration of the complete fixture population and its page
# hosts.  Do not derive this from the fixture payloads or expected_verdicts:
# either artifact is capable of carrying the same mislabel as its neighbour.
_DECLARED_FIXTURE_HOSTS = {
    "adult": "freetour.adulttime.com",
    "art": "artplayer.org",
    "banb": "site-ma.bangbros.com",
    "bang": "www.bang.com",
    "beeg": "beeg.com",
    "bit": "bitmovin.com",
    "brazzers": "site-ma.brazzers.com",
    "brightcove": "demo-brightcove.test",
    "bunny_stream": "demo-bunny.test",
    "clappr": "demo-clappr.test",
    "cloudflare_stream": "demo-cfstream.test",
    "dailymotion": "demo-dailymotion.test",
    "dashjs": "demo-dashjs.test",
    "dfx": "members.dfxtra.com",
    "dpla": "dplayer.diygod.dev",
    "embed": "site-ma.bangbros.com",
    "erome": "www.erome.com",
    "hlsjs": "app.reptyle.com",
    "iframe": "fast.wistia.net",
    "kaltura": "demo-kaltura.test",
    "kelly": "members.kellymadisonmedia.com",
    "media": "www.mediaelementjs.com",
    "mux": "demo-mux.test",
    "mxchrome": "media-chrome.mux.dev",
    "news": "newsensations.com",
    "nook": "nookies.com",
    "nubiles": "members.nubiles-porn.com",
    "peg": "www.pegasproductions.com",
    "react_player": "demo-react-player.test",
    "redgif": "www.redgifs.com",
    "reptyle": "app.reptyle.com",
    "scroller": "scrolller.com",
    "shaka": "shaka-player-demo.appspot.com",
    "shaka_clear": "demo-shaka.test",
    "sproutvideo": "demo-sprout.test",
    "teen": "members.teenmegaworld.net",
    "theo": "demo.theoplayer.com",
    "tiny": "tiny4k.com",
    "ultra": "ultrafilms.com",
    "vdash": "vidstack.io",
    "vimeo": "demo-vimeo.test",
    "vip4k": "vip4k.com",
    "vixen": "login.vixen.com",
    "wow": "auth.wowgirls.com",
    "wowza": "www.wowza.com",
    "xnxx": "www.xnxx.com",
}


def _fixtures() -> dict[str, Path]:
    suffix = ".cap.json"
    return {
        path.name.removesuffix(suffix): path
        for path in sorted(_CORPUS.glob(f"*{suffix}"))
    }


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "row176_build_recognizer_corpus", _BUILDER
    )
    assert spec is not None and spec.loader is not None, (
        f"cannot import recognizer corpus builder from {_BUILDER}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _site_mismatches(
    payloads: dict[str, dict], declared: dict[str, str]
) -> list[str]:
    return [
        f"{name}: declared {declared[name]!r}, payload host {payloads[name].get('host')!r}"
        for name in sorted(declared)
        if payloads[name].get("host") != declared[name]
    ]


def test_every_recognizer_fixture_declares_the_host_its_payload_records():
    fixtures = _fixtures()
    pins = json.loads((_CORPUS / "expected_verdicts.json").read_text("utf-8"))
    expected = set(_DECLARED_FIXTURE_HOSTS)

    assert len(expected) == 46, (
        "the independent recognizer-fixture denominator changed; classify the "
        f"new or removed fixture explicitly, got {len(expected)} declarations"
    )
    assert set(fixtures) == expected, (
        f"fixture/declaration mismatch: missing={sorted(expected - set(fixtures))}, "
        f"extra={sorted(set(fixtures) - expected)}"
    )
    assert set(pins) == expected, (
        f"pin/declaration mismatch: missing={sorted(expected - set(pins))}, "
        f"extra={sorted(set(pins) - expected)}"
    )

    payloads: dict[str, dict] = {}
    shape_errors: list[str] = []
    fired = 0
    for name in sorted(expected):
        payload = json.loads(fixtures[name].read_text("utf-8"))
        payloads[name] = payload
        network_log = payload.get("network_log")
        actual_count = len(network_log) if isinstance(network_log, list) else -1
        if actual_count <= 0:
            shape_errors.append(f"{name}: network_log has {actual_count} entries")
        if payload.get("network_log_count") != actual_count:
            shape_errors.append(
                f"{name}: network_log_count={payload.get('network_log_count')!r}, "
                f"actual={actual_count}"
            )
        host = payload.get("host")
        if not isinstance(host, str) or not host:
            shape_errors.append(f"{name}: missing nonempty payload host")
        for field in ("origin", "url"):
            recorded_host = urlsplit(payload.get(field) or "").hostname
            if recorded_host != host:
                shape_errors.append(
                    f"{name}: {field} host {recorded_host!r} != payload host {host!r}"
                )
        fired += 1

    assert fired == 46, f"site control fired {fired} times, expected exactly 46"
    assert not shape_errors, "invalid recognizer fixture preconditions:\n  " + "\n  ".join(shape_errors)
    mismatches = _site_mismatches(payloads, _DECLARED_FIXTURE_HOSTS)
    assert not mismatches, "recognizer fixture site/host mismatch:\n  " + "\n  ".join(mismatches)


def test_reptyle_fixture_producer_names_the_genuine_source_capture():
    brc = _load_builder()
    assert brc.CORPUS_SRC.get("reptyle") == "Reptyle-1.redacted.wacz", (
        "the Reptyle fixture producer is not pinned to the independently "
        "verified app.reptyle.com capture"
    )


def test_site_mismatch_control_rejects_wowgirls_bytes_labelled_reptyle():
    payloads = {"reptyle": {"host": "auth.wowgirls.com"}}
    declared = {"reptyle": "app.reptyle.com"}
    assert _site_mismatches(payloads, declared) == [
        "reptyle: declared 'app.reptyle.com', payload host 'auth.wowgirls.com'"
    ]


def test_transform_control_imports_builder_without_judging_site_identity():
    """Mutation transform control: import success is not a site verdict."""
    brc = _load_builder()
    assert brc.__name__ == "row176_build_recognizer_corpus"
