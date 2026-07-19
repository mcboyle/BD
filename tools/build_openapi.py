#!/usr/bin/env python3
"""
build_openapi.py — OpenAPI 3.1 export for the BulkDownloader API (#20 / P7).

EXTENDS tools/build_endpoint_catalog.py rather than re-deriving anything: it
reuses that module's `_import_app()` (imports the app with BD_DISABLE_KEEPALIVE=1
so the full url_map is registered), `_csrf_fires_for()` (the static CSRF answer),
`_doc_first_line()` (operation summary from the view docstring), and
`_SKIP_METHODS` (HEAD/OPTIONS noise). The result is a machine-readable companion
to the human-readable ENDPOINT_CATALOG.md — same source of truth (app.url_map),
two renderings.

For each route the spec records: the method, a `{param}`-style path (Flask
converters mapped to OpenAPI types), a summary, a tag derived from the path, and
— for routes that `_check_csrf` would 403 — a required `X-CSRF-Token` header plus
`x-csrf-required: true` and a 403 response. Responses are intentionally generic
(200/403); this documents the surface, it is not a hand-authored schema per route.

READ-ONLY: imports the app to read its url_map; writes only the output file (or
stdout). Never serves, mutates, or promotes anything.

Usage:
    python3 tools/build_openapi.py                 # write openapi.json
    python3 tools/build_openapi.py --stdout        # print, don't write
    python3 tools/build_openapi.py --check         # exit 1 if openapi.json is stale
    python3 tools/build_openapi.py --out PATH

Exit 0 = ok / in sync. 1 = stale (with --check). 2 = import/IO error.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # tools/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

import build_endpoint_catalog as BEC  # type: ignore  # noqa: E402

_PARAM_RE = re.compile(r"<(?:([a-zA-Z_]+):)?([^>]+)>")
_CONV_TYPE = {"int": "integer", "float": "number", "path": "string",
              "string": "string", "uuid": "string", "any": "string"}
_OUT_DEFAULT = "openapi.json"


def _version(root):
    try:
        with open(os.path.join(root, "bulk_downloader", "__init__.py")) as fh:
            for ln in fh:
                m = re.search(r'__version__\s*=\s*["\'](\d+\.\d+\.\d+)', ln)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return "0.0.0"


def _flask_path_to_openapi(rule):
    """`/api/sites/<sid>/x/<int:n>` -> ('/api/sites/{sid}/x/{n}', [params])."""
    params = []

    def repl(m):
        conv, name = m.group(1), m.group(2)
        params.append({"name": name, "in": "path", "required": True,
                       "schema": {"type": _CONV_TYPE.get(conv or "string", "string")}})
        return "{" + name + "}"

    return _PARAM_RE.sub(repl, rule), params


def _tag_for(path):
    segs = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    if not segs:
        return "root"
    if segs[0] == "api" and len(segs) > 1:
        return segs[1]
    return segs[0]


def build_openapi_dict(app, version=None):
    """Pure-ish: given a Flask app, return the OpenAPI dict. Used by tests."""
    paths = {}
    tags = set()
    for rule in app.url_map.iter_rules():
        methods = sorted(m for m in (rule.methods or set())
                         if m not in BEC._BORING_METHODS)
        if not methods:
            continue
        oas_path, path_params = _flask_path_to_openapi(rule.rule)
        view_fn = app.view_functions.get(rule.endpoint)
        summary = BEC._doc_first_line(view_fn) if view_fn else ""
        tag = _tag_for(oas_path)
        tags.add(tag)
        item = paths.setdefault(oas_path, {})
        for method in methods:
            csrf = BEC._csrf_fires_for(method, rule.rule)
            params = list(path_params)
            responses = {"200": {"description": "OK"}}
            if csrf:
                params.append({"name": "X-CSRF-Token", "in": "header",
                               "required": True, "schema": {"type": "string"}})
                responses["403"] = {"description": "CSRF token missing/invalid"}
            op = {
                "operationId": f"{method.lower()}_{rule.endpoint}",
                "summary": summary or rule.endpoint,
                "tags": [tag],
                "responses": responses,
                "x-csrf-required": bool(csrf),
            }
            if params:
                op["parameters"] = params
            item[method.lower()] = op
    return {
        "openapi": "3.1.0",
        "info": {"title": "BulkDownloader API",
                 "version": version or "0.0.0",
                 "description": "Auto-generated from app.url_map (companion to "
                                "ENDPOINT_CATALOG.md). Generic responses; documents "
                                "the endpoint surface, not per-route schemas."},
        "tags": [{"name": t} for t in sorted(tags)],
        "paths": dict(sorted(paths.items())),
    }


def generate(root):
    app = BEC._import_app()
    return build_openapi_dict(app, version=_version(root))


def main(argv=None):
    ap = argparse.ArgumentParser(description="OpenAPI 3.1 export (read-only).")
    ap.add_argument("--out", default=_OUT_DEFAULT)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the on-disk export is missing or stale")
    args = ap.parse_args(argv)
    root = str(Path(__file__).resolve().parent.parent)
    try:
        spec = generate(root)
    except Exception as e:  # noqa: BLE001
        print(f"error: could not build OpenAPI: {e}", file=sys.stderr)
        return 2
    text = json.dumps(spec, indent=2, sort_keys=False) + "\n"
    out_path = os.path.join(root, args.out)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        try:
            cur = open(out_path).read()
        except OSError:
            print(f"--check: {args.out} missing; run build_openapi.py", file=sys.stderr)
            return 1
        if cur != text:
            print(f"--check: {args.out} is stale; regenerate with build_openapi.py",
                  file=sys.stderr)
            return 1
        print(f"--check: {args.out} in sync ({len(spec['paths'])} paths)")
        return 0
    with open(out_path, "w") as fh:
        fh.write(text)
    print(f"wrote {args.out}: {len(spec['paths'])} paths, "
          f"{sum(len(v) for v in spec['paths'].values())} operations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
