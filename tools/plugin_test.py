#!/usr/bin/env python3
"""plugin_test.py -- O3 (plugin-v3): dry-run a plugin against the R3 golden.

`plugin test <file>` loads a plugin into an ISOLATED fresh registry and, for
every documented event the plugin subscribes to, synthesizes a payload whose
keys are EXACTLY the R3 hook-payload golden key-set for that event and fires it
through the plugin's hook -- reporting per-event pass/fail. It also dry-runs the
PURE advisory kinds (prefilter / namer / recognizer) with synthetic inputs.

It NEVER performs a real side effect: the I/O-performing kinds -- sink (deliver),
source (poll), enricher (write), processor (post-download action) -- are reported
as registered but are NOT invoked. That is the "never a real download" guarantee:
the harness only ever fires observer hooks and pure-compute kinds with synthetic
data, so it cannot initiate a download, a delivery, a poll, or a file write.

Pairs with plugin_lint.py (manifest + registration linting): plugin_lint answers
"does it load and register cleanly?"; plugin_test answers "does it handle every
documented event payload without crashing?".

POSTURE: read-only; stdlib + project module only; browser/network-free; plain
python3. Exit 0 iff every fired event/kind passed.

Usage:
    python3 tools/plugin_test.py path/to/plugin.py
    python3 tools/plugin_test.py plugins/                # whole dir
    python3 tools/plugin_test.py path/to/plugin.py --json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402

GOLDEN_PATH = _REPO / "tests" / "golden" / "hook_payloads.golden.json"

# Kinds the harness will NEVER invoke (they perform real I/O / side effects).
_SIDE_EFFECTING = ("sink", "source", "enricher", "processor")

# Record of (event, payload) the harness fired this run (for tests / --json).
_LAST_FIRED: list = []


def _last_seen() -> list:
    """The (event, payload) pairs fired by the most recent test_plugin run."""
    return list(_LAST_FIRED)


def _load_golden() -> dict:
    raw = json.loads(Path(GOLDEN_PATH).read_text(encoding="utf-8"))
    return raw.get("events", raw)


# Synthetic value per documented payload key. Unknown keys -> "".
_SYNTH = {
    "site_id": "test-site",
    "url": "https://example.test/v/1",
    "filename": "sample.mp4",
    "path": "/tmp/bd_plugin_test/sample.mp4",
    "file_size": 1234,
    "ts": int(time.time()),
    "message": "synthetic test event",
    "host": "example.test",
    "count": 1,
    "tunnel_id": "tun-test",
    "socks_port": 11080,
    "reason": "synthetic test reason",
    "retries": 1,
    "added": 1,
    "dupes": 0,
    "skipped": 0,
    "done": 1,
    "failed": 0,
    "review": 0,
    "idle_seconds": 0,
    "duration_seconds": 0,
    "key": "plugin:test",
    "fails": 1,
    "last_error": "synthetic test error",
    "enabled": True,
}


def golden_payload(event: str, golden: dict | None = None) -> dict:
    """Build a synthetic payload whose keys are EXACTLY the golden key-set for
    ``event`` (empty dict if the event documents no payload)."""
    g = golden if golden is not None else _load_golden()
    return {k: _SYNTH.get(k, "") for k in g.get(event, [])}


def _load_isolated(path: Path, full_access: bool):
    """Load one plugin file into a fresh registry. Returns (ok, error)."""
    P.reset()
    if full_access:
        try:
            P.set_full_access(True)
        except Exception:
            pass
    try:
        spec = importlib.util.spec_from_file_location(f"pt_{path.stem}", str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        return (False, f"{type(e).__name__}: {e}")
    return (True, "")


def test_plugin(path, full_access: bool = False) -> dict:
    """Dry-run one plugin against the golden. Returns a result dict:

        {file, ok, load_error, events: [{event, ok, error}],
         kinds_fired: [...], side_effecting_skipped: [...]}
    """
    global _LAST_FIRED
    _LAST_FIRED = []
    path = Path(path)
    golden = _load_golden()
    out = {"file": path.name, "ok": False, "load_error": "",
           "events": [], "kinds_fired": [], "side_effecting_skipped": []}

    ok, err = _load_isolated(path, full_access)
    if not ok:
        out["load_error"] = err
        return out

    # (1) fire each subscribed HOOK event with its golden payload. We call the
    # registered fns DIRECTLY (not via fire_hook, which isolates + only
    # quarantines after a budget) so a single raise is reported accurately --
    # detecting "does your hook handle this event's payload?" is the point.
    subscribed = list(P.list_hooks().keys())
    all_ok = True
    for event in subscribed:
        payload = golden_payload(event, golden)
        raised = False
        for fn in list(P._hooks.get(event, [])):
            try:
                fn(dict(payload))
            except Exception:
                raised = True
        _LAST_FIRED.append((event, payload))
        out["events"].append({"event": event, "ok": (not raised),
                              "error": "hook raised" if raised else ""})
        all_ok = all_ok and (not raised)

    # (2) dry-run the PURE advisory kinds (no side effects by construction)
    synth = {k: _SYNTH.get(k, "") for k in
             ("site_id", "url", "filename", "path", "file_size")}
    if P.list_prefilters():
        try:
            P.run_prefilters(synth["url"], dict(synth), {})
            out["kinds_fired"].append("prefilter")
        except Exception:
            all_ok = False
            out["events"].append({"event": "prefilter", "ok": False,
                                  "error": "prefilter raised"})
    if P.list_namers():
        try:
            P.run_namer(dict(synth), {})
            out["kinds_fired"].append("namer")
        except Exception:
            all_ok = False
            out["events"].append({"event": "namer", "ok": False,
                                  "error": "namer raised"})
    if P.list_recognizers():
        try:
            P.run_recognizers("<html></html>", {}, {})
            out["kinds_fired"].append("recognizer")
        except Exception:
            all_ok = False
            out["events"].append({"event": "recognizer", "ok": False,
                                  "error": "recognizer raised"})

    # (3) report -- but NEVER invoke -- the I/O-performing kinds
    for kind, lister in (("sink", P.list_sinks), ("source", P.list_sources),
                         ("enricher", P.list_enrichers),
                         ("processor", P.list_processors)):
        for item in lister():
            out["side_effecting_skipped"].append(item.get("name", "?"))

    out["ok"] = all_ok
    return out


def _print(res) -> None:
    tag = "OK  " if res["ok"] else "FAIL"
    print(f"[{tag}] {res['file']}")
    if res["load_error"]:
        print(f"        LOAD ERROR: {res['load_error']}")
        return
    if not res["events"] and not res["kinds_fired"]:
        print("        (plugin subscribes to no documented events)")
    for e in res["events"]:
        mark = "ok" if e["ok"] else "FAIL"
        extra = "" if e["ok"] else f"  -- {e['error']}"
        print(f"        [{mark}] {e['event']}{extra}")
    if res["kinds_fired"]:
        print(f"        dry-ran pure kinds: {', '.join(res['kinds_fired'])}")
    if res["side_effecting_skipped"]:
        print(f"        registered (NOT invoked -- side-effecting): "
              f"{', '.join(res['side_effecting_skipped'])}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Dry-run a BulkDownloader plugin against the R3 golden")
    ap.add_argument("target", help="a plugin .py file or a directory of them")
    ap.add_argument("--full-access", action="store_true",
                    help="test as if the full-access gate were enabled")
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON results")
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

    results = [test_plugin(f, full_access=args.full_access) for f in files]
    if args.json:
        print(json.dumps(results, indent=1))
    else:
        for r in results:
            _print(r)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
