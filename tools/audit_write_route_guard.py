#!/usr/bin/env python3
"""PHC-1 (B1): audit that every state-changing route is covered by the global
CSRF/origin guard.

The guard is ``app.before_request _check_csrf`` (bulk_downloader/app.py): it
enforces the double-submit CSRF token + a same-origin Origin refusal on
state-changing (POST/PUT/PATCH/DELETE) requests, but ONLY for paths under
``/api/`` (``if not path.startswith("/api/"): return None``). A state-changing
route whose rule is NOT under ``/api/`` therefore escapes the guard entirely.

This tool enumerates the live Flask url_map, isolates the write routes, and
FAILS (exit 1) on any write route that is not ``/api/``-prefixed -- so a future
route that adds a write verb off the ``/api/`` tree cannot ship CSRF-unguarded
without tripping this gate. It is read-only (imports the app, walks the map);
it never mutates anything.

Run under ``bd`` (needs Flask):  ``bd python3 tools/audit_write_route_guard.py``
``--json`` emits the machine table the tripwire test consumes.
"""
import json
import os
import sys

# v3.66.763 (DERIVE-AUDIT): the CSRF policy is DERIVED from the app, never re-typed.
# WRITE_METHODS / GUARDED_PREFIXES / KNOWN_API_EXEMPT used to be hand-kept literals
# mirroring bulk_downloader.app's guard -- the same mistake build_endpoint_catalog.py
# was fixed for @748 (one of its copies drifted and reported 28 cockpit writes as
# csrf:false). A CSRF-coverage auditor that re-types the policy it audits is wrong in
# the reassuring direction. Bound LAZILY (the app spawns subsystems at import; the
# bind sets BD_DISABLE_KEEPALIVE first) to the app's OWN objects, not copies.
WRITE_METHODS = None
GUARDED_PREFIXES = None
KNOWN_API_EXEMPT = None


def _bind_csrf_policy():
    """Bind WRITE_METHODS / GUARDED_PREFIXES / KNOWN_API_EXEMPT to the app's own
    CSRF-policy objects (identity, not copies). Safe to call repeatedly."""
    global WRITE_METHODS, GUARDED_PREFIXES, KNOWN_API_EXEMPT
    if GUARDED_PREFIXES is not None:
        return
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    import bulk_downloader.app as _A
    WRITE_METHODS = _A.CSRF_TRIPPING_METHODS
    GUARDED_PREFIXES = _A.CSRF_GUARDED_PREFIXES
    KNOWN_API_EXEMPT = _A.CSRF_EXEMPT_PATHS


def collect_write_routes():
    """Return (write_routes, escapes). Each entry: (rule, sorted_methods).
    ``escapes`` = write routes whose rule is not under /api/ (the gate miss)."""
    _bind_csrf_policy()
    from bulk_downloader.app import app
    write_routes, escapes = [], []
    for rule in app.url_map.iter_rules():
        methods = set(rule.methods or set()) & WRITE_METHODS
        if not methods:
            continue
        entry = (rule.rule, sorted(methods))
        write_routes.append(entry)
        if not rule.rule.startswith(GUARDED_PREFIXES):
            escapes.append(entry)
    write_routes.sort()
    escapes.sort()
    return write_routes, escapes


def main(argv):
    write_routes, escapes = collect_write_routes()
    as_json = "--json" in argv
    if as_json:
        print(json.dumps({
            "write_route_count": len(write_routes),
            "api_prefixed": len(write_routes) - len(escapes),
            "escapes": [{"rule": r, "methods": m} for r, m in escapes],
            "known_api_exempt": sorted(KNOWN_API_EXEMPT),
        }, indent=2))
    else:
        print(f"PHC-1 write-route CSRF/origin coverage")
        print(f"  write routes (POST/PUT/PATCH/DELETE): {len(write_routes)}")
        print(f"  under /api/ (guard-covered):          {len(write_routes) - len(escapes)}")
        print(f"  documented in-guard exemptions:       {len(KNOWN_API_EXEMPT)}")
        if escapes:
            print(f"\n  FAIL -- {len(escapes)} write route(s) escape the /api/ guard gate:")
            for r, m in escapes:
                print(f"    {','.join(m):20s} {r}")
        else:
            print(f"\n  PASS -- every write route is /api/-prefixed (guard-covered).")
    return 1 if escapes else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
