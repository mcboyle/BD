#!/usr/bin/env python3
"""rotate_token.py — v3.56 (Phase 8, #79): rotate the API bearer token.

Bulk Downloader's optional bearer-token auth (Phase 22.4) stores the
token in app_config.json under `auth_token`, or reads it from the
BD_AUTH_TOKEN env var. This script rotates the file-stored token:

  1. Reads the existing app_config.json (if any).
  2. Generates a fresh 32-byte URL-safe token.
  3. Atomically writes it back into `auth_token`.
  4. Prints the new token so the operator can update bdctl / clients.

It does NOT touch a BD_AUTH_TOKEN env override — if that's set it wins
over the file, and rotating the file would have no effect; the script
detects that case and warns.

Usage:
    python tools/rotate_token.py [--config PATH] [--print-only]

    --config PATH   app_config.json location (default: ./app_config.json)
    --print-only    generate + print a token but DON'T write the file
                    (useful for seeding BD_AUTH_TOKEN instead)

After rotating, restart Bulk Downloader for the new token to take
effect, and run `bdctl token set <new-token>` (or update BD_URL clients).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile


def _generate_token() -> str:
    """32 bytes of URL-safe randomness — ~43 chars, 256 bits entropy."""
    return secrets.token_urlsafe(32)


def _atomic_write_json(path: str, data: dict) -> None:
    """Write JSON to `path` via a temp file + os.replace, so a crash
    mid-write can never leave a truncated app_config.json. Mirrors the
    atomic-write discipline the app itself uses."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".rotate_token_",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Rotate the Bulk Downloader API bearer token.")
    p.add_argument("--config", default="app_config.json",
                   help="app_config.json path (default: ./app_config.json)")
    p.add_argument("--print-only", action="store_true",
                   help="generate + print a token but don't write the file")
    args = p.parse_args(argv)

    new_token = _generate_token()

    if args.print_only:
        print(new_token)
        print()
        print("  (not written — use this to set BD_AUTH_TOKEN, e.g.:)")
        print(f"  export BD_AUTH_TOKEN={new_token}")
        return 0

    # Warn if an env override would shadow the file we're about to write
    if os.environ.get("BD_AUTH_TOKEN"):
        print("  WARNING: BD_AUTH_TOKEN is set in the environment.",
              file=sys.stderr)
        print("  The env var wins over app_config.json — rotating the",
              file=sys.stderr)
        print("  file will have NO effect until you unset BD_AUTH_TOKEN.",
              file=sys.stderr)
        print(file=sys.stderr)

    # Load existing config (preserve every other key)
    config: dict = {}
    if os.path.exists(args.config):
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                print(f"  ERROR: {args.config} is not a JSON object.",
                      file=sys.stderr)
                return 1
        except json.JSONDecodeError as e:
            print(f"  ERROR: {args.config} is malformed JSON: {e}",
                  file=sys.stderr)
            return 1
        except OSError as e:
            print(f"  ERROR: can't read {args.config}: {e}",
                  file=sys.stderr)
            return 1

    had_token = bool(config.get("auth_token"))
    config["auth_token"] = new_token

    try:
        _atomic_write_json(args.config, config)
    except Exception as e:
        print(f"  ERROR: write failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1

    verb = "rotated" if had_token else "set"
    print(f"  Token {verb} in {args.config}")
    print(f"  New token: {new_token}")
    print()
    print("  Next steps:")
    print("    1. Restart Bulk Downloader for the new token to apply.")
    print("    2. Update clients — e.g.  bdctl token set "
          f"{new_token}")
    print("    3. Any old bdctl/extension tokens are now invalid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
