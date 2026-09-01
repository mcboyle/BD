#!/usr/bin/env python3
"""Write the selected credentials into BD's vault via its own import endpoint.

Uses /api/secrets/import_apply, the path the product already has, so only
`@cred:` references reach sites_config.json and the secret itself lands in the
encrypted backend. No password value is printed at any point.

Refuses on a locked backend rather than falling back to anything weaker.
"""
import json, os, pathlib, sys, urllib.error, urllib.request

TOKEN = os.environ["BD_TOKEN"]
BASE = "http://127.0.0.1:5555"


def call(path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")


def main():
    sel = json.loads((pathlib.Path.home() / ".bd-import" / "selected.json").read_text())
    st, before = call("/api/secrets/status", method="GET")
    print(f"before: unlocked={before.get('is_unlocked')} "
          f"initialized={before.get('is_initialized')} "
          f"stored={len(before.get('stored_keys') or [])} "
          f"plaintext={before.get('plaintext_count')}")
    if not before.get("is_unlocked"):
        print("REFUSING: backend is locked", file=sys.stderr)
        return 4

    records = [{"name": f"bulkdl-site-{site}",
                "url": e.get("url", ""),
                "username": e.get("user", ""),
                "password": e.get("password", "")}
               for site, e in sorted(sel.items())]
    status, body = call("/api/secrets/import_apply", {"records": records})
    print(f"import_apply: HTTP {status} ok={body.get('ok')} "
          f"saved={body.get('saved')} skipped={body.get('skipped')} "
          f"{str(body.get('error') or '')[:80]}")
    for err in (body.get("errors") or [])[:8]:
        print("   error:", str(err)[:100])

    st, after = call("/api/secrets/status", method="GET")
    keys = after.get("stored_keys") or []
    print(f"after : initialized={after.get('is_initialized')} "
          f"stored={len(keys)} plaintext={after.get('plaintext_count')}")
    print("stored keys:", ", ".join(sorted(keys))[:400] or "(none)")
    return 0 if keys else 5


if __name__ == "__main__":
    sys.exit(main())
