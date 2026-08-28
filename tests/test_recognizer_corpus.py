"""D++ §9 — recognizer regression corpus.

The durability backbone of the D++ program (cuts 1-4). Runs the live
build_template over a curated set of DISTILLED redacted real captures (one+ per
framework × protocol × protection class) and asserts the PINNED verdict, so a
later change to any A-E recognizer that silently alters a real-site
classification fails here.

Fixtures (tests/corpus/recognizer/*.cap.wacz) are distilled name/shape captures
(full DOM snapshot + recognizer-relevant mutations + network url/headers +
manifest bodies; heavy media/segment/mutation bodies dropped). Pins
(expected_verdicts.json) carry NAMES/COUNTS/TAGS only -- never a value (F2).

Regenerate after an intentional recognizer change:
    python3 tools/build_recognizer_corpus.py --regen-pins --out tests/corpus/recognizer
"""
import ast
import json
import os
import sys
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import build_template_from_wacz as btw  # noqa: E402
import build_recognizer_corpus as brc  # noqa: E402
sys.path.insert(0, _REPO)
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets  # noqa: E402

_CORPUS = Path(_REPO) / "tests" / "corpus" / "recognizer"
_PINS = json.loads((_CORPUS / "expected_verdicts.json").read_text(encoding="utf-8"))


# Independent scheduling denominator for row 333.  Capture uses
# ``--dist loadfile``, so the former 46 case functions in this module were one
# 220.132-second worker unit even though no case shares mutable fixture state.
# Keep the intended partition here, separate from the shard implementations,
# so deleting a case or duplicating it in another file cannot make its own
# reduced denominator look complete.
_EXPECTED_SHARDS = {
    "test_recognizer_corpus_shard_a.py": {
        "scroller", "tiny", "theo", "kelly", "xnxx", "embed", "media",
        "clappr", "brightcove", "vimeo",
    },
    "test_recognizer_corpus_shard_b.py": {
        "erome", "adult", "news", "banb", "vdash", "wowza", "mxchrome",
        "hlsjs", "nubiles", "react_player", "kaltura",
    },
    "test_recognizer_corpus_shard_c.py": {
        "ultra", "beeg", "bang", "reptyle", "redgif", "nook", "teen",
        "dpla", "iframe", "vip4k", "dashjs", "mux", "bunny_stream",
    },
    "test_recognizer_corpus_shard_d.py": {
        "dfx", "wow", "bit", "peg", "brazzers", "shaka", "vixen", "art",
        "shaka_clear", "dailymotion", "cloudflare_stream", "sproutvideo",
    },
}


def _pin(draft):
    rec = draft.get("recognition") or {}
    v = draft.get("verdict") or {}
    p = rec.get("protection") or {}
    tr = rec.get("tracks") or {}
    return {
        "site_type": v.get("site_type"),
        "recommended_path": v.get("recommended_path"),
        "downloadable": v.get("downloadable"),
        "requires_runtime_capture": v.get("requires_runtime_capture"),
        "primary_protocol": rec.get("primary_protocol"),
        "rendition_count": len(rec.get("renditions") or []),
        "player_family": rec.get("player_family"),
        "signing_schemes": sorted((p.get("signing") or {}).get("schemes") or []),
        "anti_bot": sorted(p.get("anti_bot") or []),
        "drm": bool(p.get("drm")),
        "caption_count": len(tr.get("captions") or []),
        "ssai": bool(tr.get("ssai")),
    }


def test_corpus_present():
    fixtures = sorted(_CORPUS.glob("*.cap.json"))
    assert fixtures, "no corpus fixtures found"
    assert len(_PINS) == len(fixtures), "pin/fixture count mismatch"
    assert len(fixtures) >= 24, "corpus too thin to cover the class matrix"


