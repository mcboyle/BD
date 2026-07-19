#!/usr/bin/env python3
"""ct1_synth.py — C-T1 capture synthesis CLI (offline, detect-side).

Given two bd-recon capture files of the SAME download action (as written
by ``tools/capture_session.py``), run the static 2-capture synthesizer
and print a human-reviewable config — PLAN Stage 8 "operator
confirmation": which params vary and where they come from, which
credentials the flow requires, which requests are dropped as
session-specific noise.

This tool never touches the network and never replays anything. It does
not write into ``sites_config.json``; folding an approved synthesis into
a live site is a separate, deliberate step (the synthesized flow is its
own artifact — see ``bulk_downloader/capture_synth.py``).

Usage:
    python3 tools/ct1_synth.py CAP_A.json CAP_B.json
    python3 tools/ct1_synth.py CAP_A.json CAP_B.json --json
    python3 tools/ct1_synth.py CAP_A.json CAP_B.json \\
        --site-config sites_config.json --site-id mysite
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Repo-root bootstrap so `bulk_downloader` imports whether run from the
# repo root or from tools/.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bulk_downloader.capture_synth import synthesize, cross_check  # noqa: E402


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _render(synth: dict) -> str:
    out = []
    out.append(f"C-T1 synthesis review  (capture_synth v"
               f"{synth.get('capture_synth_version')})")
    out.append(f"host: {synth.get('host')}   "
               f"confidence: {synth.get('confidence')}   "
               f"needs_review: {'yes' if synth.get('needs_review') else 'no'}")
    out.append(f"entry: {synth.get('entry_url')}")
    out.append(f"summary: {synth.get('summary')}")
    out.append("")

    reqs = synth.get("requests") or []
    out.append(f"requests ({len(reqs)}):")
    for r in reqs:
        tag = r.get("classification", "")
        if r.get("goal"):
            tag += " GOAL"
        if r.get("is_media"):
            tag += " [media]"
        out.append(f"  [{r.get('seq')}] {r.get('method')}  {tag.strip()}  "
                   f"{r.get('key')}")
        out.append(f"        template: {r.get('url_template')}")
        for p in (r.get("params") or []):
            cred = "  (credential)" if p.get("credential") else ""
            out.append(f"          - {p.get('key')}={p.get('type')}  "
                       f"source={p.get('source')}{cred}")
        ch = r.get("credential_headers") or []
        if ch:
            out.append(f"        credential headers: {', '.join(ch)}")
    out.append("")

    creds = synth.get("credentials_required") or []
    out.append(f"credentials required ({len(creds)}): "
               f"{', '.join(creds) if creds else '(none)'}")

    unres = synth.get("unresolved") or []
    out.append(f"unresolved ({len(unres)}):")
    for u in unres:
        out.append(f"  - {u.get('request')} / {u.get('param')}: "
                   f"{u.get('reason')}")

    ss = synth.get("session_specific") or {}
    out.append(f"session-specific (excluded): "
               f"only_in_a={ss.get('only_in_a')} "
               f"only_in_b={ss.get('only_in_b')}")

    out.append("notes:")
    for n in (synth.get("notes") or []):
        out.append(f"  - {n}")
    return "\n".join(out)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="C-T1 static 2-capture synthesis (offline, detect-side)")
    p.add_argument("cap_a", help="first capture JSON (bd-recon format)")
    p.add_argument("cap_b", help="second capture JSON of the same action")
    p.add_argument("--json", action="store_true",
                   help="emit raw synthesized config JSON instead of review")
    p.add_argument("--site-config",
                   help="optional sites_config.json for a structural "
                        "cross-check")
    p.add_argument("--site-id",
                   help="site id (key) within --site-config to check against")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        cap_a, cap_b = _load(args.cap_a), _load(args.cap_b)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"error reading capture: {e}\n")
        return 2

    synth = synthesize(cap_a, cap_b)

    if args.json:
        print(json.dumps(synth, indent=2, ensure_ascii=False))
    else:
        print(_render(synth))

    if args.site_config:
        if not args.site_id:
            sys.stderr.write("--site-config requires --site-id\n")
            return 2
        try:
            cfg = _load(args.site_config)
        except (OSError, ValueError) as e:
            sys.stderr.write(f"error reading site config: {e}\n")
            return 2
        entry = cfg.get(args.site_id)
        if not isinstance(entry, dict):
            sys.stderr.write(f"site id '{args.site_id}' not found in config\n")
            return 2
        rep = cross_check(synth, entry)
        print("")
        print(f"cross-check vs '{args.site_id}': "
              f"host_match={rep['host_match']} "
              f"({rep['synth_host']} vs {rep['config_host']}; "
              f"{rep['checked']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
