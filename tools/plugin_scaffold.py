#!/usr/bin/env python3
"""tools/plugin_scaffold.py -- PLG-5: `bd plugin new <kind>` scaffolder.

plugins.write_config edits plugins.json (the enable/order/full-access knobs); it
does NOT create a new plugin. This scaffolds a ready-to-edit plugin MODULE for any
of the host's extension kinds -- a valid PLUGIN manifest (api_version in the host's
supported range) plus a correctly-signed, decorated stub function -- so an author
starts from something that loads instead of a blank file.

Kinds (see bulk_downloader/plugins.py):
  extractor        @extractor("site")      fn(url, context) -> dict | None
  hook             @hook("event")          fn(payload)
  processor        @processor(priority=)   fn(payload) -> dict | None
  config_provider  @config_provider(...)   fn(site_id, cfg) -> dict
  lifecycle        @lifecycle("event")     fn(context)              (GATED: full-access)
  recognizer       @recognizer(priority=)  fn(capture) -> dict | None

Usage:
  python3 tools/plugin_scaffold.py <kind> [name] [--out DIR] [--force]
  python3 tools/plugin_scaffold.py processor plex_refresh --out plugins/
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Kept in sync with plugins.PLUGIN_API_VERSION (2). Overridable via --api-version.
DEFAULT_API_VERSION = 2

# kind -> (decorator_expr, def_signature, return_hint, capability, body)
KIND_SPECS = {
    "extractor": ('@plugins.extractor("example.com")',
                  "def extract(url, context):",
                  "dict | None",
                  "extractor",
                  "    # Return {'url': ...} to contribute a download candidate, or None.\n"
                  "    return None"),
    "hook": ('@plugins.hook("download.done")',
             "def on_event(payload):",
             "None",
             "hook",
             "    # React to a lifecycle event. Return value is ignored.\n"
             "    return None"),
    "processor": ("@plugins.processor(priority=100)",
                  "def process(payload):",
                  "dict | None",
                  "processor",
                  "    # Post-download processing. Return a patch dict or None.\n"
                  "    return None"),
    "config_provider": ("@plugins.config_provider(priority=100)",
                        "def provide(site_id, cfg):",
                        "dict",
                        "config_provider",
                        "    # Layer extra per-site config. Return a dict (may be empty).\n"
                        "    return {}"),
    "lifecycle": ('@plugins.lifecycle("browser.launched")',
                  "def on_lifecycle(context):",
                  "None",
                  "lifecycle",
                  "    # GATED: only fires when full-access is enabled.\n"
                  "    return None"),
    "recognizer": ("@plugins.recognizer(priority=100)",
                   "def recognize(capture):",
                   "dict | None",
                   "recognizer",
                   "    # Inspect a capture; return a recognition verdict dict or None.\n"
                   "    return None"),
}

KNOWN_KINDS = tuple(KIND_SPECS.keys())


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    return s or "my_plugin"


def scaffold(kind: str, *, name: str = None, author: str = "you",
             api_version: int = DEFAULT_API_VERSION) -> str:
    """Return the source text for a new plugin of `kind`. Raises ValueError on an
    unknown kind."""
    if kind not in KIND_SPECS:
        raise ValueError(f"unknown plugin kind {kind!r}; known: {', '.join(KNOWN_KINDS)}")
    decorator, sig, ret_hint, capability, body = KIND_SPECS[kind]
    pname = _slug(name or f"my_{kind}")
    return f'''"""BulkDownloader plugin: {pname} ({kind}).

Scaffolded by tools/plugin_scaffold.py. Edit the manifest + the stub below, drop
this file in the plugin dir, then enable it via the plugin-config panel.
"""
from bulk_downloader import plugins

PLUGIN = {{
    "name": "{pname}",
    "version": "0.1.0",
    "api_version": {int(api_version)},
    "author": "{author}",
    "capabilities": ["{capability}"],
    "description": "TODO: describe what this {kind} does",
}}


{decorator}
{sig}  # -> {ret_hint}
{body}
'''


def main(argv=None):
    ap = argparse.ArgumentParser(description="PLG-5: scaffold a new BD plugin")
    ap.add_argument("kind", choices=KNOWN_KINDS)
    ap.add_argument("name", nargs="?", default=None)
    ap.add_argument("--out", default=".", help="output directory (default: cwd)")
    ap.add_argument("--author", default="you")
    ap.add_argument("--api-version", type=int, default=DEFAULT_API_VERSION)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing file")
    a = ap.parse_args(argv)
    src = scaffold(a.kind, name=a.name, author=a.author, api_version=a.api_version)
    fname = _slug(a.name or f"my_{a.kind}") + ".py"
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    fp = outdir / fname
    if fp.exists() and not a.force:
        print(f"refusing to overwrite {fp} (use --force)", file=sys.stderr)
        return 1
    fp.write_text(src, encoding="utf-8")
    print(f"wrote {fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
