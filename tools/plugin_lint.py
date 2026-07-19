#!/usr/bin/env python3
"""plugin_lint.py -- validate a BulkDownloader plugin before you deploy it.

Loads a plugin (or every *.py in a directory) in an ISOLATED fresh registry,
checks its PLUGIN manifest + api_version, reports exactly what it registers
(extractors / hooks / processors / config providers / lifecycle), and -- with
--smoke -- fires synthetic events through its hooks/processors to catch obvious
runtime errors. Does NOT launch a browser or touch the network; lifecycle hooks
are validated for registration only.

Stdlib-only; runs under plain python3.

Usage:
    python3 tools/plugin_lint.py docs/plugin_examples/media_server_refresh.py
    python3 tools/plugin_lint.py docs/plugin_examples/        # whole dir
    python3 tools/plugin_lint.py path/to/plugin.py --smoke    # + synthetic fire

Exit 0 if every plugin loads clean; 1 if any error/skip/smoke failure.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _load_isolated(path: Path, allow_full_access: bool):
    """Load one plugin file into a fresh registry. Returns (ok, info)."""
    P.reset()
    if allow_full_access:
        P.set_full_access(True)
    info = {"file": path.name, "ok": False, "manifest": {}, "skipped": "",
            "error": "", "registered": {}}
    # manifest gate replicates load_all's validation
    try:
        spec = importlib.util.spec_from_file_location(f"lint_{path.stem}", str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
        return (False, info)
    ok, man, reason = P._validate_manifest(mod, path.name)
    info["manifest"] = man
    if not ok:
        info["skipped"] = reason
        # still report what it tried to register
    info["registered"] = {
        "extractors": [e["site_id"] for e in P.list_extractors()],
        "hooks": {k: len(v) for k, v in P.list_hooks().items()},
        "processors": [p["name"] for p in P.list_processors()],
        "config_providers": [c["name"] for c in P.list_config_providers()],
        "lifecycle": {k: len(v) for k, v in P.list_lifecycle().items()},
    }
    info["ok"] = ok
    return (ok, info)


_SYNTH_PAYLOAD = {
    "site_id": "lint-site", "url": "https://example.test/v/1",
    "filename": "lint_sample.mp4", "path": "", "file_size": 1234,
    "message": "", "ts": time.time(),
}


def _smoke():
    """Fire synthetic events at whatever registered. Returns list of problems."""
    problems = []
    # download.* hooks
    for ev in ("download.done", "download.failed", "download.needs_review"):
        before = len(P.list_quarantine())
        P.fire_hook(ev, dict(_SYNTH_PAYLOAD))
        after = len(P.list_quarantine())
        if after > before:
            problems.append(f"hook raised on {ev}")
    # processors
    res = P.run_processors(dict(_SYNTH_PAYLOAD))
    for r in res:
        if not r["ok"]:
            problems.append(f"processor {r['name']} failed on synthetic done")
    return problems


def _print(info, smoke_problems):
    tag = "OK  " if info["ok"] else ("SKIP" if info["skipped"] else "ERR ")
    print(f"[{tag}] {info['file']}")
    man = info["manifest"]
    if man:
        print(f"        manifest: {man.get('name','?')} v{man.get('version','?')} "
              f"api={man.get('api_version','?')} caps={man.get('capabilities', [])}")
    else:
        print("        manifest: (none -- recommended to add a PLUGIN dict)")
    if info["skipped"]:
        print(f"        SKIPPED: {info['skipped']}")
    if info["error"]:
        print(f"        ERROR: {info['error']}")
    reg = info["registered"]
    bits = []
    if reg.get("extractors"):
        bits.append(f"extractors={reg['extractors']}")
    if reg.get("hooks"):
        bits.append(f"hooks={reg['hooks']}")
    if reg.get("processors"):
        bits.append(f"processors={reg['processors']}")
    if reg.get("config_providers"):
        bits.append(f"config={reg['config_providers']}")
    if reg.get("lifecycle"):
        bits.append(f"lifecycle={reg['lifecycle']}")
    print(f"        registers: {'; '.join(bits) if bits else '(nothing)'}")
    for p in smoke_problems:
        print(f"        SMOKE FAIL: {p}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lint BulkDownloader plugins")
    ap.add_argument("target", help="a plugin .py file or a directory of them")
    ap.add_argument("--smoke", action="store_true",
                    help="fire synthetic events through hooks/processors")
    ap.add_argument("--full-access", action="store_true",
                    help="lint as if the full-access gate were enabled")
    args = ap.parse_args(argv)

    tgt = Path(args.target)
    if tgt.is_dir():
        files = [p for p in sorted(tgt.glob("*.py")) if not p.name.startswith("_")]
    elif tgt.is_file():
        files = [tgt]
    else:
        print(f"not found: {tgt}", file=sys.stderr)
        return 1
    if not files:
        print(f"no plugin files in {tgt}", file=sys.stderr)
        return 1

    rc = 0
    for f in files:
        ok, info = _load_isolated(f, args.full_access)
        problems = _smoke() if (ok and args.smoke) else []
        _print(info, problems)
        if not ok or problems:
            rc = 1
    P.reset()
    print("-" * 60)
    print("PLUGIN LINT:", "PASS" if rc == 0 else "FAIL")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
