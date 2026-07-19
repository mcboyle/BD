#!/usr/bin/env python3
"""organize_captures.py -- inventory + organize .wacz captures by website / format.

Reads the UNREDACTED capture content (host + recognition: player family,
delivery, protocol) via build_template_from_wacz.build_template, then:

  1. ALWAYS writes a CSV manifest (one row per capture -- a full inventory,
     NOT collapsed by host).
  2. OPTIONALLY builds a browsable tree under --organize DIR:
         <DIR>/<host>/<format>/<task_id>_<orig_name>.wacz
     as SYMLINKS by default (zero disk, originals untouched), or real copies
     with --copy, or hardlinks with --hardlink.

NON-DESTRUCTIVE BY DESIGN. It never renames or moves your originals in place --
each cockpit_tasks/<tid>/out/<name>.wacz is paired with a <name>.redacted.wacz
sibling and referenced by task id, so in-place renaming would break those. The
tree points AT the originals instead.

FORMAT signal comes from the network log inside the UNREDACTED .wacz. The
*.redacted.wacz siblings have that log stripped, so format reads 'unknown' on
them -- which is exactly why this skips them unless you pass --include-redacted.

format_label fallback chain (first hit wins):
    player_family  ->  primary_protocol  ->  delivery
                   ->  inferred from media/api extensions (hls/dash/progressive)
                   ->  'unknown'

Usage:
    PYTHONPATH=$PWD venv/bin/python tools/organize_captures.py                       # inventory CSV only
    PYTHONPATH=$PWD venv/bin/python tools/organize_captures.py --organize organized  # + symlink tree
    PYTHONPATH=$PWD venv/bin/python tools/organize_captures.py --organize organized --copy
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

_HLS = re.compile(r"\.m3u8(\?|$)", re.I)
_DASH = re.compile(r"\.mpd(\?|$)", re.I)
_PROG = re.compile(r"\.(mp4|m4s|ts|webm|mov)(\?|$)", re.I)


# Two kinds of host that aren't a real target site, kept SEPARATE because they
# mean opposite things:
#
# REFERENCE -- player-vendor demo/docs/playground domains. A capture here is a
#   GENUINE per-format reference rig you made on purpose (bitmovin's own demo,
#   vidstack.io, the shaka demo, a CodePen with hls.js). The host is correct;
#   it's just not a "site" -- it's a format exemplar.
#
# TELEMETRY -- analytics/CDN/support-widget domains. A capture whose recorded
#   page URL resolves here is almost always MIS-ATTRIBUTED: the recorded URL was
#   a third-party beacon/embed, not the site (e.g. a sports capture that reads
#   "newrelic.com"). These want a host fix before they become a candidate.
_REFERENCE_HOST_SUBSTR = (
    "bitmovin.com", "vidstack.io", "player.style", "jwplayer.com",
    "theoplayer.com", "flowplayer.com", "plyr.io", "artplayer.org",
    "video-dev.org", "shaka-player", "-demo.appspot.com", "demo.appspot",
    "codepen.io", "jsfiddle", "jsbin", "stackblitz", "glitch.me",
    "codesandbox.io",
)
_TELEMETRY_HOST_SUBSTR = (
    "newrelic", "google-analytics", "googletagmanager", "doubleclick",
    "sentry", "cloudflare", "cdnjs", "tsyndicate", "segment.io", "segment.com",
    "freshworks", "freshchat", "zendesk", "zdassets", "intercom", "drift.com",
    "hotjar", "optimizely", "mixpanel", "amplitude", "fullstory",
)


def _bucket(host: str) -> str:
    """Classify a host as 'reference' (player demo rig), 'suspect' (telemetry/
    widget mis-attribution), or 'site' (a plausible real target)."""
    h = (host or "").lower()
    if any(s in h for s in _REFERENCE_HOST_SUBSTR):
        return "reference"
    if any(s in h for s in _TELEMETRY_HOST_SUBSTR):
        return "suspect"
    return "site"


def _suspect_host(host: str) -> str:
    """Matched substring for the console flag (either bucket), or '' for a site.
    Both reference and telemetry hits get the '<-- SUSPECT' eyeball marker; the
    bucket column says which kind."""
    h = (host or "").lower()
    for s in _REFERENCE_HOST_SUBSTR + _TELEMETRY_HOST_SUBSTR:
        if s in h:
            return s
    return ""


def _fs_safe(s: str) -> str:
    """Make a host/format token safe as a single path segment."""
    s = (s or "").strip() or "unknown"
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    # A dot-only token ('.', '..', '...') is a traversal / self-reference
    # segment, not a name -> never emit it as a path component (F-TOOLSO04-02).
    if s.strip(".") == "":
        return "unknown"
    return s


def _infer_media_format(draft: dict) -> str | None:
    """Last-resort format from observed media/api URL extensions."""
    nd = draft.get("network_discovery") or {}
    blobs = []
    for key in ("media_patterns", "api_patterns"):
        for p in nd.get(key) or []:
            blobs.append(p if isinstance(p, str) else str(p))
    text = " ".join(blobs)
    if _HLS.search(text):
        return "hls"
    if _DASH.search(text):
        return "dash"
    if _PROG.search(text):
        return "progressive"
    return None


def _format_label(draft: dict) -> tuple[str, dict]:
    """Return (label, raw_signals) for a built draft."""
    rec = draft.get("recognition") or {}
    raw = {
        "player_family": rec.get("player_family"),
        "delivery": rec.get("delivery"),
        "primary_protocol": rec.get("primary_protocol"),
        "inferred": _infer_media_format(draft),
    }
    for key in ("player_family", "primary_protocol", "delivery", "inferred"):
        v = raw.get(key)
        if v:
            return str(v), raw
    return "unknown", raw


def _selector_count(draft: dict) -> int:
    sels = draft.get("selectors") or {}
    return sum(1 for v in sels.values() if v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("cockpit_tasks"))
    ap.add_argument("--manifest", type=Path, default=Path("captures_manifest.csv"))
    ap.add_argument("--organize", type=Path, default=None,
                    help="also build host/format tree under this dir")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--copy", action="store_true", help="real file copies (costs disk)")
    g.add_argument("--hardlink", action="store_true", help="hardlinks (same filesystem)")
    ap.add_argument("--include-redacted", action="store_true",
                    help="also process *.redacted.wacz (format will read 'unknown')")
    ap.add_argument("--split-reference", action="store_true",
                    help="in the --organize tree, route player-vendor demo rigs to "
                         "_reference/<format>/ and telemetry mis-attributions to "
                         "_suspect/<host>/, leaving real sites at <host>/<format>/")
    args = ap.parse_args()

    from tools import build_template_from_wacz as wb

    waczs = sorted(p for p in args.root.rglob("*.wacz")
                   if args.include_redacted or not p.name.endswith(".redacted.wacz"))
    if not waczs:
        print(f"no .wacz found under {args.root}", file=sys.stderr)
        return 1

    print(f"inventorying {len(waczs)} captures under {args.root}\n")

    rows = []
    by_host = Counter()
    by_format = Counter()
    by_bucket = Counter()
    errors = []

    for w in waczs:
        tid = w.parent.parent.name  # cockpit_tasks/<tid>/out/<name>.wacz
        try:
            draft = wb.build_template(w)
        except Exception as e:  # noqa: BLE001
            errors.append((w, str(e)[:140]))
            print(f"  ERR  {w.name}: {str(e)[:70]}")
            continue
        host = (draft.get("source") or {}).get("host") or "unknown"
        rec_url = (draft.get("source") or {}).get("url_no_query") or ""
        suspect = _suspect_host(host)
        bucket = _bucket(host)
        fmt, raw = _format_label(draft)
        nsel = _selector_count(draft)
        conf = draft.get("confidence")
        try:
            size = w.stat().st_size
        except OSError:
            size = -1
        rows.append({
            "path": str(w), "task_id": tid, "orig_name": w.name,
            "host": host, "format": fmt, "bucket": bucket, "suspect_host": suspect,
            "recorded_url": rec_url,
            "player_family": raw["player_family"] or "",
            "delivery": raw["delivery"] or "",
            "protocol": raw["primary_protocol"] or "",
            "inferred": raw["inferred"] or "",
            "selectors": nsel, "confidence": conf, "size_bytes": size,
        })
        by_host[host] += 1
        by_format[fmt] += 1
        by_bucket[bucket] += 1
        mark = f"  <-- {bucket.upper()}" if suspect else ""
        print(f"  {host:<26} {fmt:<14} sel={nsel:<2} {w.name}{mark}")
        if mark:
            print(f"      recorded_url={rec_url[:90]}  (matched: {suspect})")

    # manifest
    with args.manifest.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                            ["path", "task_id", "orig_name", "host", "format"])
        wr.writeheader()
        wr.writerows(rows)
    print(f"\nmanifest: {args.manifest}  ({len(rows)} rows, {len(errors)} errors)")

    print("\nby website:")
    for h, n in by_host.most_common():
        print(f"  {n:>3}  {h}")
    print("\nby format:")
    for f, n in by_format.most_common():
        print(f"  {n:>3}  {f}")
    print("\nby bucket:")
    for b in ("site", "reference", "suspect"):
        print(f"  {by_bucket.get(b, 0):>3}  {b}")

    refs = [r for r in rows if r["bucket"] == "reference"]
    susp = [r for r in rows if r["bucket"] == "suspect"]
    if refs:
        print(f"\nREFERENCE rigs ({len(refs)}) -- player-vendor demos, host is "
              f"correct (not a real site). --split-reference routes these to "
              f"_reference/<format>/:")
        for r in refs:
            print(f"  {r['orig_name']:<22} {r['host']:<34} {r['format']}")
    if susp:
        print(f"\nSUSPECT mis-attributions ({len(susp)}) -- telemetry/widget host, "
              f"likely WRONG. Fix the host before promoting. --split-reference "
              f"quarantines these to _suspect/<host>/:")
        for r in susp:
            print(f"  {r['orig_name']:<22} {r['host']:<26} recorded={r['recorded_url'][:60]}")

    # organize tree
    if args.organize:
        mode = "copy" if args.copy else "hardlink" if args.hardlink else "symlink"
        made = 0
        for r in rows:
            if args.split_reference and r["bucket"] == "reference":
                dest_dir = args.organize / "_reference" / _fs_safe(r["format"])
            elif args.split_reference and r["bucket"] == "suspect":
                dest_dir = args.organize / "_suspect" / _fs_safe(r["host"])
            else:
                dest_dir = args.organize / _fs_safe(r["host"]) / _fs_safe(r["format"])
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{r['task_id']}_{r['orig_name']}"
            src = Path(r["path"]).resolve()
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            try:
                if mode == "copy":
                    import shutil
                    shutil.copy2(src, dest)
                elif mode == "hardlink":
                    os.link(src, dest)
                else:
                    dest.symlink_to(src)
                made += 1
            except OSError as e:
                print(f"  link-err {dest}: {e}")
        layout = ("<host>/<format>/  (+ _reference/<format>/, _suspect/<host>/)"
                  if args.split_reference else "<host>/<format>/")
        print(f"\norganized {made} captures into {args.organize}/{layout} "
              f"({mode}, originals untouched)")

    if errors:
        print("\nERRORS:")
        for w, e in errors:
            print(f"  {w}: {e}")
    unknown = by_format.get("unknown", 0)
    if unknown:
        print(f"\nNOTE: {unknown} captures resolved format='unknown' -- on an "
              f"UNREDACTED original that usually means the player/protocol "
              f"wasn't recognized; on a redacted one it's expected (no network log).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
