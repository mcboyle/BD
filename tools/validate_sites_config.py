#!/usr/bin/env python3
"""validate_sites_config.py — v3.59 (Phase 11, #57): config validator.

Validates sites_config.json by running each site config through the
same `site_editor.validate_config` the app uses. Exits 0 if every site
is clean, 1 if any site has errors. Warnings don't fail the run.

Intended use — as a git pre-commit hook. Create `.git/hooks/pre-commit`:

    #!/bin/sh
    python tools/validate_sites_config.py || exit 1

so a commit that would land a broken sites_config.json is blocked.

Usage:
    python tools/validate_sites_config.py [--config PATH] [--quiet]

    --config PATH   sites_config.json location (default: ./sites_config.json)
    --quiet         only print on error

A missing sites_config.json is treated as OK (a fresh checkout has none).
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _load_validator():
    """Import site_editor.validate_config, adding the repo root to
    sys.path so the script works from a git hook (cwd = repo root)."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from bulk_downloader.site_editor import validate_config
    return validate_config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Validate sites_config.json (pre-commit hook).")
    p.add_argument("--config", default="sites_config.json",
                   help="sites_config.json path (default: ./sites_config.json)")
    p.add_argument("--quiet", action="store_true",
                   help="only print output when there's an error")
    args = p.parse_args(argv)

    if not os.path.exists(args.config):
        if not args.quiet:
            print(f"  {args.config} not found — nothing to validate (OK).")
        return 0

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"  ERROR: {args.config} is malformed JSON: {e}",
              file=sys.stderr)
        return 1
    except OSError as e:
        print(f"  ERROR: can't read {args.config}: {e}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print(f"  ERROR: {args.config} must be a JSON object "
              f"(site-id → config).", file=sys.stderr)
        return 1

    try:
        validate_config = _load_validator()
    except Exception as e:
        print(f"  ERROR: can't load the validator: {e}", file=sys.stderr)
        return 1

    total_errors = 0
    total_warnings = 0
    for sid, cfg in data.items():
        if not isinstance(cfg, dict):
            print(f"  [{sid}] ERROR: config is not an object",
                  file=sys.stderr)
            total_errors += 1
            continue
        result = validate_config(cfg)
        errors = result.get("errors", []) or []
        warnings = result.get("warnings", []) or []
        name = cfg.get("name") or sid
        for e in errors:
            print(f"  [{name}] ERROR: {e}", file=sys.stderr)
        if not args.quiet:
            for w in warnings:
                print(f"  [{name}] warning: {w}")
        total_errors += len(errors)
        total_warnings += len(warnings)

    if total_errors:
        print(f"\n  FAILED: {total_errors} error(s) across "
              f"{len(data)} site(s).", file=sys.stderr)
        return 1
    if not args.quiet:
        suffix = (f" ({total_warnings} warning(s))"
                  if total_warnings else "")
        print(f"  OK: {len(data)} site(s) valid{suffix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