def test_corpus_covers_the_class_matrix():
    sts = {v["site_type"] for v in _PINS.values()}
    paths = {v["recommended_path"] for v in _PINS.values()}
    fams = {v["player_family"] for v in _PINS.values()}
    schemes = set()
    for v in _PINS.values():
        schemes.update(v["signing_schemes"])
    protos = {v["primary_protocol"] for v in _PINS.values()}
    # every verdict class + signing scheme + the DRM path must be represented.
    # dash_manifest + iframe_embed added once a real fixture for each landed
    # (vdash = vidstack/DASH; embed = clean-path iframe_embed -- see notes below).
    assert {"signed_generic_token", "signed_cloudfront", "signed_aws_sigv4",
            "drm_protected", "hls_manifest", "direct_progressive",
            "dash_manifest", "iframe_embed"} <= sts
    # the DASH manifest path must have a real pinned protocol, not just a label
    assert "dash" in protos, "no fixture exercises primary_protocol=dash"
    assert {"auto_template", "pick_test_promote", "not_downloadable"} <= paths
    assert {"cloudfront", "aws_sigv4", "generic_token"} <= schemes
    # breadth of player frameworks incl. the expanded set (CORPUS-EXP added the
    # media_chrome web-component player and a real hls.js site).
    assert {"jwplayer", "videojs", "bitmovin", "wistia", "native_custom",
            "theoplayer", "shaka", "mediaelement", "media_chrome", "hlsjs"} <= fams
    # DRM diversity: at least two distinct DRM stacks (bitmovin + shaka)
    drm_fams = {v["player_family"] for v in _PINS.values() if v["drm"]}
    assert len(drm_fams) >= 2, f"need >=2 DRM stacks, got {drm_fams}"
    assert any(v["anti_bot"] for v in _PINS.values())
    # caption-rich site exercises recognize_aux
    assert max(v["caption_count"] for v in _PINS.values()) >= 5


def test_all_46_corpus_cases_are_partitioned_across_four_loadfile_shards():
    """The complete corpus must be split, never narrowed or duplicated."""
    expected = set().union(*_EXPECTED_SHARDS.values())
    fixtures = {path.name.removesuffix(".cap.json")
                for path in _CORPUS.glob("*.cap.json")}
    assert len(_EXPECTED_SHARDS) == 4, "recognizer shard denominator changed"
    assert len(expected) == len(fixtures) == len(_PINS) == 46, (
        "recognizer corpus denominator changed: expected=%d fixtures=%d pins=%d"
        % (len(expected), len(fixtures), len(_PINS)))
    assert expected == fixtures == set(_PINS), (
        "recognizer cases left the complete fixture/pin population: "
        f"expected-only={sorted(expected - fixtures)}, "
        f"fixture-only={sorted(fixtures - expected)}, "
        f"pin-only={sorted(set(_PINS) - expected)}")
    assert sum(len(cases) for cases in _EXPECTED_SHARDS.values()) == 46, (
        "a recognizer case appears in more than one scheduling shard")

    observed = {}
    missing = []
    for filename, cases in _EXPECTED_SHARDS.items():
        path = Path(__file__).with_name(filename)
        if not path.is_file():
            missing.append(filename)
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        call_names = [
            node.args[0].value
            for node in ast.walk(tree)
            if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_check_one"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str))
        ]
        calls = set(call_names)
        observed[filename] = calls
        assert len(call_names) == len(cases), (
            f"{filename} must call each of its {len(cases)} cases exactly once; "
            f"found {len(call_names)} calls")
        assert calls == cases, (
            f"{filename} does not execute its exact pinned population: "
            f"missing={sorted(cases - calls)}, extra={sorted(calls - cases)}")
    assert not missing, (
        "recognizer corpus remains one loadfile critical-path unit; missing "
        f"physical shards: {missing}")
    assert observed.keys() == _EXPECTED_SHARDS.keys()


def test_recognizer_partition_transform_control_imports_without_judging_cases():
    """Mutation control: the partition remains valid Python when not judged."""
    assert callable(_check_one)


def _check_one(name):
    fx = _CORPUS / f"{name}.cap.json"
    draft = brc.build_from_fixture(fx)
    got = _pin(draft)
    assert got == _PINS[name], f"{name}: verdict drift\n got={got}\n pin={_PINS[name]}"
    # F2: no secret survives into the built draft
    assert scan_artifact_secrets(draft) == [], f"{name}: secret leak in draft"


# The 46 zero-argument fixture cases live in four physical shard files.  Keep
# the helpers and the corpus-wide assertions here; the independent map above
# proves that the move changed scheduling only, not membership.
