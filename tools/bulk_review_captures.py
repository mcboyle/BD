#!/usr/bin/env python3
"""bulk_review_captures.py -- build review candidates from cockpit_tasks/*.wacz.

Walks a task tree (default: ./cockpit_tasks), and for every ORIGINAL .wacz
(skipping *.redacted.wacz siblings) runs the SAME two steps the cockpit's
/api/captures/normalize endpoint runs:

    draft = build_template_from_wacz.build_template(wacz)   # offline parse
    cand  = template_normalize.normalize_draft(draft)       # scrub + shape

then writes templates/review_candidates/<host>.candidate.json -- the review
queue the Template Manager shows under "Review candidates". normalize_draft
scrubs signing material and never emits status="enabled".

Host-collision policy: many captures map to one host. We keep the RICHEST per
host (most selectors), never blindly last-write-wins -- mirrors the builder's
gold-merge-guard spirit. An EXISTING candidate on disk is only overwritten when
the new one has strictly more selectors.

DRY-RUN BY DEFAULT: prints the file->host collapse and what WOULD be written.
Pass --write to actually write. Per-file errors are caught (one bad .wacz never
aborts the batch).

Usage:
    PYTHONPATH=$PWD venv/bin/python tools/bulk_review_captures.py            # preview
    PYTHONPATH=$PWD venv/bin/python tools/bulk_review_captures.py --write    # commit
    PYTHONPATH=$PWD venv/bin/python tools/bulk_review_captures.py --root cockpit_tasks --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# Same buckets as organize_captures.py (verified 250/250 on stash). REFERENCE =
# player-vendor demo rigs (correct host, but a format exemplar, not a site).
# TELEMETRY = analytics/widget hosts that usually mean a mis-attributed recorded
# URL. By default we build candidates only for real SITES and skip both, so the
# review queue isn't cluttered with demos and known-wrong hosts.
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
    h = (host or "").lower()
    if any(s in h for s in _REFERENCE_HOST_SUBSTR):
        return "reference"
    if any(s in h for s in _TELEMETRY_HOST_SUBSTR):
        return "suspect"
    return "site"


def _selector_count(cand: dict) -> int:
    sels = cand.get("selectors") or {}
    return sum(1 for v in sels.values() if v)


def _captured_at(cand: dict) -> str:
    return ((cand.get("source") or {}).get("captured_at")) or ""


def _cheap_host(wacz_path) -> str | None:
    """Read the recorded host from a .wacz WITHOUT the full build_template parse,
    so --host can skip non-matching captures cheaply. Reads archive/capture.json's
    'host' (the same field build_template lands on), falling back to the first page
    URL in pages/pages.jsonl. Returns None if it can't be determined cheaply (caller
    then falls through to a full parse + the post-parse host check, so nothing is
    ever wrongly skipped)."""
    import zipfile
    from urllib.parse import urlparse
    try:
        z = zipfile.ZipFile(str(wacz_path))
    except Exception:  # noqa: BLE001
        return None
    try:
        names = set(z.namelist())
        if "archive/capture.json" in names:
            try:
                d = json.loads(z.read("archive/capture.json").decode("utf-8", "replace"))
                if d.get("host"):
                    return d["host"]
                u = d.get("url") or d.get("origin")
                if u:
                    return urlparse(u).netloc
            except Exception:  # noqa: BLE001
                pass
        if "pages/pages.jsonl" in names:
            try:
                for line in z.read("pages/pages.jsonl").decode("utf-8", "replace").splitlines():
                    try:
                        u = json.loads(line).get("url")
                    except Exception:  # noqa: BLE001
                        continue
                    if u:
                        return urlparse(u).netloc
            except Exception:  # noqa: BLE001
                pass
    finally:
        z.close()
    return None


def _parse_dt(s: str):
    try:
        return datetime.fromisoformat(str(s))
    except Exception:  # noqa: BLE001
        return None


# Selector stability heuristic. STRONG = stable hooks (id/name/aria/data-*);
# WEAK = positional/structural (nth-child, first/last) that break on any DOM
# reshuffle; everything else (class, bare tag) = MEDIUM. Heuristic, not gospel --
# it FLAGS brittle selectors for review, it does not reject them.
_STRONG = ("#", "[id=", "[id*=", "[id^=", "[aria-", "[data-", "[name=")
_WEAK = (":nth-child", ":nth-of-type", ":first-child", ":last-child",
         ":nth-last-child")


def _selector_tier(sel: str) -> str:
    s = sel or ""
    low = s.lower()
    if any(t in low for t in _WEAK):
        return "weak"
    if any(t in s for t in _STRONG):
        return "strong"
    if "." in s or "[" in s:
        return "medium"
    if s and not any(ch in s for ch in ".#[:>+~ "):
        return "medium"          # a bare tag like "video" -- fine for a container
    return "medium"


def _walk_selectors(sel: dict):
    for cat, val in (sel or {}).items():
        if isinstance(val, dict):
            for leaf, lv in val.items():
                if isinstance(lv, str) and lv:
                    yield (f"{cat}.{leaf}", lv)
        elif isinstance(val, str) and val:
            yield (cat, val)
        elif isinstance(val, list):
            for i, lv in enumerate(val):
                if isinstance(lv, str) and lv:
                    yield (f"{cat}[{i}]", lv)


def _score_selectors(sel: dict) -> dict:
    q = {"strong": [], "medium": [], "weak": []}
    for path, v in _walk_selectors(sel):
        q[_selector_tier(v)].append(path)
    return q


def _merge_host_candidates(entries: list, window_days: int):
    """Consensus merge across all captures of one host:
      - SELECTORS: tally every value seen per leaf path across captures; the
        majority value wins (tiebreak: newest captured_at). Pure newest-wins is
        the degenerate 1-vote-each case. Losing values kept in merge_alternatives
        with vote counts. A disagreement whose captures span MORE than the window
        is flagged (likely site drift) and downgrades to draft_review_required.
      - UNION (additive, dedup): resolutions, media_hosts, observed_api_hosts,
        network_patterns.
      - GAP-FILL: any selector path present in a sibling but not the newest
        capture is recovered (counted in gaps_filled).
      - MODAL-ROW RECOVERY: row selectors the builder DROPPED as unsafe/not
        modal-scoped (recorded in each capture's warnings) are surfaced in
        modal_row_candidates for a human to confirm + scope -- never auto-used.
      - STABILITY: every merged selector is scored strong/medium/weak;
        selector_quality lists them and weak ones are called out in review_notes.
      - Session-specific fields (workflow/trigger/verify) come from the newest
        capture, not merged.
    Returns (selector_count, merged). entries: [{cand,n,src}], len >= 2.
    """
    from collections import defaultdict
    floor = datetime.min.replace(tzinfo=timezone.utc)

    def dt_of(c):
        return _parse_dt(_captured_at(c)) or floor

    ents = sorted(entries, key=lambda e: dt_of(e["cand"]), reverse=True)
    newest = ents[0]["cand"]
    base = json.loads(json.dumps(newest))      # non-selector fields from newest

    votes: dict = defaultdict(list)            # (cat, leaf|None) -> [(val, dt, cf)]
    cat_kind: dict = {}                        # cat -> "dict" | "scalar"
    contributors: list = []
    modal_rows: list = []

    for e in ents:
        c = e["cand"]
        d = dt_of(c)
        src = c.get("source") or {}
        cf = src.get("capture_file") or getattr(e["src"], "name", str(e["src"]))
        contributors.append({"capture_file": cf,
                             "sha256": src.get("capture_sha256", ""),
                             "captured_at": _captured_at(c)})
        for cat, val in (c.get("selectors") or {}).items():
            if isinstance(val, dict):
                cat_kind.setdefault(cat, "dict")
                for leaf, lv in val.items():
                    if isinstance(lv, str) and lv:
                        votes[(cat, leaf)].append((lv, d, cf))
            elif isinstance(val, str) and val:
                cat_kind.setdefault(cat, "scalar")
                votes[(cat, None)].append((val, d, cf))
        for wmsg in (c.get("warnings") or []):
            if "dropped row selector" in str(wmsg):
                frag = str(wmsg).split(":", 1)[-1].strip()
                if frag and frag not in [m["selector"] for m in modal_rows]:
                    modal_rows.append({"selector": frag, "from": cf,
                                       "reason": "dropped at build as not "
                                       "modal-scoped/unsafe -- confirm + scope "
                                       "in review before enabling"})

    newest_paths = set()
    for cat, val in (newest.get("selectors") or {}).items():
        if isinstance(val, dict):
            for leaf in val:
                newest_paths.add((cat, leaf))
        else:
            newest_paths.add((cat, None))

    merged_sel: dict = {}
    alts: list = []
    conflicts = flagged = gaps = 0
    for (cat, leaf), vlist in votes.items():
        tally: dict = {}
        for v, d, cf in vlist:
            t = tally.setdefault(v, {"n": 0, "dt": floor})
            t["n"] += 1
            if d > t["dt"]:
                t["dt"] = d
        winner = max(tally.items(), key=lambda kv: (kv[1]["n"], kv[1]["dt"]))[0]
        if cat_kind.get(cat) == "dict":
            merged_sel.setdefault(cat, {})[leaf] = winner
        else:
            merged_sel[cat] = winner
        if (cat, leaf) not in newest_paths:
            gaps += 1
        if len(tally) > 1:
            conflicts += 1
            dts = [t["dt"] for t in tally.values() if t["dt"] != floor]
            span = (max(dts) - min(dts)).days if len(dts) > 1 else 0
            f = span > window_days
            if f:
                flagged += 1
            path = f"selectors.{cat}" + (f".{leaf}" if leaf else "")
            losers = [{"value": v, "votes": t["n"]}
                      for v, t in tally.items() if v != winner]
            alts.append({"path": path, "kept": winner,
                         "kept_votes": tally[winner]["n"],
                         "alternatives": losers, "flagged": bool(f)})

    base["selectors"] = merged_sel

    def _union(key, numeric=False):
        seen: list = []
        for e in ents:
            for v in (e["cand"].get(key) or []):
                if v not in seen:
                    seen.append(v)
        if seen:
            base[key] = sorted(set(seen), reverse=True) if numeric else seen

    _union("media_hosts")
    _union("observed_api_hosts")
    _union("network_patterns")
    _union("resolutions", numeric=True)

    seen_c: set = set()
    uniq_c: list = []
    for c in contributors:
        k = (c["capture_file"], c["sha256"])
        if k not in seen_c:
            seen_c.add(k)
            uniq_c.append(c)

    quality = _score_selectors(merged_sel)
    base["merged_from"] = uniq_c
    base["merge_alternatives"] = alts
    base["modal_row_candidates"] = modal_rows
    base["selector_quality"] = quality
    base["merge_stats"] = {"contributors": len(uniq_c), "conflicts": conflicts,
                           "flagged": flagged, "gaps_filled": gaps,
                           "weak_selectors": len(quality["weak"]),
                           "recovered_rows": len(modal_rows)}
    notes = base.setdefault("review_notes", [])
    if isinstance(notes, list):
        notes.append(f"MERGED from {len(uniq_c)} captures via consensus "
                     f"({gaps} gap(s) filled).")
        if quality["weak"]:
            notes.append(f"{len(quality['weak'])} brittle selector(s) "
                         f"(positional/nth-child) -- verify: "
                         f"{', '.join(quality['weak'][:4])}.")
        if modal_rows:
            notes.append(f"{len(modal_rows)} modal-row selector(s) recovered from "
                         f"build warnings -- confirm + scope before enabling.")
        if flagged:
            notes.append(f"{flagged} field(s) disagree across captures spanning "
                         f">{window_days} days (likely drift) -- pick the right "
                         f"value (see merge_alternatives).")
    if flagged:
        base["status"] = "draft_review_required"
    return _selector_count(base), base


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("cockpit_tasks"),
                    help="task tree to walk (default: cockpit_tasks)")
    ap.add_argument("--outdir", type=Path,
                    default=Path("templates") / "review_candidates",
                    help="where candidates are written")
    ap.add_argument("--write", action="store_true",
                    help="actually write (default is a dry-run preview)")
    ap.add_argument("--include-reference", action="store_true",
                    help="also build candidates for player-vendor demo rigs "
                         "(default: skip them)")
    ap.add_argument("--include-suspect", action="store_true",
                    help="also build candidates for telemetry/widget hosts that "
                         "are likely mis-attributed (default: skip them)")
    ap.add_argument("--summary", action="store_true",
                    help="print a status breakdown (review_ready vs "
                         "draft_review_required) + sel=0 duds at the end")
    ap.add_argument("--merge-host", action="store_true",
                    help="merge ALL captures of a host into one candidate "
                         "(union safe fields, newest-wins on conflict, "
                         "alternatives preserved). Default: keep richest only.")
    ap.add_argument("--merge-window-days", type=int, default=30,
                    help="conflicting selectors within this captured_at window "
                         "are a benign supersede; outside it they're flagged "
                         "(default: 30)")
    ap.add_argument("--host", default=None,
                    help="only process captures for this exact host "
                         "(handy to dry-run the merge on one site)")
    args = ap.parse_args()

    from tools import build_template_from_wacz as wb
    from bulk_downloader import template_normalize as tn

    waczs = sorted(p for p in args.root.rglob("*.wacz")
                   if not p.name.endswith(".redacted.wacz"))
    if not waczs:
        print(f"no original .wacz found under {args.root}", file=sys.stderr)
        return 1

    print(f"found {len(waczs)} original .wacz under {args.root} "
          f"({'WRITE' if args.write else 'dry-run'})\n")

    # host -> (selector_count, candidate_dict, source_path)
    best: dict[str, tuple[int, dict, object]] = {}
    per_host: dict[str, list[dict]] = {}
    errors: list[tuple[Path, str]] = []
    skipped_bucket = {"reference": 0, "suspect": 0}

    for w in waczs:
        if args.host:
            ch = _cheap_host(w)            # cheap skip without full parse
            if ch is not None and ch != args.host:
                continue
        try:
            draft = wb.build_template(w)
            cand = tn.normalize_draft(draft)
        except Exception as e:  # noqa: BLE001 - one bad capture must not abort
            errors.append((w, str(e)[:160]))
            print(f"  ERR  {w}  ->  {str(e)[:80]}")
            continue
        host = (cand.get("host")
                or (draft.get("source") or {}).get("host") or "unknown")
        if args.host and host != args.host:
            continue
        bucket = _bucket(host)
        if bucket == "reference" and not args.include_reference:
            skipped_bucket["reference"] += 1
            print(f"  skip(ref)  {host:<28} {w.name}")
            continue
        if bucket == "suspect" and not args.include_suspect:
            skipped_bucket["suspect"] += 1
            print(f"  skip(susp) {host:<28} {w.name}  (mis-attributed host?)")
            continue
        n = _selector_count(cand)
        status = cand.get("status", "?")
        per_host.setdefault(host, []).append({"cand": cand, "n": n, "src": w})
        if args.merge_host:
            print(f"  cap   {host:<28} sel={n:<2} {status:<22} {w.name}")
        else:
            prev = best.get(host)
            if prev is None or n > prev[0]:
                best[host] = (n, cand, w)
                flag = "keep " if prev is None else "BEAT "
            else:
                flag = "skip "
            print(f"  {flag} {host:<28} sel={n:<2} {status:<22} {w.name}")

    if args.merge_host:
        print("\n-- MERGE (--merge-host) --")
        for host, entries in sorted(per_host.items()):
            if len(entries) == 1:
                e = entries[0]
                best[host] = (e["n"], e["cand"], e["src"])
                continue
            n, merged = _merge_host_candidates(entries, args.merge_window_days)
            best[host] = (n, merged, f"MERGED({len(entries)})")
            ms = merged.get("merge_stats", {})
            print(f"  {host:<28} {len(entries)} caps -> sel={n:<2} "
                  f"gaps+{ms.get('gaps_filled', 0)} "
                  f"conflicts={ms.get('conflicts', 0)} "
                  f"flagged={ms.get('flagged', 0)} "
                  f"weak={ms.get('weak_selectors', 0)} "
                  f"rows+{ms.get('recovered_rows', 0)} -> {merged.get('status')}")

    print(f"\n{len(best)} distinct SITE hosts from {len(waczs)} captures "
          f"({len(errors)} errors; skipped {skipped_bucket['reference']} reference"
          f" + {skipped_bucket['suspect']} suspect)")

    if args.summary:
        by_status: dict[str, list[str]] = {}
        duds: list[str] = []
        for host, (n, cand, src) in best.items():
            by_status.setdefault(cand.get("status", "?"), []).append(host)
            if n == 0:
                duds.append(host)
        print("\n-- SUMMARY --")
        for status in sorted(by_status):
            print(f"  {len(by_status[status]):>3}  {status}")
        ready = sorted(by_status.get("review_ready", []))
        if ready:
            print(f"\nreview_ready ({len(ready)}) -- approve-on-sight candidates:")
            for h in ready:
                print(f"  {h}")
        if duds:
            print(f"\nsel=0 duds ({len(duds)}) -- empty captures, consider deleting "
                  f"the candidate:")
            for h in duds:
                print(f"  {h}")

    if not args.write:
        print("\nDRY-RUN -- nothing written. Re-run with --write to commit "
              "the candidates above.")
        return 0

    args.outdir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for host, (n, cand, src) in sorted(best.items()):
        safe_host = re.sub(r"[^A-Za-z0-9._-]+", "_", str(host))
        if safe_host.strip(".") == "":
            safe_host = "unknown"
        outp = args.outdir / f"{safe_host}.candidate.json"
        if outp.exists():
            try:
                existing = json.loads(outp.read_text(encoding="utf-8"))
                if _selector_count(existing) >= n:
                    skipped += 1
                    print(f"  skip(disk) {host}  (on-disk candidate is >= richer)")
                    continue
            except Exception:  # noqa: BLE001 - unreadable -> just overwrite
                pass
        outp.write_text(json.dumps(cand, indent=2), encoding="utf-8")
        written += 1
        print(f"  wrote {outp}  (sel={n}, from {getattr(src, 'name', str(src))})")

    print(f"\nDONE: {written} written, {skipped} skipped (on-disk richer), "
          f"{len(errors)} errors -> {args.outdir}")
    if errors:
        print("\nERRORS:")
        for w, e in errors:
            print(f"  {w}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
