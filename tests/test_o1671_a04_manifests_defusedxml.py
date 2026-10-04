"""O1671 a04: DASH / Smooth Streaming manifests are untrusted network XML.

parse_dash_mpd and parse_smooth_streaming parsed them with the stdlib
xml.etree.ElementTree.fromstring, which honours DTD entity declarations: a
manifest carrying an internal-subset <!ENTITY> is expanded (billion-laughs /
quadratic blowup) and an external SYSTEM entity reaches the parser.  Both now
go through one helper, _safe_xml_fromstring (defusedxml), and a refused
document takes the same "doesn't parse" path as malformed XML: kind stays
not_dash / not_smooth, a warning is recorded, nothing raises.
"""
from __future__ import annotations

import ast
from pathlib import Path

BD_GATE_SCOPE = "module"

from bulk_downloader.deep_detect import manifests as m

_MANIFESTS_PY = Path(m.__file__)

# An entity whose expansion is visible in the parsed tree (the Representation
# id) -- on the unfixed parser the id comes back as the expanded text.
_ENTITY_DECL = ('<!DOCTYPE MPD [<!ENTITY a "AAAAAAAAAA">'
                '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>')

_MPD_BODY = (
    '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">'
    '<Period id="p0"><AdaptationSet mimeType="video/mp4">'
    '<Representation id="{rid}" width="1920" height="1080" '
    'bandwidth="5000000" codecs="avc1.640028"/>'
    '</AdaptationSet></Period></MPD>')

_ISM_BODY = (
    '<SmoothStreamingMedia MajorVersion="2" MinorVersion="0" '
    'Duration="100" Note="{note}">'
    '<StreamIndex Type="video" Name="video" QualityLevels="1">'
    '<QualityLevel Index="0" Bitrate="5000000" FourCC="H264" '
    'MaxWidth="1920" MaxHeight="1080"/>'
    '</StreamIndex></SmoothStreamingMedia>')


def _mpd(rid: str, doctype: str = "") -> str:
    return '<?xml version="1.0"?>' + doctype + _MPD_BODY.format(rid=rid)


def _ism(note: str, doctype: str = "") -> str:
    return '<?xml version="1.0"?>' + doctype + _ISM_BODY.format(note=note)


def _refused(out: dict, prefix: str) -> bool:
    return any(w.startswith(prefix) and "refused unsafe XML" in w
               for w in out["warnings"])


# --- positive controls: the probe can say yes -------------------------------

def test_benign_mpd_still_parses():
    out = m.parse_dash_mpd(_mpd("v1080"))
    assert out["kind"] == "dash_mpd", out
    assert [v["id"] for v in out["video"]] == ["v1080"], out


def test_benign_smooth_manifest_still_parses():
    out = m.parse_smooth_streaming(_ism("plain"))
    assert out["kind"] == "smooth_streaming", out
    assert len(out["video"]) == 1, out


# --- RED on the unfixed parser: entity declarations were expanded -----------

def test_mpd_entity_expansion_is_refused():
    out = m.parse_dash_mpd(_mpd("&b;", _ENTITY_DECL))
    assert out["kind"] == "not_dash", (
        "MPD with an <!ENTITY> internal subset was parsed and expanded: "
        f"{[v['id'][:24] for v in out['video']]}")
    assert out["video"] == []
    assert _refused(out, "MPD XML parse failed"), out["warnings"]


def test_mpd_external_entity_is_refused():
    doctype = '<!DOCTYPE MPD [<!ENTITY x SYSTEM "file:///etc/hostname">]>'
    out = m.parse_dash_mpd(_mpd("&x;", doctype))
    assert out["kind"] == "not_dash", out
    assert _refused(out, "MPD XML parse failed"), out["warnings"]


def test_smooth_entity_expansion_is_refused():
    doctype = _ENTITY_DECL.replace("MPD", "SmoothStreamingMedia")
    out = m.parse_smooth_streaming(_ism("&b;", doctype))
    assert out["kind"] == "not_smooth", (
        "Smooth manifest with an <!ENTITY> internal subset was parsed")
    assert out["video"] == []
    assert _refused(out, "Smooth manifest XML parse failed"), out["warnings"]


# --- one seam: no call site parses with the stdlib directly ------------------

def test_both_parsers_use_the_one_safe_helper():
    tree = ast.parse(_MANIFESTS_PY.read_text(encoding="utf-8"))
    direct = []
    helper_callers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "fromstring"
                    and isinstance(f.value, ast.Name) and f.value.id == "ET"):
                direct.append((fn.name, node.lineno))
            if isinstance(f, ast.Name) and f.id == "_safe_xml_fromstring":
                helper_callers.add(fn.name)
    assert direct == [], f"stdlib ET.fromstring on untrusted XML: {direct}"
    assert {"parse_dash_mpd", "parse_smooth_streaming"} <= helper_callers, \
        helper_callers
