"""build_recognizer_corpus.py — D++ §9 recognizer regression corpus.

Distills redacted real `.wacz` captures into small, F2-safe **name/shape**
fixtures (full DOM snapshot + recognizer-relevant interaction mutations +
network url/headers + manifest bodies only — heavy media/segment/mutation
bodies dropped) and pins the 297 recognizer VERDICT for each so the A–E
recognizers cannot silently rot as sites change.

A fixture reproduces its source capture's verdict; the pinned record carries
ONLY names / shapes / counts / tags — never a token, signed-URL, or value.

Usage:
    python3 tools/build_recognizer_corpus.py --src <dir-of-redacted-wacz> \\
        --out tests/corpus/recognizer [--only name1,name2,...]

Regenerate the pinned verdicts (after an intentional recognizer change):
    python3 tools/build_recognizer_corpus.py --regen-pins --out tests/corpus/recognizer
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_template_from_wacz as btw  # noqa: E402

# Default curated corpus: one (or few) per (framework × protocol × protection)
# class. Names are the redacted-capture stems under --src.
DEFAULT_CORPUS = [
    "reptyle", "ultra", "banb", "news", "vip4k", "dfx", "vixen", "tiny",
    "bit", "iframe", "xnxx", "beeg", "nubiles", "scroller", "peg", "teen",
    "brazzers", "nook",
    # expanded breadth — new frameworks (THEOplayer/Shaka/MediaElement/DPlayer/
    # ArtPlayer), a 2nd DRM stack (shaka), a caption-rich site (adult, 7 tracks),
    # and the redgifs/wowgirls platforms.
    "theo", "shaka", "media", "dpla", "art", "adult", "redgif", "wow",
    # gap-fillers (D++ corpus polish): the only DASH-primary capture in the
    # canonical set (vidstack/DASH -> dash_manifest), and the only iframe_embed.
    # NOTE: ``embed`` reaches iframe_embed via the clean-path fallback
    # (primary is None + no player selector), NOT the iframe_hit -> family
    # branch -- it pins the iframe_embed *site_type label*, not the iframe
    # *detection* path (family stays "unknown"). A real cross-origin
    # third-party-embed capture (family=iframe_embed, not_downloadable) would
    # be a stronger detection guard; none exists in the supplied set.
    "vdash", "embed",
    # CORPUS-EXP (v3.66.520): widened from CONSOLIDATED_corpus_FULL_v3_66_301.
    # Each adds a NET-NEW (site_type x recommended_path x primary_protocol x
    # player_family) combo. mxchrome + hlsjs are two NEW player families
    # (Mux <media-chrome> web component; a real hls.js site, operator-confirmed).
    # bang = videojs/direct_progressive; kelly = videojs/signed_generic_token over
    # progressive; wowza = 2nd flowplayer host; erome = unsigned videojs HLS manifest.
    "mxchrome", "hlsjs", "bang", "kelly", "wowza", "erome",
    # Row 453 (row 120's successor): the corpus' first capture in the true
    # signed x jwplayer x akamai intersection -- a public www.yupptv.com
    # session whose HLS master carries an Akamai token-auth `hdnts=` query and
    # whose player is JWPlayer 8.33.2 served from an akamaized.net host. Row
    # 120 was parked as corpus-bound on exactly this gap.
    "yupptv",
]

# Corpus fixture name -> exact source filename in the canonical redacted set.
# Most names are the bare ``<name>.redacted.wacz`` stem (the default), so only
# the entries whose source filename differs from the fixture name are listed.
CORPUS_SRC = {
    # Row 176: the default ``reptyle.redacted.wacz`` is a mislabelled
    # auth.wowgirls.com capture.  This independently verified source records
    # app.reptyle.com and exercises the real Reptyle movie path.
    "reptyle": "Reptyle-1.redacted.wacz",
    "theo":   "t_1619e7c3f01f442b_theo.redacted.wacz",
    "shaka":  "t_10852616644c4edf_shaka.redacted.wacz",
    "media":  "t_b1051d912ba2408f_media.redacted.wacz",
    "dpla":   "t_d801e947dca64cd3_dpla.redacted.wacz",
    "art":    "t_f85a7ee8a70e47e1_art.redacted.wacz",
    "adult":  "t_7172200c07e24480_adult.redacted.wacz",
    "redgif": "t_8f7fcb55fd1f4d2a_redgif.redacted.wacz",
    "wow":    "t_24ef7d88b98444af_wow.redacted.wacz",
    "vdash":  "vidstack-2.redacted.wacz",
    "embed":  "capture.redacted (2).wacz",
    # CORPUS-EXP (v3.66.520): source stems differing from the fixture name.
    # (kelly + wowza match the <name>.redacted.wacz default, so no entry needed.)
    "mxchrome": "chrome.redacted.wacz",
    "hlsjs":    "w.wacz",
    "bang":     "bang.com.redacted.wacz",
    "erome":    "t_0f67ad4fbee24a83_erome.redacted.wacz",
    # Row 453: the redacted twin ONLY. Its full twin `last6.wacz` embeds the
    # operator's public IPv4 in the Akamai `acl=` parameter 8 times and must
    # never be archived or distilled; the redacted file carries 0 and yields
    # the byte-identical recognizer verdict. The source stem is the capture's
    # original uninformative filename, which is why this entry is required.
    "yupptv":   "last6.redacted.wacz",
}

_MANIFEST_EXT = (".m3u8", ".mpd", ".ism")
# recognizer-relevant tokens — an interaction mutation is kept only if it
# carries one of these (drops pure styling/text deltas that bloat the fixture)


def _is_manifest(url: str, ct: str) -> bool:
    u = (url or "").split("?")[0].lower()
    return u.endswith(_MANIFEST_EXT) or "mpegurl" in ct or "dash+xml" in ct


def _load_capture(wacz: Path) -> dict:
    with zipfile.ZipFile(wacz) as z:
        name = next((n for n in z.namelist() if n.endswith("archive/capture.json")), None) \
            or next(n for n in z.namelist() if n.endswith("capture.json"))
        return json.loads(z.read(name))


def distill(cap: dict) -> dict:
    """Reduce a capture to the recognizer-relevant signals.

    DOM: collapse the whole dom_log (full-snapshot html strings + the serialized
    node/mutation trees) into the SINGLE combined HTML string the builder would
    compute, stored as one ``full_snapshot`` html entry. This is what the
    recognizers actually read, is deterministic (no marker heuristic), bakes in
    interaction-added subtrees, and is far smaller than the verbose rrweb node
    JSON. Network: url + headers + manifest bodies only (heavy bodies dropped).
    """
    out = {k: cap.get(k) for k in ("url", "origin", "host", "captured_at")}
    dom_log = cap.get("dom_log") or []
    fulls = [e for e in dom_log
             if isinstance(e, dict) and e.get("type") == "full_snapshot"
             and isinstance(e.get("html"), str)]
    combined = "\n".join(e.get("html", "") for e in fulls)
    combined = (combined + "\n" + btw._nodes_to_html(dom_log)).strip()
    out["dom_log"] = [{"type": "full_snapshot", "html": combined}]
    net = []
    for e in cap.get("network_log") or []:
        if not isinstance(e, dict):
            continue
        rh = e.get("response_headers") or []
        ct = next((str(h.get("value", "")).lower() for h in rh
                   if isinstance(h, dict) and str(h.get("name", "")).lower() == "content-type"), "")
        e2 = {"url": e.get("url"), "response_status": e.get("response_status"),
              "response_headers": rh}
        if e.get("request_headers"):
            e2["request_headers"] = e["request_headers"]
        body = e.get("response_body")
        if body and _is_manifest(e.get("url"), ct) and len(str(body)) < 200_000:
            e2["response_body"] = body
        net.append(e2)
    out["network_log"] = net
    out["cookies"] = cap.get("cookies")
    out["dom_log_count"] = 1
    out["network_log_count"] = len(net)
    return out


def write_fixture(distilled: dict, dest: Path) -> None:
    """Fixtures are stored as plain capture JSON (F2-auditable, no .wacz in the
    release zip per the manifest rule). The production unzip load path is still
    exercised at build/test time via :func:`build_from_fixture`."""
    dest.write_text(json.dumps(distilled), encoding="utf-8")


def build_from_fixture(cap_json: Path) -> dict:
    """Wrap a stored capture-JSON fixture into an in-memory .wacz and run the
    real ``build_template`` (exercises the production ``_load_capture`` unzip
    path without shipping a .wacz)."""
    import tempfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("archive/capture.json", cap_json.read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile(suffix=".wacz", delete=False) as tf:
        tf.write(buf.getvalue())
        tmp = tf.name
    try:
        return btw.build_template(Path(tmp))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def verdict_pin(draft: dict) -> dict:
    """Name/shape/count/tag pin only — no values (F2)."""
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Build/regen the recognizer regression corpus.")
    ap.add_argument("--src", type=Path, help="dir of *.redacted.wacz source captures")
    ap.add_argument("--out", type=Path, default=Path("tests/corpus/recognizer"))
    ap.add_argument("--only", help="comma-separated subset of corpus names")
    ap.add_argument("--regen-pins", action="store_true",
                    help="rebuild expected_verdicts.json from the existing fixtures")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    names = (args.only.split(",") if args.only else DEFAULT_CORPUS)
    pins = {}

    if args.regen_pins:
        for fx in sorted(args.out.glob("*.cap.json")):
            pins[fx.name[:-len(".cap.json")]] = verdict_pin(build_from_fixture(fx))
    else:
        if not args.src:
            print("--src required unless --regen-pins"); return 2
        for name in names:
            src = args.src / CORPUS_SRC.get(name, f"{name}.redacted.wacz")
            if not src.exists():
                print(f"  skip (missing): {src}"); continue
            dist = distill(_load_capture(src))
            dest = args.out / f"{name}.cap.json"
            write_fixture(dist, dest)
            full = verdict_pin(btw.build_template(src))
            got = verdict_pin(build_from_fixture(dest))
            tag = "" if full == got else "  (distill-reduced: " + \
                ",".join(k for k in full if full[k] != got[k]) + ")"
            pins[name] = got
            print(f"  {name:12} {got['site_type']:22} {got['recommended_path']:18} "
                  f"rend={got['rendition_count']}{tag}")

    (args.out / "expected_verdicts.json").write_text(
        json.dumps(pins, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(pins)} pins -> {args.out / 'expected_verdicts.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
